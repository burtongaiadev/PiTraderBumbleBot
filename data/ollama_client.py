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


@dataclass
class LLMDiagnostics:
    """Statistiques de validation du LLM"""
    total_requests: int = 0
    json_parse_success: int = 0
    json_parse_failures: int = 0
    fallback_used: int = 0
    ollama_unavailable: int = 0
    avg_confidence: float = 0.0
    sentiment_distribution: dict = None

    def __post_init__(self):
        if self.sentiment_distribution is None:
            self.sentiment_distribution = {"POSITIVE": 0, "NEGATIVE": 0, "NEUTRAL": 0}

    def success_rate(self) -> float:
        """Taux de parsing JSON réussi"""
        if self.total_requests == 0:
            return 0.0
        return self.json_parse_success / self.total_requests

    def summary(self) -> str:
        """Résumé des stats"""
        return (
            f"LLM Stats: {self.total_requests} requêtes, "
            f"{self.success_rate()*100:.0f}% JSON OK, "
            f"{self.fallback_used} fallbacks, "
            f"conf. moy: {self.avg_confidence:.2f}"
        )


class OllamaClient:
    """
    Client Ollama pour analyse IA

    Optimisé pour Raspberry Pi 5:
    - Timeout long (120s)
    - Contexte réduit (2048 tokens)
    - Parsing robuste avec fallback
    - Cache 1h sur analyses
    - Diagnostics pour valider qualité LLM
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
        # Diagnostics
        self.diagnostics = LLMDiagnostics()
        self.debug_mode = False  # Activer pour logs détaillés

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
            
            logger.debug(f"Ollama API Response: {result}")
            
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

    def _update_diagnostics(self, result: SentimentResult, used_fallback: bool, json_ok: bool):
        """Met à jour les statistiques de diagnostic"""
        self.diagnostics.total_requests += 1

        if json_ok:
            self.diagnostics.json_parse_success += 1
        else:
            self.diagnostics.json_parse_failures += 1

        if used_fallback:
            self.diagnostics.fallback_used += 1

        # Mise à jour moyenne mobile de la confiance
        n = self.diagnostics.total_requests
        old_avg = self.diagnostics.avg_confidence
        self.diagnostics.avg_confidence = old_avg + (result.confidence - old_avg) / n

        # Distribution des sentiments
        if result.sentiment == Sentiment.POSITIVE:
            self.diagnostics.sentiment_distribution["POSITIVE"] += 1
        elif result.sentiment == Sentiment.NEGATIVE:
            self.diagnostics.sentiment_distribution["NEGATIVE"] += 1
        else:
            self.diagnostics.sentiment_distribution["NEUTRAL"] += 1

    def _log_llm_response(self, text: str, raw_response: str, result: SentimentResult):
        """Log détaillé pour debug/audit"""
        if self.debug_mode:
            logger.info(
                f"[LLM DEBUG]\n"
                f"  Input: {text[:100]}...\n"
                f"  Raw: {raw_response[:200]}\n"
                f"  Result: {result.sentiment.value}, conf={result.confidence:.2f}\n"
                f"  Reason: {result.reasoning}"
            )

    def get_diagnostics(self) -> LLMDiagnostics:
        """Retourne les diagnostics actuels"""
        return self.diagnostics

    def reset_diagnostics(self):
        """Reset les statistiques"""
        self.diagnostics = LLMDiagnostics()

    def validate_llm_quality(self) -> dict:
        """
        Valide la qualité du LLM avec des cas de test connus

        Returns:
            dict avec score de validation et détails
        """
        test_cases = [
            ("Apple stock soars 15% on record iPhone sales", Sentiment.POSITIVE),
            ("Company announces massive layoffs, stock crashes", Sentiment.NEGATIVE),
            ("Quarterly results meet analyst expectations", Sentiment.NEUTRAL),
            ("Revenue beats expectations, profit margins expand", Sentiment.POSITIVE),
            ("CEO resigns amid accounting scandal investigation", Sentiment.NEGATIVE),
        ]

        correct = 0
        results = []

        for text, expected in test_cases:
            result = self.analyze_sentiment(text)
            is_correct = result.sentiment == expected
            if is_correct:
                correct += 1

            results.append({
                "text": text[:50] + "...",
                "expected": expected.value,
                "got": result.sentiment.value,
                "confidence": result.confidence,
                "correct": is_correct
            })

        accuracy = correct / len(test_cases)

        return {
            "accuracy": accuracy,
            "score": f"{correct}/{len(test_cases)}",
            "status": "OK" if accuracy >= 0.8 else "WARNING" if accuracy >= 0.6 else "FAIL",
            "details": results
        }

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
            logger.debug("Ollama not available, using fallback")
            self.diagnostics.ollama_unavailable += 1
            result = SentimentResult(
                sentiment=self._fallback_sentiment(text),
                confidence=0.3,
                reasoning="Fallback keyword detection",
                is_valid=True
            )
            self._update_diagnostics(result, used_fallback=True, json_ok=False)
            return result

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

                result = SentimentResult(
                    sentiment=sentiment,
                    confidence=confidence,
                    reasoning=parsed.get("reason"),
                    is_valid=True
                )
                self._update_diagnostics(result, used_fallback=False, json_ok=True)
                self._log_llm_response(text, response, result)
                return result
            else:
                # Fallback si parsing échoue
                result = SentimentResult(
                    sentiment=self._fallback_sentiment(text),
                    confidence=0.3,
                    reasoning="Fallback - JSON parsing failed",
                    is_valid=True
                )
                self._update_diagnostics(result, used_fallback=True, json_ok=False)
                self._log_llm_response(text, response, result)
                return result

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            result = SentimentResult(
                sentiment=self._fallback_sentiment(text),
                confidence=0.2,
                error=str(e),
                is_valid=True
            )
            self._update_diagnostics(result, used_fallback=True, json_ok=False)
            return result

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

    def analyze_sentiment_batch(self, texts: list) -> list:
        """
        Analyse le sentiment de plusieurs textes en une seule requête

        Plus efficace que des appels individuels car:
        - Une seule requête réseau
        - Le modèle reste chargé en mémoire

        Args:
            texts: Liste de textes à analyser

        Returns:
            Liste de SentimentResult
        """
        if not texts:
            return []

        # Vérifier disponibilité Ollama
        if not self.is_available():
            logger.debug("Ollama not available, using fallback for batch")
            return [
                SentimentResult(
                    sentiment=self._fallback_sentiment(t),
                    confidence=0.3,
                    reasoning="Fallback keyword detection",
                    is_valid=True
                )
                for t in texts
            ]

        # Construire prompt batch
        batch_prompt = """Analyze the sentiment of each news headline below.
