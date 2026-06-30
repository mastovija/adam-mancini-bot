"""
scrapers/substack_scraper.py - Downloads the Trade Companion newsletter
======================================================================
Scraper for tradecompanion.substack.com, Adam Mancini's daily newsletter.
Uses Substack's public API to list articles and requests/beautifulsoup
to download the content of the free articles.

USAGE:
    python scrapers/substack_scraper.py

WHAT IT DOES:
    1. Gets the full list of articles via the Substack API
    2. Saves the index to data/raw/newsletter/index.json
    3. Downloads the full content of the free articles
    4. For paid ones, saves only metadata (title, date, excerpt)
    5. Each article is saved as an individual JSON

COST: $0 — uses the public API (no subscription required for free ones)
NOTE: For paid articles, subscribe and update SUBSTACK_COOKIES in .env
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Add the project root to the path to import config.py
sys.path.append(str(Path(__file__).parent.parent))


from config import SUBSTACK_URL, NEWSLETTER_DIR, SUBSTACK_COOKIES


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
# Headers to look like a normal browser
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html',
}

# Pause between requests to avoid overloading the server
DELAY_BETWEEN_REQUESTS = 1.5  # seconds

# Articles per page in the Substack API
BATCH_SIZE = 12
PREVIEW_MAX_LENGTH = 500  # articles shorter than this are considered previews

def get_cookies_dict() -> dict:
    """
    Parses SUBSTACK_COOKIES from .env into a dict for requests.
    With a valid substack.sid cookie, Substack serves the full
    paid article instead of the public preview.
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

def has_paid_access() -> bool:
    """Returns True if paid cookies are configured in .env."""
    return bool(get_cookies_dict())

# ─────────────────────────────────────────────
# Phase 1: Get the list of articles
# ─────────────────────────────────────────────

def get_all_post_metadata() -> list:
    """
    Gets the metadata of every newsletter article using
    Substack's public API (/api/v1/archive).

    The API returns articles in batches of 12, ordered by date.
    We paginate until there are no more articles.

    Returns: list of dicts with id, title, slug, date, is_free
    """
    print("📋 Fetching full list of articles...")

    all_posts = []
    offset    = 0

    while True:
        # Substack's public endpoint for listing articles
        url = (f"{SUBSTACK_URL}/api/v1/archive"
               f"?sort=new&search=&offset={offset}&limit={BATCH_SIZE}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            batch = response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ❌ API error: {e}")
            break

        if not batch:
            break  # No more articles

        for post in batch:
            # 'audience': 'everyone' = free, 'paid' = paid
            is_free = post.get('audience') == 'everyone'

            all_posts.append({
                'id':           post.get('id'),
                'title':        post.get('title', ''),
                'subtitle':     post.get('subtitle', ''),
                'slug':         post.get('slug', ''),
                'published_at': post.get('post_date', '')[:10],  # date only
                'is_free':      is_free,
                'url':          f"{SUBSTACK_URL}/p/{post.get('slug', '')}",
            })

        print(f"  Found: {len(all_posts)} articles...")

        # If the response has fewer than a page's worth, there are no more
        if len(batch) < BATCH_SIZE:
            break

        offset += BATCH_SIZE
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"✅ Total articles found: {len(all_posts)}\n")
    return all_posts


# ─────────────────────────────────────────────
# Phase 2: Download the content of each article
# ─────────────────────────────────────────────

