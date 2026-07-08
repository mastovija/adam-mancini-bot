"""
backtest/june/crossref_and_summary.py — Deliverables 3 & 4
==========================================================
Cross-references Mancini's confirmed real entries (ground truth) against the
signals produced by the real-time and delayed backtests, then reports how much of
the signal shortfall the 15-minute delay explains.

GROUND TRUTH (deliverable 1 output, hand-reviewed)
--------------------------------------------------
Reads data/backtest_june/real_entries_candidates.csv. By default it uses rows
where `candidate_real_entry == yes` as the confirmed entries. If you have hand-
corrected the file, keep that column as your verdict; you may also add an optional
`entry_level` column (an ES number) to tighten the match to a specific level.
The entry TIME is `time_est`; because Adam often tweets shortly AFTER the trigger,
matching uses a ±20-minute window (configurable) which absorbs that lag.

MATCH (deliverable 3)
---------------------
For each confirmed entry, a view "caught" it if that view's signals CSV has an
`entrar == yes` row within ±MATCH_MINUTES of the entry time (and, if an
entry_level is given, within LEVEL_TOL points of it). We emit a per-entry table:
  entry_dt, level, caught_realtime, caught_delayed, llm_verdict_differs, notes

SUMMARY (deliverable 4)
-----------------------
Over the EVALUABLE entries (those falling on a day we have full ES coverage +
levels, inside 07:30–16:00):
  - % captured by real-time
  - % captured by delayed
  - delay-explained gap  = realtime% − delayed%
  - unexplained          = 100% − realtime%  (entries even real-time missed;
                           threshold/LLM/level issues — out of scope to fix here)
Entries that are NOT evaluable (non-trading day, no bars/levels, outside session)
are listed separately so they do not dilute the percentages.

Prints the summary to the console AND writes data/backtest_june/summary.md, plus
data/backtest_june/crossref_table.csv.
"""

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR
from levels_loader import TRADING_DAYS

OUT_DIR = DATA_DIR / 'backtest_june'
ES_BARS_DIR = DATA_DIR / 'backtest' / 'es_bars'
REAL_ENTRIES = OUT_DIR / 'real_entries_candidates.csv'
RT_SIGNALS = OUT_DIR / 'signals_realtime.csv'
DL_SIGNALS = OUT_DIR / 'signals_delayed.csv'
TRIGGERS_SUMMARY = OUT_DIR / 'triggers_summary.json'

MATCH_MINUTES = 20            # ±window around the real entry (per the spec)
LEVEL_TOL = 5.0              # points, only used if entry_level is provided
SESSION_START, SESSION_END = '07:30', '16:00'


