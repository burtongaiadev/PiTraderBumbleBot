# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PiTrader is an algorithmic trading signal bot designed for Raspberry Pi 5 (4GB RAM). It performs top-down financial analysis to generate buy signals for stocks, integrating Telegram alerts, local Ollama LLM for sentiment analysis, Twelve Data API for market data, and NewsAPI for news.

## Commands

```bash
# Run single analysis
python3 main.py

# Test mode (skips Telegram notifications)
python3 main.py --test

# Loop mode (continuous execution)
python3 main.py --loop --interval 3600

# Health check (verify all services)
python3 main.py --health

# Systemd service management
sudo systemctl start pitrader
sudo systemctl status pitrader
journalctl -u pitrader -f
```

## Architecture

### Analysis Pipeline (Top-Down Flow)

The core analysis runs in `PiTrader.run_full_analysis()` in [main.py](main.py):

1. **Market Context** ([analysis/market_context.py](analysis/market_context.py)) - Watchlist momentum via Twelve Data → score -1 to +1 (normalized 0-4, weight: 40%)
2. **Fundamentals** ([analysis/fundamentals.py](analysis/fundamentals.py)) - 30-day stock momentum → score 0 to 3 (weight: 30%)
3. **Sentiment** ([analysis/sentiment.py](analysis/sentiment.py)) - News sentiment via Ollama batch → score 0 to 3 (weight: 30%)

**Scoring Logic:**
- Total score range: 0-10. Alert threshold: 7.5
- **Blocking condition**: If market is negative → no signals generated
- Each signal includes a confidence score (0-1)

### Data Layer

- [data/twelve_data.py](data/twelve_data.py) - Market data (quotes, historical). Rate limited to 8 req/min. Circuit breaker protected. Supports batch quotes via `get_multiple_quotes()`. Includes volume ratio detection for abnormal trading activity.
- [data/news_client.py](data/news_client.py) - NewsAPI client with 15min TTL cache. Filtered to financial sources only. Circuit breaker protected.
- [data/ollama_client.py](data/ollama_client.py) - Local LLM (qwen2.5:1.5b model, 120s timeout). Supports batch analysis via `analyze_sentiment_batch()`.

### Storage Layer

- [storage/signals_store.py](storage/signals_store.py) - Signal history with UUID tracking, confidence scores, and 7-day performance updates. Exports: `export_csv()` for basic export, `export_ml_ready()` for normalized ML features.
- [storage/cache_store.py](storage/cache_store.py) - JSON-based persistent cache in `runtime_data/cache/`

### Cache System ([utils/cache.py](utils/cache.py))

- `TTLCache` - LRU cache with time-to-live expiration
- `PersistentCache` - Survives restarts, saves to JSON files
- `PersistentCacheManager` - Manages multiple persistent caches
- `ttl_lru_cache` - Decorator combining LRU + TTL for functions

### Resilience Patterns ([utils/decorators.py](utils/decorators.py))

- `retry_with_backoff` - Exponential backoff for transient failures
- `CircuitBreaker` - Prevents cascading failures (120s recovery timeout). Applied to TwelveData and NewsAPI clients.
- `rate_limiter` - Enforces API rate limits
- `thermal_aware` - Pauses if CPU temp exceeds thresholds (Pi-specific)

### Memory Management ([utils/memory.py](utils/memory.py), [utils/cache.py](utils/cache.py))

TTL + LRU caching optimized for 4GB RAM. MemoryMonitor triggers GC at 3GB warning / 3.5GB critical thresholds.

## Configuration

All configuration in [config.py](config.py) using frozen dataclasses. Environment variables loaded from `.env`:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram alerts
- `TWELVEDATA_API_KEY` - Market data
- `NEWSAPI_KEY` - News data
- `OLLAMA_URL` - Local LLM endpoint (default: http://localhost:11434)

Key config sections:
- `config.watchlist` - 110 stocks: Top 50 S&P 500 + Top 30 CAC 40 + Top 30 DAX
- `config.ticker_names` - Mapping ticker → company name for better NewsAPI search
- `config.news_api.domains` - Whitelisted financial news sources

## Startup Notification

On first run after a system reboot, PiTrader sends a Telegram notification with:
- System info (hostname, uptime, CPU temperature)
- Configuration (watchlist count, Ollama status)

Detection uses Linux `/proc/sys/kernel/random/boot_id` to identify new boots.

## Code Conventions

- French comments and docstrings throughout
- Type hints on all functions
- Dataclasses for configs and result objects
- Module-level logger: `logger = logging.getLogger(__name__)`
- Error handling returns `valid=False` objects rather than raising
- Emoji in log output for visual scanning

## Raspberry Pi Constraints

- Synchronous execution (no async) for simpler debugging on limited hardware
- Ollama timeout set to 120s for slow ARM inference
- Ollama warmup at startup to avoid cold start latency
- Batch sentiment analysis to reduce Ollama calls
- Batch TwelveData quotes (1 API call for all watchlist symbols)
- Persistent cache survives reboots (avoids re-fetching after power loss)
- Systemd service limited to 80% CPU, 1GB memory
- Thermal monitoring pauses operations when CPU overheats

## ML Export

The `signals_store.export_ml_ready()` method exports signals with normalized features for machine learning:

**Features (normalized 0-1):**
- `market_norm` - Market score normalized from [-1,+1]
- `fundamental_norm`, `sentiment_norm` - Scores normalized from [0,3]
- `total_score_norm` - Total score normalized from [0,10]
- `confidence` - Signal confidence (already 0-1)
- `day_of_week`, `hour` - Temporal features

**Targets:**
- `actual_return` - Continuous % return after 7 days
- `is_success` - Binary (1 if return > 0)
- `is_strong_success` - Binary (1 if return > 2%)
