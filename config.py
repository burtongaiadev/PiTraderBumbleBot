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
    # Channel ID optionnel - si défini, les signaux sont publiés dans le channel
    # Utiliser @username (ex: @pitrader_signals) ou l'ID numérique (ex: -1001234567890)
    channel_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHANNEL_ID", ""))
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
    # Watchlist réduite pour plan gratuit Twelve Data (800 crédits/jour)
    # ~80 stocks = 160 crédits/cycle (quote + time_series)
    # Permet ~4 analyses/jour avec marge de sécurité
    #
    # Composition: Top 50 US + Top 15 CAC40 + Top 15 DAX
    watchlist: List[str] = field(default_factory=lambda: [
        # === TOP 50 US (Mega caps + growth) ===
        "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK.B",
        "LLY", "JPM", "WMT", "V", "ORCL", "MA", "XOM", "JNJ", "PLTR", "BAC",
        "ABBV", "NFLX", "COST", "AMD", "HD", "PG", "GE", "MU", "CSCO", "UNH",
        "KO", "CVX", "CRM", "MCD", "TMO", "ABT", "ISRG", "DIS", "PEP", "QCOM",
        "ADBE", "TXN", "NOW", "UBER", "PANW", "CRWD", "COIN", "DDOG", "SNOW", "SQ",
        # === TOP 15 CAC 40 ===
        "MC.PA", "OR.PA", "RMS.PA", "TTE.PA", "SAN.PA", "AIR.PA", "SU.PA", "AI.PA",
        "BNP.PA", "SAF.PA", "EL.PA", "KER.PA", "DG.PA", "DSY.PA", "STM.PA",
        # === TOP 15 DAX ===
        "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MBG.DE", "BMW.DE", "MUV2.DE",
        "BAS.DE", "IFX.DE", "ADS.DE", "DB1.DE", "DPW.DE", "VOW3.DE", "RWE.DE", "MTX.DE",
    ])

    # === MAPPING TICKER → NOM (pour NewsAPI) ===
    # Réduit pour correspondre à la watchlist de 80 actions
    ticker_names: Dict[str, str] = field(default_factory=lambda: {
        # TOP 50 US
        "NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon",
        "GOOGL": "Google Alphabet", "META": "Meta Facebook", "AVGO": "Broadcom",
        "TSLA": "Tesla", "BRK.B": "Berkshire Hathaway", "LLY": "Eli Lilly", "JPM": "JPMorgan",
        "WMT": "Walmart", "V": "Visa", "ORCL": "Oracle", "MA": "Mastercard",
        "XOM": "ExxonMobil", "JNJ": "Johnson & Johnson", "PLTR": "Palantir", "BAC": "Bank of America",
        "ABBV": "AbbVie", "NFLX": "Netflix", "COST": "Costco", "AMD": "AMD",
        "HD": "Home Depot", "PG": "Procter & Gamble", "GE": "General Electric", "MU": "Micron",
        "CSCO": "Cisco", "UNH": "UnitedHealth", "KO": "Coca-Cola", "CVX": "Chevron",
        "CRM": "Salesforce", "MCD": "McDonald's", "TMO": "Thermo Fisher", "ABT": "Abbott",
        "ISRG": "Intuitive Surgical", "DIS": "Disney", "PEP": "PepsiCo", "QCOM": "Qualcomm",
        "ADBE": "Adobe", "TXN": "Texas Instruments", "NOW": "ServiceNow", "UBER": "Uber",
        "PANW": "Palo Alto Networks", "CRWD": "CrowdStrike", "COIN": "Coinbase",
        "DDOG": "Datadog", "SNOW": "Snowflake", "SQ": "Block Square",
        # TOP 15 CAC 40
        "MC.PA": "LVMH", "OR.PA": "L'Oréal", "RMS.PA": "Hermès", "TTE.PA": "TotalEnergies",
        "SAN.PA": "Sanofi", "AIR.PA": "Airbus", "SU.PA": "Schneider Electric", "AI.PA": "Air Liquide",
        "BNP.PA": "BNP Paribas", "SAF.PA": "Safran", "EL.PA": "EssilorLuxottica",
        "KER.PA": "Kering", "DG.PA": "Vinci", "DSY.PA": "Dassault Systèmes", "STM.PA": "STMicroelectronics",
        # TOP 15 DAX
        "SAP.DE": "SAP", "SIE.DE": "Siemens", "ALV.DE": "Allianz", "DTE.DE": "Deutsche Telekom",
        "MBG.DE": "Mercedes-Benz", "BMW.DE": "BMW", "MUV2.DE": "Munich Re",
        "BAS.DE": "BASF", "IFX.DE": "Infineon", "ADS.DE": "Adidas",
        "DB1.DE": "Deutsche Börse", "DPW.DE": "Deutsche Post", "VOW3.DE": "Volkswagen",
        "RWE.DE": "RWE", "MTX.DE": "MTU Aero",
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
