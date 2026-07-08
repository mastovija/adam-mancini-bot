"""
scrapers/format_examples.py — Phase 3.2: build the few-shot block
=================================================================
Turns examples.json (distilled real setups) into examples.md, a compact few-shot
block grouped into TAKE (A+/good) vs SKIP/DOWN-RATE (low-quality/avoid). This is
appended to the cached methodology system prompt so the decision model reasons by
precedent — take the deep Failed Breakdown of a genuine significant low, skip the
mid-range / tested-to-death chop (the exact call it got wrong live).

USAGE   python scrapers/format_examples.py
OUTPUT  knowledge_base/methodology/examples.md
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / 'knowledge_base' / 'methodology' / 'examples.json'
OUT = ROOT / 'knowledge_base' / 'methodology' / 'examples.md'


def line(e):
    lvl = e.get('level')
    parts = [e.get('date', ''), e.get('setup', ''), f"@{lvl:g}" if isinstance(lvl, (int, float)) else '']
    meta = []
    if e.get('significant_low') and e['significant_low'] != 'n/a':
        meta.append(e['significant_low'])
    if e.get('flush_pts'):
        meta.append(f"{e['flush_pts']:g}pt flush")
    head = ' · '.join(p for p in parts if p)
    if meta:
        head += ' · ' + ', '.join(meta)
    outcome = (e.get('outcome', '') or '').rstrip('. ')
    return f"- {head} → {outcome}. {e.get('lesson','')}"


def main():
    ex = [e for e in json.load(open(EX, encoding='utf-8'))
          if e.get('level') and e.get('quality') in ('A+', 'good', 'low-quality', 'avoid')]
    take = [e for e in ex if e['quality'] in ('A+', 'good')]
    skip = [e for e in ex if e['quality'] in ('low-quality', 'avoid')]
    take.sort(key=lambda e: e.get('date', ''))
    skip.sort(key=lambda e: e.get('date', ''))

    md = []
    md.append("# Worked examples from your own recent sessions (reason by precedent)")
    md.append("")
    md.append("These are REAL setups from your newsletters. Weight the current decision "
              "toward the patterns you TAKE and away from the ones you SKIP. The most "
              "common live mistake is entering mid-range / tested-to-death chop instead of "
              "the deep Failed Breakdown of a genuine significant low — these examples are "
              "the corrective.")
    md.append("")
    md.append("## Setups you TAKE — deep Failed Breakdowns of a genuine significant low")
    md += [line(e) for e in take]
    md.append("")
    md.append("## Setups you SKIP or size down — chop, mid-range, tested-to-death, shallow-in-chop")
    md += [line(e) for e in skip]
    md.append("")
    md.append("Rule of thumb from these: the freshest, deepest flush of a genuine "
              "significant low (prior-day / multi-hour / cluster / range low) with acceptance "
              "is the trade. A minor level in the middle of a range that has already been "
              "tapped repeatedly today is NOT — even if a small failed breakdown prints there.")

    OUT.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print(f"✅ few-shot block → {OUT}  ({len(take)} take, {len(skip)} skip examples)")


if __name__ == '__main__':
    main()
