"""
data - Clients de données pour PiTrader

Modules:
- twelve_data: Client Twelve Data API (données financières)
- news_client: Client NewsAPI (news)
- ollama_client: Client Ollama (analyse sentiment IA)
"""
from .twelve_data import TwelveDataClient, twelve_data_client
from .news_client import NewsAPIClient, news_client
from .ollama_client import OllamaClient, ollama_client

__all__ = [
    'TwelveDataClient',
    'twelve_data_client',
    'NewsAPIClient',
    'news_client',
    'OllamaClient',
    'ollama_client'
]