For EACH headline, output a JSON on a separate line:
{"id": N, "sentiment": "POSITIF" or "NEGATIF" or "NEUTRE", "confidence": 0.0-1.0}

Headlines:
"""
        for i, text in enumerate(texts):
            truncated = text[:200] if text else ""
            batch_prompt += f"{i+1}. {truncated}\n"

        batch_prompt += "\nJSON outputs (one per line):"

        try:
            response = self._generate(batch_prompt)
            results = self._parse_batch_response(response, texts)
            return results

        except Exception as e:
            logger.error(f"Batch sentiment analysis failed: {e}")
            # Fallback individuel
            return [
                SentimentResult(
                    sentiment=self._fallback_sentiment(t),
                    confidence=0.2,
                    error=str(e),
                    is_valid=True
                )
                for t in texts
            ]

    def _parse_batch_response(self, response: str, original_texts: list) -> list:
        """Parse la réponse batch et retourne les résultats"""
        results = []
        lines = response.strip().split('\n')

        sentiment_map = {
            "POSITIF": Sentiment.POSITIVE,
            "POSITIVE": Sentiment.POSITIVE,
            "NEGATIF": Sentiment.NEGATIVE,
            "NEGATIVE": Sentiment.NEGATIVE,
            "NEUTRE": Sentiment.NEUTRAL,
            "NEUTRAL": Sentiment.NEUTRAL
        }

        parsed_count = 0
        for line in lines:
            parsed = self._parse_json_response(line)
            if parsed and parsed_count < len(original_texts):
                sentiment_str = parsed.get("sentiment", "").upper()
                sentiment = sentiment_map.get(sentiment_str, Sentiment.NEUTRAL)
                confidence = float(parsed.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))

                results.append(SentimentResult(
                    sentiment=sentiment,
                    confidence=confidence,
                    is_valid=True
                ))
                parsed_count += 1

        # Compléter avec fallback si parsing incomplet
        while len(results) < len(original_texts):
            idx = len(results)
            results.append(SentimentResult(
                sentiment=self._fallback_sentiment(original_texts[idx]),
                confidence=0.3,
                reasoning="Fallback - batch parsing incomplete",
                is_valid=True
            ))

        return results


# Instance singleton
ollama_client = OllamaClient()
