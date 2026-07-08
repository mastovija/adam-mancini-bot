"""
backtest/june/levels_loader.py — Per-day newsletter levels for the backtest
============================================================================
Maps each trading day in the backtest window (Jun 16 → Jul 6, 2026) to the
newsletter-levels dict that the live bot would have been holding that day, and
returns it in the exact `today`-shaped structure that
`signals.signal_engine.get_all_levels()` consumes.

WHY THIS EXISTS
---------------
In production the bot only ever keeps ONE `data/daily/today.json`, overwritten
each morning by the newsletter parser. Historical copies live in
`data/daily/history/<date>.json`. For the backtest we need that day's levels for
every day in the window, so we index the history folder (plus today.json).

The daily files use the parser's ORIGINAL Spanish keys
(`soportes`, `resistencias`, `nivel_critico`, `content_plan`, `bias`, ...),
which is exactly what `get_all_levels()` already reads — so we hand the raw dict
straight through, no key translation.

MISSING-DAY FALLBACK (flagged, never invented)
----------------------------------------------
Some trading days have no complete daily file (Jun 18, 26 and Jul 1, 2 in this
window). For those we fall back to the most recent PRIOR complete newsletter —
the same actual, published levels the live bot would still be holding on a day it
failed to re-parse. Every fallback is reported (see `resolve_levels`), so the
backtest stays auditable and no level is fabricated.

A file is considered COMPLETE only if it carries a real content_plan (the LLM
prompt needs it) and at least one level. The two early-June stubs
(2026-06-05, 2026-06-09, content_plan empty) are therefore ignored — they are
outside this window anyway.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config import DATA_DIR

HISTORY_DIR = DATA_DIR / 'daily' / 'history'
TODAY_FILE = DATA_DIR / 'daily' / 'today.json'

# Minimum content_plan length to treat a daily file as usable for the LLM prompt.
# Mirrors the load_today() guard in signal_engine.py (warns below 500 chars).
MIN_CONTENT_PLAN_LEN = 500

# Backtest window trading days (NYSE open), Jun 16 → Jul 6 2026.
# Excludes Jun 19 (Juneteenth) and Jul 3 (Independence Day observed) — both closed.
TRADING_DAYS = [
    '2026-06-16', '2026-06-17', '2026-06-18',
    '2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25', '2026-06-26',
    '2026-06-29', '2026-06-30',
    '2026-07-01', '2026-07-02', '2026-07-06',
]


def _is_complete(data: dict) -> bool:
    """True if the daily dict has a usable content_plan and at least one level."""
    if not isinstance(data, dict):
        return False
    if len(data.get('content_plan') or '') < MIN_CONTENT_PLAN_LEN:
        return False
    has_levels = bool(data.get('soportes')) or bool(data.get('resistencias')) \
        or data.get('nivel_critico') is not None
    return has_levels


def _load_index() -> dict[str, dict]:
    """
    Builds {date_str: daily_dict} from every COMPLETE file in history/ plus
    today.json. Keyed by the file's own `date` field (falling back to the stem).
    """
    index: dict[str, dict] = {}

    candidates = list(HISTORY_DIR.glob('*.json'))
    if TODAY_FILE.exists():
        candidates.append(TODAY_FILE)

    for path in candidates:
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        if not _is_complete(data):
            continue
        date_key = data.get('date') or path.stem
        # Prefer the richer copy if a date appears twice (e.g. history vs today).
        prev = index.get(date_key)
        if prev is None or len(data.get('content_plan') or '') > len(prev.get('content_plan') or ''):
            index[date_key] = data

    return index


def resolve_levels(trading_day: str, index: dict[str, dict] | None = None) -> tuple[dict | None, str | None, bool]:
    """
    Resolve the levels dict to use for `trading_day` (YYYY-MM-DD).

    Returns (today_dict, source_date, is_fallback):
      - today_dict:  the daily dict for get_all_levels() (None if nothing found)
      - source_date: the date whose newsletter was actually used
      - is_fallback: True if source_date != trading_day (a prior newsletter reused)
    """
    if index is None:
        index = _load_index()

    if trading_day in index:
        return index[trading_day], trading_day, False

    # Fallback: most recent complete newsletter strictly on or before this day.
    prior = sorted(d for d in index if d <= trading_day)
    if prior:
        src = prior[-1]
        return index[src], src, True

    return None, None, False


def get_window_levels() -> dict[str, dict]:
    """
    Resolve levels for every trading day in the window.

    Returns {trading_day: {'levels': today_dict, 'source_date': str,
                           'is_fallback': bool}}.
    """
    index = _load_index()
    out: dict[str, dict] = {}
    for day in TRADING_DAYS:
        today_dict, src, is_fb = resolve_levels(day, index)
        out[day] = {'levels': today_dict, 'source_date': src, 'is_fallback': is_fb}
    return out


def _main():
    """Print the day → levels-source mapping for manual verification."""
    print("=" * 68)
    print("  Backtest levels mapping — Jun 16 → Jul 6 2026")
    print("=" * 68)
    resolved = get_window_levels()
    for day, info in resolved.items():
        lv = info['levels']
        if lv is None:
            print(f"  {day} | ❌ NO LEVELS FOUND")
            continue
        from signals.signal_engine import get_all_levels
        n = len(get_all_levels(lv))
        tag = f"⤷ fallback from {info['source_date']}" if info['is_fallback'] else "exact"
        print(f"  {day} | {tag:28s} | bias={lv.get('bias'):8s} | "
              f"{n} levels | crit={lv.get('nivel_critico')}")
    print("=" * 68)


if __name__ == '__main__':
    _main()
