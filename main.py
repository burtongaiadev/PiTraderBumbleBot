#!/usr/bin/env python3
"""
main.py - PiTrader Orchestrator

Analyse Top-Down: Market -> Momentum -> Sentiment -> Signals
"""
import argparse
import logging
import time
import gc
from datetime import datetime
from typing import List, Optional

from config import config
from analysis.market_context import market_analyzer, MarketContext
from analysis.fundamentals import fundamentals_analyzer, FundamentalScore
from analysis.sentiment import sentiment_analyzer, SentimentScore
from analysis.technical import technical_analyzer, TechnicalScore
from storage.signals_store import signals_store, SignalRecord
from telegram import telegram_bot
from data.ollama_client import ollama_client
from data.twelve_data import twelve_data_client

# Logging avec format stylisé
class ColoredFormatter(logging.Formatter):
    """Formatter avec couleurs pour une meilleure lisibilité"""
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record):
        # Ajouter des couleurs seulement pour la console
        if hasattr(record, 'levelname'):
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            record.levelname = f"{color}{record.levelname}{self.COLORS['RESET']}"
        
        # Formater le message
        formatted = super().format(record)
        
        # Nettoyer les noms de modules pour un affichage plus court
        if '__main__' in formatted:
            formatted = formatted.replace('__main__', 'PiTrader')
        elif 'data.' in formatted:
            module = formatted.split(' │ ')[1]
            short_module = module.replace('data.', '').replace('twelve_data', '12data')
            formatted = formatted.replace(module, short_module)
        elif 'analysis.' in formatted:
            module = formatted.split(' │ ')[1]
            short_module = module.replace('analysis.', '')
            formatted = formatted.replace(module, short_module)
            
        return formatted

# Configuration du logging
console_formatter = ColoredFormatter(
    '%(asctime)s │ %(name)-15s │ %(levelname)-8s │ %(message)s',
    datefmt='%H:%M:%S'
)

file_formatter = logging.Formatter(
    '%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s',
    datefmt='%H:%M:%S'
)

# Configuration principale
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[]
)

# Handler console avec couleurs
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)

# Handler fichier sans couleurs
file_handler = logging.FileHandler('pitrader.log')
file_handler.setFormatter(file_formatter)

