"""
scrapers/fetch_methodology.py — Phase 0.1: fetch Mancini's methodology doc
=========================================================================
Downloads Adam Mancini's canonical trading-methodology article
("My Trade Methodology Fundamentals") from tradecompanion.substack.com and
stores it as the authoritative source for the bot's reasoning brain (Phase 1).

WHY SEPARATE FROM THE DAILY NEWSLETTER
--------------------------------------
The daily newsletter gives that day's levels/plan; THIS doc is the fixed
rulebook — the 15-point methodology (philosophy, risk/trade management, the
actionable entry setups, how he reads price action around levels). It changes
rarely (he calls it "a dynamic page that will be constantly expanding"), so it
belongs in a cached system-prompt block, not re-sent per signal.

REUSE
-----
Uses scrapers.substack_scraper.scrape_post_content (same cookie-authenticated
requests + BeautifulSoup extraction the newsletter scraper uses).

OUTPUT
------
knowledge_base/methodology/fundamentals.json   (full scrape result + metadata)
knowledge_base/methodology/fundamentals.txt     (clean text — the rubric source)

USAGE
-----
    python scrapers/fetch_methodology.py         # refresh from Substack
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.substack_scraper import scrape_post_content, has_paid_access
from config import SUBSTACK_URL

# The canonical methodology article.
METHODOLOGY_SLUG = 'my-trade-methodology-fundamentals'
OUT_DIR = ROOT / 'knowledge_base' / 'methodology'
# A real methodology page is tens of KB; below this we assume a paywalled preview.
MIN_VALID_LEN = 5000


def fetch_methodology() -> dict:
    """Fetch and persist the methodology doc; return the scrape result."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not has_paid_access():
        print("❌ No SUBSTACK_COOKIES in .env — cannot fetch the paid methodology doc.")
        sys.exit(1)

    post = {
        'url': f"{SUBSTACK_URL}/p/{METHODOLOGY_SLUG}",
        'slug': METHODOLOGY_SLUG,
        'title': 'My Trade Methodology Fundamentals',
        'published_at': '',
        'is_free': False,
    }
    print(f"🌐 Fetching {post['url']} ...")
    art = scrape_post_content(post)

    status = art.get('scrape_status')
    length = art.get('content_length', 0)
    if status != 'ok' or length < MIN_VALID_LEN:
        print(f"❌ Fetch failed or looks like a preview "
              f"(status={status}, length={length}). Are the cookies still valid?")
        sys.exit(1)

    art['fetched_at'] = datetime.now().isoformat()
    json.dump(art, open(OUT_DIR / 'fundamentals.json', 'w', encoding='utf-8'),
              indent=2, ensure_ascii=False)
    (OUT_DIR / 'fundamentals.txt').write_text(art['content'], encoding='utf-8')

    print(f"✅ Methodology saved — {length:,} chars")
    print(f"   📄 {OUT_DIR / 'fundamentals.json'}")
    print(f"   📄 {OUT_DIR / 'fundamentals.txt'}")
    return art


if __name__ == '__main__':
    art = fetch_methodology()
    # Show the numbered-point headings so we can eyeball completeness.
    import re
    points = re.findall(r'(?m)^\s*(\d{1,2})\)\s*(.{0,70})', art['content'])
    print(f"\nMethodology points found: {len(points)}")
    for num, head in points[:20]:
        print(f"   {num}) {head.strip()}")
