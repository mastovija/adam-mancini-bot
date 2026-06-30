"""
parsers/newsletter_parser.py — Parser for Adam Mancini's daily newsletter
=============================================================================
Every morning it downloads the most recent Trade Companion article
(tradecompanion.substack.com), extracts the day map with Claude Haiku
and saves the result to data/daily/today.json.

This file is what the signal engine (Phase 5) consults throughout the
whole session to know the bias, the key levels and the conditions.

KEY FIELDS in today.json:
  - soportes, resistencias, nivel_critico, bias: extracted by Haiku
  - content_plan: last 8000 chars of the article = Trade Plan section
    (contains "I'd bid direct", "Bull/Bear case", level context)
    This is the most important field — the signal engine passes it whole to the LLM.

Manual usage:
    python parsers/newsletter_parser.py

With --force it re-parses even if today's already exists:
    python parsers/newsletter_parser.py --force

Automatic usage: called by the scheduler at 7:30 AM EST
"""

import json
import sys
import requests
from datetime import datetime, date
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent.parent))

from config import SUBSTACK_URL, DATA_DIR, SUBSTACK_COOKIES
from knowledge_base.processor import extract_trading_info


# ─────────────────────────────────────────────
# File paths
# ─────────────────────────────────────────────
DAILY_DIR   = DATA_DIR / 'daily'
TODAY_FILE  = DAILY_DIR / 'today.json'
HISTORY_DIR = DAILY_DIR / 'history'


# ─────────────────────────────────────────────
# Paid subscription cookies
# ─────────────────────────────────────────────

def get_substack_cookies() -> dict:
    """
    Parses SUBSTACK_COOKIES from .env into a dict for requests.
    With substack.sid, Substack serves the full article (30,000+ chars).
    """
    cookies = {}
    if not SUBSTACK_COOKIES:
        return cookies
    for part in SUBSTACK_COOKIES.split(';'):
        part = part.strip()
        if '=' in part:
            name, value = part.split('=', 1)
            cookies[name.strip()] = value.strip()
    return cookies


# ─────────────────────────────────────────────
# Get the most recent article
# ─────────────────────────────────────────────

def get_latest_article() -> dict | None:
    """Queries Substack's public API for the most recent article."""
    try:
        url = f"{SUBSTACK_URL}/api/v1/archive?sort=new&offset=0&limit=1"
        r   = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        articles = r.json()

        if not articles:
            return None

        a = articles[0]
        return {
            'slug':         a.get('slug', ''),
            'title':        a.get('title', ''),
            'subtitle':     a.get('subtitle', ''),
            'url':          f"{SUBSTACK_URL}/p/{a.get('slug', '')}",
            'published_at': a.get('post_date', '')[:10],
            'is_free':      a.get('audience') == 'everyone',
        }
    except Exception as e:
        print(f"❌ Error querying Substack: {e}")
        return None


def scrape_article_content(url: str) -> str:
    """
    Downloads the full article content using the subscription cookies.
    Without cookies it only gets the preview (2,000-3,000 chars).
    """
    cookies = get_substack_cookies()

    if cookies:
        print("   🔑 Using subscription cookies (full content)")
    else:
        print("   🆓 No cookies — public preview only")

    try:
        r = requests.get(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'
                )
            },
            cookies=cookies,
            timeout=15
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        for selector in ['div.available-content', 'div.body.markup', 'article']:
            el = soup.select_one(selector)
            if el:
                text = el.get_text('\n', strip=True)
                if len(text) > 200:
                    return text

        return ''
    except Exception as e:
        print(f"  ⚠️  Error downloading: {e}")
        return ''


# ─────────────────────────────────────────────
# Save the day map
# ─────────────────────────────────────────────

