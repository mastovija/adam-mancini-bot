"""
scrapers/extract_examples.py — Phase 3.1: mine real trade examples
==================================================================
Every Trade Companion newsletter contains a "Recap/Daily Summary" section where
Adam walks through REAL examples of his three setups from the last couple of days
— with the level, why it qualified (or was low-quality), the acceptance, and the
outcome, in his own words. That is the perfect few-shot material for teaching the
decision model to reason by precedent: take the A+ Failed Breakdown of a genuine
significant low, and skip / down-rate the tested-to-death mid-range chop.

This script pulls the recap section out of a set of recent newsletters and saves
the raw text so it can be distilled into a compact few-shot block (Phase 3.2).

USAGE
    python scrapers/extract_examples.py                 # default recent window
    python scrapers/extract_examples.py --since 2026-06-15
OUTPUT
    knowledge_base/methodology/recaps_raw.json          # {date: recap_text}
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NEWSLETTER_DIR = ROOT / 'data' / 'raw' / 'newsletter'
OUT = ROOT / 'knowledge_base' / 'methodology' / 'recaps_raw.json'

# The recap section starts at this header; the plan-for-tomorrow follows it.
RECAP_START = re.compile(r'Recap\s*/?\s*Daily Summary', re.I)
# Reasonable cap so we grab the examples without the whole rest of the letter.
RECAP_MAX_CHARS = 7000


def extract_recap(content: str) -> str | None:
    m = RECAP_START.search(content)
    if not m:
        return None
    return content[m.start():m.start() + RECAP_MAX_CHARS].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-06-10',
                    help='Only newsletters published on/after this date (YYYY-MM-DD).')
    args = ap.parse_args()

    recaps: dict[str, str] = {}
    for f in NEWSLETTER_DIR.glob('*.json'):
        if f.name == 'index.json':
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        d = d[0] if isinstance(d, list) else d
        pub = str(d.get('published_at') or '')[:10]
        if pub < args.since:
            continue
        recap = extract_recap(d.get('content', '') or '')
        if recap and len(recap) > 400:
            # keep the richest recap per date if duplicates
            if pub not in recaps or len(recap) > len(recaps[pub]):
                recaps[pub] = recap

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(recaps, open(OUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    print(f"✅ Extracted recap sections from {len(recaps)} newsletters (since {args.since})")
    for date in sorted(recaps):
        first = recaps[date].split('\n')[0][:60]
        print(f"   {date} | {len(recaps[date]):5d} chars | {first}")
    print(f"📄 {OUT}")


if __name__ == '__main__':
    main()
