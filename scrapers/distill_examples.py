"""
scrapers/distill_examples.py — Phase 3.1: distill recaps into few-shot examples
===============================================================================
Turns the raw newsletter recap sections (recaps_raw.json) into compact, STRUCTURED
examples of Adam's real trades — the few-shot library the decision model reasons
from. Uses Haiku (cheap) for extraction; the result is hand-reviewable JSON.

Each example captures the discrimination that matters: was this an A+ Failed
Breakdown of a genuine significant low (take it), or a shallow / tested-to-death
mid-range level (skip or down-rate) — exactly the call the bot got wrong live.

USAGE   python scrapers/distill_examples.py
OUTPUT  knowledge_base/methodology/examples.json   (list of structured examples)
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anthropic
from config import ANTHROPIC_API_KEY, LLM_MODEL

RECAPS = ROOT / 'knowledge_base' / 'methodology' / 'recaps_raw.json'
OUT = ROOT / 'knowledge_base' / 'methodology' / 'examples.json'

PROMPT = """Below is Adam Mancini's own "recap" section from his ES futures newsletter,
where he walks through real examples of his setups from recent days.

Extract each CONCRETE setup he describes. For each, output an object:
{{
  "setup": "Failed Breakdown" | "Level Reclaim" | "Back-test" | "Breakdown short" | "No-trade (chop)",
  "level": <ES price number, or null>,
  "quality": "A+" | "good" | "low-quality" | "avoid",
  "significant_low": "prior-day low" | "multi-hour low" | "cluster/shelf" | "range low" | "n/a",
  "flush_pts": <approx points of the flush, or null>,
  "outcome": "<≤12 words: what happened — worked / failed / he skipped it>",
  "lesson": "<≤25 words, in Adam's voice: WHY it qualified or should be avoided — cite significant-low type, flush, acceptance, or tested-to-death/chop>"
}}

Rules:
- Only include setups with a concrete level and a clear lesson. Skip vague commentary.
- Mark mid-range, heavily-tested, or shallow-in-chop setups as "low-quality" or "avoid".
- Mark deep Failed Breakdowns of a genuine significant low (prior-day/multi-hour/cluster/range low) as "A+" or "good".

Return ONLY a JSON array (possibly empty). No prose.

RECAP ({date}):
{recap}"""


def distill_one(client, date, recap):
    msg = client.messages.create(
        model=LLM_MODEL, max_tokens=1200,
        messages=[{"role": "user", "content": PROMPT.format(date=date, recap=recap[:6500])}],
    )
    raw = msg.content[0].text.strip()
    i, j = raw.find('['), raw.rfind(']')
    if i == -1 or j == -1:
        return []
    try:
        items = json.loads(raw[i:j + 1])
    except Exception:
        return []
    for it in items:
        if isinstance(it, dict):
            it['date'] = date
    return [it for it in items if isinstance(it, dict) and it.get('level')]


def main():
    recaps = json.load(open(RECAPS, encoding='utf-8'))
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    all_ex = []
    for date in sorted(recaps):
        ex = distill_one(client, date, recaps[date])
        print(f"  {date}: {len(ex)} example(s)")
        all_ex.extend(ex)
    json.dump(all_ex, open(OUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    from collections import Counter
    q = Counter(e.get('quality') for e in all_ex)
    s = Counter(e.get('setup') for e in all_ex)
    print(f"\n✅ {len(all_ex)} examples → {OUT}")
    print("   by quality:", dict(q))
    print("   by setup:  ", dict(s))


if __name__ == '__main__':
    main()
