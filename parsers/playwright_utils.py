"""
parsers/playwright_utils.py — Shared utilities for scraping with Playwright
==================================================================================
Functions reused by the historical scraper and the real-time monitor:
- normalizar_cookies(): converts Cookie-Editor format to Playwright format
- extract_tweets(): parses Twitter's GraphQL response and extracts clean tweets
- get_browser_context(): creates a Playwright context with the cookies loaded
"""

import json
from pathlib import Path
from typing import Optional
import sys

sys.path.append(str(Path(__file__).parent.parent))
from config import RAW_DIR


# ─────────────────────────────────────────────
# Cookies
# ─────────────────────────────────────────────

COOKIES_FILE = RAW_DIR / 'cookies.json'

# Maps Cookie-Editor sameSite values to the ones Playwright accepts
SAME_SITE_MAP = {
    'no_restriction': 'None',
    'lax':            'Lax',
    'strict':         'Strict',
    'unspecified':    'None',
    'none':           'None',
}


def normalizar_cookies(cookies_list: list) -> list:
    """
    Converts cookies exported from Cookie-Editor to the format Playwright accepts.

    Cookie-Editor uses: sameSite = "no_restriction" | "lax" | "strict"
    Playwright requires: sameSite = "None" | "Lax" | "Strict"
    """
    resultado = []
    for c in cookies_list:
        cookie = {
            'name':     c.get('name', ''),
            'value':    c.get('value', ''),
            'domain':   c.get('domain', ''),
            'path':     c.get('path', '/'),
            'secure':   bool(c.get('secure', False)),
            'httpOnly': bool(c.get('httpOnly', False)),
            'sameSite': SAME_SITE_MAP.get(
                str(c.get('sameSite', '')).lower(), 'None'
            ),
        }
        # Cookie-Editor uses 'expirationDate', Playwright uses 'expires'
        if c.get('expirationDate'):
            cookie['expires'] = float(c['expirationDate'])

        if cookie['name']:  # Ignore cookies without a name
            resultado.append(cookie)

    return resultado


def cargar_cookies() -> list:
    """Loads and normalizes the cookies from the saved file."""
    if not COOKIES_FILE.exists():
        raise FileNotFoundError(
            f"No se encontraron cookies en {COOKIES_FILE}\n"
            "Exporta las cookies de x.com con Cookie-Editor en Chrome."
        )
    with open(COOKIES_FILE, 'r') as f:
        return normalizar_cookies(json.load(f))


# ─────────────────────────────────────────────
# Extracting tweets from Twitter's JSON
# ─────────────────────────────────────────────

def extract_tweets(api_response: dict) -> list:
    """
    Extracts tweets from the UserTweets GraphQL response.

    Twitter's structure is:
    data → user → result → timeline → timeline → instructions
    → entries → content → itemContent → tweet_results → result → legacy

    Args:
        api_response: dict with the JSON response from Twitter's API

    Returns:
        List of dicts with id, text, created_at, counts, is_retweet
    """
    tweets = []

    try:
        # Navigate down to the timeline instructions
        instructions = (
            api_response['user']['result']['timeline']['timeline']['instructions']
        )
    except (KeyError, TypeError):
        return tweets

    for instruction in instructions:
        # We only process the entries that add tweets
        if instruction.get('type') != 'TimelineAddEntries':
            continue

        for entry in instruction.get('entries', []):
            try:
                # Each entry can be a tweet, a pagination cursor, etc.
                tweet_result = (
                    entry['content']['itemContent']['tweet_results']['result']
                )
                legacy = tweet_result.get('legacy', {})

                text = legacy.get('full_text', '')
                if not text:
                    continue

                tweets.append({
                    'id':            legacy.get('id_str', ''),
                    'text':          text,
                    'created_at':    legacy.get('created_at', ''),
                    'favorite_count': legacy.get('favorite_count', 0),
                    'retweet_count': legacy.get('retweet_count', 0),
                    'reply_count':   legacy.get('reply_count', 0),
                    'quote_count':   legacy.get('quote_count', 0),
                    'is_retweet':    text.startswith('RT @'),
                })
            except (KeyError, TypeError):
                # This entry is not a tweet (cursor, separator, etc.)
                continue

    return tweets


async def crear_contexto_con_cookies(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled']
    )
    context = await browser.new_context(
        user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    )
    cookies = cargar_cookies()
    await context.add_cookies(cookies)

    # ── Anti-detection ────────────────────────────────────────────────────
    # Without this, Twitter serves cached content to automated browsers
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    """)
    # ─────────────────────────────────────────────────────────────────────

    return browser, context