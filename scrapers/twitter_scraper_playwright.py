"""
scrapers/twitter_scraper_playwright.py — Downloads tweet history with Playwright
=====================================================================================
Uses Playwright to open a real browser, load Adam Mancini's profile,
scroll automatically to the bottom and capture every available tweet
(up to ~3200, the limit Twitter imposes in its web interface).

USAGE:
    python scrapers/twitter_scraper_playwright.py

HOW IT WORKS:
    1. Opens Chromium with your session cookies (no login)
    2. Navigates to x.com/AdamMancini4
    3. Intercepts the GraphQL UserTweets calls Twitter makes
    4. Scrolls to the bottom so Twitter loads more tweets
    5. Repeats until there are no new tweets
    6. Saves everything to data/raw/tweets/adam_mancini_tweets.json

ESTIMATED TIME: ~15-20 minutes for ~3000 tweets
RESUMABLE: yes, already-downloaded tweets are skipped automatically
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import TWEETS_DIR, TWITTER_TARGET
from parsers.playwright_utils import extract_tweets, crear_contexto_con_cookies

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
OUTPUT_FILE      = TWEETS_DIR / 'adam_mancini_tweets.json'
PROFILE_URL      = f'https://x.com/{TWITTER_TARGET}'
SCROLL_DELAY_MS  = 2000   # ms between scrolls (time for tweets to load)
MAX_SCROLLS      = 150    # max scrolls = ~3000 tweets (20 per scroll)
MAX_SIN_NUEVOS   = 5      # stop if N consecutive scrolls have no new tweets


# ─────────────────────────────────────────────
# File utilities
# ─────────────────────────────────────────────

def cargar_tweets_existentes() -> tuple[list, set]:
    """Loads already-downloaded tweets so we don't repeat work."""
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            tweets = json.load(f)
        return tweets, {t['id'] for t in tweets}
    return [], set()


def guardar_tweets(tweets: list):
    """Saves all the tweets to the JSON file."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(tweets, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

async def scrape_historico():
    """
    Downloads every available tweet from @AdamMancini4 using Playwright.

    The scroll process:
    - Each time you scroll to the bottom, Twitter makes a UserTweets call
    - Playwright intercepts that call and extracts the tweets
    - We repeat until there are no new tweets or we hit the limit
    """
    print("=" * 60)
    print("  Adam Mancini Bot — Tweet Scraper (Playwright)")
    print("=" * 60)
    print(f"🎯 Target: @{TWITTER_TARGET}")
    print(f"📁 Saving to: {OUTPUT_FILE}\n")

    # Load already-downloaded tweets
    todos_tweets, ids_existentes = cargar_tweets_existentes()
    if todos_tweets:
        print(f"📂 Resuming: {len(todos_tweets)} tweets already downloaded\n")

    nuevos_total     = 0
    scrolls_sin_nuevos = 0

    async with async_playwright() as p:
        browser, context = await crear_contexto_con_cookies(p)
        page = await context.new_page()

        # ── Intercept API responses ───────────────────────────────────────
        # Each time Twitter returns tweets, we process them immediately
        async def procesar_respuesta(response):
            nonlocal nuevos_total, scrolls_sin_nuevos

            if 'UserTweets' not in response.url or response.status != 200:
                return

            try:
                data = await response.json()
                tweets_batch = extract_tweets(data.get('data', {}))

                nuevos_en_batch = 0
                for tweet in tweets_batch:
                    if tweet['id'] and tweet['id'] not in ids_existentes:
                        # Add download metadata
                        tweet['scraped_at'] = datetime.now().isoformat()
                        todos_tweets.append(tweet)
                        ids_existentes.add(tweet['id'])
                        nuevos_en_batch += 1
                        nuevos_total    += 1

                if nuevos_en_batch > 0:
                    print(f"  ✅ Batch: +{nuevos_en_batch} tweets | "
                          f"Total: {len(todos_tweets):,}")
                    guardar_tweets(todos_tweets)  # Save after each batch
                    scrolls_sin_nuevos = 0
                else:
                    scrolls_sin_nuevos += 1

            except Exception as e:
                print(f"  ⚠️  Error processing batch: {e}")

        page.on('response', procesar_respuesta)

        # ── Load profile ──────────────────────────────────────────────────
        print("🌐 Loading Adam Mancini's profile...")
        try:
            await page.goto(PROFILE_URL, wait_until='load', timeout=20000)
        except Exception:
            pass  # The load timeout is normal on Twitter

        # Wait for the first tweets to load
        await page.wait_for_timeout(3000)
        print(f"📥 Starting scroll ({MAX_SCROLLS} max)...\n")

        # ── Automatic scroll ──────────────────────────────────────────────
        for scroll_num in range(MAX_SCROLLS):

            # Stop if too many scrolls with no new tweets
            if scrolls_sin_nuevos >= MAX_SIN_NUEVOS:
                print(f"\n⏹️  {MAX_SIN_NUEVOS} scrolls with no new tweets — reached the end")
                break

            # Scroll to the bottom of the page
            # This makes Twitter load the next batch of tweets
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(SCROLL_DELAY_MS)

            # Show progress every 10 scrolls
            if (scroll_num + 1) % 10 == 0:
                print(f"  📜 Scroll {scroll_num + 1}/{MAX_SCROLLS} | "
                      f"Total accumulated: {len(todos_tweets):,}")

        await browser.close()

    # ── Save the final result ─────────────────────────────────────────────
    guardar_tweets(todos_tweets)

    print("\n" + "=" * 60)
    print(f"✅ Download complete")
    print(f"📊 Total tweets: {len(todos_tweets):,}")
    print(f"🆕 New this session: {nuevos_total:,}")

    if todos_tweets:
        # Statistics — datetime is already imported at the top of the module
        def _parse(d):
            try: return datetime.strptime(d, '%a %b %d %H:%M:%S +0000 %Y')
            except: return datetime.min
        fechas = sorted([t['created_at'] for t in todos_tweets if t.get('created_at')], key=_parse)
        if fechas:
            print(f"📅 Oldest: {fechas[0]}")
            print(f"📅 Newest: {fechas[-1]}")

        originales = sum(1 for t in todos_tweets if not t.get('is_retweet'))
        print(f"📝 Original tweets: {originales:,}")

    print(f"📁 File: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == '__main__':
    asyncio.run(scrape_historico())
