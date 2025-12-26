#!/usr/bin/env python3
"""
main.py - Orchestrateur principal PiTrader

Architecture Top-Down:
1. Analyse Macro (si très défavorable -> alerte + stop)
2. Analyse Market Context (si bear/high vol -> prudence)
3. Analyse Fondamentale des actions
4. Analyse Sentiment (pour top picks seulement)
5. Génération signaux + alertes Telegram

Optimisations Raspberry Pi:
- Délais entre étapes pour gestion thermique
- Garbage collection entre phases
- Logging détaillé pour debug

Usage:
    python main.py              # Run once
    python main.py --loop       # Run en boucle (interval par défaut: 1h)
    python main.py --test       # Mode test (pas d'envoi Telegram)
    python main.py --review     # Lancer le mode review interactif
"""
import argparse
import logging
import time
import gc
import sys
from datetime import datetime
from typing import List, Optional

from config import config
from analysis.macro_economy import macro_analyzer, MacroAnalysis
from analysis.market_context import market_analyzer, MarketContext
from analysis.fundamentals import fundamentals_analyzer, FundamentalScore
from analysis.sentiment import sentiment_analyzer, SentimentScore
from storage.signals_store import signals_store, SignalRecord
from telegram import telegram_bot
from utils.memory import MemoryMonitor, memory_scope
from utils.cache import cache_manager

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pitrader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PiTrader:
    """
    Bot de trading Top-Down pour Raspberry Pi

    Flux d'exécution:
    Macro -> Market -> Fundamentals -> Sentiment -> Signals

    Chaque étape peut court-circuiter si conditions défavorables.
    """

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode
        self.inter_phase_delay = config.thermal.inter_request_delay

    def run_full_analysis(self) -> dict:
        """
        Exécute l'analyse complète Top-Down

        Returns:
            Dict avec résultats de chaque phase
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("Starting PiTrader Full Analysis")
        logger.info(f"Watchlist: {config.watchlist}")
        logger.info(f"Test mode: {self.test_mode}")
        logger.info("=" * 60)

        results = {
            "macro": None,
            "market": None,
            "fundamentals": [],
            "sentiment": [],
            "signals_generated": 0,
            "execution_time": None
        }

        try:
            # ===== PHASE 1: ANALYSE MACRO =====
            with memory_scope():
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 1: Macro Economy Analysis")
                logger.info("=" * 40)
                results["macro"] = self._run_macro_analysis()
                self._phase_cooldown()

            # Court-circuit si macro très défavorable
            if results["macro"].total_score <= -2:
                logger.warning("⚠️ Macro très défavorable - Envoi alerte seulement")
                self._send_macro_warning(results["macro"])
                results["execution_time"] = (datetime.now() - start_time).seconds
                return results

            # ===== PHASE 2: ANALYSE MARKET CONTEXT =====
            with memory_scope():
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 2: Market Context Analysis")
                logger.info("=" * 40)
                results["market"] = self._run_market_analysis()
                self._phase_cooldown()

            # ===== PHASE 3: ANALYSE FONDAMENTALE =====
            with memory_scope():
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 3: Fundamental Analysis")
                logger.info("=" * 40)
                results["fundamentals"] = self._run_fundamental_analysis()
                self._phase_cooldown()

            # ===== PHASE 4: ANALYSE SENTIMENT (Top 5 seulement) =====
            with memory_scope():
                logger.info("\n" + "=" * 40)
                logger.info("PHASE 4: Sentiment Analysis")
                logger.info("=" * 40)
                top_symbols = [f.symbol for f in results["fundamentals"][:5]]
                results["sentiment"] = self._run_sentiment_analysis(top_symbols)
                self._phase_cooldown()

            # ===== PHASE 5: GÉNÉRATION SIGNAUX =====
            logger.info("\n" + "=" * 40)
            logger.info("PHASE 5: Signal Generation")
            logger.info("=" * 40)
            signals = self._generate_signals(
                results["macro"],
                results["market"],
                results["fundamentals"],
                results["sentiment"]
            )
            results["signals_generated"] = len(signals)

            # Envoi résumé
            self._send_daily_summary(results)

        except Exception as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            if not self.test_mode:
                telegram_bot.send_error_alert(str(e))

        finally:
            # Cleanup
            cache_manager.cleanup_all()
            gc.collect()

            results["execution_time"] = (datetime.now() - start_time).seconds
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Analysis completed in {results['execution_time']}s")
            logger.info(f"Signals generated: {results['signals_generated']}")
            MemoryMonitor.log_stats()
            logger.info("=" * 60)

        return results

    def _run_macro_analysis(self) -> MacroAnalysis:
        """Exécute analyse macro"""
        analysis = macro_analyzer.analyze()

        logger.info(f"📊 Macro Score: {analysis.total_score}")
        for factor in analysis.factors:
            emoji = "🟢" if factor.score > 0 else "🔴" if factor.score < 0 else "⚪"
            logger.info(f"  {emoji} {factor.name}: {factor.score:+d} - {factor.interpretation}")

        logger.info(f"📝 {analysis.recommendation}")
        return analysis

    def _run_market_analysis(self) -> MarketContext:
        """Exécute analyse marché"""
        context = market_analyzer.analyze()

        logger.info(f"📊 Market Score: {context.market_score}")
        if context.sp500_current:
            logger.info(f"  📈 S&P500: {context.sp500_current:.0f} (Drawdown: {context.sp500_drawdown:.1f}%)")
        if context.vix_current:
            logger.info(f"  📊 VIX: {context.vix_current:.1f} ({context.volatility_level})")
        if context.is_bear_market:
            logger.warning("  🐻 BEAR MARKET DETECTED!")

        logger.info(f"📝 {context.recommendation}")
        return context

    def _run_fundamental_analysis(self) -> List[FundamentalScore]:
        """Exécute analyse fondamentale"""
        results = fundamentals_analyzer.analyze_watchlist()

        logger.info("🏆 Fundamental Rankings:")
        for i, score in enumerate(results[:5], 1):
            emoji = "🟢" if score.total_score >= 4 else "🟡" if score.total_score >= 2 else "🔴"
            logger.info(f"  {i}. {emoji} {score.symbol}: {score.total_score}/5 ({score.quality_rating})")

        return results

    def _run_sentiment_analysis(self, symbols: List[str]) -> List[SentimentScore]:
        """Exécute analyse sentiment"""
        results = sentiment_analyzer.analyze_multiple(symbols)

        logger.info("💬 Sentiment Analysis:")
        for score in results:
            emoji = "🟢" if score.total_score >= 2 else "🟡" if score.total_score >= 1 else "🔴"
            logger.info(f"  {emoji} {score.symbol}: {score.total_score}/3 ({score.sentiment_label})")

        return results

    def _generate_signals(
        self,
        macro: MacroAnalysis,
        market: MarketContext,
        fundamentals: List[FundamentalScore],
        sentiments: List[SentimentScore]
    ) -> List[SignalRecord]:
        """
        Génère les signaux d'achat

        Score Total = Macro + Market + Fundamental + Sentiment
        Range: -5 à +10

        Seuil d'alerte: 7.5
        """
        signals = []

        # Créer dict sentiment par symbol
        sentiment_by_symbol = {s.symbol: s for s in sentiments}

        # Malus global (macro + market)
        global_malus = macro.total_score + market.market_score
        logger.info(f"🌍 Global malus (macro + market): {global_malus:+d}")

        for fund in fundamentals:
            # Récupérer sentiment si disponible
            sent = sentiment_by_symbol.get(fund.symbol)
            sent_score = sent.total_score if sent else 1.5  # Neutre par défaut

            # Calculer score total
            total_score = (
                macro.total_score +      # -3 à +1
                market.market_score +    # -2 à +1
                fund.total_score +       # 0 à 5
                sent_score               # 0 à 3
            )

            # Normaliser sur 10
            # Range réel: -5 à +10, on shift pour 0-10
            normalized_score = total_score + 5  # Maintenant 0 à 15
            normalized_score = min(10, max(0, normalized_score * 10 / 15))

            logger.info(
                f"  {fund.symbol}: Score {normalized_score:.1f}/10 "
                f"(M:{macro.total_score:+d} Mk:{market.market_score:+d} "
                f"F:{fund.total_score:.1f} S:{sent_score:.1f})"
            )

            # Vérifier seuil
            if normalized_score >= config.scoring.alert_threshold:
                logger.info(f"  ✅ {fund.symbol} ABOVE THRESHOLD ({normalized_score:.1f} >= {config.scoring.alert_threshold})")

                # Créer signal
                from data.twelve_data import twelve_data_client
                quote = twelve_data_client.get_quote(fund.symbol)

                signal = SignalRecord(
                    symbol=fund.symbol,
                    price_at_signal=quote.price if quote.is_valid else None,
                    scores={
                        "macro": macro.total_score,
                        "market": market.market_score,
                        "fundamental": fund.total_score,
                        "sentiment": sent_score
                    },
                    total_score=normalized_score,
                    macro_summary=macro.recommendation[:50],
                    market_summary=market.recommendation[:50],
                    fundamental_summary=fund.quality_rating,
                    sentiment_summary=sent.sentiment_label if sent else "N/A"
                )

                # Sauvegarder signal
                signals_store.save_signal(signal)

                # Envoyer alerte Telegram
                if not self.test_mode:
                    telegram_bot.send_signal_alert(signal)

                signals.append(signal)

        return signals

    def _send_macro_warning(self, macro: MacroAnalysis):
        """Envoie alerte macro défavorable"""
        if self.test_mode:
            logger.info("[TEST] Would send macro warning")
            return

        text = f"""⚠️ <b>ALERTE MACRO DÉFAVORABLE</b>