# Appliquer les handlers
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('requests').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PiTrader:
    """Bot de trading Top-Down"""

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode

    def health_check(self) -> dict:
        """Vérifie l'état de tous les services"""
        status = {
            "ollama": False,
            "twelve_data": False,
            "news_api": False,
            "telegram": False
        }

        # Test Ollama
        try:
            status["ollama"] = ollama_client.is_available()
        except Exception:
            pass

        # Test Twelve Data (avec un ticker simple)
        try:
            quote = twelve_data_client.get_quote("AAPL")
            status["twelve_data"] = quote.is_valid
        except Exception:
            pass

        # Test NewsAPI
        try:
            result = news_client.search_news("test", page_size=1)
            status["news_api"] = result.is_valid
        except Exception:
            pass

        # Test Telegram
        try:
            status["telegram"] = bool(config.telegram.bot_token and config.telegram.chat_id)
        except Exception:
            pass

        return status

    def warmup(self):
        """Pré-chauffe le modèle Ollama pour éviter le cold start"""
        logger.info("🔥 Warmup Ollama...")
        try:
            if ollama_client.is_available():
                # Petite requête pour charger le modèle en mémoire
                ollama_client.analyze_sentiment("Warming up the model.")
                logger.info("   → Ollama prêt")
            else:
                logger.warning("   → Ollama non disponible")
        except Exception as e:
            logger.warning(f"   → Warmup échoué: {e}")

    def run_full_analysis(self):
        """Exécute l'analyse complète"""
        start = datetime.now()

        logger.info("═" * 50)
        logger.info(f"🚀 PiTrader - Analyse de {len(config.watchlist)} actions")
        logger.info("═" * 50)

        try:
            # Phase 1: Market
            logger.info("📊 Phase 1: Analyse Marché...")
            market = market_analyzer.analyze()
            logger.info(f"   → Score marché: {market.market_score:+d}")

            # Phase 2: Momentum
            logger.info("📈 Phase 2: Analyse Momentum...")
            fundamentals = fundamentals_analyzer.analyze_watchlist()
            valid = [f for f in fundamentals if f.is_valid]
            logger.info(f"   → {len(valid)} actions analysées")

            # Phase 3: Technique (top 10 momentum)
            logger.info("📉 Phase 3: Analyse Technique...")
            top_momentum = [f.symbol for f in fundamentals[:10] if f.is_valid]
            technicals = technical_analyzer.analyze_batch(top_momentum)

            # Phase 4: Sentiment (top 5 après filtre technique)
            logger.info("💬 Phase 4: Analyse Sentiment...")
            # Filtrer: garder seulement ceux au-dessus de MM50
            bullish_symbols = [t.symbol for t in technicals if t.is_valid and t.above_ma50][:5]
            sentiments = sentiment_analyzer.analyze_multiple(bullish_symbols) if bullish_symbols else []

            # Phase 5: Signaux
            logger.info("🎯 Phase 5: Génération Signaux...")
            signals = self._generate_signals(market, fundamentals, technicals, sentiments)

            # Résumé
            duration = (datetime.now() - start).seconds
            logger.info("═" * 50)
            logger.info(f"✅ Terminé en {duration}s - {len(signals)} signaux")
            logger.info("═" * 50)

            # Envoi Telegram
            self._send_summary(market, fundamentals, technicals, sentiments, signals)

        except Exception as e:
            logger.error(f"❌ Erreur: {e}")
            if not self.test_mode:
                telegram_bot.send_error_alert(str(e))

        finally:
            gc.collect()

    def _generate_signals(
        self,
        market: MarketContext,
        fundamentals: List[FundamentalScore],
        technicals: List[TechnicalScore],
        sentiments: List[SentimentScore]
    ) -> List[SignalRecord]:
        """Génère les signaux d'achat"""
        signals = []
        sentiment_map = {s.symbol: s for s in sentiments}
        technical_map = {t.symbol: t for t in technicals}

        # Condition bloquante: si market négatif, pas de signal
        if market.market_score < 0:
            logger.info("   ⛔ Marché défavorable - Pas de signal")
            return signals

        for fund in fundamentals:
            if not fund.is_valid:
                continue

            tech = technical_map.get(fund.symbol)
            sent = sentiment_map.get(fund.symbol)

            # Filtre technique: doit être au-dessus de MM50
            if tech and tech.is_valid and not tech.above_ma50:
                continue  # Skip si sous MM50

            # Filtre RSI: éviter les surachats
            if tech and tech.is_valid and tech.rsi_signal == "OVERBOUGHT":
                continue  # Skip si RSI > 70

            tech_score = tech.total_score if (tech and tech.is_valid) else 1.5
            sent_score = sent.total_score if sent else 1.5

            # Score total (0-10)
            # Market: -1 à +1 → normalisé 0-2.5 (poids: 25%)
            # Technical: 0-3 → normalisé 0-2.5 (poids: 25%)
            # Momentum: 0-3 → normalisé 0-2.5 (poids: 25%)
            # Sentiment: 0-3 → normalisé 0-2.5 (poids: 25%)
            market_norm = (market.market_score + 1) * 1.25  # -1→0, 0→1.25, +1→2.5
            tech_norm = (tech_score / 3) * 2.5  # 0→0, 3→2.5
            fund_norm = (fund.total_score / 3) * 2.5  # 0→0, 3→2.5
            sent_norm = (sent_score / 3) * 2.5  # 0→0, 3→2.5

            # Score brut: 0 à 10
            score = market_norm + tech_norm + fund_norm + sent_norm

            if score >= config.scoring.alert_threshold:
                # Récupérer prix actuel (déjà dans tech si disponible)
                price = tech.price if (tech and tech.is_valid) else None
                if not price:
                    quote = twelve_data_client.get_quote(fund.symbol)
                    price = quote.price if quote.is_valid else None

                # Calculer confiance globale
                confidence = self._calculate_confidence(market, fund, tech, sent)

                signal = SignalRecord(
                    symbol=fund.symbol,
                    total_score=score,
                    confidence=confidence,
                    scores={
                        "market": market.market_score,
                        "technical": tech_score,
                        "momentum": fund.total_score,
                        "sentiment": sent_score
                    },
                    price_at_signal=price
                )
                signals.append(signal)
                signals_store.save_signal(signal)

                # Log avec détails techniques
                ma_info = f"MA50:{tech.ma50_distance:+.0f}%" if (tech and tech.is_valid) else ""
                rsi_info = f"RSI:{tech.rsi:.0f}" if (tech and tech.is_valid and tech.rsi) else ""
                logger.info(f"   🚨 SIGNAL: {fund.symbol} ({score:.1f}/10, {ma_info} {rsi_info})")

        return signals

    def _calculate_confidence(
        self,
        market: MarketContext,
        fund: FundamentalScore,
        tech: Optional[TechnicalScore],
        sent: Optional[SentimentScore]
    ) -> float:
        """
        Calcule un score de confiance global (0-1)

        Facteurs:
        - Validité des données (market, technical, fundamentals, sentiment)
        - Force des indicateurs techniques
        - Confiance Ollama sur le sentiment
        """
        factors = []

        # 1. Validité des sources (0.25 chacune)
        if market.is_valid:
            factors.append(0.25)
        if fund.is_valid:
            factors.append(0.25)
        if tech and tech.is_valid:
            factors.append(0.25)
        else:
            factors.append(0.10)
        if sent and sent.is_valid:
            # Pondérer par la confiance Ollama
            factors.append(0.25 * sent.avg_confidence if sent.avg_confidence > 0 else 0.15)
        else:
            factors.append(0.10)

        # 2. Bonus technique: forte position au-dessus de MM50
        if tech and tech.is_valid and tech.ma50_distance > 5:
            factors.append(0.05)

        # 3. Bonus technique: RSI en zone idéale (40-60)
        if tech and tech.is_valid and tech.rsi and 40 <= tech.rsi <= 60:
            factors.append(0.05)

        # 4. Bonus: nombre d'articles analysés (plus = plus confiant)
        if sent and sent.articles_analyzed >= 3:
            factors.append(0.05)

        # 5. Bonus: volume anormal détecté (signal plus fort)
        if market.high_volume_count > 0:
            factors.append(0.05)

        return min(1.0, sum(factors))

    def _send_summary(
        self,
        market: MarketContext,
        fundamentals: List[FundamentalScore],
        technicals: List[TechnicalScore],
        _sentiments: List[SentimentScore],
        signals: List[SignalRecord]
    ):
        """Envoie résumé Telegram"""
        if self.test_mode:
            logger.info("[TEST] Message Telegram non envoyé")
            return

        technical_map = {t.symbol: t for t in technicals}

        # Construire message
        lines = ["📊 <b>PiTrader - Résumé</b>\n"]

        # Contexte marché
        market_emoji = "🟢" if market.market_score > 0 else "🔴" if market.market_score < 0 else "⚪"
        lines.append(f"{market_emoji} Marché: {market.market_score:+d} ({market.recommendation})\n")

        # Top 3 momentum + technique
        lines.append("<b>Top Actions:</b>")
        for f in fundamentals[:3]:
            if f.is_valid:
                tech = technical_map.get(f.symbol)
                emoji = "🟢" if f.momentum > 0.1 else "🔴" if f.momentum < -0.1 else "⚪"
                tech_info = ""
                if tech and tech.is_valid:
                    ma_emoji = "📈" if tech.above_ma50 else "📉"
                    tech_info = f" {ma_emoji}MM50:{tech.ma50_distance:+.0f}%"
                lines.append(f"  {emoji} {f.symbol}: {f.momentum:+.0%}{tech_info}")

        # Signaux
        if signals:
            lines.append("\n<b>🚨 Signaux:</b>")
            for s in signals:
                tech = technical_map.get(s.symbol)
                rsi_info = f" RSI:{tech.rsi:.0f}" if (tech and tech.rsi) else ""
                lines.append(f"  • {s.symbol}: {s.total_score:.1f}/10{rsi_info}")
        else:
            lines.append("\n<i>Pas de signal aujourd'hui</i>")

        message = "\n".join(lines)
        telegram_bot.send_message(message)


