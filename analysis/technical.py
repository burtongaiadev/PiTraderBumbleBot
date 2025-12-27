"""
analysis/technical.py - Analyse technique (MM50, RSI)

Score basé sur:
- Position du prix vs MM50 (tendance)
- RSI 14 jours (momentum/surachat)
- Distance à la MM50 (force)

Score: 0 à 3 points
"""
import logging
from typing import List, Optional
from dataclasses import dataclass

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.twelve_data import twelve_data_client

logger = logging.getLogger(__name__)


@dataclass
class TechnicalScore:
    """Score technique d'une action"""
    symbol: str
    total_score: float = 0.0  # 0-3

    # Indicateurs
    price: Optional[float] = None
    ma50: Optional[float] = None
    rsi: Optional[float] = None

    # Métriques dérivées
    above_ma50: bool = False  # Prix > MM50
    ma50_distance: float = 0.0  # % au-dessus/dessous MM50

    # === TIMING (nouveau) ===
    days_above_ma50: int = 0  # Depuis combien de jours au-dessus de MM50
    momentum_5d: float = 0.0  # Momentum 5 jours (%)
    momentum_20d: float = 0.0  # Momentum 20 jours (%)
    is_accelerating: bool = False  # momentum_5d > momentum_20d
    timing_signal: str = "NEUTRAL"  # EARLY, OPTIMAL, LATE

    # Signaux
    trend_signal: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    rsi_signal: str = "NEUTRAL"  # OVERBOUGHT, OVERSOLD, NEUTRAL

    is_valid: bool = True
    error: Optional[str] = None


