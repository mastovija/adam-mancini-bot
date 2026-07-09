# Adam Mancini Trading Bot 🤖📈

**Version 0.4 — Phase 4: Calibration & Validation** · updated 2026-07-09

Bot that replicates the trading methodology of [Adam Mancini](https://x.com/AdamMancini4) (@AdamMancini4) on ES Futures (S&P 500).

Reads his daily Substack newsletter and live tweets, monitors ES Futures price via IBKR, and sends Telegram alerts when it detects his characteristic setups: **Failed Breakdown** and **Level Reclaim** entries with stop loss and targets, managed level-to-level. The trade decision is made by **Claude Sonnet 5** reasoning from Adam's own distilled methodology (a cached rubric + few-shot examples), gated by a confidence threshold, a deterministic mid-range-chop veto, and his real trade-discipline rules.

> **Disclaimer:** This bot operates in observation / paper-trading mode only. It does not execute real trades. Always apply your own judgment before trading.

---

## How it works

Adam's methodology: each morning he publishes key support and resistance levels in his newsletter. During the session he trades "level to level" — entering when price reaches a key level after an **elevator down flush + recovery** (Failed Breakdown), with a stop just below and a target at the next level up. The 15-minute candle is his primary confirmation timeframe.

This bot:

1. Reads Adam's newsletter each morning → extracts (via Claude Haiku): bias, critical level, supports, resistances, full trade plan
2. Monitors ES Futures price every 60 seconds via IBKR (15-min delayed on paper account)
3. Detects when price reaches a key level (±3 ES pts tolerance) **or** when a fresh Failed Breakdown has fired and price is in the acceptance zone just above the low
4. Fetches recent 1-min and 15-min candles from IBKR to confirm the Failed Breakdown pattern (`detect_failed_breakdown`, incl. intra-bar flushes)
5. Checks Adam's live tweets for real-time context (polled every 3 min via Playwright)
6. **Deterministic pre-filters (no LLM spend):**
   - **Mid-range chop veto** — once the day's deep-flush significant low is established, suppresses tested higher levels showing only a shallow flush (the "tested-to-death" chop Adam avoids)
   - **Day-state gate** — enforces his rules: 1–3 trades/day, first win → stop until the post-2pm session, first non-win → one retry only
7. Asks **Claude Sonnet 5**: *"Would Adam enter here?"* — reasoning from a **cached methodology rubric + few-shot examples** distilled from his real newsletters, plus the full plan, today's tweets, candle data, and trading window. Returns entry/stop/targets + a confidence score
8. If the model says enter **and** confidence ≥ `MIN_SIGNAL_CONFIDENCE`: sends a Telegram alert with entry, stop, T1, T2 and R/R ratio (sub-threshold setups are logged to `data/near_misses.jsonl` for calibration)
9. Manages active trade: alerts for T1 hit (→ move stop to breakeven), T2 hit, stop hit; records the outcome (win/loss/scratch) into the day-state

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

## Changelog — methodology brain & calibration (July 2026)

A second roadmap layered on the build phases above, focused on decision *quality*.

- [x] **Methodology brain** — the LLM now reasons from Adam's **actual published
  methodology** (`knowledge_base/methodology/rubric.md`, distilled from his
  fundamentals doc) sent as a **cached system prompt** (~0.1× cost after the first
  call), instead of a hardcoded paraphrase. Restores his real "significant low"
  definition, the acceptance protocol, and the Level Reclaim setup.
- [x] **Sonnet 5 decision model** — the trade decision (`generar_señal_llm`) runs on
  **Claude Sonnet 5** (`LLM_DECISION_MODEL`) with adaptive thinking; Haiku
  (`LLM_MODEL`) is kept for cheap, high-volume tasks (newsletter parsing, tweet
  classification).
- [x] **Confidence gate + near-miss log** — a signal is sent only if the model says
  enter **and** `confianza ≥ MIN_SIGNAL_CONFIDENCE` (0.6). Suppressed setups are
  logged to `data/near_misses.jsonl`.
- [x] **Reason-by-example** — a few-shot block of real setups
  (`knowledge_base/methodology/examples.md`) appended to the cached prompt, teaching
  the take-the-deep-low / skip-the-mid-range-chop discrimination.
- [x] **Phase 4 deterministic fixes** (from the July 8 paper-trading loss):
  - **Mid-range chop veto** (`update_significant_low` / `is_midrange_chop_veto`,
    `DEEP_FLUSH_PTS=20`, `MIDRANGE_BUFFER_PTS=10`) — free 13-day replay: 70 chop
    vetoes, **0 false vetoes** against Adam's real entries.
  - **Day-state machine** (`_entrada_permitida_por_estado` /
    `_registrar_resultado_trade`) — his real trade-discipline rules, enforced in
    code; covered by `tests/test_phase4_deterministic.py`.
  - **Error 162 fix** (`ibkr_feed.get_bars`) — the 1-min trade-management fetch
    asked for a 6-min window inside the 15-min delay blackout; floored to ~23 min.
    Verified live (0 → 14 bars).
- [x] **Ground truth + scoring** — hand-reviewed real entries in
  `backtest/june/ground_truth_entries.csv` (+ `GROUND_TRUTH.md`), scored by
  `backtest/june/score_vs_ground_truth.py`.
- [ ] **Confidence-threshold calibration** — needs a paid Sonnet backtest
  (~$3–19 by scope) to score signals vs. ground truth and finalize
  `MIN_SIGNAL_CONFIDENCE`. Deferred.

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
| **Confidence threshold not yet calibrated** | `MIN_SIGNAL_CONFIDENCE` (0.6) is a starting guess, not measured | Paid Sonnet backtest scored vs. `ground_truth_entries.csv`, or tune from live paper-trading logs |
| **Detector-level recall gaps** | 2 of Adam's real June/July entries had no structural trigger (7528, 7540) | Broaden `detect_failed_breakdown` for those cases (Phase-1 work) |
| **RTH-window blind spot** | Many of Adam's best entries are overnight / Sunday-eve / post-2pm, outside the 07:30–16:00 window | Documented in `backtest/june/GROUND_TRUTH.md`; live overnight monitoring is out of scope for now |

---

## Monthly cost

| Component | Tool | Cost/month |
|---|---|---|
| Newsletter (paid content) | Substack subscription | $10–15 |
| LLM — newsletter parsing + tweet classification | Claude Haiku API | ~$2–4 |
| LLM — trade decision (per-signal) | Claude Sonnet 5 API | ~$0.02–0.03 / decision (a live day ≈ $0.10–0.50) |
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
├── knowledge_base/            # Methodology brain + ChromaDB vector store
│   ├── methodology/
│   │   ├── rubric.md              # Adam's distilled methodology — cached system prompt
│   │   ├── examples.md           # Few-shot real setups appended to the cached prompt
│   │   └── fundamentals.txt      # His raw methodology doc (source for the rubric)
│   ├── build_kb.py
│   ├── add_tweets_to_kb.py
│   ├── processor.py
│   └── vectordb.py               # ChromaDB (indexed, not yet wired to the LLM)
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
│   └── signal_engine.py           # FB detection + engagement zone + mid-range veto
│                                  # + day-state rules + Sonnet 5 decision (async)
│
├── bot/                       # Alerts
│   └── telegram_alerts.py         # All formatted alerts: briefing, signal, T1/T2/stop, tweet
│
├── backtest/                  # Validation & calibration
│   ├── backtester.py
│   ├── download_data.py
│   └── june/                      # Backtest + ground-truth tooling
│       ├── backtest_harness.py        # Replays production decision code over stored bars
│       ├── download_es_bars.py        # 1-min ES bars per day via IBKR
│       ├── levels_loader.py           # Per-day newsletter levels
│       ├── ground_truth_entries.csv   # Hand-reviewed real entries (TRACKED)
│       ├── GROUND_TRUTH.md            # Methodology + findings (TRACKED)
│       └── score_vs_ground_truth.py   # Structural-recall / veto-safety / precision-recall
│
├── tests/
│   └── test_phase4_deterministic.py   # Veto + day-state regression tests (no pytest dep)
│
└── data/                      # Mostly local — generated outputs git-ignored
    ├── raw/
    │   ├── tweets/                # adam_mancini_tweets.json (~694 tweets)
    │   └── newsletter/            # one JSON per article (~1444 articles)
    ├── daily/
    │   └── today.json             # Today's parsed newsletter
    ├── near_misses.jsonl          # Sub-threshold + vetoed setups (calibration data)
    ├── signal_engine_state.json   # Active trade + cooldowns + day-state (persisted)
    └── chromadb/                  # ChromaDB persistent storage
```

---

## Architecture decisions

**Why IBKR instead of Alpaca?** Adam trades ES Futures, not SPY. IBKR provides ES Futures data directly at the correct price (no SPY→ES conversion needed). With a paper account, data is delayed 15 minutes — which is the current main limitation. Alpaca remains as a fallback.

**Why asyncio.to_thread for Anthropic calls?** The bot uses ib_insync which runs its own asyncio event loop. Synchronous `client.messages.create()` calls blocked that loop for 1–3 seconds, preventing IBKR price ticks from being processed during LLM evaluation. `asyncio.to_thread()` runs the blocking call in a thread pool, keeping the event loop free.

**Why not wire ChromaDB?** The full newsletter text (`content_plan`, up to 32k characters) already contains everything Adam wrote about today's levels. Historical newsletters from 2-3 years ago reflect a different market structure. The decision is to keep ChromaDB indexed but not query it in the signal engine — if pattern recognition over historical setups is added later, the data is ready.
