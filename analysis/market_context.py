"""
analysis/market_context.py - Analyse du contexte de marché

Deuxième étape de l'analyse Top-Down.
Après la macro, évalue l'état du marché.

Score: -2 à +1
Détecte:
- Bear Market (S&P500 -20% depuis ATH)
- Correction (-10%)
- Volatilité élevée (VIX > 25)
- Volatilité extrême (VIX > 35)
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
class MarketContext:
    """Contexte de marché actuel"""
    # S&P 500
    sp500_current: Optional[float] = None
    sp500_high_52w: Optional[float] = None
    sp500_drawdown: Optional[float] = None  # % depuis high
    is_bear_market: bool = False
    is_correction: bool = False

    # VIX
    vix_current: Optional[float] = None
    volatility_level: str = "NORMAL"  # NORMAL, HIGH, EXTREME

    # Scoring
    market_score: int = 0  # -2 à +1
    recommendation: str = ""

    # Metadata
    analysis_time: datetime = field(default_factory=datetime.now)
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)


class MarketContextAnalyzer:
    """
    Analyseur du contexte de marché

    Logique Top-Down:
    - Après validation macro, évaluer l'état du marché
    - Détecter bear market / high volatility
    - Ajuster les attentes en conséquence
    """

    def __init__(self):
        self.scoring = config.scoring

    def analyze(self) -> MarketContext:
        """
        Exécute l'analyse complète du contexte de marché

        Returns:
            MarketContext avec score -2 à +1
        """
        context = MarketContext()

        # 1. Analyser S&P 500
        logger.info("Analyzing S&P 500...")
        self._analyze_sp500(context)

        time.sleep(config.thermal.inter_request_delay)

        # 2. Analyser VIX
        logger.info("Analyzing VIX...")
        self._analyze_vix(context)

        # 3. Calculer score et recommandation
        self._calculate_score(context)

        return context

    def _analyze_sp500(self, context: MarketContext):
        """Analyse S&P 500 et détection bear market"""
        try:
            symbol = config.market_indices.get("sp500", "SPX")

            # Prix actuel
            quote = twelve_data_client.get_quote(symbol)
            if quote.is_valid and quote.price:
                context.sp500_current = quote.price
            else:
                context.errors.append("S&P500 current price unavailable")
                return

            # Historique pour calculer le high
            time.sleep(config.thermal.inter_request_delay)
            history = twelve_data_client.get_time_series(
                symbol,
                interval="1day",
                outputsize=252  # ~1 an de trading
            )

            if history.is_valid and history.prices:
                # Trouver le high sur la période
                highs = [p["high"] for p in history.prices if p.get("high")]
                if highs:
                    context.sp500_high_52w = max(highs)
                    context.sp500_drawdown = (
                        (context.sp500_current - context.sp500_high_52w)
                        / context.sp500_high_52w * 100
                    )

                    # Détection bear market / correction
                    context.is_bear_market = (
                        context.sp500_drawdown <= self.scoring.sp500_bear_threshold
                    )
                    context.is_correction = (
                        context.sp500_drawdown <= self.scoring.sp500_correction_threshold
                        and not context.is_bear_market
                    )
            else:
                context.errors.append("S&P500 historical data unavailable")

        except Exception as e:
            logger.error(f"S&P500 analysis failed: {e}")
            context.errors.append(str(e))

    def _analyze_vix(self, context: MarketContext):
        """Analyse VIX (indice de volatilité)"""
        try:
            symbol = config.market_indices.get("vix", "VIX")
            quote = twelve_data_client.get_quote(symbol)

            if quote.is_valid and quote.price:
                context.vix_current = quote.price

                if context.vix_current > self.scoring.vix_extreme:
                    context.volatility_level = "EXTREME"
                elif context.vix_current > self.scoring.vix_high:
                    context.volatility_level = "HIGH"
                else:
                    context.volatility_level = "NORMAL"
            else:
                context.errors.append("VIX data unavailable")

        except Exception as e:
            logger.error(f"VIX analysis failed: {e}")
            context.errors.append(str(e))

    def _calculate_score(self, context: MarketContext):
        """Calcule le score de marché et la recommandation"""
        score = 0

        # Impact Bear Market / Correction
        if context.is_bear_market:
            score -= 2
        elif context.is_correction:
            score -= 1
        elif context.sp500_drawdown is not None and context.sp500_drawdown > -5:
            # Proche des highs = positif
            score += 1

        # Impact Volatilité
        if context.volatility_level == "EXTREME":
            score -= 1
        elif context.volatility_level == "HIGH":
            # High vol sans être extreme = -0.5 arrondi
            pass

        # Limiter entre -2 et +1
        context.market_score = max(-2, min(1, score))

        # Recommandation
        if context.is_bear_market:
            context.recommendation = (
                "BEAR MARKET DÉTECTÉ - Risque élevé. "
                "Privilégier cash et positions défensives."
            )
        elif context.volatility_level == "EXTREME":
            context.recommendation = (
                "VOLATILITÉ EXTRÊME - Attendre stabilisation "
                "avant nouvelles positions."
            )
        elif context.volatility_level == "HIGH":
            context.recommendation = (
                "VOLATILITÉ ÉLEVÉE - Réduire taille des positions. "
                "Stop-loss serrés recommandés."
            )
        elif context.is_correction:
            context.recommendation = (
                "CORRECTION EN COURS - Opportunités potentielles "
                "sur valeurs de qualité."
            )
        elif context.market_score >= 0:
            context.recommendation = (
                "MARCHÉ FAVORABLE - Conditions propices "
                "à la prise de positions."
            )
        else:
            context.recommendation = "PRUDENCE - Marché incertain."

        context.is_valid = len(context.errors) == 0


# Instance exportée
market_analyzer = MarketContextAnalyzer()
