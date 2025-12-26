"""
data/twelve_data.py - Client données marché

Utilise:
- Twelve Data API pour actions (plan gratuit: 800 req/jour)
- yfinance comme fallback pour indices et fondamentaux

Endpoints Twelve Data:
- /quote: Prix actuel
- /time_series: Historique prix

yfinance pour:
- Indices (^GSPC, ^VIX, ^TNX, DX-Y.NYB)
- Fondamentaux (info)
"""
import requests
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from utils.decorators import retry_with_backoff, rate_limiter, CircuitBreaker
from utils.cache import ttl_lru_cache
from utils.memory import memory_efficient

logger = logging.getLogger(__name__)

# Import yfinance avec gestion d'erreur
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed - some features disabled")


@dataclass
class StockQuote:
    """Données de prix actuel"""
    symbol: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[datetime] = None
    is_valid: bool = True
    error: Optional[str] = None


@dataclass
class StockFundamentals:
    """Données fondamentales"""
    symbol: str
    # Profitabilité
    net_margin: Optional[float] = None  # En pourcentage
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    # Structure financière
    debt_to_equity: Optional[float] = None  # Ratio
    current_ratio: Optional[float] = None
    # Rentabilité
    roe: Optional[float] = None  # Return on Equity en %
    roa: Optional[float] = None  # Return on Assets en %
    # Valorisation
    pe_ratio: Optional[float] = None
    market_cap: Optional[float] = None
    # Metadata
    is_valid: bool = True
    error: Optional[str] = None


@dataclass
class HistoricalData:
    """Données historiques"""
    symbol: str
    prices: List[Dict[str, Any]] = field(default_factory=list)
    is_valid: bool = True
    error: Optional[str] = None