Score: {macro.total_score}/1

{macro.recommendation}

<i>Analyse complète suspendue - Conditions trop risquées</i>"""

        telegram_bot.send_message(text)

    def _send_daily_summary(self, results: dict):
        """Envoie le résumé quotidien"""
        if self.test_mode:
            logger.info("[TEST] Would send daily summary")
            return

        top_picks = [f.symbol for f in results["fundamentals"][:3]]

        telegram_bot.send_daily_summary(
            macro_score=results["macro"].total_score if results["macro"] else 0,
            market_score=results["market"].market_score if results["market"] else 0,
            signals_count=results["signals_generated"],
            top_picks=top_picks
        )

    def _phase_cooldown(self):
        """Pause entre phases pour gestion thermique"""
        MemoryMonitor.check_and_cleanup()
        time.sleep(self.inter_phase_delay)


def run_review_mode():
    """
    Mode review interactif

    Permet de noter les signaux via Telegram
    """
    logger.info("Starting Review Mode")

    # Récupérer signaux non notés
    unrated = signals_store.get_unrated_signals(limit=10)

    if not unrated:
        print("✅ Aucun signal à noter!")
        telegram_bot.send_message("✅ Aucun signal à noter!")
        return

    # Envoyer liste via Telegram
    telegram_bot.send_review_list(unrated)
    print(f"📋 {len(unrated)} signaux envoyés à Telegram pour review")
    print("Utilisez les boutons Telegram pour noter")


def run_performance_update():
    """
    Met à jour la performance des vieux signaux
    """
    logger.info("Updating signal performances...")

    signals = signals_store.get_signals_for_performance_update(days=7)

    if not signals:
        logger.info("No signals to update")
        return

    for signal in signals:
        logger.info(f"Updating {signal.symbol}...")
        signals_store.update_performance(signal.id)
        time.sleep(config.thermal.inter_request_delay)


def main():
    """Point d'entrée CLI"""
    parser = argparse.ArgumentParser(
        description='PiTrader - Trading Bot for Raspberry Pi',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py              # Run analysis once
  python main.py --loop       # Run every hour
  python main.py --test       # Test mode (no Telegram)
  python main.py --review     # Review and rate signals
  python main.py --stats      # Show statistics
  python main.py --export     # Export signals to CSV
        """
    )

    parser.add_argument(
        '--test', '-t',
        action='store_true',
        help='Test mode (no Telegram notifications)'
    )
    parser.add_argument(
        '--loop', '-l',
        action='store_true',
        help='Run in loop mode'
    )
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=3600,
        help='Interval between runs in seconds (default: 3600)'
    )
    parser.add_argument(
        '--review', '-r',
        action='store_true',
        help='Review and rate signals'
    )
    parser.add_argument(
        '--stats', '-s',
        action='store_true',
        help='Show statistics'
    )
    parser.add_argument(
        '--export', '-e',
        action='store_true',
        help='Export signals to CSV'
    )
    parser.add_argument(
        '--update-performance', '-u',
        action='store_true',
        help='Update performance for old signals'
    )

    args = parser.parse_args()

    # Mode review
    if args.review:
        run_review_mode()
        return

    # Mode stats
    if args.stats:
        stats = signals_store.get_statistics()
        print("\n📊 PiTrader Statistics")
        print("=" * 40)
        for key, value in stats.items():
            print(f"  {key}: {value}")
        telegram_bot.send_stats()
        return

    # Mode export
    if args.export:
        path = signals_store.export_csv()
        print(f"✅ Exported to {path}")
        return

    # Mode update performance
    if args.update_performance:
        run_performance_update()
        return

    # Mode analyse
    trader = PiTrader(test_mode=args.test)

    if args.loop:
        logger.info(f"Starting loop mode - interval: {args.interval}s")
        while True:
            try:
                trader.run_full_analysis()
                logger.info(f"Sleeping {args.interval}s until next analysis...")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in loop: {e}")
                time.sleep(60)  # Pause avant retry
    else:
        trader.run_full_analysis()


if __name__ == "__main__":
    main()