class TechnicalAnalyzer:
    """Analyse technique basée sur MM50 et RSI"""

    def __init__(self):
        # Paramètres RSI
        self.rsi_period = 14
        self.rsi_overbought = 70
        self.rsi_oversold = 30

        # Paramètres MM
        self.ma_period = 50

    def _calculate_rsi(self, prices: List[float]) -> Optional[float]:
        """
        Calcule le RSI sur une liste de prix (du plus récent au plus ancien)

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        if len(prices) < self.rsi_period + 1:
            return None

        # Inverser pour avoir chronologique (ancien → récent)
        prices = list(reversed(prices))

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        if len(gains) < self.rsi_period:
            return None

        # Moyenne simple pour la première période
        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period

        # Lissage exponentiel pour les périodes suivantes
        for i in range(self.rsi_period, len(gains)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 1)

    def _calculate_ma(self, prices: List[float], period: int) -> Optional[float]:
        """Calcule la moyenne mobile simple"""
        if len(prices) < period:
            return None

        return sum(prices[:period]) / period

    def _count_days_above_ma(self, closes: List[float], ma_period: int) -> int:
        """
        Compte depuis combien de jours le prix est au-dessus de la MM

        Returns:
            Nombre de jours consécutifs au-dessus (0 si actuellement en-dessous)
        """
        if len(closes) < ma_period + 10:
            return 0

        days = 0
        # Parcourir du plus récent au plus ancien
        for i in range(min(30, len(closes) - ma_period)):
            # Calculer la MM50 à ce jour
            ma = sum(closes[i:i + ma_period]) / ma_period
            price = closes[i]

            if price > ma:
                days += 1
            else:
                break  # On s'arrête dès qu'on passe en-dessous

        return days

    def _calculate_momentum(self, closes: List[float], days: int) -> float:
        """Calcule le momentum sur N jours (% de variation)"""
        if len(closes) < days + 1:
            return 0.0

        current = closes[0]
        past = closes[days]

        if past == 0:
            return 0.0

        return ((current - past) / past) * 100

    def _determine_timing(
        self,
        days_above: int,
        momentum_5d: float,
        momentum_20d: float,
        ma50_distance: float
    ) -> str:
        """
        Détermine le timing d'entrée

        EARLY: Début de tendance (1-5 jours au-dessus, accélération)
        OPTIMAL: Bon timing (pullback ou continuation saine)
        LATE: Trop tard (>15 jours, pas d'accélération, trop loin de MM50)
        """
        # Pas au-dessus de MM50
        if days_above == 0:
            return "NEUTRAL"

        # Croisement très récent (1-5 jours) avec accélération
        if days_above <= 5 and momentum_5d > momentum_20d / 4:
            return "EARLY"

        # Zone optimale: 5-15 jours, proche de MM50 (pullback potentiel)
        if 5 < days_above <= 15:
            if ma50_distance < 8:  # Pas trop éloigné
                return "OPTIMAL"
            elif momentum_5d > momentum_20d / 4:  # Ou accélération
                return "OPTIMAL"

        # Trop tard: > 15 jours ou trop loin sans accélération
        if days_above > 15 or ma50_distance > 15:
            if momentum_5d <= 0:  # Ralentissement
                return "LATE"

        return "NEUTRAL"

    def analyze(self, symbol: str) -> TechnicalScore:
        """Analyse technique complète d'une action"""
        # Récupérer 60 jours d'historique (marge pour MM50 + RSI + timing)
        history = twelve_data_client.get_time_series(symbol, interval="1day", outputsize=60)

        if not history.is_valid or len(history.prices) < self.ma_period:
            return TechnicalScore(
                symbol=symbol,
                is_valid=False,
                error=f"Historique insuffisant ({len(history.prices) if history.prices else 0} jours)"
            )

        # Extraire les prix de clôture (du plus récent au plus ancien)
        closes = [p["close"] for p in history.prices if p["close"] is not None]

        if len(closes) < self.ma_period:
            return TechnicalScore(
                symbol=symbol,
                is_valid=False,
                error="Données de prix manquantes"
            )

        current_price = closes[0]

        # Calculer MM50
        ma50 = self._calculate_ma(closes, self.ma_period)

        # Calculer RSI
        rsi = self._calculate_rsi(closes)

        # Analyser position vs MM50
        above_ma50 = False
        ma50_distance = 0.0
        trend_signal = "NEUTRAL"

        if ma50:
            above_ma50 = current_price > ma50
            ma50_distance = ((current_price - ma50) / ma50) * 100

            if ma50_distance > 5:
                trend_signal = "BULLISH"
            elif ma50_distance < -5:
                trend_signal = "BEARISH"

        # Analyser RSI
        rsi_signal = "NEUTRAL"
        if rsi:
            if rsi > self.rsi_overbought:
                rsi_signal = "OVERBOUGHT"
            elif rsi < self.rsi_oversold:
                rsi_signal = "OVERSOLD"

        # === TIMING ===
        days_above_ma50 = self._count_days_above_ma(closes, self.ma_period)
        momentum_5d = self._calculate_momentum(closes, 5)
        momentum_20d = self._calculate_momentum(closes, 20)
        is_accelerating = momentum_5d > (momentum_20d / 4) if momentum_20d != 0 else momentum_5d > 0
        timing_signal = self._determine_timing(days_above_ma50, momentum_5d, momentum_20d, ma50_distance)

        # Calculer score (0-3) avec timing
        score = self._calculate_score(
            above_ma50, ma50_distance, rsi, rsi_signal,
            timing_signal, is_accelerating
        )

        return TechnicalScore(
            symbol=symbol,
            total_score=score,
            price=current_price,
            ma50=round(ma50, 2) if ma50 else None,
            rsi=rsi,
            above_ma50=above_ma50,
            ma50_distance=round(ma50_distance, 1),
            days_above_ma50=days_above_ma50,
            momentum_5d=round(momentum_5d, 2),
            momentum_20d=round(momentum_20d, 2),
            is_accelerating=is_accelerating,
            timing_signal=timing_signal,
            trend_signal=trend_signal,
            rsi_signal=rsi_signal,
            is_valid=True
        )

    def _calculate_score(
        self,
        above_ma50: bool,
        ma50_distance: float,
        rsi: Optional[float],
        rsi_signal: str,
        timing_signal: str = "NEUTRAL",
        is_accelerating: bool = False
    ) -> float:
        """
        Calcule le score technique (0-3)

        Répartition:
        - Position MM50: 0-1 point
        - RSI: 0-1 point
        - Timing: 0-1 point (nouveau)
        """
        score = 0.0

        # === MM50 (0-1) ===
        if above_ma50:
            score += 0.5  # Base: au-dessus

            # Bonus distance modéré (jusqu'à +0.5)
            # Attention: trop loin = potentiellement trop tard
            if 0 < ma50_distance <= 10:
                score += 0.5  # Zone idéale
            elif ma50_distance > 10:
                score += 0.25  # Trop loin, prudence
        else:
            # En dessous = opportunité si proche
            if ma50_distance > -3:
                score += 0.3  # Très proche, pullback potentiel

        # === RSI (0-1) ===
        if rsi is not None:
            if rsi_signal == "OVERBOUGHT":
                score += 0  # Surachat = danger
            elif rsi_signal == "OVERSOLD":
                score += 0.75  # Survente = opportunité
            else:
                if 40 <= rsi <= 60:
                    score += 0.75  # Zone neutre idéale
                elif 30 <= rsi < 40:
                    score += 1.0  # Proche survente = meilleure opportunité
                elif 60 < rsi <= 70:
                    score += 0.4  # Proche surachat = prudence
                else:
                    score += 0.5
        else:
            score += 0.5

        # === TIMING (0-1) - NOUVEAU ===
        if timing_signal == "EARLY":
            score += 1.0  # Début de tendance = meilleur timing
        elif timing_signal == "OPTIMAL":
            score += 0.75  # Bon timing
        elif timing_signal == "LATE":
            score += 0.0  # Trop tard = pénalité
        else:
            score += 0.4  # Neutre

        # Bonus accélération
        if is_accelerating and above_ma50:
            score += 0.25

        return round(min(3.0, score), 1)

    def is_bullish(self, symbol: str) -> bool:
        """Vérifie si l'action est en tendance haussière (filtre rapide)"""
        result = self.analyze(symbol)
        return result.is_valid and result.above_ma50 and result.rsi_signal != "OVERBOUGHT"

    def analyze_batch(self, symbols: List[str]) -> List[TechnicalScore]:
        """Analyse technique de plusieurs actions"""
        results = []

        for symbol in symbols:
            result = self.analyze(symbol)
            results.append(result)

            if result.is_valid:
                logger.debug(
                    f"Technical {symbol}: {result.total_score}/3 "
                    f"(MA50: {result.ma50_distance:+.1f}%, RSI: {result.rsi})"
                )

        # Trier par score décroissant
        results.sort(key=lambda x: x.total_score if x.is_valid else -1, reverse=True)

        # Log résumé
        valid = [r for r in results if r.is_valid]
        bullish = [r for r in valid if r.above_ma50]

        logger.info(
            f"Technical: {len(valid)} analysés, "
            f"{len(bullish)} au-dessus MM50 ({len(bullish)/len(valid)*100:.0f}%)" if valid else "Technical: 0 analysés"
        )

        return results


# Instance exportée
technical_analyzer = TechnicalAnalyzer()
