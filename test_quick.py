#!/usr/bin/env python3
"""
test_quick.py - Test rapide sur 3 actions
"""
import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("QuickTest")

# Imports
from data.twelve_data import twelve_data_client
from data.ollama_client import ollama_client
from analysis.technical import technical_analyzer
from analysis.sentiment import sentiment_analyzer
from analysis.fundamentals import fundamentals_analyzer

# 3 actions de test
TEST_STOCKS = ["AAPL", "NVDA", "MSFT"]


def main():
    logger.info("=" * 50)
    logger.info("🧪 Test rapide sur 3 actions")
    logger.info("=" * 50)

    # 1. Test Twelve Data (quotes)
    logger.info("\n📊 Phase 1: Quotes via Twelve Data")
    quotes = twelve_data_client.get_multiple_quotes(TEST_STOCKS)
    for symbol, quote in quotes.items():
        if quote.is_valid:
            logger.info(f"   ✅ {symbol}: ${quote.price:.2f} ({quote.change_percent:+.2f}%)")
        else:
            logger.info(f"   ❌ {symbol}: {quote.error}")

    # 2. Test Technique (MM50, RSI)
    logger.info("\n📉 Phase 2: Analyse Technique")
    for symbol in TEST_STOCKS:
        tech = technical_analyzer.analyze(symbol)
        if tech.is_valid:
            ma_status = "📈" if tech.above_ma50 else "📉"
            logger.info(
                f"   {ma_status} {symbol}: MA50 {tech.ma50_distance:+.1f}%, "
                f"RSI {tech.rsi:.0f}, Timing: {tech.timing_signal}"
            )
        else:
            logger.info(f"   ❌ {symbol}: {tech.error}")

    # 3. Test Ollama (sentiment)
    logger.info("\n💬 Phase 3: Analyse Sentiment (Ollama)")
    if not ollama_client.is_available():
        logger.warning("   ⚠️ Ollama non disponible - skip")
    else:
        sentiments = sentiment_analyzer.analyze_multiple(TEST_STOCKS)
        for sent in sentiments:
            if sent.is_valid:
                emoji = "🟢" if sent.total_score > 1.5 else "🔴" if sent.total_score < 1.5 else "⚪"
                logger.info(
                    f"   {emoji} {sent.symbol}: {sent.total_score:.1f}/3 "
                    f"({sent.articles_analyzed} articles, conf: {sent.avg_confidence:.2f})"
                )
            else:
                logger.info(f"   ❌ {sent.symbol}: {sent.error}")

    # 4. Test Fundamentals (momentum)
    logger.info("\n📈 Phase 4: Momentum 30j")
    for symbol in TEST_STOCKS:
        fund = fundamentals_analyzer.analyze(symbol)
        if fund.is_valid:
            emoji = "🟢" if fund.momentum > 0 else "🔴"
            logger.info(f"   {emoji} {symbol}: {fund.momentum:+.0%} ({fund.quality_rating})")
        else:
            logger.info(f"   ❌ {symbol}: {fund.error}")

    logger.info("\n" + "=" * 50)
    logger.info("✅ Test terminé!")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
