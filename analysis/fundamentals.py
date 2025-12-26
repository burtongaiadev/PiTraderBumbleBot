"""
analysis/fundamentals.py - Analyse fondamentale des actions

Troisième étape de l'analyse Top-Down.
Après validation macro et marché, analyser la qualité des entreprises.

Score: 0 à 5 points
- Marge Nette: 0-2 points
- Dette/Equity: 0-2 points
- ROE: 0-1 point
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
    total_score: float  # 0-5

    # Composants
    net_margin: Optional[float] = None  # En %
    net_margin_score: float = 0

    debt_to_equity: Optional[float] = None  # Ratio
    debt_equity_score: float = 0

    roe: Optional[float] = None  # En %
    roe_score: float = 0

    # Interprétation
    quality_rating: str = "UNKNOWN"  # EXCELLENT, GOOD, AVERAGE, POOR
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)

    # Metadata
    analysis_time: datetime = field(default_factory=datetime.now)
    is_valid: bool = True
    error: Optional[str] = None


class FundamentalsAnalyzer:
    """
    Analyseur fondamental

    Logique Top-Down:
    - Après validation macro et marché
    - Analyser la qualité fondamentale de chaque action
    - Scorer sur 5 points
    """

    def __init__(self):
        self.scoring = config.scoring

    def analyze(self, symbol: str) -> FundamentalScore:
        """
        Analyse les fondamentaux d'une action

        Args:
            symbol: Ticker de l'action

        Returns:
            FundamentalScore (0-5 points)
        """
        # Récupérer données fondamentales
        fundamentals = twelve_data_client.get_fundamentals(symbol)

        if not fundamentals.is_valid:
            return FundamentalScore(
                symbol=symbol,
                total_score=0,
                is_valid=False,
                error=fundamentals.error
            )

        score = FundamentalScore(symbol=symbol, total_score=0)

        # 1. Analyser Marge Nette (0-2 points)
        self._score_net_margin(score, fundamentals.net_margin)

        # 2. Analyser Dette/Equity (0-2 points)
        self._score_debt_equity(score, fundamentals.debt_to_equity)

        # 3. Analyser ROE (0-1 point)
        self._score_roe(score, fundamentals.roe)

        # Calculer total
        score.total_score = (
            score.net_margin_score +
            score.debt_equity_score +
            score.roe_score
        )

        # Déterminer rating
        self._determine_rating(score)

        return score

    def _score_net_margin(self, score: FundamentalScore, net_margin: Optional[float]):
        """Score la marge nette (0-2 points)"""
        if net_margin is None:
            score.weaknesses.append("Marge nette non disponible")
            return

        # Convertir en % si nécessaire (API peut retourner 0.15 ou 15)
        if net_margin < 1:
            net_margin = net_margin * 100

        score.net_margin = net_margin

        if net_margin > self.scoring.net_margin_excellent:
            score.net_margin_score = 2
            score.strengths.append(f"Excellente marge nette ({net_margin:.1f}%)")
        elif net_margin > self.scoring.net_margin_good:
            score.net_margin_score = 1
            score.strengths.append(f"Bonne marge nette ({net_margin:.1f}%)")
        else:
            score.net_margin_score = 0
            score.weaknesses.append(f"Marge nette faible ({net_margin:.1f}%)")

    def _score_debt_equity(self, score: FundamentalScore, debt_to_equity: Optional[float]):
        """Score le ratio dette/equity (0-2 points)"""
        if debt_to_equity is None:
            score.weaknesses.append("Dette/Equity non disponible")
            return

        # Normaliser si exprimé en % (ex: 150 au lieu de 1.5)
        if debt_to_equity > 10:
            debt_to_equity = debt_to_equity / 100

        score.debt_to_equity = debt_to_equity

        if debt_to_equity < self.scoring.debt_equity_excellent:
            score.debt_equity_score = 2
            score.strengths.append(f"Très faible endettement (D/E: {debt_to_equity:.2f})")
        elif debt_to_equity < self.scoring.debt_equity_good:
            score.debt_equity_score = 1
            score.strengths.append(f"Endettement raisonnable (D/E: {debt_to_equity:.2f})")
        else:
            score.debt_equity_score = 0
            score.weaknesses.append(f"Endettement élevé (D/E: {debt_to_equity:.2f})")

    def _score_roe(self, score: FundamentalScore, roe: Optional[float]):
        """Score le ROE (0-1 point)"""
        if roe is None:
            score.weaknesses.append("ROE non disponible")
            return

        # Convertir en % si nécessaire
        if roe < 1:
            roe = roe * 100

        score.roe = roe

        if roe > self.scoring.roe_good:
            score.roe_score = 1
            score.strengths.append(f"Bon ROE ({roe:.1f}%)")
        else:
            score.roe_score = 0
            score.weaknesses.append(f"ROE faible ({roe:.1f}%)")

    def _determine_rating(self, score: FundamentalScore):
        """Détermine le rating qualité"""
        if score.total_score >= 4:
            score.quality_rating = "EXCELLENT"
        elif score.total_score >= 3:
            score.quality_rating = "GOOD"
        elif score.total_score >= 2:
            score.quality_rating = "AVERAGE"
        else:
            score.quality_rating = "POOR"

    def analyze_watchlist(
        self,
        symbols: Optional[List[str]] = None
    ) -> List[FundamentalScore]:
        """
        Analyse toute la watchlist

        Args:
            symbols: Liste de tickers (défaut: config.watchlist)

        Returns:
            Liste de FundamentalScore triée par score décroissant
        """
        symbols = symbols or config.watchlist
        results = []

        for i, symbol in enumerate(symbols):
            logger.info(f"Analyzing fundamentals {i+1}/{len(symbols)}: {symbol}")
            results.append(self.analyze(symbol))

            # Pause thermique entre chaque analyse
            if i < len(symbols) - 1:
                time.sleep(config.thermal.inter_request_delay)

        # Trier par score décroissant
        results.sort(key=lambda x: x.total_score, reverse=True)
        return results


# Instance exportée
fundamentals_analyzer = FundamentalsAnalyzer()
