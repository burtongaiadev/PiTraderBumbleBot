"""
data/twelve_data.py - Client Twelve Data API

API officielle et stable pour données financières.
Remplace yfinance qui est instable (scraping).

Endpoints utilisés:
- /quote: Prix actuel
- /time_series: Historique prix
- /statistics: Données fondamentales

Plan gratuit: 800 req/jour
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
    # Chaque prix: {datetime, open, high, low, close, volume}
    is_valid: bool = True
    error: Optional[str] = None


class TwelveDataClient:
    """
    Client Twelve Data API

    Caractéristiques:
    - API officielle, stable
    - Retry automatique avec backoff
    - Circuit breaker
    - Cache pour réduire requêtes
    - Rate limiting intégré
    """

    # Circuit breaker partagé
    _circuit_breaker = CircuitBreaker(
        failure_threshold=5,
        recovery_timeout=120,
        exceptions=(requests.RequestException, ConnectionError, TimeoutError)
    )

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
        """
        Requête HTTP vers Twelve Data

        Args:
            endpoint: Endpoint API (ex: "/quote")
            params: Paramètres de requête

        Returns:
            Réponse JSON
        """
        self._enforce_rate_limit()

        params["apikey"] = self.api_key
        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            # Vérifier erreur API
            if "code" in data and data.get("status") == "error":
                raise ValueError(f"API Error: {data.get('message', 'Unknown error')}")

            return data

        except requests.Timeout:
            logger.error(f"Timeout on {endpoint}")
            raise TimeoutError(f"Timeout on {endpoint}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise

    @ttl_lru_cache(maxsize=50, ttl=300)
    def get_quote(self, symbol: str) -> StockQuote:
        """
        Récupère le prix actuel d'une action

        Args:
            symbol: Ticker (ex: "AAPL")

        Returns:
            StockQuote avec prix et variation
        """
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
            logger.error(f"Failed to get quote for {symbol}: {e}")
            return StockQuote(
                symbol=symbol,
                is_valid=False,
                error=str(e)
            )

    @ttl_lru_cache(maxsize=50, ttl=300)
    def get_fundamentals(self, symbol: str) -> StockFundamentals:
        """
        Récupère les données fondamentales

        Args:
            symbol: Ticker

        Returns:
            StockFundamentals avec marges, ratios, etc.
        """
        try:
            # Endpoint statistics pour fondamentaux
            data = self._request("/statistics", {"symbol": symbol})

            stats = data.get("statistics", {})
            financials = stats.get("financials", {})
            balance = financials.get("balance_sheet", {})
            income = financials.get("income_statement", {})
            valuation = stats.get("valuations_metrics", {})

            return StockFundamentals(
                symbol=symbol,
                net_margin=self._safe_float(income.get("net_profit_margin", {}).get("value")),
                gross_margin=self._safe_float(income.get("gross_profit_margin", {}).get("value")),
                operating_margin=self._safe_float(income.get("operating_margin", {}).get("value")),
                debt_to_equity=self._safe_float(balance.get("debt_to_equity", {}).get("value")),
                current_ratio=self._safe_float(balance.get("current_ratio", {}).get("value")),
                roe=self._safe_float(financials.get("return_on_equity", {}).get("value")),
                roa=self._safe_float(financials.get("return_on_assets", {}).get("value")),
                pe_ratio=self._safe_float(valuation.get("trailing_pe", {}).get("value")),
                market_cap=self._safe_float(valuation.get("market_capitalization", {}).get("value")),
                is_valid=True
            )

        except Exception as e:
            logger.error(f"Failed to get fundamentals for {symbol}: {e}")
            return StockFundamentals(
                symbol=symbol,
                is_valid=False,
                error=str(e)
            )

    @memory_efficient
    def get_time_series(
        self,
        symbol: str,
        interval: str = "1day",
        outputsize: int = 30
    ) -> HistoricalData:
        """
        Récupère l'historique des prix

        Args:
            symbol: Ticker
            interval: Intervalle (1min, 5min, 15min, 30min, 45min, 1h, 2h, 4h, 1day, 1week, 1month)
            outputsize: Nombre de points (max 5000)

        Returns:
            HistoricalData avec liste de prix
        """
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

            return HistoricalData(
                symbol=symbol,
                prices=prices,
                is_valid=True
            )

        except Exception as e:
            logger.error(f"Failed to get time series for {symbol}: {e}")
            return HistoricalData(
                symbol=symbol,
                is_valid=False,
                error=str(e)
            )

    def get_index_quote(self, index_symbol: str) -> StockQuote:
        """
        Récupère le prix d'un indice

        Args:
            index_symbol: Symbol de l'indice (SPX, VIX, etc.)

        Returns:
            StockQuote
        """
        return self.get_quote(index_symbol)

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, StockQuote]:
        """
        Récupère plusieurs quotes avec délai

        Args:
            symbols: Liste de tickers

        Returns:
            Dict symbol -> StockQuote
        """
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
            return float(value)
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
