# Ground truth — Adam's real entries (Phase 4.D)

Hand-reviewed from Adam's own tweets (`data/raw/tweets/adam_mancini_tweets.json`)
across **2026-06-16 → 2026-07-08**. This replaces the old heuristic
`real_entries_candidates.csv`, which conflated *runner-continuation* and
*target-hit* tweets with actual new entries and produced a meaningless "0/10".

Source of truth: `ground_truth_entries.csv` (this dir). Scorer: `score_vs_ground_truth.py` (this dir).

## How entries were identified

Adam narrates every real entry in his own words ("we swept X, recovered, longed",
"today's trade complete", "X reclaim was the long trigger"). Everything else —
"target hit", "ride runner", "hold runner" — is **continuation of an existing
position, not a new trade**. Days where he says "I don't trade chop / low vol,
holding runner" are recorded as **no-trade** (they are the negative examples).

## What the data shows (the honest picture)

- **He trades ~once a day, and often not at all.** 4 of 13 backtest days are
  explicit *no-trade* days (6/16, 6/18, 6/30, 7/6). If the bot fires on those, it
  is a false positive — exactly the July 8 failure mode.
- **A large share of his entries are OUTSIDE the RTH backtest window (07:30–16:00).**
  Overnight / Sunday-evening entries (6/22 8:04PM, 6/26 pre-open, 6/29 6:05PM) and
  post-2pm entries (6/17 3:45PM, 6/24 3:08PM, 7/2 3:10PM) are how he catches many
  of his best setups. The RTH replay **structurally cannot** capture the overnight
  ones — this caps any achievable "capture rate" and must be stated up front.
- **His setups are Failed Breakdowns and Level Reclaims of a genuine significant
  low**, taken once, then he rides a runner. This is exactly what the Phase 4.A
  veto is built to protect and the mid-range chop it is built to skip.

## Entry inventory (RTH-intraday = what the backtest can evaluate)

| Bucket | Count | Notes |
|---|---|---|
| RTH-intraday (evaluable) | 8 | 6/17, 6/22, 6/23, 6/25, 6/29, 7/1, 7/7, 7/8 |
| post-2pm (session edge, partial) | 3 | 6/17, 6/24, 7/2 |
| overnight / pre-open (NOT evaluable in RTH) | 3 | 6/22, 6/26, 6/29 |
| explicit no-trade (negative examples) | 4 | 6/16, 6/18, 6/30, 7/6 |

## Free validation already run (no credits)

`score_vs_ground_truth.py` against the `--no-llm` structural replay:

- **Veto safety = 100%: 0 false vetoes.** The Phase 4.A mid-range veto did not
  suppress a single one of Adam's real RTH entries — it only killed chop.
- **Structural-recall ceiling ≈ 5/7** (excluding 7/8, which has no bars): the
  detector fired near 5 of 7 real entries; **2 are detector gaps** (6/22 7528,
  7/7 7540) that no threshold or LLM tuning can recover — they are Phase-1
  detector work, tracked separately.

## Still needs credits (deferred)

Picking `MIN_SIGNAL_CONFIDENCE` needs a real Sonnet backtest so the LLM
precision/recall section of the scorer has `entrar` verdicts to score. Estimated
~$3–10 (see PHASE4_HANDOFF.md). Not run yet — this file + the free structural
validation are the prerequisites that make that run interpretable.
