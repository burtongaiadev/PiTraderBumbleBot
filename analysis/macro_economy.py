"""
analysis/macro_economy.py - Analyse macro-économique

Première étape de l'analyse Top-Down.
Évalue l'environnement macro avant d'analyser les actions.

Score: -3 à +1
- -3: Environnement très défavorable
- -2: Défavorable
- -1: Légèrement négatif
-  0: Neutre
- +1: Favorable

Facteurs analysés:
- Taux 10 ans US (TNX)
- Dollar Index (DXY)
- Ton des communications FED
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
from data.news_client import news_client
from data.ollama_client import ollama_client, FedTone

logger = logging.getLogger(__name__)


@dataclass
class MacroFactor:
    """Un facteur macro-économique"""
    name: str
    value: Optional[float]
    score: int  # Contribution au score total
    interpretation: str
    fetch_time: datetime = field(default_factory=datetime.now)


@dataclass
class MacroAnalysis:
    """Résultat complet de l'analyse macro"""
    total_score: int  # -3 à +1
    factors: List[MacroFactor] = field(default_factory=list)
    recommendation: str = ""
    analysis_time: datetime = field(default_factory=datetime.now)
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)


class MacroAnalyzer:
    """
    Analyseur macro-économique

    Logique Top-Down:
    - D'abord évaluer l'environnement macro global
    - Si très défavorable, court-circuiter l'analyse
    - Sinon, passer au contexte de marché
    """

    def __init__(self):
        self.scoring = config.scoring

    def analyze(self) -> MacroAnalysis:
        """
        Exécute l'analyse macro complète

        Returns:
            MacroAnalysis avec score -3 à +1
        """
        factors = []
        errors = []

        # 1. Analyser Taux 10 ans US
        logger.info("Analyzing Treasury 10Y...")
        treasury_factor = self._analyze_treasury_10y()
        factors.append(treasury_factor)
        if treasury_factor.value is None:
            errors.append("Treasury 10Y data unavailable")

        # Pause thermique
        time.sleep(config.thermal.inter_request_delay)

        # 2. Analyser Dollar Index
        logger.info("Analyzing Dollar Index...")
        dollar_factor = self._analyze_dollar_index()
        factors.append(dollar_factor)
        if dollar_factor.value is None:
            errors.append("Dollar Index data unavailable")

        time.sleep(config.thermal.inter_request_delay)

        # 3. Analyser ton FED via news
        logger.info("Analyzing FED news tone...")
        fed_factor = self._analyze_fed_news()
        factors.append(fed_factor)

        # Calculer score total
        total_score = sum(f.score for f in factors)
        # Limiter entre -3 et +1
        total_score = max(-3, min(1, total_score))

        # Générer recommandation
        recommendation = self._generate_recommendation(total_score, factors)

        return MacroAnalysis(
            total_score=total_score,
            factors=factors,
            recommendation=recommendation,
            is_valid=len(errors) == 0,
            errors=errors
        )

    def _analyze_treasury_10y(self) -> MacroFactor:
        """
        Analyse taux 10 ans US

        Logique:
        - Taux > 4.5%: Défavorable pour actions (-2)
        - Taux 3.0-4.5%: Neutre (0)
        - Taux < 3.0%: Favorable (+1)
        """
        symbol = config.market_indices.get("treasury_10y", "TNX")
        quote = twelve_data_client.get_quote(symbol)

        if not quote.is_valid or quote.price is None:
            return MacroFactor(
                name="Treasury 10Y",
                value=None,
                score=0,
                interpretation="Data unavailable"
            )

        rate = quote.price

        if rate > self.scoring.treasury_10y_high:
            score = -2
            interp = f"Taux élevés ({rate:.2f}%) - Pression sur valuations"
        elif rate < self.scoring.treasury_10y_low:
            score = 1
            interp = f"Taux bas ({rate:.2f}%) - Environnement favorable"
        else:
            score = 0
            interp = f"Taux neutres ({rate:.2f}%)"

        return MacroFactor(
            name="Treasury 10Y",
            value=rate,
            score=score,
            interpretation=interp
        )

    def _analyze_dollar_index(self) -> MacroFactor:
        """
        Analyse Dollar Index

        Logique:
        - Dollar fort > 105: Négatif pour multinationales US (-1)
        - Dollar faible < 100: Positif (+1)
        - Entre: Neutre (0)
        """
        symbol = config.market_indices.get("dollar_index", "DXY")
        quote = twelve_data_client.get_quote(symbol)

        if not quote.is_valid or quote.price is None:
            return MacroFactor(
                name="Dollar Index",
                value=None,
                score=0,
                interpretation="Data unavailable"
            )

        dxy = quote.price

        if dxy > self.scoring.dxy_high:
            score = -1
            interp = f"Dollar fort ({dxy:.1f}) - Pression exports"
        elif dxy < self.scoring.dxy_low:
            score = 1
            interp = f"Dollar faible ({dxy:.1f}) - Favorable exports"
        else:
            score = 0
            interp = f"Dollar neutre ({dxy:.1f})"

        return MacroFactor(
            name="Dollar Index",
            value=dxy,
            score=score,
            interpretation=interp
        )

    def _analyze_fed_news(self) -> MacroFactor:
        """
        Analyse le ton des news FED via IA

        Logique:
        - HAWKISH (restrictif): Négatif pour actions (-1)
        - DOVISH (accommodant): Positif (+1)
        - NEUTRAL: (0)
        """
        try:
            # Récupérer news macro
            news_result = news_client.get_macro_news(page_size=5)

            if not news_result.is_valid or not news_result.articles:
                return MacroFactor(
                    name="FED News Tone",
                    value=None,
                    score=0,
                    interpretation="No recent FED news"
                )

            # Analyser les 3 premières news avec Ollama
            tones = []
            for article in news_result.articles[:3]:
                text = f"{article.title}. {article.description or ''}"
                result = ollama_client.analyze_fed_tone(text)
                if result.is_valid:
                    tones.append(result.tone)

                # Pause entre analyses Ollama
                time.sleep(config.thermal.cooldown_delay)

            if not tones:
                return MacroFactor(
                    name="FED News Tone",
                    value=None,
                    score=0,
                    interpretation="Could not analyze FED news"
                )

            # Agrégation des tons
            hawkish_count = tones.count(FedTone.HAWKISH)
            dovish_count = tones.count(FedTone.DOVISH)

            if hawkish_count > dovish_count:
                score = -1
                interp = f"FED hawkish ({hawkish_count}/{len(tones)} articles)"
                value = -1.0
            elif dovish_count > hawkish_count:
                score = 1
                interp = f"FED dovish ({dovish_count}/{len(tones)} articles)"
                value = 1.0
            else:
                score = 0
                interp = "FED neutral tone"
                value = 0.0

            return MacroFactor(
                name="FED News Tone",
                value=value,
                score=score,
                interpretation=interp
            )

        except Exception as e:
            logger.error(f"FED news analysis failed: {e}")
            return MacroFactor(
                name="FED News Tone",
                value=None,
                score=0,
                interpretation=f"Analysis error: {e}"
            )

    def _generate_recommendation(
        self,
        total_score: int,
        factors: List[MacroFactor]
    ) -> str:
        """Génère recommandation basée sur le score"""

        if total_score <= -2:
            return (
                "PRUDENCE MAXIMALE - Environnement macro très défavorable. "
                "Privilégier liquidités et positions défensives."
            )
        elif total_score == -1:
            return (
                "PRUDENCE - Environnement légèrement négatif. "
                "Réduire exposition aux valeurs cycliques."
            )
        elif total_score == 0:
            return (
                "NEUTRE - Pas de signal macro fort. "
                "Analyser le contexte de marché."
            )
        else:  # +1
            return (
                "FAVORABLE - Environnement macro positif. "
                "Opportunités sur valeurs de qualité."
            )


# Instance exportée
macro_analyzer = MacroAnalyzer()
