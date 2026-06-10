# Adam Mancini Trading Bot 🤖📈

Bot that replicates the trading methodology of [Adam Mancini](https://x.com/AdamMancini4) (@AdamMancini4) on the S&P 500 / ES futures.

Studies his historical tweets and daily newsletter ([Trade Companion](https://tradecompanion.substack.com)), monitors SPY in real time, and sends Telegram alerts when it detects Adam's characteristic setups: level-to-level entries with stop loss and targets.

> **Disclaimer:** This bot generates informational alerts only. It does not execute trades automatically. Always apply your own judgment before trading.

---

## How it works

Adam's methodology is simple: he identifies key support and resistance levels each morning in his newsletter, then trades "level to level" — entering when price reaches a key level, with a stop just beyond it and a target at the next level. The 15-minute candle is his primary confirmation timeframe.

This bot:
1. Reads Adam's newsletter each morning and extracts: bias, critical level, supports, resistances
2. Monitors SPY price every 60 seconds (converted to ES equivalent)
3. Detects when price reaches a key level from the newsletter
4. Confirms with the 15-min candle direction
5. Queries 1,800+ historical newsletters for similar past setups
6. Asks Claude Haiku: "Would Adam enter here?" — with all context
7. If yes: sends a Telegram alert with entry, stop loss, targets, and R/R ratio
8. Also monitors Adam's live tweets every 3 minutes for real-time signals

---

## Project status

All 7 phases are complete and functional.

- [x] **Phase 1** — Data collection: 1,420 newsletter articles + ~517 tweets scraped
- [x] **Phase 2** — Vector knowledge base: ChromaDB with 1,827 documents (ONNX embeddings, Python 3.13 compatible)
- [x] **Phase 3** — Daily parser: newsletter → `today.json` at 7:30 AM EST + live tweet monitor (Playwright)
- [x] **Phase 4** — Market data: Alpaca free tier (SPY real-time, IEX feed) with stale price guard
- [x] **Phase 5** — Signal engine: level detection + 15-min candle confirmation + LLM arbitration
- [x] **Phase 6** — Telegram alerts: morning briefing, signal alerts (entry/SL/TP/RR), live tweet alerts
- [x] **Phase 7** — Backtesting: 50% match rate on free newsletter previews (expected 65-70% with paid content)
- [ ] **Phase 8** — Production deployment 24/7 (Oracle Cloud Madrid — pending VM availability)

---

## Current limitations and known issues

| Issue | Impact | Fix |
|---|---|---|
| Newsletter is paid content | Previews only — no resistance levels extracted → zero SHORT signals | Subscribe to newsletter (~$10-15/mo), re-index |
| Twitter history capped at ~517 tweets | Browser scroll limit; no API access | No reliable workaround currently |
| Bot runs on local Mac | Stops if computer sleeps | Oracle Cloud deployment (pending) |
| Backtesting match rate 50% | With free previews only | Improves to ~65-70% with paid content |

---

## Monthly cost

| Component | Tool | Cost/month |
|---|---|---|
| Newsletter (paid content) | Substack subscription | $10–15 |
| LLM (parsing + signal arbitration) | Claude Haiku API | ~$3–5 |
| Market data (SPY real-time) | Alpaca free tier | $0 |
| Vector database | ChromaDB local | $0 |
| Telegram alerts | Telegram Bot API | $0 |
| Twitter scraping | Playwright + your account | $0 |
| Server (production) | Oracle Cloud free tier | $0 |
| **Total** | | **~$13–20/month** |

---

## Requirements

- Python 3.11 or 3.12 (not 3.13 — PyTorch/sentence-transformers incompatible)
- Accounts needed: [Anthropic](https://console.anthropic.com), [Alpaca](https://alpaca.markets), Telegram, Twitter/X
- Playwright + Chromium (for Twitter scraping)

---

## Full setup from scratch

### 1. Clone and install

```bash
git clone https://github.com/yourusername/adam-mancini-bot.git
cd adam-mancini-bot

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in all values:

```
ANTHROPIC_API_KEY=sk-ant-...          # console.anthropic.com
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXX     # alpaca.markets → Paper Trading → API Keys
ALPACA_SECRET_KEY=XXXXXXXXXXXX...
TELEGRAM_BOT_TOKEN=1234567890:ABC...  # @BotFather on Telegram → /newbot
TELEGRAM_CHAT_ID=123456789            # @userinfobot on Telegram
TWITTER_USERNAME=your_username        # your X account (any account works)
TWITTER_EMAIL=your@email.com
TWITTER_PASSWORD=yourpassword
```

### 3. Download historical data

```bash
# Download all newsletter articles (~1420, takes ~30 min)
python scrapers/substack_scraper.py

# Download Twitter history via Playwright (requires X cookies)
# First: log in to x.com in Chrome, then export cookies to data/raw/tweets/cookies.json
python scrapers/twitter_scraper_playwright.py
```

For Twitter cookies: open Chrome → log in to x.com → F12 → Application → Cookies → export `x.com` cookies as JSON to `data/raw/tweets/cookies.json`.

### 4. Build the knowledge base

```bash
# Process all newsletters with Claude Haiku and index in ChromaDB
# Takes ~35-40 min, costs ~$0.10-0.15 with free previews
python knowledge_base/build_kb.py

# Index tweets in ChromaDB (no LLM calls, fast)
python knowledge_base/add_tweets_to_kb.py
```

### 5. Parse today's newsletter

```bash
python parsers/newsletter_parser.py
```

This downloads today's article, extracts bias + levels with Haiku, and saves to `data/daily/today.json`. The bot reads this file all day.

### 6. Verify everything works

```bash
# Test Telegram — should receive 4 example messages on your phone
python bot/telegram_alerts.py

# Test Alpaca feed — shows current SPY price (run during market hours)
python market_data/alpaca_feed.py

# Run backtesting
python backtest/backtester.py
```

### 7. Start the bot

```bash
# On Mac — prevents sleep while running
caffeinate -i python main.py

# Background mode (keep running after closing terminal)
caffeinate -i nohup python main.py > bot.log 2>&1 &
tail -f bot.log    # follow logs
```

The bot will immediately send a morning briefing to Telegram with Adam's plan for the day, then monitor the market every 60 seconds and tweets every 3 minutes until 5:00 PM EST.

Press `Ctrl+C` to stop — it sends a shutdown notification to Telegram.

---

## Upgrading to paid newsletter content

The biggest single improvement available. With full article content, resistance levels are properly extracted and the bot generates both LONG and SHORT signals.

**Steps:**

1. Subscribe at [tradecompanion.substack.com](https://tradecompanion.substack.com)
2. Log in to Substack in Chrome → F12 → Application → Cookies → copy the value of `substack.sid`
3. Add to `.env`: `SUBSTACK_COOKIES=substack.sid=your_value_here`
4. Re-download all articles:
   ```bash
   python scrapers/substack_scraper.py
   ```
5. Force re-processing (deletes progress file so all articles are re-extracted):
   ```bash
   rm data/processed/processed_slugs.json
   python knowledge_base/build_kb.py
   ```
   Cost: ~$3–5 one-time. Time: ~40 minutes.
6. Re-run backtesting to see the improved match rate:
   ```bash
   python backtest/backtester.py
   ```

---

## Daily update routine

Run this before market open (or let the scheduler handle it automatically at 7:30 AM EST):

```bash
# Get new tweets (last few days)
python scrapers/twitter_scraper_playwright.py
python knowledge_base/add_tweets_to_kb.py

# Get new newsletter articles
python scrapers/substack_scraper.py
python knowledge_base/build_kb.py

# Refresh today's plan
python parsers/newsletter_parser.py --force
```

The scheduler inside `main.py` handles the newsletter re-parse automatically every weekday at 7:30 AM EST. Twitter and new newsletter articles are not auto-updated — run the scripts above when needed.

---

## Production deployment (Oracle Cloud)

Oracle Cloud Always Free tier provides a permanent VM at no cost.

```bash
# On Oracle Cloud VM (Ubuntu 22.04)
sudo apt update && sudo apt install python3.12 python3.12-venv git -y

git clone https://github.com/yourusername/adam-mancini-bot.git
cd adam-mancini-bot
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

cp .env.example .env
# fill in credentials

# Run as a systemd service (auto-restart, starts on boot)
sudo nano /etc/systemd/system/adam-bot.service
```

`adam-bot.service` content:
```ini
[Unit]
Description=Adam Mancini Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/adam-mancini-bot
ExecStart=/home/ubuntu/adam-mancini-bot/venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable adam-bot
sudo systemctl start adam-bot
sudo journalctl -u adam-bot -f   # follow logs
```

> Note: Oracle Cloud VM.Standard.A1.Flex (ARM, free) and VM.Standard.E2.1.Micro (x86, free) in Madrid region may have limited availability. Check back periodically or try other regions.

---

## Possible improvements

**High impact:**
- Subscribe to paid newsletter → proper resistance levels → SHORT signals → match rate 65-70%
- Increase level tolerance from ±3 to ±5 or ±8 ES points (reduce false negatives)
- Allow multiple signals per day (currently stops after first match)

**Medium impact:**
- Download more Twitter history (currently capped at ~500 tweets by browser scroll limit)
- Add weekly performance tracking: compare signals sent vs Adam's actual trades
- Adjust market monitoring window (currently 7:30–17:00 EST) to match Adam's active hours

**Lower priority:**
- Add a simple web UI to see today's plan and recent signals
- Store signal history in SQLite for performance analysis
- Add SMS fallback if Telegram is unavailable

---

## Project structure

```
adam-mancini-bot/
├── main.py                    # Entry point — starts all components
├── config.py                  # Central config: paths, API keys, parameters
├── requirements.txt
├── .env.example               # Credentials template
│
├── scrapers/                  # Phase 1: Data collection
│   ├── substack_scraper.py        # Downloads all newsletter articles
│   └── twitter_scraper_playwright.py  # Downloads tweet history via Playwright
│
├── knowledge_base/            # Phase 2: ChromaDB vector store
│   ├── build_kb.py                # Processes newsletters → indexes in ChromaDB
│   ├── add_tweets_to_kb.py        # Indexes tweets → ChromaDB
│   ├── processor.py               # Claude Haiku extraction (bias, levels, setup)
│   └── vectordb.py                # ChromaDB wrapper (add, query)
│
├── parsers/                   # Phase 3: Daily parsing
│   ├── newsletter_parser.py       # Downloads today's article → today.json
│   ├── tweet_monitor.py           # Polls Adam's tweets every 3 min
│   └── playwright_utils.py        # Shared Playwright utilities
│
├── market_data/               # Phase 4: Price feed
│   └── alpaca_feed.py             # SPY real-time via Alpaca (free IEX feed)
│
├── signals/                   # Phase 5: Signal detection
│   └── signal_engine.py           # Level detection + candle confirmation + LLM
│
├── bot/                       # Phase 6: Alerts
│   └── telegram_alerts.py         # Briefing, signal alerts, tweet alerts
│
├── backtest/                  # Phase 7: Validation
│   ├── backtester.py              # Simulates bot on historical data
│   └── download_data.py           # Downloads historical SPY bars from Alpaca
│
└── data/                      # Local data — not committed to git
    ├── raw/
    │   ├── tweets/                # adam_mancini_tweets.json
    │   └── newsletter/            # one JSON per article
    ├── processed/                 # Haiku-extracted trading info per article
    └── chromadb/                  # ChromaDB persistent storage
```
