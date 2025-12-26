#!/usr/bin/env python3
"""
main.py - PiTrader Orchestrator

Analyse Top-Down: Macro -> Market -> Momentum -> Sentiment -> Signals
"""
import argparse
import logging
import time
import gc
from datetime import datetime
from typing import List

from config import config
from analysis.macro_economy import macro_analyzer, MacroAnalysis
from analysis.market_context import market_analyzer, MarketContext
from analysis.fundamentals import fundamentals_analyzer, FundamentalScore
from analysis.sentiment import sentiment_analyzer, SentimentScore
from storage.signals_store import signals_store, SignalRecord
from telegram import telegram_bot

# Logging simplifié
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('pitrader.log'),
        logging.StreamHandler()
    ]
)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('requests').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PiTrader:
    """Bot de trading Top-Down"""

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode

    def run_full_analysis(self):
        """Exécute l'analyse complète"""
        start = datetime.now()

        logger.info("═" * 50)
        logger.info(f"🚀 PiTrader - Analyse de {len(config.watchlist)} actions")
        logger.info("═" * 50)

        try:
            # Phase 1: Macro
            logger.info("📰 Phase 1: Analyse Macro (news FED)...")
            macro = macro_analyzer.analyze()
            logger.info(f"   → FED {macro.fed_tone} (score: {macro.total_score:+d})")

            # Phase 2: Market
            logger.info("📊 Phase 2: Analyse Marché...")
            market = market_analyzer.analyze()
            logger.info(f"   → Score marché: {market.market_score:+d}")

            # Phase 3: Momentum
            logger.info("📈 Phase 3: Analyse Momentum...")
            fundamentals = fundamentals_analyzer.analyze_watchlist()
            valid = [f for f in fundamentals if f.is_valid]
            logger.info(f"   → {len(valid)} actions analysées")

            # Phase 4: Sentiment (top 3)
            logger.info("💬 Phase 4: Analyse Sentiment...")
            top_symbols = [f.symbol for f in fundamentals[:3]]
            sentiments = sentiment_analyzer.analyze_multiple(top_symbols)

            # Phase 5: Signaux
            logger.info("🎯 Phase 5: Génération Signaux...")
            signals = self._generate_signals(macro, market, fundamentals, sentiments)

            # Résumé
            duration = (datetime.now() - start).seconds
            logger.info("═" * 50)
            logger.info(f"✅ Terminé en {duration}s - {len(signals)} signaux")
            logger.info("═" * 50)

            # Envoi Telegram
            self._send_summary(macro, market, fundamentals, sentiments, signals)

        except Exception as e:
            logger.error(f"❌ Erreur: {e}")
            if not self.test_mode:
                telegram_bot.send_error_alert(str(e))

        finally:
            gc.collect()

    def _generate_signals(
        self,
        macro: MacroAnalysis,
        market: MarketContext,
        fundamentals: List[FundamentalScore],
        sentiments: List[SentimentScore]
    ) -> List[SignalRecord]:
        """Génère les signaux d'achat"""
        signals = []
        sentiment_map = {s.symbol: s for s in sentiments}

        for fund in fundamentals:
            if not fund.is_valid:
                continue

            sent = sentiment_map.get(fund.symbol)
            sent_score = sent.total_score if sent else 1.5

            # Score total (0-10)
            raw = macro.total_score + market.market_score + fund.total_score + sent_score
            score = min(10, max(0, (raw + 3) * 10 / 11))

            if score >= config.scoring.alert_threshold:
                # Récupérer prix actuel
                from data.twelve_data import twelve_data_client
                quote = twelve_data_client.get_quote(fund.symbol)
                price = quote.price if quote.is_valid else None

                signal = SignalRecord(
                    symbol=fund.symbol,
                    total_score=score,
                    scores={
                        "macro": macro.total_score,
                        "market": market.market_score,
                        "fundamental": fund.total_score,
                        "sentiment": sent_score
                    },
                    price_at_signal=price
                )
                signals.append(signal)
                signals_store.save_signal(signal)

                logger.info(f"   🚨 SIGNAL: {fund.symbol} ({score:.1f}/10)")

        return signals

    def _send_summary(
        self,
        macro: MacroAnalysis,
        market: MarketContext,
        fundamentals: List[FundamentalScore],
        _sentiments: List[SentimentScore],
        signals: List[SignalRecord]
    ):
        """Envoie résumé Telegram"""
        if self.test_mode:
            logger.info("[TEST] Message Telegram non envoyé")
            return

        # Construire message
        lines = ["📊 <b>PiTrader - Résumé</b>\n"]

        # Contexte
        lines.append(f"🌍 Macro: {macro.fed_tone} ({macro.total_score:+d})")
        lines.append(f"📈 Marché: {market.market_score:+d}\n")

        # Top 3 momentum
        lines.append("<b>Top Momentum:</b>")
        for f in fundamentals[:3]:
            if f.is_valid:
                emoji = "🟢" if f.momentum > 0.1 else "🔴" if f.momentum < -0.1 else "⚪"
                lines.append(f"  {emoji} {f.symbol}: {f.momentum:+.0%}")

        # Signaux
        if signals:
            lines.append("\n<b>🚨 Signaux:</b>")
            for s in signals:
                lines.append(f"  • {s.symbol}: {s.total_score:.1f}/10")
        else:
            lines.append("\n<i>Pas de signal aujourd'hui</i>")

        message = "\n".join(lines)
        telegram_bot.send_message(message)


def main():
    parser = argparse.ArgumentParser(description="PiTrader - Bot de signaux")
    parser.add_argument("--test", action="store_true", help="Mode test (pas d'envoi Telegram)")
    parser.add_argument("--loop", action="store_true", help="Mode boucle")
    parser.add_argument("--interval", type=int, default=3600, help="Intervalle en secondes")
    args = parser.parse_args()

    trader = PiTrader(test_mode=args.test)

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
