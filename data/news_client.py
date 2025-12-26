"""
data/news_client.py - Client NewsAPI

API pour récupérer les news financières.
Plan gratuit: 100 req/jour

Utilisé pour:
- News macro (FED, inflation, etc.)
- News spécifiques à une action
"""
import requests
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from utils.decorators import retry_with_backoff, CircuitBreaker
from utils.cache import ttl_lru_cache

logger = logging.getLogger(__name__)

# Circuit breaker pour NewsAPI
_news_api_cb = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=120,
    exceptions=(requests.RequestException, ConnectionError, TimeoutError, ValueError)
)


@dataclass
class NewsArticle:
    """Article de news"""
    title: str
    description: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    content: Optional[str] = None


@dataclass
class NewsResult:
    """Résultat de recherche de news"""
    query: str
    articles: List[NewsArticle] = field(default_factory=list)
    total_results: int = 0
    is_valid: bool = True
    error: Optional[str] = None


class NewsAPIClient:
    """
    Client NewsAPI

    Caractéristiques:
    - Recherche par mot-clé ou ticker
    - Filtrage par source
    - Cache 15 minutes
    """

    def __init__(self):
        self.api_key = config.news_api.api_key
        self.base_url = config.news_api.base_url
        self.timeout = config.news_api.timeout

    @_news_api_cb
    @retry_with_backoff(
        exceptions=(requests.RequestException, ConnectionError, TimeoutError),
        max_retries=3,
        initial_delay=2.0
    )
    def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Requête HTTP vers NewsAPI

        Args:
            endpoint: Endpoint (everything, top-headlines)
            params: Paramètres

        Returns:
            Réponse JSON
        """
        params["apiKey"] = self.api_key
        url = f"{self.base_url}/{endpoint}"

        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            query_info = params.get('q', 'unknown')
            logger.debug(f"NewsAPI URL: {url}?q={query_info}")

            if data.get("status") != "ok":
                raise ValueError(f"API Error: {data.get('message', 'Unknown')}")

            return data

        except requests.Timeout:
            logger.error(f"Timeout on NewsAPI {endpoint}")
            raise
        except requests.RequestException as e:
            logger.error(f"NewsAPI request failed: {e}")
            raise

    @ttl_lru_cache(maxsize=100, ttl=900)  # Cache 15 min
    def search_news(
        self,
        query: str,
        language: str = "en",
        sort_by: str = "publishedAt",
        page_size: int = 10,
        days_back: int = 7
    ) -> NewsResult:
        """
        Recherche des news par mot-clé

        Args:
            query: Terme de recherche
            language: Langue (en, fr, etc.)
            sort_by: Tri (publishedAt, relevancy, popularity)
            page_size: Nombre d'articles
            days_back: Jours dans le passé

        Returns:
            NewsResult avec liste d'articles
        """
        try:
            # Calculer date de début
            from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

            params = {
                "q": query,
                "language": language,
                "sortBy": sort_by,
                "pageSize": page_size,
                "from": from_date
            }

            # Filtrer par sources financières fiables
            if config.news_api.domains:
                params["domains"] = config.news_api.domains

            data = self._request("everything", params)

            articles = []
            for item in data.get("articles", []):
                pub_date = None
                if item.get("publishedAt"):
                    try:
                        pub_date = datetime.fromisoformat(
                            item["publishedAt"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                articles.append(NewsArticle(
                    title=item.get("title", ""),
                    description=item.get("description"),
                    source=item.get("source", {}).get("name"),
                    url=item.get("url"),
                    published_at=pub_date,
                    content=item.get("content")
                ))

            return NewsResult(
                query=query,
                articles=articles,
                total_results=data.get("totalResults", 0),
                is_valid=True
            )

        except Exception as e:
            logger.error(f"Failed to search news for '{query}': {e}")
            return NewsResult(
                query=query,
                is_valid=False,
                error=str(e)
            )

    def get_macro_news(self, page_size: int = 10) -> NewsResult:
        """
        Récupère les news macro-économiques

        Recherche: FED, inflation, interest rates, etc.

        Returns:
            NewsResult
        """
        # Construire requête avec mots-clés macro
        keywords = " OR ".join([
            '"Federal Reserve"',
            '"interest rate"',
            'inflation',
            'CPI',
            '"Jerome Powell"',
            'recession'
        ])

        return self.search_news(
            query=keywords,
            page_size=page_size,
            days_back=3  # News récentes seulement
        )

    def get_stock_news(self, symbol: str, company_name: Optional[str] = None, page_size: int = 5) -> NewsResult:
        """
        Récupère les news d'une action spécifique

        Args:
            symbol: Ticker (ex: AAPL)
            company_name: Nom complet (ex: Apple) - auto-résolu depuis config si None
            page_size: Nombre d'articles

        Returns:
            NewsResult
        """
        # Auto-résoudre le nom depuis le mapping si non fourni
        if company_name is None:
            company_name = config.ticker_names.get(symbol)

        # Construire requête
        if company_name:
            query = f'"{company_name}" OR {symbol}'
        else:
            query = symbol

        return self.search_news(
            query=query,
            page_size=page_size,
            days_back=7
        )

    def get_headlines(self, category: str = "business", country: str = "us") -> NewsResult:
        """
        Récupère les top headlines

        Args:
            category: business, technology, etc.
            country: Code pays

        Returns:
            NewsResult
        """
        try:
            data = self._request("top-headlines", {
                "category": category,
                "country": country,
                "pageSize": 10
            })

            articles = []
            for item in data.get("articles", []):
                articles.append(NewsArticle(
                    title=item.get("title", ""),
                    description=item.get("description"),
                    source=item.get("source", {}).get("name"),
                    url=item.get("url")
                ))

            return NewsResult(
                query=f"headlines:{category}",
                articles=articles,
                total_results=data.get("totalResults", 0),
                is_valid=True
            )

        except Exception as e:
            logger.error(f"Failed to get headlines: {e}")
            return NewsResult(
                query=f"headlines:{category}",
                is_valid=False,
                error=str(e)
            )


# Instance singleton
news_client = NewsAPIClient()
