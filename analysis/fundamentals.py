"""
analysis/fundamentals.py - Analyse fondamentale (momentum)

Score basé sur le momentum 30 jours.
Score: 0 à 3 points
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
from data.twelve_data import twelve_data_client

logger = logging.getLogger(__name__)


@dataclass
class FundamentalScore:
    """Score fondamental d'une action"""
    symbol: str
    total_score: float = 0.0  # 0-3
    momentum: float = 0.0  # Variation 30j en %
    quality_rating: str = "NEUTRAL"
    is_valid: bool = True
    error: Optional[str] = None


class FundamentalsAnalyzer:
    """Analyse basée sur le momentum prix"""

    def analyze(self, symbol: str) -> FundamentalScore:
        """Analyse le momentum d'une action"""
        fundamentals = twelve_data_client.get_fundamentals(symbol)

        if not fundamentals.is_valid:
            return FundamentalScore(
                symbol=symbol,
                total_score=0,
                is_valid=False,
                error=fundamentals.error
            )

        # Convertir momentum (-1 à +1) en score (0 à 3)
        momentum = fundamentals.momentum_score
        score = (momentum + 1) * 1.5  # -1->0, 0->1.5, +1->3

        if momentum > 0.3:
            rating = "BULLISH"
        elif momentum < -0.3:
            rating = "BEARISH"
        else:
            rating = "NEUTRAL"

        return FundamentalScore(
            symbol=symbol,
            total_score=round(score, 1),
            momentum=momentum,
            quality_rating=rating,
            is_valid=True
        )

    def analyze_watchlist(self, symbols: Optional[List[str]] = None) -> List[FundamentalScore]:
        """Analyse toute la watchlist"""
        symbols = symbols or config.watchlist
        results = []

        for i, symbol in enumerate(symbols):
            results.append(self.analyze(symbol))
            if i < len(symbols) - 1:
                time.sleep(0.5)

        # Trier par score décroissant
        results.sort(key=lambda x: x.total_score, reverse=True)

        # Log résumé
        valid = [r for r in results if r.is_valid]
        if valid:
            top = valid[0]
            logger.info(f"Fundamentals: Top {top.symbol} ({top.total_score}/3, {top.momentum:+.0%})")

        return results


# Instance exportée
fundamentals_analyzer = FundamentalsAnalyzer()
