"""
analysis/sentiment.py - Analyse de sentiment des news

Quatrième étape de l'analyse Top-Down.
Analyse le sentiment des news spécifiques à chaque action.

Score: 0 à 3 points
- Basé sur l'analyse IA (Ollama) des news récentes
- Agrège plusieurs articles pour robustesse
"""
import logging
from typing import List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import time

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.news_client import news_client
from data.ollama_client import ollama_client, Sentiment

logger = logging.getLogger(__name__)


@dataclass
class SentimentScore:
    """Score de sentiment pour une action"""
    symbol: str
    total_score: float  # 0-3

    # Détails
    articles_analyzed: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0

    # Headlines analysées
    headlines: List[str] = field(default_factory=list)

    # Interprétation
    sentiment_label: str = "UNKNOWN"  # VERY_POSITIVE, POSITIVE, NEUTRAL, NEGATIVE, VERY_NEGATIVE
    summary: str = ""

    # Metadata
    analysis_time: datetime = field(default_factory=datetime.now)
    is_valid: bool = True
    error: Optional[str] = None


class SentimentAnalyzer:
    """
    Analyseur de sentiment

    Utilise:
    - NewsAPI pour récupérer les news
    - Ollama pour analyser le sentiment
    - Agrégation pour robustesse
    """

    def __init__(self):
        self.news_count = config.scoring.news_count

    def analyze(self, symbol: str, company_name: Optional[str] = None) -> SentimentScore:
        """
        Analyse le sentiment des news pour une action

        Args:
            symbol: Ticker de l'action
            company_name: Nom complet de l'entreprise (optionnel)

        Returns:
            SentimentScore (0-3 points)
        """
        score = SentimentScore(symbol=symbol)

        try:
            # Récupérer news
            news_result = news_client.get_stock_news(
                symbol=symbol,
                company_name=company_name,
                page_size=self.news_count
            )

            if not news_result.is_valid or not news_result.articles:
                score.error = "No news found"
                score.is_valid = False
                return score

            # Analyser chaque article
            sentiments = []
            for article in news_result.articles:
                if not article.title:
                    continue

                # Stocker headline
                score.headlines.append(article.title)

                # Analyser avec Ollama
                text = f"{article.title}. {article.description or ''}"
                result = ollama_client.analyze_sentiment(text)

                if result.is_valid:
                    sentiments.append(result.sentiment)

                    if result.sentiment == Sentiment.POSITIVE:
                        score.positive_count += 1
                    elif result.sentiment == Sentiment.NEGATIVE:
                        score.negative_count += 1
                    else:
                        score.neutral_count += 1

                # Pause thermique entre analyses Ollama
                time.sleep(config.thermal.cooldown_delay)

            score.articles_analyzed = len(sentiments)

            if not sentiments:
                score.error = "Could not analyze any articles"
                score.is_valid = False
                return score

            # Calculer score (0-3)
            self._calculate_score(score)

        except Exception as e:
            logger.error(f"Sentiment analysis failed for {symbol}: {e}")
            score.error = str(e)
            score.is_valid = False

        return score

    def _calculate_score(self, score: SentimentScore):
        """
        Calcule le score de sentiment (0-3)

        Logique:
        - Majorité positive: 2-3 points
        - Équilibré: 1-2 points
        - Majorité négative: 0-1 points
        """
        total = score.articles_analyzed
        if total == 0:
            score.total_score = 1.5  # Neutre par défaut
            score.sentiment_label = "NEUTRAL"
            return

        pos_ratio = score.positive_count / total
        neg_ratio = score.negative_count / total

        # Score basé sur le ratio positif/négatif
        if pos_ratio >= 0.6:
            score.total_score = 3.0
            score.sentiment_label = "VERY_POSITIVE"
            score.summary = f"Sentiment très positif ({score.positive_count}/{total} articles)"
        elif pos_ratio >= 0.4:
            score.total_score = 2.0
            score.sentiment_label = "POSITIVE"
            score.summary = f"Sentiment positif ({score.positive_count}/{total} articles)"
        elif neg_ratio >= 0.6:
            score.total_score = 0.0
            score.sentiment_label = "VERY_NEGATIVE"
            score.summary = f"Sentiment très négatif ({score.negative_count}/{total} articles)"
        elif neg_ratio >= 0.4:
            score.total_score = 1.0
            score.sentiment_label = "NEGATIVE"
            score.summary = f"Sentiment négatif ({score.negative_count}/{total} articles)"
        else:
            score.total_score = 1.5
            score.sentiment_label = "NEUTRAL"
            score.summary = f"Sentiment neutre/mixte"

    def analyze_multiple(
        self,
        symbols: List[str],
        company_names: Optional[dict] = None
    ) -> List[SentimentScore]:
        """
        Analyse le sentiment pour plusieurs actions

        Args:
            symbols: Liste de tickers
            company_names: Dict ticker -> nom complet

        Returns:
            Liste de SentimentScore
        """
        company_names = company_names or {}
        results = []

        for i, symbol in enumerate(symbols):
            logger.info(f"Analyzing sentiment {i+1}/{len(symbols)}: {symbol}")
            company = company_names.get(symbol)
            results.append(self.analyze(symbol, company))

            # Pause plus longue entre actions (Ollama + news)
            if i < len(symbols) - 1:
                time.sleep(config.thermal.inter_request_delay * 2)

        return results


# Instance exportée
sentiment_analyzer = SentimentAnalyzer()
