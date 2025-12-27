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
from utils.decorators import retry_with_backoff, CircuitBreaker
from utils.cache import ttl_lru_cache

logger = logging.getLogger(__name__)

# Circuit breaker pour Twelve Data API
_twelve_data_cb = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=120,
    exceptions=(requests.RequestException, ConnectionError, TimeoutError, ValueError)
)


@dataclass
class StockQuote:
    """Données de prix actuel"""
    symbol: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None  # Volume moyen 20 jours
    volume_ratio: Optional[float] = None  # volume / avg_volume (>2 = anormal)
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
        self._request_times = []  # Fenêtre glissante des requêtes
        self._max_requests_per_minute = config.twelve_data.requests_per_minute
        self._min_delay = config.twelve_data.request_delay

    def _enforce_rate_limit(self, credits_used: int = 1):
        """
        Rate limiting strict avec fenêtre glissante

        Assure qu'on ne dépasse jamais 8 crédits/min en:
        1. Attendant le délai minimum entre requêtes
        2. Vérifiant qu'on n'a pas dépassé 8 crédits dans la dernière minute

        Note: Twelve Data compte 1 crédit par symbole dans les requêtes batch!
        Une requête /quote?symbol=AAPL,MSFT,NVDA = 3 crédits

        Args:
            credits_used: Nombre de crédits que cette requête va utiliser
        """
        now = time.time()

        # 1. Nettoyer les requêtes de plus d'une minute
        self._request_times = [(t, c) for t, c in self._request_times if now - t < 60]

        # 2. Calculer les crédits utilisés dans la dernière minute
        total_credits = sum(c for _, c in self._request_times)

        # 3. Si on va dépasser la limite, attendre
        if total_credits + credits_used > self._max_requests_per_minute:
            if self._request_times:
                oldest_time = self._request_times[0][0]
                wait_time = 60 - (now - oldest_time) + 2  # +2s de marge
                if wait_time > 0:
                    logger.warning(f"Rate limit: {total_credits}/{self._max_requests_per_minute} crédits, waiting {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    now = time.time()
                    self._request_times = [(t, c) for t, c in self._request_times if now - t < 60]

        # 4. Respecter le délai minimum entre requêtes
        if self._request_times:
            elapsed = now - self._request_times[-1][0]
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)

        self._request_times.append((time.time(), credits_used))

    @_twelve_data_cb
    @retry_with_backoff(
        exceptions=(requests.RequestException, ConnectionError, TimeoutError),
        max_retries=3,
        initial_delay=2.0,
        backoff_factor=2.0
    )
    def _request(self, endpoint: str, params: Dict[str, Any], credits: int = 1) -> Dict[str, Any]:
        """
        Requête HTTP vers Twelve Data

        Args:
            endpoint: Endpoint API
            params: Paramètres de la requête
            credits: Nombre de crédits API utilisés (1 par symbole pour batch)
        """
        self._enforce_rate_limit(credits)

        params["apikey"] = self.api_key
        url = f"{self.base_url}{endpoint}"

        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()

        logger.debug(f"TwelveData {endpoint}: {params.get('symbol', 'unknown')} ({credits} crédits)")

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
        """
        Récupère plusieurs quotes en une seule requête batch

        Utilise l'endpoint /quote avec symboles séparés par virgules.
        ATTENTION: Twelve Data compte 1 crédit par symbole dans la requête!
        """
        if not symbols:
            return {}

        results = {}

        # Twelve Data compte 1 crédit par symbole, pas par requête
        credits_needed = len(symbols)

        try:
            # Requête batch: /quote?symbol=AAPL,MSFT,GOOGL
            symbols_str = ",".join(symbols)
            data = self._request("/quote", {"symbol": symbols_str}, credits=credits_needed)

            # TwelveData batch response formats:
            # 1. Single symbol: {"symbol": "AAPL", "close": "150.00", ...}
            # 2. Multiple symbols: {"AAPL": {"symbol": "AAPL", ...}, "MSFT": {...}}
            if isinstance(data, dict):
                if "symbol" in data and len(symbols) == 1:
                    # Réponse unique
                    quote = self._parse_quote_data(data)
                    results[quote.symbol] = quote
                else:
                    # Réponse batch: dict keyed by symbol
                    for key, value in data.items():
                        if isinstance(value, dict):
                            # Vérifier si c'est une erreur pour ce symbole
                            if value.get("status") == "error":
                                error_msg = value.get("message", "Unknown error")
                                results[key] = StockQuote(symbol=key, is_valid=False, error=error_msg)
                            else:
                                quote = self._parse_quote_data(value)
                                results[quote.symbol] = quote
            else:
                # Format inattendu, fallback individuel
                logger.warning("Unexpected batch response format, falling back to individual requests")
                for symbol in symbols:
                    results[symbol] = self.get_quote(symbol)

        except Exception as e:
            logger.error(f"Batch quote failed: {e}, falling back to individual requests")
            for symbol in symbols:
                results[symbol] = self.get_quote(symbol)

        # S'assurer que tous les symboles ont un résultat
        for symbol in symbols:
            if symbol not in results:
                results[symbol] = StockQuote(symbol=symbol, is_valid=False, error="Missing from batch response")

        return results

    def _parse_quote_data(self, data: Dict[str, Any]) -> StockQuote:
        """Parse les données d'une quote depuis la réponse API"""
        symbol = data.get("symbol", "UNKNOWN")
        volume = self._safe_int(data.get("volume"))
        avg_volume = self._safe_int(data.get("average_volume"))

        # Calculer le ratio volume/moyenne
        volume_ratio = None
        if volume and avg_volume and avg_volume > 0:
            volume_ratio = volume / avg_volume

        return StockQuote(
            symbol=symbol,
            price=self._safe_float(data.get("close")),
            change=self._safe_float(data.get("change")),
            change_percent=self._safe_float(data.get("percent_change")),
            volume=volume,
            avg_volume=avg_volume,
            volume_ratio=volume_ratio,
            timestamp=datetime.now(),
            is_valid=True
        )

    def has_abnormal_volume(self, symbol: str, threshold: float = 2.0) -> bool:
        """
        Détecte si le volume est anormalement élevé

        Args:
            symbol: Ticker
            threshold: Ratio minimum (défaut 2.0 = 2x la moyenne)

        Returns:
            True si volume > threshold * moyenne
        """
        quote = self.get_quote(symbol)
        if quote.is_valid and quote.volume_ratio:
            return quote.volume_ratio >= threshold
        return False

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
