"""
config.py - Configuration centralisée pour PiTrader

Architecture Top-Down:
1. D'abord l'économie (macro)
2. Ensuite le marché (context)
3. Puis l'entreprise (fundamentals + sentiment)

Optimisé pour Raspberry Pi 5 (4GB RAM)
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict
from pathlib import Path

# Charger variables d'environnement
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optionnel


@dataclass(frozen=True)
class TelegramConfig:
    """Configuration Telegram"""
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    enabled: bool = True


@dataclass(frozen=True)
class OllamaConfig:
    """Configuration Ollama pour analyse sentiment"""
    model: str = "qwen2.5:1.5b"  # Modèle léger pour Pi
    base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    timeout: int = 120  # Secondes - important pour RPi
    max_retries: int = 3
    num_ctx: int = 2048  # Contexte réduit pour économiser RAM
    num_thread: int = 4  # Threads limités pour éviter surchauffe


@dataclass(frozen=True)
class TwelveDataConfig:
    """Configuration Twelve Data API"""
    api_key: str = field(default_factory=lambda: os.getenv("TWELVEDATA_API_KEY", ""))
    base_url: str = "https://api.twelvedata.com"
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 2.0
    # Rate limiting
    requests_per_minute: int = 8  # Plan gratuit: 800/jour ≈ 8/min pour être safe
    request_delay: float = 1.5  # Délai entre requêtes


@dataclass(frozen=True)
class NewsAPIConfig:
    """Configuration NewsAPI"""
    api_key: str = field(default_factory=lambda: os.getenv("NEWSAPI_KEY", ""))
    base_url: str = "https://newsapi.org/v2"
    timeout: int = 30
    max_retries: int = 3
    # Plan gratuit: 100 req/jour
    requests_per_day: int = 100


@dataclass(frozen=True)
class CacheConfig:
    """Configuration cache - optimisé pour 4GB RAM"""
    # Tailles des caches LRU
    market_cache_size: int = 50
    news_cache_size: int = 100
    sentiment_cache_size: int = 200
    # TTL en secondes
    market_ttl: int = 300       # 5 minutes
    news_ttl: int = 900         # 15 minutes
    sentiment_ttl: int = 3600   # 1 heure


@dataclass(frozen=True)
class ThermalConfig:
    """Gestion thermique pour Raspberry Pi"""
    cpu_temp_warning: float = 70.0   # Celsius
    cpu_temp_critical: float = 80.0
    cooldown_delay: float = 5.0      # Secondes de pause si temp élevée
    inter_request_delay: float = 1.0  # Délai standard entre requêtes


@dataclass(frozen=True)
class ScoringConfig:
    """Seuils de scoring pour l'analyse"""

    # === MACRO ECONOMY (score: -3 à +1) ===
    # Taux 10 ans US
    treasury_10y_high: float = 4.5   # Au-dessus = négatif (-2)
    treasury_10y_low: float = 3.0    # En-dessous = positif (+1)
    treasury_spike_threshold: float = 0.03  # +3% en 24h = DANGER

    # Dollar Index
    dxy_high: float = 105.0   # Dollar fort = négatif pour actions US (-1)
    dxy_low: float = 100.0    # Dollar faible = positif (+1)

    # === MARKET CONTEXT (score: -2 à +1) ===
    sp500_bear_threshold: float = -20.0  # % depuis ATH = Bear Market
    sp500_correction_threshold: float = -10.0  # Correction
    vix_high: float = 25.0      # Volatilité élevée
    vix_extreme: float = 35.0   # Volatilité extrême

    # === FUNDAMENTALS (score: 0 à 5) ===
    # Marge Nette (0-2 points)
    net_margin_excellent: float = 20.0  # % -> +2
    net_margin_good: float = 5.0        # % -> +1

    # Dette/Equity (0-2 points)
    debt_equity_excellent: float = 0.5  # ratio -> +2
    debt_equity_good: float = 1.5       # ratio -> +1

    # ROE (0-1 point)
    roe_good: float = 10.0  # % -> +1

    # === SENTIMENT (score: 0 à 3) ===
    # Nombre d'articles à analyser
    news_count: int = 5

    # === SEUIL D'ALERTE ===
    alert_threshold: float = 7.5  # Score minimum pour envoyer alerte


@dataclass
class Config:
    """Configuration principale PiTrader"""

    # === WATCHLIST ===
    watchlist: List[str] = field(default_factory=lambda: [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "JPM", "V", "JNJ",
        "AVGO", "LLY", "JPM", "WMT", "ORCL", "MA"
    ])

    # === INDICES DE RÉFÉRENCE ===
    # Symbols Twelve Data pour les indices
    market_indices: Dict[str, str] = field(default_factory=lambda: {
        "sp500": "SPX",        # S&P 500
        "vix": "VIX",          # Volatility Index
        "treasury_10y": "TNX", # 10-Year Treasury Yield (peut nécessiter ajustement)
        "dollar_index": "DXY"  # Dollar Index
    })

    # === MOTS-CLÉS MACRO ===
    macro_keywords: List[str] = field(default_factory=lambda: [
        "Federal Reserve", "Fed", "Jerome Powell",
        "interest rate", "inflation", "CPI",
        "employment", "GDP", "recession"
    ])

    # === SOUS-CONFIGURATIONS ===
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    twelve_data: TwelveDataConfig = field(default_factory=TwelveDataConfig)
    news_api: NewsAPIConfig = field(default_factory=NewsAPIConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    thermal: ThermalConfig = field(default_factory=ThermalConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    # === CHEMINS ===
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent)

    @property
    def runtime_dir(self) -> Path:
        return self.base_dir / "runtime_data"

    @property
    def cache_dir(self) -> Path:
        return self.runtime_dir / "cache"

    @property
    def signals_dir(self) -> Path:
        return self.runtime_dir / "signals"

    def ensure_dirs(self):
        """Crée les répertoires nécessaires"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.signals_dir.mkdir(parents=True, exist_ok=True)


# Instance globale
config = Config()
config.ensure_dirs()