class TwelveDataClient:
    """
    Client données marché hybride

    - Twelve Data pour actions (quotes)
    - yfinance pour indices et fondamentaux
    """

    def __init__(self):
        self.api_key = config.twelve_data.api_key
        self.base_url = config.twelve_data.base_url
        self.timeout = config.twelve_data.timeout
        self._last_request_time = 0.0
        self._request_delay = config.twelve_data.request_delay

    def _enforce_rate_limit(self):
        """Respecte le délai entre requêtes"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._request_delay:
            time.sleep(self._request_delay - elapsed)
        self._last_request_time = time.time()

    @retry_with_backoff(
        exceptions=(requests.RequestException, ConnectionError, TimeoutError),
        max_retries=3,
        initial_delay=2.0,
        backoff_factor=2.0
    )
    def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Requête HTTP vers Twelve Data"""
        self._enforce_rate_limit()

        params["apikey"] = self.api_key
        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if "code" in data and data.get("status") == "error":
                raise ValueError(f"API Error: {data.get('message', 'Unknown error')}")

            return data

        except requests.Timeout:
            raise TimeoutError(f"Timeout on {endpoint}")
        except requests.RequestException as e:
            raise

    def _is_index(self, symbol: str) -> bool:
        """Vérifie si le symbol est un indice"""
        index_symbols = ["SPX", "VIX", "TNX", "DXY", "^GSPC", "^VIX", "^TNX", "DX-Y.NYB"]
        return symbol.upper() in [s.upper() for s in index_symbols]

    def _get_yf_symbol(self, symbol: str) -> str:
        """Convertit le symbol en format yfinance"""
        mapping = {
            "SPX": "^GSPC",
            "VIX": "^VIX",
            "TNX": "^TNX",
            "DXY": "DX-Y.NYB"
        }
        return mapping.get(symbol.upper(), symbol)

    @ttl_lru_cache(maxsize=50, ttl=300)
    def get_quote(self, symbol: str) -> StockQuote:
        """
        Récupère le prix actuel

        Utilise yfinance pour les indices, Twelve Data pour les actions
        """
        # Pour les indices, utiliser yfinance
        if self._is_index(symbol):
            return self._get_quote_yfinance(symbol)

        # Pour les actions, essayer Twelve Data
        try:
            data = self._request("/quote", {"symbol": symbol})

            return StockQuote(
                symbol=symbol,
                price=self._safe_float(data.get("close")),
                change=self._safe_float(data.get("change")),
                change_percent=self._safe_float(data.get("percent_change")),
                volume=self._safe_int(data.get("volume")),
                timestamp=datetime.now(),
                is_valid=True
            )

        except Exception as e:
            logger.warning(f"Twelve Data failed for {symbol}, trying yfinance: {e}")
            return self._get_quote_yfinance(symbol)

    def _get_quote_yfinance(self, symbol: str) -> StockQuote:
        """Récupère quote via yfinance"""
        if not YFINANCE_AVAILABLE:
            return StockQuote(symbol=symbol, is_valid=False, error="yfinance not available")

        try:
            yf_symbol = self._get_yf_symbol(symbol)
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info

            price = info.get("regularMarketPrice") or info.get("previousClose")
            change = info.get("regularMarketChange")
            change_pct = info.get("regularMarketChangePercent")

            return StockQuote(
                symbol=symbol,
                price=self._safe_float(price),
                change=self._safe_float(change),
                change_percent=self._safe_float(change_pct),
                volume=self._safe_int(info.get("regularMarketVolume")),
                timestamp=datetime.now(),
                is_valid=price is not None
            )

        except Exception as e:
            logger.error(f"yfinance failed for {symbol}: {e}")
            return StockQuote(symbol=symbol, is_valid=False, error=str(e))

    @ttl_lru_cache(maxsize=50, ttl=300)
    def get_fundamentals(self, symbol: str) -> StockFundamentals:
        """
        Récupère les données fondamentales via yfinance

        Twelve Data /statistics nécessite plan Pro
        """
        if not YFINANCE_AVAILABLE:
            return StockFundamentals(symbol=symbol, is_valid=False, error="yfinance not available")

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            return StockFundamentals(
                symbol=symbol,
                net_margin=self._safe_float(info.get("profitMargins")),
                gross_margin=self._safe_float(info.get("grossMargins")),
                operating_margin=self._safe_float(info.get("operatingMargins")),
                debt_to_equity=self._safe_float(info.get("debtToEquity")),
                current_ratio=self._safe_float(info.get("currentRatio")),
                roe=self._safe_float(info.get("returnOnEquity")),
                roa=self._safe_float(info.get("returnOnAssets")),
                pe_ratio=self._safe_float(info.get("trailingPE")),
                market_cap=self._safe_float(info.get("marketCap")),
                is_valid=True
            )

        except Exception as e:
            logger.error(f"Failed to get fundamentals for {symbol}: {e}")
            return StockFundamentals(symbol=symbol, is_valid=False, error=str(e))

    @memory_efficient
    def get_time_series(
        self,
        symbol: str,
        interval: str = "1day",
        outputsize: int = 30
    ) -> HistoricalData:
        """
        Récupère l'historique des prix

        Utilise yfinance pour les indices, Twelve Data pour les actions
        """
        if self._is_index(symbol):
            return self._get_time_series_yfinance(symbol, outputsize)

        try:
            data = self._request("/time_series", {
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize
            })

            values = data.get("values", [])
            prices = []

            for v in values:
                prices.append({
                    "datetime": v.get("datetime"),
                    "open": self._safe_float(v.get("open")),
                    "high": self._safe_float(v.get("high")),
                    "low": self._safe_float(v.get("low")),
                    "close": self._safe_float(v.get("close")),
                    "volume": self._safe_int(v.get("volume"))
                })

            return HistoricalData(symbol=symbol, prices=prices, is_valid=True)

        except Exception as e:
            logger.warning(f"Twelve Data failed for {symbol} history, trying yfinance")
            return self._get_time_series_yfinance(symbol, outputsize)

    def _get_time_series_yfinance(self, symbol: str, days: int = 30) -> HistoricalData:
        """Récupère historique via yfinance"""
        if not YFINANCE_AVAILABLE:
            return HistoricalData(symbol=symbol, is_valid=False, error="yfinance not available")

        try:
            yf_symbol = self._get_yf_symbol(symbol)
            ticker = yf.Ticker(yf_symbol)

            # Déterminer période
            period = "1mo" if days <= 30 else "3mo" if days <= 90 else "1y"
            df = ticker.history(period=period)

            if df.empty:
                return HistoricalData(symbol=symbol, is_valid=False, error="No data")

            prices = []
            for idx, row in df.iterrows():
                prices.append({
                    "datetime": idx.strftime("%Y-%m-%d"),
                    "open": self._safe_float(row.get("Open")),
                    "high": self._safe_float(row.get("High")),
                    "low": self._safe_float(row.get("Low")),
                    "close": self._safe_float(row.get("Close")),
                    "volume": self._safe_int(row.get("Volume"))
                })

            return HistoricalData(symbol=symbol, prices=prices, is_valid=True)

        except Exception as e:
            logger.error(f"yfinance history failed for {symbol}: {e}")
            return HistoricalData(symbol=symbol, is_valid=False, error=str(e))

    def get_index_quote(self, index_symbol: str) -> StockQuote:
        """Récupère le prix d'un indice (via yfinance)"""
        return self._get_quote_yfinance(index_symbol)

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, StockQuote]:
        """Récupère plusieurs quotes avec délai"""
        results = {}
        for i, symbol in enumerate(symbols):
            logger.info(f"Fetching quote {i+1}/{len(symbols)}: {symbol}")
            results[symbol] = self.get_quote(symbol)
        return results

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Conversion sécurisée en float"""
        if value is None:
            return None
        try:
            f = float(value)
            # Vérifier NaN
            if f != f:  # NaN check
                return None
            return f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """Conversion sécurisée en int"""
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None


# Instance singleton
twelve_data_client = TwelveDataClient()
