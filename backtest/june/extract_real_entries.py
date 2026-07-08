"""
backtest/june/extract_real_entries.py — Deliverable 1: real-entry extractor
============================================================================
Scans Adam Mancini's tweets over the backtest window (Jun 16 → Jul 6, 2026) and
flags which ones look like a REAL, live entry (his ground-truth trades) versus
historical recaps / commentary / bare level posts.

This is a HEURISTIC starting point, not ground truth on its own: it exports a CSV
for the user to review and correct by hand. The corrected file then becomes the
ground-truth input to the cross-reference step (crossref_and_summary.py).

REUSE (no copy-pasted classification logic)
-------------------------------------------
- `tweet_es_referencia_pasada()` and `PALABRAS_PASADO` from
  parsers.tweet_monitor  → the exact recap/past-trade filter used in production.
- Optional `--llm` enrichment calls the production `clasificar_tweet()` (Claude
  Haiku) to add its actionable/direction/entry/stop verdict as extra columns.

HEURISTIC
---------
candidate_real_entry = (tweet contains a present-tense entry phrase)
                       AND (NOT a past/recap reference)
The entry phrases below are Mancini's usual live-entry wording. "triggered" on its
own is included but "triggered yesterday" etc. is knocked out by the recap filter.

OUTPUT
------
data/backtest_june/real_entries_candidates.csv with columns:
  date, time_est, tweet_id, candidate_real_entry, is_past_ref,
  matched_keywords, tweet_text[, llm_accionable, llm_direccion, llm_entrada,
  llm_stop, llm_resumen]
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config import DATA_DIR, MARKET_TIMEZONE
from parsers.tweet_monitor import tweet_es_referencia_pasada

TWEETS_FILE = DATA_DIR / 'raw' / 'tweets' / 'adam_mancini_tweets.json'
OUT_DIR = DATA_DIR / 'backtest_june'
OUT_CSV = OUT_DIR / 'real_entries_candidates.csv'

# Backtest window (inclusive), in New-York calendar dates.
WINDOW_START = '2026-06-16'
WINDOW_END = '2026-07-06'

# Present-tense entry phrases Adam uses when announcing a live trade.
# Matched case-insensitively as substrings. Recap phrasing is removed separately
# by tweet_es_referencia_pasada(), so e.g. "triggered yesterday" won't survive.
ENTRY_KEYWORDS = [
    'triggered', 'trigger at', 'long trigger',
    'in at', 'in long', 'long here', 'going long', 'go long',
    'entered', 'entering', 'adding', 'add here',
    'buy the', 'buying', 'bought here',
    'stop below', 'stop above', 'stop under',
    'fb of', 'failed breakdown of', 'reclaim of',
]


def _to_ny(created_at: str) -> datetime | None:
    """Parse Twitter's UTC 'created_at' and convert to America/New_York."""
    try:
        dt_utc = datetime.strptime(created_at, '%a %b %d %H:%M:%S +0000 %Y')
        return pytz.utc.localize(dt_utc).astimezone(pytz.timezone(MARKET_TIMEZONE))
    except Exception:
        return None


def _matched_keywords(text: str) -> list[str]:
    """Return the entry phrases present in the tweet (case-insensitive)."""
    low = text.lower()
    return [kw for kw in ENTRY_KEYWORDS if kw in low]


def load_window_tweets() -> list[dict]:
    """Load non-retweet tweets whose NY date falls inside the backtest window."""
    tweets = json.load(open(TWEETS_FILE, encoding='utf-8'))
    rows = []
    for t in tweets:
        if t.get('is_retweet'):
            continue
        dt_ny = _to_ny(t.get('created_at', ''))
        if not dt_ny:
            continue
        day = dt_ny.strftime('%Y-%m-%d')
        if WINDOW_START <= day <= WINDOW_END:
            rows.append({'tweet': t, 'dt_ny': dt_ny, 'day': day})
    rows.sort(key=lambda r: r['dt_ny'])
    return rows


async def _classify_all(candidates: list[dict]) -> dict[str, dict]:
    """Run the production LLM classifier on the given rows; keyed by tweet id."""
    from parsers.tweet_monitor import clasificar_tweet
    out: dict[str, dict] = {}
    for i, row in enumerate(candidates, 1):
        t = row['tweet']
        print(f"  🤖 LLM {i}/{len(candidates)}: {t.get('text','')[:60]!r}")
        out[t['id']] = await clasificar_tweet(t.get('text', ''), t.get('created_at', ''))
    return out


def extract(use_llm: bool = False) -> list[dict]:
    """Build the candidate rows; optionally enrich with the LLM classifier."""
    rows = load_window_tweets()

    records = []
    for r in rows:
        t = r['tweet']
        text = (t.get('text') or '').replace('\n', ' ').strip()
        is_past = tweet_es_referencia_pasada(text)
        matched = _matched_keywords(text)
        candidate = bool(matched) and not is_past
        records.append({
            'date': r['day'],
            'time_est': r['dt_ny'].strftime('%H:%M'),
            'tweet_id': t.get('id', ''),
            'candidate_real_entry': 'yes' if candidate else 'no',
            'is_past_ref': 'yes' if is_past else 'no',
            'matched_keywords': '|'.join(matched),
            'tweet_text': text,
        })

    if use_llm:
        # Only classify the heuristic candidates (keeps token spend tiny).
        cand_rows = [r for r, rec in zip(rows, records)
                     if rec['candidate_real_entry'] == 'yes']
        verdicts = asyncio.run(_classify_all(cand_rows))
        for rec in records:
            v = verdicts.get(rec['tweet_id'], {})
            rec['llm_accionable'] = ('yes' if v.get('accionable') else 'no') if v else ''
            rec['llm_direccion'] = v.get('direccion') or ''
            rec['llm_entrada'] = v.get('entrada') if v.get('entrada') is not None else ''
            rec['llm_stop'] = v.get('stop') if v.get('stop') is not None else ''
            rec['llm_resumen'] = (v.get('resumen') or '').replace('\n', ' ')

    return records


def write_csv(records: list[dict]):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not records:
        print("⚠️  No tweets found in the window.")
        return
    fields = list(records[0].keys())
    with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(records)


def _main():
    ap = argparse.ArgumentParser(description="Extract candidate real entries from Adam's tweets.")
    ap.add_argument('--llm', action='store_true',
                    help="Enrich heuristic candidates with the production LLM classifier.")
    args = ap.parse_args()

    print("=" * 68)
    print(f"  Real-entry extractor — {WINDOW_START} → {WINDOW_END}")
    print("=" * 68)

    records = extract(use_llm=args.llm)
    write_csv(records)

    n_total = len(records)
    n_cand = sum(1 for r in records if r['candidate_real_entry'] == 'yes')
    n_past = sum(1 for r in records if r['is_past_ref'] == 'yes')
    by_day: dict[str, int] = {}
    for r in records:
        if r['candidate_real_entry'] == 'yes':
            by_day[r['date']] = by_day.get(r['date'], 0) + 1

    print(f"  Tweets in window:        {n_total}")
    print(f"  Past/recap references:   {n_past}")
    print(f"  Candidate real entries:  {n_cand}")
    print(f"  Candidates per day:")
    for day in sorted(by_day):
        print(f"      {day}: {by_day[day]}")
    print(f"\n  📄 CSV → {OUT_CSV}")
    print("  ⚠️  Heuristic only — review/correct the 'candidate_real_entry' column by hand.")
    print("=" * 68)


if __name__ == '__main__':
    _main()
