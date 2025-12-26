"""
analysis/macro_economy.py - Analyse macro-économique

Première étape de l'analyse Top-Down.
Évalue l'environnement macro via les news FED.

Score: -1 à +1
- -1: FED hawkish (restrictive)
-  0: Neutre
- +1: FED dovish (accommodante)
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
from data.ollama_client import ollama_client, FedTone

logger = logging.getLogger(__name__)


@dataclass
class MacroAnalysis:
    """Résultat de l'analyse macro"""
    total_score: int  # -1 à +1
    fed_tone: str = "NEUTRAL"
    articles_analyzed: int = 0
    recommendation: str = ""
    analysis_time: datetime = field(default_factory=datetime.now)
    is_valid: bool = True


class MacroAnalyzer:
    """Analyseur macro basé sur le ton FED"""

    def analyze(self) -> MacroAnalysis:
        """Analyse le ton des news FED"""
        try:
            # Récupérer news macro
            news_result = news_client.get_macro_news(page_size=5)

            if not news_result.is_valid or not news_result.articles:
                logger.info("Macro: Pas de news FED récentes")
                return MacroAnalysis(
                    total_score=0,
                    fed_tone="NEUTRAL",
                    recommendation="Pas de signal macro"
                )

            # Analyser les news avec Ollama
            tones = []
            for article in news_result.articles[:3]:
                text = f"{article.title}. {article.description or ''}"
                result = ollama_client.analyze_fed_tone(text)
                if result.is_valid:
                    tones.append(result.tone)
                time.sleep(1)

            if not tones:
                return MacroAnalysis(
                    total_score=0,
                    fed_tone="NEUTRAL",
                    articles_analyzed=0,
                    recommendation="Analyse FED impossible"
                )

            # Agrégation
            hawkish = tones.count(FedTone.HAWKISH)
            dovish = tones.count(FedTone.DOVISH)

            if hawkish > dovish:
                score = -1
                tone = "HAWKISH"
                reco = "FED restrictive - Prudence sur les actions"
            elif dovish > hawkish:
                score = 1
                tone = "DOVISH"
                reco = "FED accommodante - Favorable aux actions"
            else:
                score = 0
                tone = "NEUTRAL"
                reco = "FED neutre - Pas de signal fort"

            logger.info(f"Macro: FED {tone} ({dovish}D/{hawkish}H sur {len(tones)} articles)")

            return MacroAnalysis(
                total_score=score,
                fed_tone=tone,
                articles_analyzed=len(tones),
                recommendation=reco,
                is_valid=True
            )

        except Exception as e:
            logger.warning(f"Macro: Erreur analyse - {e}")
            return MacroAnalysis(
                total_score=0,
                fed_tone="NEUTRAL",
                recommendation="Erreur analyse macro",
                is_valid=False
            )


# Instance exportée
macro_analyzer = MacroAnalyzer()