def guardar_mapa_dia(article: dict, trading_info: dict, content: str) -> dict:
    """
    Builds and saves the day map in two places:
    - data/daily/today.json              → overwritten each day (the engine reads it here)
    - data/daily/history/YYYY-MM-DD.json → permanent historical copy

    The most important field is 'content_plan': the last 8000 chars of the
    article, which correspond to the "Trade Plan" section where Adam writes:
    - "In terms of lvls I'd bid direct:" (the actually actionable levels)
    - "Bull case tomorrow:" / "Bear case tomorrow:"
    - Specific context for each level ("tested to death", "obvious FB", etc.)

    The signal engine passes this full text to the LLM so it makes
    decisions with Adam's exact words, not just with lists of numbers.
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    mapa = {
        'date':           article.get('published_at', str(date.today())),
        'title':          article.get('title', ''),
        'url':            article.get('url', ''),
        'is_complete':    len(content) > 5000,
        'parsed_at':      datetime.now().isoformat(),

        # Fields extracted by Haiku (structured levels)
        'bias':           trading_info.get('bias', 'unknown'),
        'condicion_bias': trading_info.get('condicion_bias'),
        'nivel_critico':  trading_info.get('nivel_critico'),
        'soportes':       trading_info.get('soportes', []),
        'resistencias':   trading_info.get('resistencias', []),
        'setup':          trading_info.get('setup'),
        'invalida_si':    trading_info.get('invalida_si'),

        # Full article — the LLM reads it whole to understand Adam's plan
        # Cost: ~$0.007 per LLM call, only when the price touches a level
        'content_plan':   content,
    }

    with open(TODAY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapa, f, indent=2, ensure_ascii=False)

    history_file = HISTORY_DIR / f"{mapa['date']}.json"
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(mapa, f, indent=2, ensure_ascii=False)

    return mapa


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

def parse_daily_newsletter(force: bool = False) -> dict | None:
    """
    Downloads and parses Adam Mancini's most recent newsletter.

    Flow:
    1. Checks whether we already have today's map (avoids reprocessing)
    2. Gets the most recent article from Substack
    3. Downloads the full content (with subscription cookies)
    4. Extracts bias and levels with Claude Haiku
    5. Saves to data/daily/today.json with the full content_plan
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Newsletter Parser")
    print("=" * 55)

    hoy = str(date.today())

    if TODAY_FILE.exists() and not force:
        with open(TODAY_FILE) as f:
            existing = json.load(f)
        if existing.get('date') == hoy:
            print(f"✅ Today's newsletter ({hoy}) already processed.")
            _mostrar_resumen(existing)
            return existing

    print(f"🔍 Querying Substack for {hoy}...")
    article = get_latest_article()

    if not article:
        print("❌ Could not connect to Substack")
        return None

    print(f"📰 Article: [{article['published_at']}] {article['title'][:60]}")

    print("📥 Downloading content...")
    content = scrape_article_content(article['url'])

    if content:
        print(f"   ✅ {len(content):,} characters fetched")
    else:
        print("   ⚠️  No content — using title")
        content = f"{article['title']}\n{article.get('subtitle', '')}"

    print("🤖 Extracting day map with Claude Haiku...")

    try:
        trading_info = extract_trading_info({
            'title':        article['title'],
            'published_at': article['published_at'],
            'content':      content,
        })
        soportes     = trading_info.get('soportes', [])
        resistencias = trading_info.get('resistencias', [])
        print(f"   ✅ Bias: {trading_info.get('bias')} | "
              f"Critical: {trading_info.get('nivel_critico')} | "
              f"Supports: {len(soportes)} | "
              f"Resistances: {len(resistencias)}")
    except Exception as e:
        print(f"   ❌ LLM error: {e}")
        trading_info = {}

    mapa = guardar_mapa_dia(article, trading_info, content)
    print(f"\n💾 Saved to: {TODAY_FILE}")
    print(f"   content_plan: {len(mapa.get('content_plan', ''))} chars (full Trade Plan)")
    _mostrar_resumen(mapa)
    return mapa


def _mostrar_resumen(mapa: dict):
    """Shows the day map in a readable form in the console."""
    print()
    print("┌─ DAY MAP " + "─" * 43)
    print(f"│ Date:          {mapa.get('date', '?')}")
    print(f"│ Bias:          {mapa.get('bias', '?').upper()}")
    print(f"│ Complete:      {'✅ Yes' if mapa.get('is_complete') else '⚠️  Preview only'}")
    print(f"│ Full plan:     {'✅ Yes' if mapa.get('content_plan') else '❌ No'} "
          f"({len(mapa.get('content_plan', ''))} chars)")

    if mapa.get('nivel_critico'):
        print(f"│ Critical level: {mapa['nivel_critico']}")

    if mapa.get('soportes'):
        print(f"│ Supports ({len(mapa['soportes'])}):  {mapa['soportes']}")

    if mapa.get('resistencias'):
        print(f"│ Resists ({len(mapa['resistencias'])}):   {mapa['resistencias'][:8]}...")

    if mapa.get('setup'):
        texto = mapa['setup']
        print(f"│ Setup:")
        for linea in texto.split('.'):
            linea = linea.strip()
            if linea:
                print(f"│   {linea}.")

    if mapa.get('invalida_si'):
        print(f"│ Invalidated if: {mapa['invalida_si']}")

    print("└" + "─" * 53)
    print()


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == '__main__':
    force = '--force' in sys.argv
    parse_daily_newsletter(force=force)
