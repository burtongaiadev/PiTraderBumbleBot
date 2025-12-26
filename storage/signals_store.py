"""
storage/signals_store.py - Historique des signaux avec notation

Stocke les signaux générés pour:
- Historique et traçabilité
- Notation différée (feedback loop)
- Suivi de performance
- Export pour analyse ML future

Workflow:
1. Signal généré -> save_signal()
2. Plus tard -> get_unrated_signals() pour /review
3. Utilisateur note -> rate_signal()
4. J+7 -> update_performance() automatique
5. Export -> export_csv() pour analyse
"""
import json
import uuid
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.twelve_data import twelve_data_client

logger = logging.getLogger(__name__)


@dataclass
class SignalRecord:
    """
    Enregistrement complet d'un signal

    Contient toutes les informations pour:
    - Reproduire l'analyse
    - Évaluer la qualité du signal
    - Entraîner un modèle futur
    """
    # Identifiant unique
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Timestamp
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # Action concernée
    symbol: str = ""
    price_at_signal: Optional[float] = None

    # Scores détaillés
    scores: Dict[str, float] = field(default_factory=dict)
    # scores = {"macro": -1, "market": 0, "fundamental": 4.5, "sentiment": 2.5}

    total_score: float = 0.0
    confidence: float = 0.0  # 0.0 - 1.0, indique la fiabilité du signal

    # Contexte
    macro_summary: str = ""
    market_summary: str = ""
    fundamental_summary: str = ""
    sentiment_summary: str = ""

    # Notation utilisateur (feedback loop)
    rating: Optional[int] = None  # 1-5 étoiles
    rated_at: Optional[str] = None

    # Suivi performance
    price_after_7d: Optional[float] = None
    actual_return: Optional[float] = None  # En %
    performance_updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Convertit en dictionnaire"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'SignalRecord':
        """Crée depuis un dictionnaire"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SignalsStore:
    """
    Gestionnaire des signaux

    Stockage en fichiers JSON individuels pour:
    - Simplicité (pas de SQLite)
    - Lisibilité (un fichier par signal)
    - Robustesse (pas de corruption DB)
    """

    def __init__(self, signals_dir: Optional[Path] = None):
        self.signals_dir = signals_dir or config.signals_dir
        self.signals_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, signal_id: str) -> Path:
        """Chemin du fichier signal"""
        return self.signals_dir / f"{signal_id}.json"

    def save_signal(self, signal: SignalRecord) -> str:
        """
        Sauvegarde un nouveau signal

        Args:
            signal: SignalRecord à sauvegarder

        Returns:
            ID du signal
        """
        path = self._get_path(signal.id)

        try:
            with open(path, "w") as f:
                json.dump(signal.to_dict(), f, indent=2, default=str)

            logger.info(f"Saved signal {signal.id} for {signal.symbol}")
            return signal.id

        except IOError as e:
            logger.error(f"Failed to save signal: {e}")
            raise

    def get_signal(self, signal_id: str) -> Optional[SignalRecord]:
        """
        Récupère un signal par son ID

        Args:
            signal_id: ID du signal

        Returns:
            SignalRecord ou None
        """
        path = self._get_path(signal_id)

        if not path.exists():
            return None

        try:
            with open(path, "r") as f:
                data = json.load(f)
            return SignalRecord.from_dict(data)

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to read signal {signal_id}: {e}")
            return None

    def rate_signal(self, signal_id: str, rating: int) -> bool:
        """
        Note un signal (1-5 étoiles)

        Args:
            signal_id: ID du signal
            rating: Note 1-5

        Returns:
            True si succès
        """
        if not 1 <= rating <= 5:
            raise ValueError("Rating must be between 1 and 5")

        signal = self.get_signal(signal_id)
        if not signal:
            return False

        signal.rating = rating
        signal.rated_at = datetime.now().isoformat()

        self.save_signal(signal)
        logger.info(f"Rated signal {signal_id}: {rating} stars")
        return True

    def get_unrated_signals(self, limit: int = 10) -> List[SignalRecord]:
        """
        Récupère les signaux non notés

        Pour la commande /review

        Args:
            limit: Nombre max de signaux

        Returns:
            Liste de SignalRecord non notés
        """
        unrated = []

        for path in sorted(self.signals_dir.glob("*.json"), reverse=True):
            if len(unrated) >= limit:
                break

            try:
                with open(path, "r") as f:
                    data = json.load(f)

                if data.get("rating") is None:
                    unrated.append(SignalRecord.from_dict(data))

            except (json.JSONDecodeError, IOError):
                continue

        return unrated

    def get_signals_for_performance_update(self, days: int = 7) -> List[SignalRecord]:
        """
        Récupère les signaux de plus de N jours sans performance mise à jour

        Pour le cron de suivi J+7

        Args:
            days: Nombre de jours minimum

        Returns:
            Liste de SignalRecord à mettre à jour
        """
        cutoff = datetime.now() - timedelta(days=days)
        to_update = []

        for path in self.signals_dir.glob("*.json"):
            try:
                with open(path, "r") as f:
                    data = json.load(f)

                # Vérifier si signal assez ancien
                signal_time = datetime.fromisoformat(data["timestamp"])
                if signal_time > cutoff:
                    continue

                # Vérifier si performance pas encore mise à jour
                if data.get("price_after_7d") is None:
                    to_update.append(SignalRecord.from_dict(data))

            except (json.JSONDecodeError, IOError, ValueError):
                continue

        return to_update

    def update_performance(self, signal_id: str) -> bool:
        """
        Met à jour la performance d'un signal

        Récupère le prix actuel et calcule le return

        Args:
            signal_id: ID du signal

        Returns:
            True si succès
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.price_at_signal is None:
            return False

        try:
            # Récupérer prix actuel
            quote = twelve_data_client.get_quote(signal.symbol)
            if not quote.is_valid or quote.price is None:
                return False

            signal.price_after_7d = quote.price
            signal.actual_return = (
                (quote.price - signal.price_at_signal)
                / signal.price_at_signal * 100
            )
            signal.performance_updated_at = datetime.now().isoformat()

            self.save_signal(signal)
            logger.info(
                f"Updated performance for {signal.symbol}: "
                f"{signal.actual_return:+.2f}%"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to update performance: {e}")
            return False

    def get_all_signals(self, limit: int = 100) -> List[SignalRecord]:
        """
        Récupère tous les signaux

        Args:
            limit: Nombre max

        Returns:
            Liste de SignalRecord triée par date décroissante
        """
        signals = []

        for path in sorted(self.signals_dir.glob("*.json"), reverse=True):
            if len(signals) >= limit:
                break

            try:
                with open(path, "r") as f:
                    data = json.load(f)
                signals.append(SignalRecord.from_dict(data))

            except (json.JSONDecodeError, IOError):
                continue

        return signals

    def export_csv(self, output_path: Optional[Path] = None) -> Path:
        """
        Exporte tous les signaux en CSV

        Pour analyse ML / statistiques

        Args:
            output_path: Chemin du fichier (défaut: signals_export.csv)

        Returns:
            Chemin du fichier créé
        """
        import csv

        output_path = output_path or (self.signals_dir / "signals_export.csv")
        signals = self.get_all_signals(limit=10000)

        if not signals:
            logger.warning("No signals to export")
            return output_path

        # Définir colonnes
        fieldnames = [
            "id", "timestamp", "symbol", "price_at_signal",
            "macro_score", "market_score", "fundamental_score", "sentiment_score",
            "total_score", "rating", "price_after_7d", "actual_return"
        ]

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for s in signals:
                row = {
                    "id": s.id,
                    "timestamp": s.timestamp,
                    "symbol": s.symbol,
                    "price_at_signal": s.price_at_signal,
                    "macro_score": s.scores.get("macro"),
                    "market_score": s.scores.get("market"),
                    "fundamental_score": s.scores.get("fundamental"),
                    "sentiment_score": s.scores.get("sentiment"),
                    "total_score": s.total_score,
                    "rating": s.rating,
                    "price_after_7d": s.price_after_7d,
                    "actual_return": s.actual_return
                }
                writer.writerow(row)

        logger.info(f"Exported {len(signals)} signals to {output_path}")
        return output_path

    def export_ml_ready(self, output_path: Optional[Path] = None) -> Path:
        """
        Exporte les signaux avec features normalisées pour ML

        Features:
        - Scores normalisés [0, 1]
        - One-hot encoding du symbole
        - Target: succès binaire (return > 0)
        - Colonnes prêtes pour sklearn/pandas

        Args:
            output_path: Chemin du fichier (défaut: signals_ml.csv)

        Returns:
            Chemin du fichier créé
        """
        import csv
        from datetime import datetime as dt

        output_path = output_path or (self.signals_dir / "signals_ml.csv")
        signals = self.get_all_signals(limit=10000)

        # Filtrer: garder uniquement les signaux avec return connu
        signals_with_outcome = [s for s in signals if s.actual_return is not None]

        if not signals_with_outcome:
            logger.warning("No signals with outcomes to export for ML")
            return output_path

        # Colonnes ML-ready
        fieldnames = [
            # Identifiants
            "signal_id",
            "symbol",
            "timestamp_unix",
            "day_of_week",  # 0-6
            "hour",  # 0-23

            # Features normalisées [0, 1]
            "market_norm",  # (-1,+1) -> (0,1)
            "fundamental_norm",  # (0,3) -> (0,1)
            "sentiment_norm",  # (0,3) -> (0,1)
            "total_score_norm",  # (0,10) -> (0,1)
            "confidence",  # déjà 0-1

            # Features brutes
            "price_at_signal",

            # Targets
            "actual_return",  # Valeur continue
            "is_success",  # Binaire: 1 si return > 0
            "is_strong_success",  # Binaire: 1 si return > 2%
            "rating",  # Note utilisateur si disponible
        ]

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for s in signals_with_outcome:
                # Parser timestamp
                try:
                    ts = dt.fromisoformat(s.timestamp)
                    timestamp_unix = ts.timestamp()
                    day_of_week = ts.weekday()
                    hour = ts.hour
                except ValueError:
                    timestamp_unix = 0
                    day_of_week = 0
                    hour = 0

                # Normaliser les scores
                market = s.scores.get("market", 0)
                fundamental = s.scores.get("fundamental", 0)
                sentiment = s.scores.get("sentiment", 0)

                row = {
                    "signal_id": s.id,
                    "symbol": s.symbol,
                    "timestamp_unix": timestamp_unix,
                    "day_of_week": day_of_week,
                    "hour": hour,

                    # Normalisation: [-1,+1] -> [0,1]
                    "market_norm": (market + 1) / 2,
                    # Normalisation: [0,3] -> [0,1]
                    "fundamental_norm": fundamental / 3,
                    "sentiment_norm": sentiment / 3,
                    # Normalisation: [0,10] -> [0,1]
                    "total_score_norm": s.total_score / 10,
                    "confidence": s.confidence,

                    "price_at_signal": s.price_at_signal,

                    # Targets
                    "actual_return": s.actual_return,
                    "is_success": 1 if s.actual_return > 0 else 0,
                    "is_strong_success": 1 if s.actual_return > 2 else 0,
                    "rating": s.rating,
                }
                writer.writerow(row)

        logger.info(f"Exported {len(signals_with_outcome)} ML-ready signals to {output_path}")
        return output_path

    def get_statistics(self) -> Dict[str, Any]:
        """
        Statistiques des signaux

        Returns:
            Dict avec stats
        """
        signals = self.get_all_signals(limit=10000)

        if not signals:
            return {"count": 0}

        rated = [s for s in signals if s.rating is not None]
        with_return = [s for s in signals if s.actual_return is not None]

        stats = {
            "total_count": len(signals),
            "rated_count": len(rated),
            "unrated_count": len(signals) - len(rated),
            "with_return_count": len(with_return)
        }

        # Stats par rating
        if rated:
            stats["avg_rating"] = sum(s.rating for s in rated) / len(rated)
            stats["rating_distribution"] = {
                i: sum(1 for s in rated if s.rating == i)
                for i in range(1, 6)
            }

        # Stats de performance
        if with_return:
            returns = [s.actual_return for s in with_return]
            stats["avg_return"] = sum(returns) / len(returns)
            stats["positive_returns"] = sum(1 for r in returns if r > 0)
            stats["negative_returns"] = sum(1 for r in returns if r < 0)

        return stats


# Instance singleton
signals_store = SignalsStore()
