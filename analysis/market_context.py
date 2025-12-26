"""
analysis/market_context.py - Analyse du contexte de marché

Score basé sur le momentum moyen de la watchlist.
Score: -1 à +1
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.twelve_data import twelve_data_client

logger = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """Contexte de marché"""
    market_score: int = 0  # -1 à +1
    avg_change: float = 0.0  # Variation moyenne watchlist
    positive_count: int = 0
    negative_count: int = 0
    recommendation: str = ""
    is_valid: bool = True


class MarketContextAnalyzer:
    """Analyse le contexte via momentum de la watchlist"""

    def analyze(self) -> MarketContext:
        """Calcule le momentum moyen de la watchlist"""
        try:
            changes = []
            positive = 0
            negative = 0

            # Récupérer les quotes de la watchlist
            for symbol in config.watchlist[:5]:  # Top 5 pour économiser API calls
                quote = twelve_data_client.get_quote(symbol)
                if quote.is_valid and quote.change_percent is not None:
                    changes.append(quote.change_percent)
                    if quote.change_percent > 0:
                        positive += 1
                    else:
                        negative += 1

            if not changes:
                logger.info("Market: Pas de données disponibles")
                return MarketContext(
                    market_score=0,
                    recommendation="Données marché indisponibles"
                )

            avg = sum(changes) / len(changes)

            # Score basé sur momentum
            if avg > 1.0:
                score = 1
                reco = "Marché haussier - Momentum positif"
            elif avg < -1.0:
                score = -1
                reco = "Marché baissier - Prudence"
            else:
                score = 0
                reco = "Marché neutre"

            logger.info(f"Market: {positive}↑ {negative}↓ (avg: {avg:+.1f}%)")

            return MarketContext(
                market_score=score,
                avg_change=avg,
                positive_count=positive,
                negative_count=negative,
                recommendation=reco,
                is_valid=True
            )

        except Exception as e:
            logger.warning(f"Market: Erreur - {e}")
            return MarketContext(
                market_score=0,
                recommendation="Erreur analyse marché",
                is_valid=False
            )


# Instance exportée
market_analyzer = MarketContextAnalyzer()
