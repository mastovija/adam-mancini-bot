# Adam Mancini Trading Bot 🤖📈

Bot that replicates the trading methodology of [Adam Mancini](https://x.com/AdamMancini4) (@AdamMancini4) on ES Futures (S&P 500).

Reads his daily Substack newsletter and live tweets, monitors ES Futures price via IBKR, and sends Telegram alerts when it detects his characteristic setups: **Failed Breakdown** entries with stop loss and targets, managed level-to-level.

> **Disclaimer:** This bot operates in observation / paper-trading mode only. It does not execute real trades. Always apply your own judgment before trading.

---

## How it works

Adam's methodology: each morning he publishes key support and resistance levels in his newsletter. During the session he trades "level to level" — entering when price reaches a key level after an **elevator down flush + recovery** (Failed Breakdown), with a stop just below and a target at the next level up. The 15-minute candle is his primary confirmation timeframe.

This bot:

1. Reads Adam's newsletter each morning → extracts: bias, critical level, supports, resistances, full trade plan
2. Monitors ES Futures price every 60 seconds via IBKR (15-min delayed on paper account)
3. Detects when price reaches a key level (±3 ES pts tolerance)
4. Fetches recent 1-min and 15-min candles from IBKR to confirm Failed Breakdown pattern
5. Checks Adam's live tweets for real-time context (polled every 3 min via Playwright)
6. Asks Claude Haiku: *"Would Adam enter here?"* — with the full newsletter plan, today's tweets, candle data, and trading window
7. If yes: sends a Telegram alert with entry, stop, T1, T2 and R/R ratio
8. Manages active trade: alerts for T1 hit (→ move stop to breakeven), T2 hit, stop hit

---

## Project status — June 2026

Running in **paper-trading / observation mode** on a local Mac. Manual restart each trading day at ~13:15 Spain time (07:15 EST).

- [x] **Phase 1** — Data collection: 1,403 newsletter articles + 639 tweets scraped
- [x] **Phase 2** — Vector knowledge base: ChromaDB with ONNX embeddings (indexed, not yet wired to signal engine — see limitations)
- [x] **Phase 3** — Daily parser: newsletter → `today.json` at 7:30 AM EST + live tweet monitor (Playwright)
- [x] **Phase 4** — Market data: IBKR ES Futures via `ib_insync` (primary) · Alpaca SPY (fallback)
- [x] **Phase 5** — Signal engine: Failed Breakdown detection + 15-min candle confirmation + LLM arbitration
- [x] **Phase 6** — Telegram alerts: morning briefing, signal alerts, live tweet alerts, T1/T2/stop management
- [x] **Phase 7** — Bug fixes: 13 bugs resolved (see below)
- [ ] **Phase 8** — Cloud deployment (Fly.io — Dockerfile pending)
- [ ] **Phase 9** — Live IBKR account + CME data subscription → real-time data

---

## Bugs fixed (June 2026)

| # | Bug | Impact |
|---|-----|--------|
| A-1 | `parse_mode=HTML` silently dropped alerts when tweet/LLM text contained `<`, `>`, `&` | Alerts lost with no error |
| A-2 | Trade state and cooldowns lost on restart | Re-triggered signals after crash |
| A-3 | T1/T2/stop detected from single delayed tick, not candle high/low | Missed T1, wrong stop-hit alerts |
| A-4 | `ib_insync` missing from `requirements.txt` | Clean install would fail at runtime |
| B-5 | Duplicate levels between supports and resistances | Double LLM calls for same level |
| B-6 | 15-min candles fetched inside level loop (once per level) | IBKR pacing violations |
| B-7 | Hardcoded date ("25 Jun") and prices in chop window LLM prompt | LLM anchored to stale levels |
| B-8 | Tweet date comparison used Spain local time, not New York time | After-close tweets filtered out |
| B-9 | Scheduler fired at 7:30 AM even when bot started at 7:23 AM | Double morning briefing |
| B-extra | LLM response JSON with trailing text caused `Extra data` parse error | All LLM decisions silently failed |
| C-10 | Cooldown constant `60` duplicated in three places | Silent bug if one was changed |
| C-11 | Two divergent Telegram formatters — `telegram_alerts.py` methods unused | Code duplication, plain-text alerts |
| C-13 | Synchronous Anthropic API calls inside async functions | Blocked IBKR event loop 1–3s per call |
| C-14 | Unused imports (`re`, `timezone`, `TELEGRAM_BOT_TOKEN`, etc.) | Dead code |

---

## Current limitations

| Limitation | Impact | Fix |
|---|---|---|
| **15-min IBKR delayed data** (paper account) | Fast setups (<15 min elevator) are missed. Slow setups detected reliably. | Live IBKR account + CME data subscription (~$10–15/mo). Change `reqMarketDataType(3)` → `reqMarketDataType(1)` |
| **Bot runs on local Mac** | Stops if computer sleeps; manual restart each day | Fly.io deployment (Dockerfile not yet created) |
| **ChromaDB not wired to signal engine** | 1,403 indexed articles not used in LLM decisions | Decision pending: wire it or remove it |
| **LLM calibration** | Claude Haiku is conservative — may miss valid setups | Adjust prompt criteria based on observed false negatives |

---

## Monthly cost

| Component | Tool | Cost/month |
|---|---|---|
| Newsletter (paid content) | Substack subscription | $10–15 |
| LLM (parsing + signal arbitration) | Claude Haiku API | ~$3–5 |
| Market data — paper account | IBKR (delayed, free) | $0 |
| Market data — live account | IBKR + CME ES data subscription | ~$10–15 |
| Vector database | ChromaDB local | $0 |
| Telegram alerts | Telegram Bot API | $0 |
| Twitter scraping | Playwright + your account | $0 |
| Server (production) | Fly.io free tier (256MB RAM) | $0 |
| **Total (paper mode)** | | **~$13–20/month** |
| **Total (live data)** | | **~$23–35/month** |

---

## Requirements

- **Python 3.13**
- **IB Gateway** (Interactive Brokers) — paper trading account, port 4002, API enabled
- Accounts needed: [Anthropic](https://console.anthropic.com), Telegram, Twitter/X, Interactive Brokers
- Playwright + Chromium (for Twitter scraping)
- Alpaca account (optional fallback — set `DATA_SOURCE=alpaca` in `.env`)

---

## Setup from scratch

### 1. Clone and install

```bash
git clone https://github.com/yourusername/adam-mancini-bot.git
cd adam-mancini-bot

python3.13 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM
ANTHROPIC_API_KEY=sk-ant-...

# Telegram
TELEGRAM_BOT_TOKEN=1234567890:ABC...   # @BotFather → /newbot
TELEGRAM_CHAT_ID=123456789             # @userinfobot

# IBKR (primary market data)
IBKR_HOST=127.0.0.1
IBKR_PORT=4002                         # IB Gateway paper trading port
IBKR_CLIENT_ID=1
IBKR_ES_EXPIRY=202609                  # Current ES contract expiry (YYYYMM)

# Alpaca (fallback only — set DATA_SOURCE=alpaca to use)
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=XXXXXXXXXXXX

# Data source
DATA_SOURCE=ibkr                       # 'ibkr' or 'alpaca'

# Substack (paid subscription recommended)
SUBSTACK_COOKIES=substack.sid=your_value_here
```

### 3. Set up IB Gateway

1. Download [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway.php)
2. Log in with your IBKR paper trading account
3. Go to **Configure → Settings → API → Enable ActiveX and Socket Clients**
4. Set port to **4002**, check **Allow connections from localhost only**
5. Leave IB Gateway running before starting the bot

### 4. Download historical data

```bash
# Download newsletter articles (~1400, takes ~30 min)
python scrapers/substack_scraper.py

# Download Twitter history via Playwright
# First: log in to x.com in Chrome, export cookies to data/raw/tweets/cookies.json
python scrapers/twitter_scraper_playwright.py
```

For Twitter cookies: open Chrome → log in to x.com → F12 → Application → Cookies → export `x.com` cookies as JSON to `data/raw/tweets/cookies.json`.

### 5. Build the knowledge base (optional — ChromaDB)

```bash
# Process newsletters with Claude Haiku and index in ChromaDB
# Takes ~35-40 min, costs ~$0.10-0.15
python knowledge_base/build_kb.py

# Index tweets
python knowledge_base/add_tweets_to_kb.py
```

> Note: ChromaDB is indexed but not currently wired to the signal engine's LLM call. The full newsletter plan (`content_plan`) is passed directly to the LLM instead.

### 6. Start the bot

```bash
# Step 1: update Twitter history (run before market open)
python scrapers/twitter_scraper_playwright.py

# Step 2: start the bot (Mac — prevents sleep)
caffeinate -i python main.py
```

The bot parses the newsletter immediately on startup, sends a morning briefing to Telegram, then monitors price every 60s and tweets every 3 min until 4:00 PM EST.

Press `Ctrl+C` to stop — sends a shutdown notification to Telegram.

### 7. Verify components

```bash
# Test Telegram — should receive example messages
python bot/telegram_alerts.py

# Test IBKR feed (IB Gateway must be running)
python market_data/ibkr_feed.py

# Test Alpaca feed (fallback)
python market_data/alpaca_feed.py
```

---

## Daily routine

```bash
# Before market open (~13:10 Spain / 07:10 EST)
source venv/bin/activate
python scrapers/twitter_scraper_playwright.py   # get latest tweets
caffeinate -i python main.py                    # start bot
```

The scheduler inside `main.py` re-parses the newsletter automatically at 7:30 AM EST on weekdays. If the bot started within 20 minutes before 7:30 AM, the scheduler skips to avoid a duplicate briefing.

---

## Upgrading to live data

The single biggest improvement available once you have confidence in the bot's signals.

1. Open a live IBKR account (or upgrade paper → live)
2. Subscribe to CME ES market data in IBKR account management (~$10–15/month)
3. Change in `ibkr_feed.py`: `reqMarketDataType(3)` → `reqMarketDataType(1)`
4. IB Gateway: use live account credentials, port 4001

This eliminates the 15-min delay and allows the bot to catch fast setups (elevator moves in <15 minutes).

---

## Cloud deployment (Fly.io — pending)

Fly.io persistent free tier (256MB RAM) is the preferred deployment target. Dockerfile not yet created.

```bash
# Future deployment (once Dockerfile is ready)
flyctl launch
flyctl deploy
flyctl logs
```

> Oracle Cloud Madrid has had persistent capacity issues. Render is not suitable (sleep behavior conflicts with the bot's active hours). Fly.io is the current recommendation.

---

## Project structure

```
adam-mancini-bot/
├── main.py                    # Entry point — starts all components
├── config.py                  # Central config: paths, API keys, parameters
├── requirements.txt
├── .env.example               # Credentials template
│
├── scrapers/                  # Data collection
│   ├── substack_scraper.py        # Downloads newsletter articles
│   └── twitter_scraper_playwright.py  # Downloads tweet history via Playwright
│
├── knowledge_base/            # ChromaDB vector store (indexed, not yet wired to LLM)
│   ├── build_kb.py
│   ├── add_tweets_to_kb.py
│   ├── processor.py
│   └── vectordb.py
│
├── parsers/                   # Daily parsing
│   ├── newsletter_parser.py       # Downloads today's article → today.json
│   ├── tweet_monitor.py           # Polls Adam's tweets every 3 min (async)
│   └── playwright_utils.py        # Shared Playwright utilities
│
├── market_data/               # Price feed
│   ├── ibkr_feed.py               # ES Futures via ib_insync (primary)
│   └── alpaca_feed.py             # SPY via Alpaca (fallback)
│
├── signals/                   # Signal engine
│   └── signal_engine.py           # FB detection + candle confirmation + async LLM
│
├── bot/                       # Alerts
│   └── telegram_alerts.py         # All formatted alerts: briefing, signal, T1/T2/stop, tweet
│
├── backtest/                  # Validation
│   ├── backtester.py
│   └── download_data.py
│
└── data/                      # Local data — not committed to git
    ├── raw/
    │   ├── tweets/                # adam_mancini_tweets.json (~639 tweets)
    │   └── newsletter/            # one JSON per article (~1403 articles)
    ├── daily/
    │   └── today.json             # Today's parsed newsletter
    ├── signal_engine_state.json   # Active trade + cooldowns (persisted across restarts)
    └── chromadb/                  # ChromaDB persistent storage
```

---

## Architecture decisions

**Why IBKR instead of Alpaca?** Adam trades ES Futures, not SPY. IBKR provides ES Futures data directly at the correct price (no SPY→ES conversion needed). With a paper account, data is delayed 15 minutes — which is the current main limitation. Alpaca remains as a fallback.

**Why asyncio.to_thread for Anthropic calls?** The bot uses ib_insync which runs its own asyncio event loop. Synchronous `client.messages.create()` calls blocked that loop for 1–3 seconds, preventing IBKR price ticks from being processed during LLM evaluation. `asyncio.to_thread()` runs the blocking call in a thread pool, keeping the event loop free.

**Why not wire ChromaDB?** The full newsletter text (`content_plan`, up to 32k characters) already contains everything Adam wrote about today's levels. Historical newsletters from 2-3 years ago reflect a different market structure. The decision is to keep ChromaDB indexed but not query it in the signal engine — if pattern recognition over historical setups is added later, the data is ready.
