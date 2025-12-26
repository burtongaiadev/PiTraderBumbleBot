"""
data/ollama_client.py - Client Ollama pour analyse sentiment

Utilise Ollama local sur Raspberry Pi pour:
- Analyse de sentiment des news
- Classification Hawkish/Dovish pour news FED

Modèle: qwen2.5:1.5b (léger, adapté Pi)
Timeout: 120s (Pi peut être lent)
"""
import requests
import json
import re
import logging
from typing import Optional
from dataclasses import dataclass
from enum import Enum

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from utils.decorators import retry_with_backoff
from utils.cache import ttl_lru_cache

logger = logging.getLogger(__name__)


class Sentiment(Enum):
    """Sentiment d'une news"""
    POSITIVE = "POSITIF"
    NEGATIVE = "NEGATIF"
    NEUTRAL = "NEUTRE"
    UNKNOWN = "INCONNU"


class FedTone(Enum):
    """Ton des communications FED"""
    HAWKISH = "HAWKISH"    # Restrictif, négatif pour actions
    DOVISH = "DOVISH"      # Accommodant, positif pour actions
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class SentimentResult:
    """Résultat d'analyse de sentiment"""
    sentiment: Sentiment
    confidence: float  # 0.0 - 1.0
    reasoning: Optional[str] = None
    is_valid: bool = True
    error: Optional[str] = None


@dataclass
class FedToneResult:
    """Résultat d'analyse du ton FED"""
    tone: FedTone
    confidence: float
    reasoning: Optional[str] = None
    is_valid: bool = True
    error: Optional[str] = None