def is_first_run_after_boot() -> bool:
    """
    Vérifie si c'est le premier lancement après un reboot

    Utilise un fichier marqueur avec le boot_id du système.
    """
    marker_file = config.runtime_dir / ".last_boot_id"

    # Récupérer le boot_id actuel (Linux)
    try:
        with open('/proc/sys/kernel/random/boot_id', 'r') as f:
            current_boot_id = f.read().strip()
    except (FileNotFoundError, IOError):
        # Pas sur Linux, utiliser l'uptime comme fallback
        try:
            with open('/proc/uptime', 'r') as f:
                uptime = float(f.readline().split()[0])
                # Si uptime < 10 min, considérer comme premier run
                return uptime < 600
        except (FileNotFoundError, IOError):
            return False

    # Vérifier si le boot_id a changé
    try:
        if marker_file.exists():
            with open(marker_file, 'r') as f:
                last_boot_id = f.read().strip()
            if last_boot_id == current_boot_id:
                return False
    except IOError:
        pass

    # Sauvegarder le nouveau boot_id
    try:
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        with open(marker_file, 'w') as f:
            f.write(current_boot_id)
    except IOError:
        pass

    return True


def main():
    parser = argparse.ArgumentParser(description="PiTrader - Bot de signaux")
    parser.add_argument("--test", action="store_true", help="Mode test (pas d'envoi Telegram)")
    parser.add_argument("--loop", action="store_true", help="Mode boucle")
    parser.add_argument("--interval", type=int, default=3600, help="Intervalle en secondes")
    parser.add_argument("--health", action="store_true", help="Vérifie l'état des services")
    parser.add_argument("--validate-llm", action="store_true", help="Valide la qualité du LLM")
    parser.add_argument("--llm-debug", action="store_true", help="Active les logs détaillés LLM")
    args = parser.parse_args()

    trader = PiTrader(test_mode=args.test)

    # Health check
    if args.health:
        status = trader.health_check()
        logger.info("🏥 Health Check:")
        for service, ok in status.items():
            emoji = "✅" if ok else "❌"
            logger.info(f"   {emoji} {service}")
        return

    # Validation LLM
    if args.validate_llm:
        logger.info("🧪 Validation LLM...")
        if not ollama_client.is_available():
            logger.error("❌ Ollama non disponible!")
            return

        result = ollama_client.validate_llm_quality()
        logger.info(f"📊 Résultat: {result['score']} - {result['status']}")

        for detail in result['details']:
            emoji = "✅" if detail['correct'] else "❌"
            logger.info(
                f"   {emoji} {detail['text']}\n"
                f"      Attendu: {detail['expected']}, Obtenu: {detail['got']} "
                f"(conf: {detail['confidence']:.2f})"
            )
        return

    # Mode debug LLM
    if args.llm_debug:
        ollama_client.debug_mode = True
        logger.info("🔍 Mode debug LLM activé")

    # Warmup Ollama au démarrage
    trader.warmup()

    # Notification Telegram au premier lancement après reboot
    if not args.test and is_first_run_after_boot():
        logger.info("📱 Premier lancement après reboot - Envoi notification...")
        telegram_bot.send_startup_notification(
            watchlist_count=len(config.watchlist),
            ollama_available=ollama_client.is_available()
        )

    if args.loop:
        logger.info(f"Mode boucle - intervalle: {args.interval}s")
        while True:
            try:
                trader.run_full_analysis()
                logger.info(f"💤 Pause {args.interval}s...")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("Arrêt demandé")
                break
    else:
        trader.run_full_analysis()


if __name__ == "__main__":
    main()