def scrape_post_content(post: dict) -> dict:
    """
    Downloads and extracts the full text of an article.

    For free articles: extracts the full content.
    For paid articles: only saves the visible preview.

    Args:
        post: dict with the article metadata (from get_all_post_metadata)

    Returns:
        dict with all the metadata + the 'content' field holding the text
    """
    cookies = get_cookies_dict()   # session cookie for paid content
    try:
        response = requests.get(
            post['url'],
            headers={**HEADERS, 'Accept': 'text/html'},
            cookies=cookies,       # sends substack.sid → full content
            timeout=15
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {**post, 'content': '', 'scrape_status': 'error', 'error': str(e)}

    soup = BeautifulSoup(response.text, 'html.parser')

    # ── Detect paywall ────────────────────────────────────────────────────
    # Substack adds specific classes when the content requires a subscription
    paywall_classes = ['paywall', 'subscribe-paywall', 'subscription-required']
    has_paywall = any(
        soup.find(attrs={'class': lambda c: c and any(p in str(c) for p in paywall_classes)})
        for _ in [1]  # trick to make the any() cleaner
    )

    # ── Extract the article content ──────────────────────────────────────
    # Substack has changed its HTML structure several times,
    # we try multiple selectors to stay compatible with older versions
    content_selectors = [
        'div.available-content',          # recent versions
        'div.body.markup',                # earlier versions
        'div[class*="available-content"]', # variant with a compound class
        'article',                         # generic fallback
    ]

    content_text = ''
    for selector in content_selectors:
        element = soup.select_one(selector)
        if element:
            # get_text with '\n' as the separator to keep paragraph structure
            content_text = element.get_text('\n', strip=True)
            break

    # Previews: 900-2500 chars | Full articles: 5000-40000 chars
    # We don't use has_paywall because Substack shows paywall elements
    # even when the user is authenticated and has the full content.
    is_complete = len(content_text) > 3000

    return {
        **post,
        'content':              content_text,
        'content_length':       len(content_text),
        'is_complete':          is_complete,
        'has_paywall_detected': has_paywall,
        'scraped_at':           datetime.now().isoformat(),
        'scrape_status':        'ok',
    }


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

def scrape_newsletter():
    """
    Main function that orchestrates the whole newsletter scraping process.

    Full flow:
    1. Get the list of all articles via the API
    2. Save the full index to index.json
    3. Download free articles with full content
    4. Save metadata for paid articles (to know what exists)
    5. Each article = one individual JSON file (slug.json)
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Newsletter Scraper")
    print("=" * 55)
    print(f"🌐 URL: {SUBSTACK_URL}")
    print(f"📁 Saving to: {NEWSLETTER_DIR}\n")

    # Create directory
    NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Get the full list ──────────────────────────────────────────────
    all_posts = get_all_post_metadata()
    if not all_posts:
        print("❌ No articles found. Is the URL in config.py correct?")
        return

    # ── 2. Save the index ─────────────────────────────────────────────────
    # The index is useful to know what exists even if we haven't downloaded the content
    index_file = NEWSLETTER_DIR / 'index.json'
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(all_posts, f, indent=2, ensure_ascii=False)
    print(f"📑 Index saved: {index_file}")

    # ── 3. Statistics ─────────────────────────────────────────────────────
    free_posts  = [p for p in all_posts if p['is_free']]
    paid_posts  = [p for p in all_posts if not p['is_free']]
    print(f"📊 Free articles: {len(free_posts)}")
    print(f"🔒 Paid articles: {len(paid_posts)}\n")

    # ── 4. Filter out the already-downloaded ones ─────────────────────────
    # We read the slugs of the existing JSONs so we don't repeat work
    existing_slugs = {f.stem for f in NEWSLETTER_DIR.glob('*.json')
                      if f.stem != 'index'}

    def needs_download(post: dict) -> bool:
        """
        Re-downloads paid articles if they only have preview content.
        Key: with cookies configured, replaces all short previews with
        the full article text automatically.
        """
        if post['slug'] not in existing_slugs:
            return True             # never downloaded → always download
        if post['is_free']:
            return False            # free articles are already complete
        if not has_paid_access():
            return False            # no cookies → can't improve the preview
        # Paid article + cookies → re-download if the file is just a preview
        f = NEWSLETTER_DIR / f"{post['slug']}.json"
        try:
            data = json.load(open(f, encoding='utf-8'))
            return (not data.get('is_complete', False) or
                    data.get('content_length', 0) < PREVIEW_MAX_LENGTH)
        except Exception:
            return True

    to_download_free = [p for p in free_posts if needs_download(p)]
    to_download_paid = [p for p in paid_posts if needs_download(p)]

    if has_paid_access():
        est_min = (len(to_download_paid) * DELAY_BETWEEN_REQUESTS) / 60
        print(f"🆕 To download: {len(to_download_free)} free, "
              f"{len(to_download_paid)} paid (full content with cookies)")
        print(f"⏱️  Estimated time: ~{est_min:.0f} minutes")
    else:
        print(f"🆕 To download: {len(to_download_free)} free, "
              f"{len(to_download_paid)} paid (metadata/preview only)")

    print(f"🆕 To download: {len(to_download_free)} free, "
          f"{len(to_download_paid)} paid (metadata only)")

    # ── 5. Download free articles with content ────────────────────────────
    print("\n📥 Downloading free articles...")
    print("-" * 40)

    downloaded_ok  = 0
    downloaded_err = 0

    for i, post in enumerate(to_download_free, 1):
        date_str  = post['published_at']
        title_str = post['title'][:55] + ('...' if len(post['title']) > 55 else '')
        print(f"  [{i:3d}/{len(to_download_free)}] {date_str} — {title_str}")

        article = scrape_post_content(post)

        # Save as JSON with the slug as the filename
        output_file = NEWSLETTER_DIR / f"{post['slug']}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(article, f, indent=2, ensure_ascii=False)

        # Report the result
        if article['scrape_status'] == 'ok':
            downloaded_ok += 1
            status = "✅ complete" if article.get('is_complete') else "⚠️  partial"
            print(f"       → {status} ({article['content_length']:,} chars)")
        else:
            downloaded_err += 1
            print(f"       → ❌ Error: {article.get('error', 'unknown')}")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── 6. Save metadata for paid articles ────────────────────────────────
    # Even though we don't have the content, we save the metadata to know what exists
    if to_download_paid:
        print(f"\n📥 Downloading preview of {len(to_download_paid)} paid articles...")
        print("-" * 40)
        for i, post in enumerate(to_download_paid, 1):
            date_str  = post['published_at']
            title_str = post['title'][:55] + ('...' if len(post['title']) > 55 else '')
            print(f"  [{i:4d}/{len(to_download_paid)}] {date_str} — {title_str}")

            # We download the same way as the free ones — captures whatever is visible
            article = scrape_post_content(post)
            output_file = NEWSLETTER_DIR / f"{post['slug']}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(article, f, indent=2, ensure_ascii=False)

            time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── Final summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"✅ Scraping complete")
    print(f"📥 Downloaded with content: {downloaded_ok}")
    print(f"❌ Errors:                  {downloaded_err}")
    print(f"🔒 Without content (paid):  {len(to_download_paid)}")
    print(f"📁 Directory: {NEWSLETTER_DIR}")
    print("=" * 55)
    print()
    print("💡 To download paid articles in the future:")
    print("   1. Subscribe to the newsletter at tradecompanion.substack.com")
    print("   2. Open Chrome → F12 → Application → Cookies → substack.com")
    print("   3. Copy the cookies into SUBSTACK_COOKIES in your .env")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == '__main__':
    scrape_newsletter()
