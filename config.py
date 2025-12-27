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
    # S&P 500 (503 stocks) + Top 30 CAC 40 + Top 30 DAX
    watchlist: List[str] = field(default_factory=lambda: [
        # === S&P 500 - 503 stocks ===
        "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "AVGO", "TSLA", "BRK.B",
        "LLY", "JPM", "WMT", "V", "ORCL", "MA", "XOM", "JNJ", "PLTR", "BAC",
        "ABBV", "NFLX", "COST", "AMD", "HD", "PG", "GE", "MU", "CSCO", "UNH",
        "KO", "CVX", "WFC", "MS", "IBM", "CAT", "GS", "MRK", "AXP", "PM",
        "CRM", "RTX", "APP", "TMUS", "LRCX", "MCD", "TMO", "ABT", "C", "AMAT",
        "ISRG", "DIS", "LIN", "PEP", "INTU", "QCOM", "SCHW", "GEV", "AMGN", "BKNG",
        "T", "TJX", "INTC", "VZ", "BA", "UBER", "BLK", "APH", "KLAC", "NEE",
        "ACN", "ANET", "DHR", "TXN", "SPGI", "NOW", "COF", "GILD", "ADBE", "PFE",
        "BSX", "UNP", "LOW", "ADI", "SYK", "PGR", "PANW", "WELL", "DE", "HON",
        "ETN", "MDT", "CB", "CRWD", "BX", "PLD", "VRTX", "KKR", "NEM", "COP",
        "CEG", "PH", "LMT", "BMY", "HCA", "CMCSA", "HOOD", "ADP", "MCK", "CVS",
        "DASH", "CME", "SBUX", "MO", "SO", "ICE", "MCO", "GD", "MMC", "SNPS",
        "DUK", "NKE", "WM", "TT", "CDNS", "CRH", "APO", "MMM", "DELL", "USB",
        "UPS", "HWM", "MAR", "PNC", "ABNB", "AMT", "REGN", "NOC", "BK", "SHW",
        "RCL", "ORLY", "ELV", "GM", "CTAS", "GLW", "AON", "EMR", "FCX", "MNST",
        "ECL", "EQIX", "JCI", "CI", "TDG", "ITW", "WMB", "CMI", "WBD", "MDLZ",
        "FDX", "TEL", "HLT", "CSX", "AJG", "COR", "RSG", "NSC", "TRV", "TFC",
        "PWR", "CL", "COIN", "ADSK", "MSI", "STX", "WDC", "CVNA", "AEP", "SPG",
        "FTNT", "KMI", "PCAR", "ROST", "WDAY", "SRE", "AFL", "AZO", "NDAQ", "SLB",
        "EOG", "PYPL", "NXPI", "BDX", "ZTS", "LHX", "APD", "IDXX", "VST", "ALL",
        "DLR", "F", "MET", "URI", "O", "PSX", "EA", "D", "EW", "VLO",
        "CMG", "CAH", "MPC", "CBRE", "GWW", "ROP", "DDOG", "AME", "FAST", "TTWO",
        "AIG", "AMP", "AXON", "DAL", "OKE", "PSA", "CTVA", "MPWR", "CARR", "TGT",
        "ROK", "LVS", "BKR", "XEL", "MSCI", "EXC", "DHI", "YUM", "FANG", "FICO",
        "ETR", "CTSH", "PAYX", "CCL", "XYZ", "PEG", "KR", "PRU", "GRMN", "TRGP",
        "OXY", "A", "MLM", "VMC", "EL", "HIG", "IQV", "EBAY", "CCI", "KDP",
        "GEHC", "NUE", "CPRT", "WAB", "VTR", "HSY", "ARES", "STT", "UAL", "SNDK",
        "FISV", "ED", "RMD", "SYY", "KEYS", "EXPE", "MCHP", "FIS", "ACGL", "PCG",
        "WEC", "OTIS", "FIX", "LYV", "XYL", "EQT", "KMB", "ODFL", "KVUE", "HPE",
        "RJF", "IR", "WTW", "FITB", "MTB", "TER", "HUM", "SYF", "NRG", "VRSK",
        "DG", "VICI", "IBKR", "ROL", "MTD", "FSLR", "KHC", "CSGP", "EME", "HBAN",
        "ADM", "EXR", "BRO", "DOV", "ATO", "EFX", "TSCO", "AEE", "ULTA", "TPR",
        "WRB", "CHTR", "CBOE", "DTE", "BR", "NTRS", "DXCM", "EXE", "BIIB", "PPL",
        "AVB", "FE", "LEN", "CINF", "CFG", "STLD", "AWK", "VLTO", "ES", "JBL",
        "OMC", "GIS", "STE", "CNP", "DLTR", "LULU", "RF", "TDY", "STZ", "IRM",
        "HUBB", "EQR", "LDOS", "HAL", "PPG", "PHM", "KEY", "WAT", "EIX", "TROW",
        "VRSN", "WSM", "DVN", "ON", "L", "DRI", "NTAP", "RL", "CPAY", "HPQ",
        "LUV", "CMS", "IP", "LH", "PTC", "TSN", "SBAC", "CHD", "EXPD", "PODD",
        "SW", "NVR", "CNC", "TYL", "TPL", "NI", "WST", "INCY", "PFG", "CTRA",
        "DGX", "CHRW", "AMCR", "TRMB", "GPN", "JBHT", "PKG", "TTD", "MKC", "SNA",
        "SMCI", "IT", "CDW", "ZBH", "FTV", "ALB", "Q", "GPC", "LII", "PNR",
        "DD", "IFF", "BG", "GDDY", "TKO", "GEN", "WY", "ESS", "INVH", "LNT",
        "EVRG", "APTV", "HOLX", "DOW", "COO", "MAA", "J", "TXT", "FOXA", "FOX",
        "FFIV", "DECK", "PSKY", "ERIE", "BBY", "DPZ", "UHS", "VTRS", "EG", "BALL",
        # === CAC 40 - Top 30 ===
        "MC.PA", "OR.PA", "RMS.PA", "TTE.PA", "SAN.PA", "AIR.PA", "SU.PA", "AI.PA",
        "BNP.PA", "CS.PA", "SAF.PA", "EL.PA", "KER.PA", "DG.PA", "RI.PA", "CAP.PA",
        "SGO.PA", "BN.PA", "ENGI.PA", "ACA.PA", "VIV.PA", "DSY.PA", "PUB.PA", "STM.PA",
        "LR.PA", "ML.PA", "GLE.PA", "ORA.PA", "HO.PA", "WLN.PA",
        # === DAX 40 - Top 30 ===
        "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BMW.DE", "MUV2.DE",
        "BAS.DE", "BAYN.DE", "IFX.DE", "ADS.DE", "DB1.DE", "DPW.DE", "HEN3.DE", "SHL.DE",
        "VOW3.DE", "RWE.DE", "DBK.DE", "EOAN.DE", "FRE.DE", "MTX.DE", "BEI.DE", "HEI.DE",
        "CON.DE", "MRK.DE", "VNA.DE", "FME.DE", "SY1.DE", "PAH3.DE",
    ])

    # === MAPPING TICKER → NOM (pour NewsAPI) ===
    ticker_names: Dict[str, str] = field(default_factory=lambda: {
        # S&P 500 - 503 stocks
        "NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon",
        "GOOGL": "Google Alphabet", "GOOG": "Google Alphabet", "META": "Meta Facebook", "AVGO": "Broadcom",
        "TSLA": "Tesla", "BRK.B": "Berkshire Hathaway", "LLY": "Eli Lilly", "JPM": "JPMorgan",
        "WMT": "Walmart", "V": "Visa", "ORCL": "Oracle", "MA": "Mastercard",
        "XOM": "ExxonMobil", "JNJ": "Johnson & Johnson", "PLTR": "Palantir", "BAC": "Bank of America",
        "ABBV": "AbbVie", "NFLX": "Netflix", "COST": "Costco", "AMD": "AMD",
        "HD": "Home Depot", "PG": "Procter & Gamble", "GE": "General Electric", "MU": "Micron",
        "CSCO": "Cisco", "UNH": "UnitedHealth", "KO": "Coca-Cola", "CVX": "Chevron",
        "WFC": "Wells Fargo", "MS": "Morgan Stanley", "IBM": "IBM", "CAT": "Caterpillar",
        "GS": "Goldman Sachs", "MRK": "Merck", "AXP": "American Express", "PM": "Philip Morris",
        "CRM": "Salesforce", "RTX": "Raytheon", "APP": "AppLovin", "TMUS": "T-Mobile",
        "LRCX": "Lam Research", "MCD": "McDonald's", "TMO": "Thermo Fisher", "ABT": "Abbott",
        "C": "Citigroup", "AMAT": "Applied Materials", "ISRG": "Intuitive Surgical", "DIS": "Disney",
        "LIN": "Linde", "PEP": "PepsiCo", "INTU": "Intuit", "QCOM": "Qualcomm",
        "SCHW": "Charles Schwab", "GEV": "GE Vernova", "AMGN": "Amgen", "BKNG": "Booking",
        "T": "AT&T", "TJX": "TJX Companies", "INTC": "Intel", "VZ": "Verizon",
        "BA": "Boeing", "UBER": "Uber", "BLK": "BlackRock", "APH": "Amphenol",
        "KLAC": "KLA Corporation", "NEE": "NextEra Energy", "ACN": "Accenture", "ANET": "Arista Networks",
        "DHR": "Danaher", "TXN": "Texas Instruments", "SPGI": "S&P Global", "NOW": "ServiceNow",
        "COF": "Capital One", "GILD": "Gilead Sciences", "ADBE": "Adobe", "PFE": "Pfizer",
        "BSX": "Boston Scientific", "UNP": "Union Pacific", "LOW": "Lowe's", "ADI": "Analog Devices",
        "SYK": "Stryker", "PGR": "Progressive", "PANW": "Palo Alto Networks", "WELL": "Welltower",
        "DE": "John Deere", "HON": "Honeywell", "ETN": "Eaton", "MDT": "Medtronic",
        "CB": "Chubb", "CRWD": "CrowdStrike", "BX": "Blackstone", "PLD": "Prologis",
        "VRTX": "Vertex Pharmaceuticals", "KKR": "KKR", "NEM": "Newmont", "COP": "ConocoPhillips",
        "CEG": "Constellation Energy", "PH": "Parker Hannifin", "LMT": "Lockheed Martin", "BMY": "Bristol-Myers Squibb",
        "HCA": "HCA Healthcare", "CMCSA": "Comcast", "HOOD": "Robinhood", "ADP": "ADP",
        "MCK": "McKesson", "CVS": "CVS Health", "DASH": "DoorDash", "CME": "CME Group",
        "SBUX": "Starbucks", "MO": "Altria", "SO": "Southern Company", "ICE": "Intercontinental Exchange",
        "MCO": "Moody's", "GD": "General Dynamics", "MMC": "Marsh McLennan", "SNPS": "Synopsys",
        "DUK": "Duke Energy", "NKE": "Nike", "WM": "Waste Management", "TT": "Trane Technologies",
        "CDNS": "Cadence Design", "CRH": "CRH", "APO": "Apollo Global", "MMM": "3M",
        "DELL": "Dell Technologies", "USB": "U.S. Bancorp", "UPS": "UPS", "HWM": "Howmet Aerospace",
        "MAR": "Marriott", "PNC": "PNC Financial", "ABNB": "Airbnb", "AMT": "American Tower",
        "REGN": "Regeneron", "NOC": "Northrop Grumman", "BK": "Bank of New York Mellon", "SHW": "Sherwin-Williams",
        "RCL": "Royal Caribbean", "ORLY": "O'Reilly Auto Parts", "ELV": "Elevance Health", "GM": "General Motors",
        "CTAS": "Cintas", "GLW": "Corning", "AON": "Aon", "EMR": "Emerson Electric",
        "FCX": "Freeport-McMoRan", "MNST": "Monster Beverage", "ECL": "Ecolab", "EQIX": "Equinix",
        "JCI": "Johnson Controls", "CI": "Cigna", "TDG": "TransDigm", "ITW": "Illinois Tool Works",
        "WMB": "Williams Companies", "CMI": "Cummins", "WBD": "Warner Bros Discovery", "MDLZ": "Mondelez",
        "FDX": "FedEx", "TEL": "TE Connectivity", "HLT": "Hilton", "CSX": "CSX",
        "AJG": "Arthur J Gallagher", "COR": "Cencora", "RSG": "Republic Services", "NSC": "Norfolk Southern",
        "TRV": "Travelers", "TFC": "Truist Financial", "PWR": "Quanta Services", "CL": "Colgate-Palmolive",
        "COIN": "Coinbase", "ADSK": "Autodesk", "MSI": "Motorola Solutions", "STX": "Seagate",
        "WDC": "Western Digital", "CVNA": "Carvana", "AEP": "American Electric Power", "SPG": "Simon Property",
        "FTNT": "Fortinet", "KMI": "Kinder Morgan", "PCAR": "PACCAR", "ROST": "Ross Stores",
        "WDAY": "Workday", "SRE": "Sempra", "AFL": "Aflac", "AZO": "AutoZone",
        "NDAQ": "Nasdaq", "SLB": "Schlumberger", "EOG": "EOG Resources", "PYPL": "PayPal",
        "NXPI": "NXP Semiconductors", "BDX": "Becton Dickinson", "ZTS": "Zoetis", "LHX": "L3Harris",
        "APD": "Air Products", "IDXX": "IDEXX Laboratories", "VST": "Vistra", "ALL": "Allstate",
        "DLR": "Digital Realty", "F": "Ford", "MET": "MetLife", "URI": "United Rentals",
        "O": "Realty Income", "PSX": "Phillips 66", "EA": "Electronic Arts", "D": "Dominion Energy",
        "EW": "Edwards Lifesciences", "VLO": "Valero Energy", "CMG": "Chipotle", "CAH": "Cardinal Health",
        "MPC": "Marathon Petroleum", "CBRE": "CBRE Group", "GWW": "Grainger", "ROP": "Roper Technologies",
        "DDOG": "Datadog", "AME": "AMETEK", "FAST": "Fastenal", "TTWO": "Take-Two Interactive",
        "AIG": "AIG", "AMP": "Ameriprise", "AXON": "Axon Enterprise", "DAL": "Delta Air Lines",
        "OKE": "ONEOK", "PSA": "Public Storage", "CTVA": "Corteva", "MPWR": "Monolithic Power",
        "CARR": "Carrier Global", "TGT": "Target", "ROK": "Rockwell Automation", "LVS": "Las Vegas Sands",
        "BKR": "Baker Hughes", "XEL": "Xcel Energy", "MSCI": "MSCI", "EXC": "Exelon",
        "DHI": "D.R. Horton", "YUM": "Yum! Brands", "FANG": "Diamondback Energy", "FICO": "Fair Isaac",
        "ETR": "Entergy", "CTSH": "Cognizant", "PAYX": "Paychex", "CCL": "Carnival",
        "XYZ": "Block", "PEG": "Public Service Enterprise", "KR": "Kroger", "PRU": "Prudential",
        "GRMN": "Garmin", "TRGP": "Targa Resources", "OXY": "Occidental Petroleum", "A": "Agilent Technologies",
        "MLM": "Martin Marietta", "VMC": "Vulcan Materials", "EL": "Estee Lauder", "HIG": "Hartford Financial",
        "IQV": "IQVIA", "EBAY": "eBay", "CCI": "Crown Castle", "KDP": "Keurig Dr Pepper",
        "GEHC": "GE HealthCare", "NUE": "Nucor", "CPRT": "Copart", "WAB": "Westinghouse Air Brake",
        "VTR": "Ventas", "HSY": "Hershey", "ARES": "Ares Management", "STT": "State Street",
        "UAL": "United Airlines", "SNDK": "SanDisk", "FISV": "Fiserv", "ED": "Consolidated Edison",
        "RMD": "ResMed", "SYY": "Sysco", "KEYS": "Keysight Technologies", "EXPE": "Expedia",
        "MCHP": "Microchip Technology", "FIS": "Fidelity National", "ACGL": "Arch Capital", "PCG": "PG&E",
        "WEC": "WEC Energy", "OTIS": "Otis Worldwide", "FIX": "Comfort Systems", "LYV": "Live Nation",
        "XYL": "Xylem", "EQT": "EQT Corporation", "KMB": "Kimberly-Clark", "ODFL": "Old Dominion Freight",
        "KVUE": "Kenvue", "HPE": "Hewlett Packard Enterprise", "RJF": "Raymond James", "IR": "Ingersoll Rand",
        "WTW": "Willis Towers Watson", "FITB": "Fifth Third", "MTB": "M&T Bank", "TER": "Teradyne",
        "HUM": "Humana", "SYF": "Synchrony Financial", "NRG": "NRG Energy", "VRSK": "Verisk Analytics",
        "DG": "Dollar General", "VICI": "VICI Properties", "IBKR": "Interactive Brokers", "ROL": "Rollins",
        "MTD": "Mettler-Toledo", "FSLR": "First Solar", "KHC": "Kraft Heinz", "CSGP": "CoStar Group",
        "EME": "EMCOR Group", "HBAN": "Huntington Bancshares", "ADM": "Archer-Daniels-Midland", "EXR": "Extra Space Storage",
        "BRO": "Brown & Brown", "DOV": "Dover", "ATO": "Atmos Energy", "EFX": "Equifax",
        "TSCO": "Tractor Supply", "AEE": "Ameren", "ULTA": "Ulta Beauty", "TPR": "Tapestry",
        "WRB": "W.R. Berkley", "CHTR": "Charter Communications", "CBOE": "Cboe Global Markets", "DTE": "DTE Energy",
        "BR": "Broadridge Financial", "NTRS": "Northern Trust", "DXCM": "DexCom", "EXE": "Expand Energy",
        "BIIB": "Biogen", "PPL": "PPL Corporation", "AVB": "AvalonBay Communities", "FE": "FirstEnergy",
        "LEN": "Lennar", "CINF": "Cincinnati Financial", "CFG": "Citizens Financial", "STLD": "Steel Dynamics",
        "AWK": "American Water Works", "VLTO": "Veralto", "ES": "Eversource Energy", "JBL": "Jabil",
        "OMC": "Omnicom", "GIS": "General Mills", "STE": "STERIS", "CNP": "CenterPoint Energy",
        "DLTR": "Dollar Tree", "LULU": "Lululemon", "RF": "Regions Financial", "TDY": "Teledyne Technologies",
        "STZ": "Constellation Brands", "IRM": "Iron Mountain", "HUBB": "Hubbell", "EQR": "Equity Residential",
        "LDOS": "Leidos", "HAL": "Halliburton", "PPG": "PPG Industries", "PHM": "PulteGroup",
        "KEY": "KeyCorp", "WAT": "Waters Corporation", "EIX": "Edison International", "TROW": "T. Rowe Price",
        "VRSN": "VeriSign", "WSM": "Williams-Sonoma", "DVN": "Devon Energy", "ON": "ON Semiconductor",
        "L": "Loews", "DRI": "Darden Restaurants", "NTAP": "NetApp", "RL": "Ralph Lauren",
        "CPAY": "Corpay", "HPQ": "HP Inc", "LUV": "Southwest Airlines", "CMS": "CMS Energy",
        "IP": "International Paper", "LH": "Labcorp", "PTC": "PTC Inc", "TSN": "Tyson Foods",
        "SBAC": "SBA Communications", "CHD": "Church & Dwight", "EXPD": "Expeditors International", "PODD": "Insulet",
        "SW": "Smurfit Westrock", "NVR": "NVR Inc", "CNC": "Centene", "TYL": "Tyler Technologies",
        "TPL": "Texas Pacific Land", "NI": "NiSource", "WST": "West Pharmaceutical", "INCY": "Incyte",
        "PFG": "Principal Financial", "CTRA": "Coterra Energy", "DGX": "Quest Diagnostics", "CHRW": "C.H. Robinson",
        "AMCR": "Amcor", "TRMB": "Trimble", "GPN": "Global Payments", "JBHT": "J.B. Hunt",
        "PKG": "Packaging Corp", "TTD": "The Trade Desk", "MKC": "McCormick", "SNA": "Snap-on",
        "SMCI": "Super Micro Computer", "IT": "Gartner", "CDW": "CDW Corporation", "ZBH": "Zimmer Biomet",
        "FTV": "Fortive", "ALB": "Albemarle", "Q": "Quintiles IMS", "GPC": "Genuine Parts",
        "LII": "Lennox International", "PNR": "Pentair", "DD": "DuPont", "IFF": "International Flavors",
        "BG": "Bunge", "GDDY": "GoDaddy", "TKO": "TKO Group", "GEN": "Gen Digital",
        "WY": "Weyerhaeuser", "ESS": "Essex Property", "INVH": "Invitation Homes", "LNT": "Alliant Energy",
        "EVRG": "Evergy", "APTV": "Aptiv", "HOLX": "Hologic", "DOW": "Dow Inc",
        "COO": "Cooper Companies", "MAA": "Mid-America Apartment", "J": "Jacobs Solutions", "TXT": "Textron",
        "FOXA": "Fox Corporation A", "FOX": "Fox Corporation B", "FFIV": "F5 Networks", "DECK": "Deckers Outdoor",
        "PSKY": "Paramount Global", "ERIE": "Erie Indemnity", "BBY": "Best Buy", "DPZ": "Domino's Pizza",
        "UHS": "Universal Health Services", "VTRS": "Viatris", "EG": "Everest Group", "BALL": "Ball Corporation",
        # CAC 40
        "MC.PA": "LVMH", "OR.PA": "L'Oréal", "RMS.PA": "Hermès", "TTE.PA": "TotalEnergies",
        "SAN.PA": "Sanofi", "AIR.PA": "Airbus", "SU.PA": "Schneider Electric", "AI.PA": "Air Liquide",
        "BNP.PA": "BNP Paribas", "CS.PA": "AXA", "SAF.PA": "Safran", "EL.PA": "EssilorLuxottica",
        "KER.PA": "Kering", "DG.PA": "Vinci", "RI.PA": "Pernod Ricard", "CAP.PA": "Capgemini",
        "SGO.PA": "Saint-Gobain", "BN.PA": "Danone", "ENGI.PA": "Engie", "ACA.PA": "Crédit Agricole",
        "VIV.PA": "Vivendi", "DSY.PA": "Dassault Systèmes", "PUB.PA": "Publicis", "STM.PA": "STMicroelectronics",
        "LR.PA": "Legrand", "ML.PA": "Michelin", "GLE.PA": "Société Générale", "ORA.PA": "Orange",
        "HO.PA": "Thales", "WLN.PA": "Worldline",
        # DAX 40
        "SAP.DE": "SAP", "SIE.DE": "Siemens", "ALV.DE": "Allianz", "DTE.DE": "Deutsche Telekom",
        "AIR.DE": "Airbus", "MBG.DE": "Mercedes-Benz", "BMW.DE": "BMW", "MUV2.DE": "Munich Re",
        "BAS.DE": "BASF", "BAYN.DE": "Bayer", "IFX.DE": "Infineon", "ADS.DE": "Adidas",
        "DB1.DE": "Deutsche Börse", "DPW.DE": "Deutsche Post", "HEN3.DE": "Henkel", "SHL.DE": "Siemens Healthineers",
        "VOW3.DE": "Volkswagen", "RWE.DE": "RWE", "DBK.DE": "Deutsche Bank", "EOAN.DE": "E.ON",
        "FRE.DE": "Fresenius", "MTX.DE": "MTU Aero", "BEI.DE": "Beiersdorf", "HEI.DE": "HeidelbergCement",
        "CON.DE": "Continental", "MRK.DE": "Merck KGaA", "VNA.DE": "Vonovia", "FME.DE": "Fresenius Medical",
        "SY1.DE": "Symrise", "PAH3.DE": "Porsche Holding",
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
