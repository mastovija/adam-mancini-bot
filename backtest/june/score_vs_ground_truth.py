"""
backtest/june/score_vs_ground_truth.py — Phase 4.D scoring
==========================================================
Scores the backtest against a HAND-REVIEWED ground truth of Adam's real entries
(data/backtest_june/ground_truth_entries.csv), not the old heuristic tweet dump.

Two things it reports, both driven off signals_realtime.csv:

  1. STRUCTURAL-RECALL CEILING + VETO SAFETY (works on a --no-llm dry run):
     for each real RTH-intraday entry, did the structural detector fire nearby,
     and — critically — did the Phase 4.A mid-range veto wrongly suppress it?

  2. LLM PRECISION / RECALL (only meaningful after a real LLM run, i.e. rows have
     entrar in {yes,no}): of the real entries, how many did the bot ENTER; of the
     bot's ENTER signals, how many matched a real entry (the rest are false
     positives — especially on the explicit NO-TRADE days).

Level match tolerance ±LEVEL_PTS, time match ±TIME_MIN. Run:
    python backtest/june/score_vs_ground_truth.py
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data' / 'backtest_june'
GT_FILE = Path(__file__).resolve().parent / 'ground_truth_entries.csv'  # curated, tracked
SIG_FILE = OUT / 'signals_realtime.csv'                                 # generated, ignored

LEVEL_PTS = 6.0     # a signal "matches" a real entry if within this many ES points
TIME_MIN = 60       # ...and within this many minutes of the stated entry time


def _load():
    gt = list(csv.DictReader(open(GT_FILE)))
    sig = list(csv.DictReader(open(SIG_FILE))) if SIG_FILE.exists() else []
    return gt, sig


def _sig_time(r):
    return datetime.strptime(r['datetime'], '%Y-%m-%d %H:%M')


def structural_and_veto(gt, sig):
    rth = [g for g in gt if g['entry_type'] == 'rth_intraday']
    print(f"STRUCTURAL-RECALL CEILING + VETO SAFETY  (vs {len(rth)} real RTH entries)")
    print("=" * 78)
    fired = missed = veto_hits = 0
    for g in rth:
        day, lvl, tstr = g['date'], float(g['level']), g['approx_time_est']
        try:
            gt_t = datetime.strptime(f"{day} {tstr}", '%Y-%m-%d %H:%M')
        except ValueError:
            gt_t = None   # 'pre-open' etc. — level-only match
        near = [r for r in sig if r['datetime'][:10] == day
                and abs(float(r['level']) - lvl) <= LEVEL_PTS
                and (gt_t is None or abs((_sig_time(r) - gt_t).total_seconds()) <= TIME_MIN * 60)]
        vetoed = [r for r in near if r['entrar'] == 'veto']
        non_veto = [r for r in near if r['entrar'] != 'veto']
        if not near:
            status = "❌ NO structural trigger (detector gap / no bars)"
            missed += 1
        elif vetoed and not non_veto:
            status = "⛔ VETOED his REAL entry — FALSE VETO"
            veto_hits += 1
        else:
            status = "✅ trigger fired" + ("  (⚠️ also a veto nearby)" if vetoed else "")
            fired += 1
        print(f"  {day} {tstr:>8} lvl {lvl:.0f} {g['setup']:16} -> {status}")
    print("-" * 78)
    print(f"  structural ceiling: {fired}/{len(rth)} fired | {missed} detector-gap/no-bars "
          f"| FALSE VETOES: {veto_hits}")
    print()
    return veto_hits


def llm_precision_recall(gt, sig):
    enters = [r for r in sig if r['entrar'] == 'yes']
    if not enters:
        print("LLM PRECISION/RECALL: skipped — no ENTER signals in signals_realtime.csv")
        print("  (this section needs a real LLM backtest run, not a --no-llm dry run)")
        return
    real = [g for g in gt if g['entry_type'] in ('rth_intraday', 'post_2pm')]
    matched_real = set()
    tp = 0
    for r in enters:
        rt = _sig_time(r)
        hit = None
        for i, g in enumerate(real):
            if g['date'] != r['datetime'][:10] or not g['level']:
                continue
            if abs(float(g['level']) - float(r['level'])) <= LEVEL_PTS:
                hit = i
                break
        if hit is not None:
            tp += 1
            matched_real.add(hit)
    fp = len(enters) - tp
    print("LLM PRECISION / RECALL")
    print("=" * 78)
    print(f"  bot ENTER signals: {len(enters)} | matched real entry (TP): {tp} | "
          f"false positives (FP): {fp}")
    print(f"  real entries captured: {len(matched_real)}/{len(real)}")
    if enters:
        print(f"  precision: {tp/len(enters):.0%}   recall: {len(matched_real)/max(1,len(real)):.0%}")


if __name__ == '__main__':
    gt, sig = _load()
    if not sig:
        print("No signals_realtime.csv — run the backtest harness first.")
        sys.exit(1)
    false_vetoes = structural_and_veto(gt, sig)
    llm_precision_recall(gt, sig)
    # non-zero exit if the veto ever suppressed a real entry (regression guard)
    sys.exit(1 if false_vetoes else 0)
