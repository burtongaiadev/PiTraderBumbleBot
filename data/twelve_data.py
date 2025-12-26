"""
data/twelve_data.py - Client Twelve Data API

Endpoints utilisés:
- /quote: Prix actuel
- /time_series: Historique prix

Plan gratuit: 800 requêtes/jour
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
from utils.decorators import retry_with_backoff
from utils.cache import ttl_lru_cache

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
    """Données fondamentales (estimées depuis price action)"""
    symbol: str
    pe_ratio: Optional[float] = None
    market_cap: Optional[float] = None
    # Scores simplifiés basés sur momentum
    momentum_score: float = 0.0  # -1 à +1
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
    """Client Twelve Data API uniquement"""

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

        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()

        if "code" in data and data.get("status") == "error":
            raise ValueError(f"API Error: {data.get('message', 'Unknown error')}")

        return data

    @ttl_lru_cache(maxsize=50, ttl=300)
    def get_quote(self, symbol: str) -> StockQuote:
        """Récupère le prix actuel via Twelve Data"""
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
            logger.debug(f"Quote failed for {symbol}: {e}")
            return StockQuote(symbol=symbol, is_valid=False, error=str(e))

    @ttl_lru_cache(maxsize=50, ttl=600)
    def get_fundamentals(self, symbol: str) -> StockFundamentals:
        """
        Calcule un score fondamental basé sur le momentum (variation %)

        Note: Les vraies données fondamentales (PE, margins) nécessitent
        un plan Twelve Data payant. On utilise le momentum comme proxy.
        """
        try:
            # Récupérer historique 30 jours pour calculer momentum
            history = self.get_time_series(symbol, interval="1day", outputsize=30)

            if not history.is_valid or len(history.prices) < 5:
                return StockFundamentals(symbol=symbol, is_valid=False, error="No history")

            # Calculer momentum (variation sur 30j)
            prices = history.prices
            if prices[0]["close"] and prices[-1]["close"]:
                first_price = prices[-1]["close"]  # Plus ancien
                last_price = prices[0]["close"]    # Plus récent
                momentum = (last_price - first_price) / first_price

                # Normaliser entre -1 et +1
                momentum_score = max(-1, min(1, momentum * 5))
            else:
                momentum_score = 0.0

            return StockFundamentals(
                symbol=symbol,
                momentum_score=momentum_score,
                is_valid=True
            )

        except Exception as e:
            logger.debug(f"Fundamentals failed for {symbol}: {e}")
            return StockFundamentals(symbol=symbol, is_valid=False, error=str(e))

    def get_time_series(
        self,
        symbol: str,
        interval: str = "1day",
        outputsize: int = 30
    ) -> HistoricalData:
        """Récupère l'historique des prix"""
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
            logger.debug(f"Time series failed for {symbol}: {e}")
            return HistoricalData(symbol=symbol, is_valid=False, error=str(e))

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, StockQuote]:
        """Récupère plusieurs quotes"""
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_quote(symbol)
        return results

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Conversion sécurisée en float"""
        if value is None:
            return None
        try:
            f = float(value)
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