class OllamaClient:
    """
    Client Ollama pour analyse IA

    Optimisé pour Raspberry Pi 5:
    - Timeout long (120s)
    - Contexte réduit (2048 tokens)
    - Parsing robuste avec fallback
    - Cache 1h sur analyses
    """

    # Prompts
    SENTIMENT_PROMPT = """Analyze the sentiment of this financial news.
Reply ONLY with a JSON in this exact format:
{"sentiment": "POSITIF" or "NEGATIF" or "NEUTRE", "confidence": 0.0-1.0, "reason": "short explanation"}

News to analyze:
{text}

JSON:"""

    FED_TONE_PROMPT = """Analyze the Federal Reserve communication tone.
HAWKISH = restrictive monetary policy, rate hikes, fighting inflation (negative for stocks)
DOVISH = accommodative policy, rate cuts, supporting growth (positive for stocks)

Reply ONLY with a JSON:
{"tone": "HAWKISH" or "DOVISH" or "NEUTRAL", "confidence": 0.0-1.0, "reason": "short explanation"}

Text to analyze:
{text}

JSON:"""

    def __init__(self):
        self.model = config.ollama.model
        self.base_url = config.ollama.base_url
        self.timeout = config.ollama.timeout
        self.num_ctx = config.ollama.num_ctx
        self.num_thread = config.ollama.num_thread

    def is_available(self) -> bool:
        """Vérifie si le serveur Ollama est disponible"""
        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    @retry_with_backoff(
        exceptions=(requests.RequestException, ConnectionError, TimeoutError),
        max_retries=2,  # Moins de retries car Ollama peut être lent
        initial_delay=3.0
    )
    def _generate(self, prompt: str) -> str:
        """
        Appel API Ollama generate

        Args:
            prompt: Prompt à envoyer

        Returns:
            Réponse texte du modèle
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "num_thread": self.num_thread,
                "temperature": 0.1,  # Bas pour réponses consistantes
                "top_p": 0.9
            }
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")

        except requests.Timeout:
            logger.error(f"Ollama timeout after {self.timeout}s")
            raise TimeoutError(f"Ollama timeout after {self.timeout}s")
        except requests.ConnectionError as e:
            logger.error(f"Cannot connect to Ollama: {e}")
            raise ConnectionError(f"Cannot connect to Ollama at {self.base_url}")

    def _parse_json_response(self, response: str) -> Optional[dict]:
        """
        Parse la réponse JSON avec robustesse

        Args:
            response: Réponse brute du modèle

        Returns:
            Dict parsé ou None
        """
        try:
            # Nettoyer la réponse
            response = response.strip()

            # Chercher JSON dans la réponse
            json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())

            return None

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON: {e}")
            return None

    def _fallback_sentiment(self, text: str) -> Sentiment:
        """
        Détection de sentiment par mots-clés (fallback)

        Args:
            text: Texte à analyser

        Returns:
            Sentiment détecté
        """
        text_lower = text.lower()

        positive_words = [
            "surge", "soar", "gain", "profit", "growth", "beat",
            "positive", "bullish", "upgrade", "record", "strong"
        ]
        negative_words = [
            "fall", "drop", "loss", "decline", "crash", "miss",
            "negative", "bearish", "downgrade", "weak", "warning"
        ]

        pos_count = sum(1 for w in positive_words if w in text_lower)
        neg_count = sum(1 for w in negative_words if w in text_lower)

        if pos_count > neg_count:
            return Sentiment.POSITIVE
        elif neg_count > pos_count:
            return Sentiment.NEGATIVE
        else:
            return Sentiment.NEUTRAL

    def _fallback_fed_tone(self, text: str) -> FedTone:
        """
        Détection du ton FED par mots-clés (fallback)

        Args:
            text: Texte à analyser

        Returns:
            FedTone détecté
        """
        text_lower = text.lower()

        hawkish_words = [
            "rate hike", "tightening", "inflation fight", "restrictive",
            "higher rates", "reduce balance", "hawkish"
        ]
        dovish_words = [
            "rate cut", "easing", "accommodation", "supportive",
            "lower rates", "stimulus", "dovish", "pause"
        ]

        hawk_count = sum(1 for w in hawkish_words if w in text_lower)
        dove_count = sum(1 for w in dovish_words if w in text_lower)

        if hawk_count > dove_count:
            return FedTone.HAWKISH
        elif dove_count > hawk_count:
            return FedTone.DOVISH
        else:
            return FedTone.NEUTRAL

    @ttl_lru_cache(maxsize=200, ttl=3600)  # Cache 1h
    def analyze_sentiment(self, text: str) -> SentimentResult:
        """
        Analyse le sentiment d'un texte

        Args:
            text: Texte à analyser (news, titre, etc.)

        Returns:
            SentimentResult
        """
        if not text or len(text.strip()) < 10:
            return SentimentResult(
                sentiment=Sentiment.UNKNOWN,
                confidence=0.0,
                error="Text too short",
                is_valid=False
            )

        # Limiter longueur pour économiser tokens
        text = text[:500]

        # Vérifier disponibilité Ollama
        if not self.is_available():
            logger.warning("Ollama not available, using fallback")
            return SentimentResult(
                sentiment=self._fallback_sentiment(text),
                confidence=0.3,
                reasoning="Fallback keyword detection",
                is_valid=True
            )

        try:
            prompt = self.SENTIMENT_PROMPT.format(text=text)
            response = self._generate(prompt)

            parsed = self._parse_json_response(response)
            if parsed:
                sentiment_str = parsed.get("sentiment", "").upper()
                sentiment_map = {
                    "POSITIF": Sentiment.POSITIVE,
                    "POSITIVE": Sentiment.POSITIVE,
                    "NEGATIF": Sentiment.NEGATIVE,
                    "NEGATIVE": Sentiment.NEGATIVE,
                    "NEUTRE": Sentiment.NEUTRAL,
                    "NEUTRAL": Sentiment.NEUTRAL
                }
                sentiment = sentiment_map.get(sentiment_str, Sentiment.UNKNOWN)

                confidence = float(parsed.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))

                return SentimentResult(
                    sentiment=sentiment,
                    confidence=confidence,
                    reasoning=parsed.get("reason"),
                    is_valid=True
                )
            else:
                # Fallback si parsing échoue
                return SentimentResult(
                    sentiment=self._fallback_sentiment(text),
                    confidence=0.3,
                    reasoning="Fallback - JSON parsing failed",
                    is_valid=True
                )

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            return SentimentResult(
                sentiment=self._fallback_sentiment(text),
                confidence=0.2,
                error=str(e),
                is_valid=True
            )

    @ttl_lru_cache(maxsize=100, ttl=3600)
    def analyze_fed_tone(self, text: str) -> FedToneResult:
        """
        Analyse le ton d'une communication FED

        Args:
            text: Texte de la communication

        Returns:
            FedToneResult (HAWKISH, DOVISH, NEUTRAL)
        """
        if not text or len(text.strip()) < 10:
            return FedToneResult(
                tone=FedTone.UNKNOWN,
                confidence=0.0,
                error="Text too short",
                is_valid=False
            )

        text = text[:500]

        if not self.is_available():
            return FedToneResult(
                tone=self._fallback_fed_tone(text),
                confidence=0.3,
                reasoning="Fallback keyword detection",
                is_valid=True
            )

        try:
            prompt = self.FED_TONE_PROMPT.format(text=text)
            response = self._generate(prompt)

            parsed = self._parse_json_response(response)
            if parsed:
                tone_str = parsed.get("tone", "").upper()
                tone_map = {
                    "HAWKISH": FedTone.HAWKISH,
                    "DOVISH": FedTone.DOVISH,
                    "NEUTRAL": FedTone.NEUTRAL
                }
                tone = tone_map.get(tone_str, FedTone.UNKNOWN)

                confidence = float(parsed.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))

                return FedToneResult(
                    tone=tone,
                    confidence=confidence,
                    reasoning=parsed.get("reason"),
                    is_valid=True
                )
            else:
                return FedToneResult(
                    tone=self._fallback_fed_tone(text),
                    confidence=0.3,
                    reasoning="Fallback - JSON parsing failed",
                    is_valid=True
                )

        except Exception as e:
            logger.error(f"FED tone analysis failed: {e}")
            return FedToneResult(
                tone=self._fallback_fed_tone(text),
                confidence=0.2,
                error=str(e),
                is_valid=True
            )


# Instance singleton
ollama_client = OllamaClient()