def _dt(day: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{day} {hhmm}", '%Y-%m-%d %H:%M')


def load_confirmed_entries() -> list[dict]:
    """Confirmed real entries from the (optionally hand-corrected) CSV."""
    if not REAL_ENTRIES.exists():
        print(f"❌ {REAL_ENTRIES} not found — run extract_real_entries.py first.")
        sys.exit(1)
    rows = list(csv.DictReader(open(REAL_ENTRIES, encoding='utf-8')))
    entries = []
    for r in rows:
        if (r.get('candidate_real_entry') or '').strip().lower() != 'yes':
            continue
        lvl = r.get('entry_level', '').strip()
        entries.append({
            'date': r['date'], 'time_est': r['time_est'],
            'entry_level': float(lvl) if lvl else None,
            'tweet_text': r.get('tweet_text', ''),
        })
    return entries


def load_signals(path: Path) -> list[dict]:
    """ENTER signals (entrar == yes) with parsed datetime + level."""
    if not path.exists():
        return []
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8')):
        if (r.get('entrar') or '').strip().lower() != 'yes':
            continue
        try:
            out.append({'dt': datetime.strptime(r['datetime'], '%Y-%m-%d %H:%M'),
                        'level': float(r['level'])})
        except Exception:
            continue
    return out


def caught(entry_dt, entry_level, signals) -> bool:
    """True if a matching ENTER signal exists within ±MATCH_MINUTES (and level)."""
    lo, hi = entry_dt - timedelta(minutes=MATCH_MINUTES), entry_dt + timedelta(minutes=MATCH_MINUTES)
    for s in signals:
        if lo <= s['dt'] <= hi:
            if entry_level is None or abs(s['level'] - entry_level) <= LEVEL_TOL:
                return True
    return False


def _evaluated_days() -> list[str]:
    """Days the backtest actually ran (so untested days aren't scored as 'missed')."""
    if TRIGGERS_SUMMARY.exists():
        data = json.load(open(TRIGGERS_SUMMARY))
        days = data.get('days_evaluated')
        if days:
            return days
    idx = ES_BARS_DIR / 'index.json'
    return json.load(open(idx)) if idx.exists() else TRADING_DAYS


def evaluability(entry, evaluated: list[str]) -> str | None:
    """Return None if evaluable, else a reason string (not-evaluable)."""
    if entry['date'] not in TRADING_DAYS:
        return 'non-trading day'
    if entry['date'] not in evaluated:
        return 'day not backtested'
    if not (SESSION_START <= entry['time_est'] <= SESSION_END):
        return 'outside 07:30-16:00'
    return None


def main():
    entries = load_confirmed_entries()
    rt_sig = load_signals(RT_SIGNALS)
    dl_sig = load_signals(DL_SIGNALS)

    evaluated = _evaluated_days()
    table = []
    for e in entries:
        entry_dt = _dt(e['date'], e['time_est'])
        reason = evaluability(e, evaluated)
        c_rt = caught(entry_dt, e['entry_level'], rt_sig)
        c_dl = caught(entry_dt, e['entry_level'], dl_sig)
        table.append({
            'entry_dt': entry_dt.strftime('%Y-%m-%d %H:%M'),
            'level': ('' if e['entry_level'] is None else f"{e['entry_level']:.0f}"),
            'caught_realtime': 'yes' if c_rt else 'no',
            'caught_delayed': 'yes' if c_dl else 'no',
            'llm_verdict_differs': 'yes' if c_rt != c_dl else 'no',
            'evaluable': 'no' if reason else 'yes',
            'notes': reason or e['tweet_text'][:60],
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / 'crossref_table.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()) if table else
                           ['entry_dt', 'level', 'caught_realtime', 'caught_delayed',
                            'llm_verdict_differs', 'evaluable', 'notes'],
                           quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(table)

    # ── Summary over evaluable entries ──
    evaluable = [r for r in table if r['evaluable'] == 'yes']
    n_eval = len(evaluable)
    n_rt = sum(1 for r in evaluable if r['caught_realtime'] == 'yes')
    n_dl = sum(1 for r in evaluable if r['caught_delayed'] == 'yes')
    not_eval = [r for r in table if r['evaluable'] == 'no']

    def pct(n):
        return (100.0 * n / n_eval) if n_eval else 0.0

    rt_pct, dl_pct = pct(n_rt), pct(n_dl)
    delay_gap = rt_pct - dl_pct
    unexplained = 100.0 - rt_pct

    lines = []
    lines.append("# June-window backtest summary — 15-min delay impact\n")
    days_run = f"{evaluated[0]} → {evaluated[-1]}" if evaluated else "none"
    lines.append(f"Days backtested: {days_run} ({len(evaluated)} trading day(s), "
                 f"single contract ESU2026, post-roll).\n")
    lines.append("_LLM gate: FB-fired (detect_failed_breakdown) + price-at-level + valid long + "
                 "not-in-cooldown, applied identically to both views._\n")
    lines.append(f"- Confirmed real entries (from reviewed tweets): **{len(table)}**")
    lines.append(f"- Not evaluable (non-trading day / no coverage / off-hours): **{len(not_eval)}**")
    lines.append(f"- Evaluable entries: **{n_eval}**\n")
    lines.append(f"- Captured REAL-TIME:  **{n_rt}/{n_eval} = {rt_pct:.0f}%**")
    lines.append(f"- Captured DELAYED:    **{n_dl}/{n_eval} = {dl_pct:.0f}%**\n")
    lines.append(f"- **Delay-explained gap** (real-time − delayed): **{delay_gap:.0f} pts** "
                 f"of capture rate")
    lines.append(f"- **Unexplained** (missed even in real-time → threshold/LLM/level, "
                 f"out of scope here): **{unexplained:.0f}%**\n")
    if not_eval:
        lines.append("## Not-evaluable entries")
        for r in not_eval:
            lines.append(f"- {r['entry_dt']} — {r['notes']}")
        lines.append("")
    lines.append("## Per-entry cross-reference")
    lines.append("| entry_dt | level | real-time | delayed | verdict differs | notes |")
    lines.append("|---|---|---|---|---|---|")
    for r in table:
        lines.append(f"| {r['entry_dt']} | {r['level']} | {r['caught_realtime']} | "
                     f"{r['caught_delayed']} | {r['llm_verdict_differs']} | {r['notes'][:40]} |")
    md = '\n'.join(lines) + '\n'
    open(OUT_DIR / 'summary.md', 'w', encoding='utf-8').write(md)

    # ── Console ──
    print("=" * 68)
    print("  JUNE-WINDOW BACKTEST SUMMARY — 15-MIN DELAY IMPACT")
    print("=" * 68)
    print(f"  Confirmed real entries : {len(table)}")
    print(f"  Not evaluable          : {len(not_eval)}")
    print(f"  Evaluable              : {n_eval}")
    print(f"  Captured real-time     : {n_rt}/{n_eval} = {rt_pct:.0f}%")
    print(f"  Captured delayed       : {n_dl}/{n_eval} = {dl_pct:.0f}%")
    print(f"  Delay-explained gap    : {delay_gap:.0f} percentage points")
    print(f"  Unexplained (RT missed): {unexplained:.0f}%")
    print("-" * 68)
    print(f"  📄 {OUT_DIR}/summary.md")
    print(f"  📄 {OUT_DIR}/crossref_table.csv")
    print("=" * 68)


if __name__ == '__main__':
    main()
