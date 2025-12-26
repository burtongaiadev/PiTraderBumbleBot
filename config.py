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
    # Rate limiting - STRICT pour respecter 8 req/min
    requests_per_minute: int = 8  # Plan gratuit: 800/jour, max 8/min
    request_delay: float = 8.0  # 60s / 8 req = 7.5s minimum, on prend 8s pour marge


@dataclass(frozen=True)
class NewsAPIConfig:
    """Configuration NewsAPI"""
    api_key: str = field(default_factory=lambda: os.getenv("NEWSAPI_KEY", ""))
    base_url: str = "https://newsapi.org/v2"
    timeout: int = 30
    max_retries: int = 3
    # Plan gratuit: 100 req/jour
    requests_per_day: int = 100
    # Sources financières fiables uniquement
    domains: str = ",".join([
        "reuters.com",
        "bloomberg.com",
        "cnbc.com",
        "wsj.com",
        "ft.com",
        "marketwatch.com",
        "finance.yahoo.com",
        "barrons.com",
        "seekingalpha.com",
        "investors.com",
    ])


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
        "AVGO", "LLY", "WMT", "ORCL", "MA"
    ])

    # === MAPPING TICKER → NOM (pour NewsAPI) ===
    ticker_names: Dict[str, str] = field(default_factory=lambda: {
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "GOOGL": "Google Alphabet",
        "AMZN": "Amazon",
        "NVDA": "Nvidia",
        "META": "Meta Facebook",
        "TSLA": "Tesla",
        "JPM": "JPMorgan",
        "V": "Visa",
        "JNJ": "Johnson & Johnson",
        "AVGO": "Broadcom",
        "LLY": "Eli Lilly",
        "WMT": "Walmart",
        "ORCL": "Oracle",
        "MA": "Mastercard",
    })

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
