"""
config.py - Central configuration for the Adam Mancini bot
==========================================================
This file is the only place where paths and parameters are defined.
The rest of the modules import from here, never hardcoding values.

To configure the bot: copy .env.example to .env and fill in the values.

MAIN CHANGE (June 2026):
  - DATA_SOURCE = 'ibkr' → we use ES futures directly from IBKR paper trading
  - SPY_TO_ES_MULTIPLIER = 1.0 → IBKR returns ES prices directly, no conversion
  - Alpaca/SPY kept as a fallback in case of problems with IBKR Gateway
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load variables from .env (in development)
# In production (server) they come directly from the environment
load_dotenv()


# ─────────────────────────────────────────────
# Project paths
# ─────────────────────────────────────────────
# Path(__file__).parent points to the directory where this file lives (the root)
BASE_DIR        = Path(__file__).parent
DATA_DIR        = BASE_DIR / 'data'
RAW_DIR         = DATA_DIR / 'raw'
TWEETS_DIR      = RAW_DIR  / 'tweets'      # JSON with Adam's tweets
NEWSLETTER_DIR  = RAW_DIR  / 'newsletter'  # JSON with newsletter articles
PROCESSED_DIR   = DATA_DIR / 'processed'   # Already processed and cleaned data
CHROMA_DIR      = DATA_DIR / 'chromadb'    # Vector store (no longer actively used)

# Create directories automatically if they don't exist
for _dir in [TWEETS_DIR, NEWSLETTER_DIR, PROCESSED_DIR, CHROMA_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Market data source
# ─────────────────────────────────────────────
# 'ibkr'   → ES Futures directly from IBKR paper trading (RECOMMENDED)
#              Advantages: exact price, pre-market 7:30-9:30 AM, no proxy
#              Requires: IB Gateway running on the mac with the API enabled
#
# 'alpaca' → SPY from Alpaca Markets (fallback)
#              Drawbacks: SPY*10 proxy, no pre-market, conversion noise
DATA_SOURCE = 'ibkr'


# ─────────────────────────────────────────────
# Twitter/X
# ─────────────────────────────────────────────
TWITTER_USERNAME = os.getenv('TWITTER_USERNAME')    # your @username without @
TWITTER_EMAIL    = os.getenv('TWITTER_EMAIL')       # your account email
TWITTER_PASSWORD = os.getenv('TWITTER_PASSWORD')    # password

TWITTER_TARGET        = 'AdamMancini4'  # Adam's profile that we monitor
TWITTER_POLL_INTERVAL = 180             # seconds between real-time checks (3 min)


# ─────────────────────────────────────────────
# Substack - Adam's Newsletter
# ─────────────────────────────────────────────
SUBSTACK_URL     = 'https://tradecompanion.substack.com'
SUBSTACK_COOKIES = os.getenv('SUBSTACK_COOKIES', '')  # for paid content


# ─────────────────────────────────────────────
# Anthropic API (Claude Haiku for parsing and signals)
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# Haiku is enough to extract levels/bias and evaluate setups
LLM_MODEL = 'claude-haiku-4-5-20251001'


# ─────────────────────────────────────────────
# IBKR - ES Futures (primary source)
# ─────────────────────────────────────────────
# IB Gateway paper trading — runs on your Mac and exposes the API on localhost
# IB Gateway paper port: 4002
# TWS paper port:        7497
# (IB Gateway is lighter — use that one, not TWS)
IBKR_HOST      = os.getenv('IBKR_HOST', '127.0.0.1')
IBKR_PORT      = int(os.getenv('IBKR_PORT', '4002'))
IBKR_CLIENT_ID = int(os.getenv('IBKR_CLIENT_ID', '1'))

# Expiration month of the active ES contract (front month)
# IBKR format: YYYYMM
#   202609 = September 2026 (ESU2026) — current front month after the June 14 roll
#   202612 = December 2026 (ESZ2026)  — next one when sep expires in September
# Adam confirmed in the newsletter that he rolled to ESU2026 on Sunday June 14, 2026
IBKR_ES_EXPIRY = os.getenv('IBKR_ES_EXPIRY', '202609')


# ─────────────────────────────────────────────
# Alpaca - SPY market data (fallback / legacy)
# ─────────────────────────────────────────────
# Kept in case IBKR Gateway has problems and you need a quick fallback.
# To use: change DATA_SOURCE = 'alpaca' above
ALPACA_API_KEY    = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_PAPER      = True

MARKET_TICKER = 'SPY'  # only used if DATA_SOURCE = 'alpaca'

# SPY→ES MULTIPLIER:
#   'ibkr'   → 1.0 (ES futures already come in direct ES points, no conversion)
#   'alpaca' → 10.0 (SPY * 10 ≈ ES level, e.g. SPY 750 → ES 7500)
SPY_TO_ES_MULTIPLIER = 1.0 if DATA_SOURCE == 'ibkr' else 10.0

# Tolerance for considering the price to be "at" one of Adam's levels
# Adam typically uses levels with a ±3 point margin
LEVEL_TOLERANCE_POINTS = 3.0  # in ES points


# ─────────────────────────────────────────────
# Telegram Bot
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')


# ─────────────────────────────────────────────
# ChromaDB - Vector store (legacy)
# ─────────────────────────────────────────────
# No longer actively used for signal queries
# (removed the noise from historical 2021-2024 levels that aren't relevant
#  to the current 7000-8000 range)
CHROMA_COLLECTION = 'adam_mancini_knowledge'


# ─────────────────────────────────────────────
# Market hours (NYSE / Adam's window)
# ─────────────────────────────────────────────
MARKET_TIMEZONE   = 'America/New_York'

# We start at 7:30 AM to cover Adam's prime window
# (ES futures has data from 6 PM the previous day, but Adam
#  starts trading at 7:30 AM)
MARKET_OPEN_HOUR  = 7
MARKET_OPEN_MIN   = 30

# FIX: was 17, corrected to 16 (4 PM EST = NYSE close)
# With 17 it generated 'stale bar' warnings in the after-hours
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0
