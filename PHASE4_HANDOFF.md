# Adam Mancini Bot — Phase 4 Handoff

You are continuing a multi-phase project to build an LLM "clone" of trader Adam
Mancini that watches ES futures live and sends Telegram entry signals (with
stop/targets) in his exact style. Phases 0–3 are done; **you are starting Phase 4
(Calibration & Validation)**, plus two deterministic fixes surfaced by live
trading. Read this whole file first, then the key files it names.

Repo: `/Users/user/Developer/adam-mancini-bot` (Python, venv at `venv/`).

## What the bot is / how it runs

- `python main.py` runs everything: parses today's newsletter → `data/daily/today.json`,
  connects to IBKR Gateway (paper, port 4002, **15-min delayed** data, contract
  ESU2026 / `IBKR_ES_EXPIRY=202609`), runs the signal engine every 60s + a tweet
  monitor, sends Telegram alerts.
- **Decision path** (`signals/signal_engine.py`): each minute, for levels near
  price it runs `detect_failed_breakdown()` (structural filter), and when a Failed
  Breakdown fires at/near a level (see Phase 1.3 engagement zone) it calls
  `generar_señal_llm()` → **Claude Sonnet 5** (`LLM_DECISION_MODEL`) with a cached
  system prompt = methodology rubric + few-shot examples. A signal is SENT only if
  `entrar==True AND confianza >= MIN_SIGNAL_CONFIDENCE (0.6)`.
- Cheap tasks (newsletter level extraction, tweet classification) still use Haiku
  (`LLM_MODEL`).

## What's already built (Phases 0–3)

- **Phase 0 — data:** full 2026 newsletter archive (`data/raw/newsletter/`, scraper
  `scrapers/substack_scraper.py`, cookies in `.env` valid), tweets
  (`data/raw/tweets/adam_mancini_tweets.json`), his methodology doc
  (`knowledge_base/methodology/fundamentals.txt`, refetch via
  `scrapers/fetch_methodology.py`).
- **Phase 1 — methodology brain:** `knowledge_base/methodology/rubric.md`
  (distilled from his real doc) replaced the old hardcoded paraphrase; it's a
  cached system prompt. `detect_failed_breakdown` significant-low broadened, and
  an **engagement zone** (`FB_ENTRY_ZONE_PTS=15`) so the bot keeps evaluating a
  level through the acceptance window (price 5–15 pts above the low), not just
  ±3pts. Validated: recovers the Jun 26 7405 trade.
- **Phase 2 — model + gate:** decision upgraded to Sonnet 5; **confidence gate**
  (`MIN_SIGNAL_CONFIDENCE`) + **near-miss logging** to `data/near_misses.jsonl`.
- **Phase 3 — reason-by-example:** `knowledge_base/methodology/examples.md`
  (few-shot block of real setups distilled from his newsletter "Recap/Daily
  Summary" sections via `scrapers/extract_examples.py` → `distill_examples.py` →
  `format_examples.py`) appended to the cached system prompt.

## Backtest / calibration tooling (already built — Phase 4 builds on this)

`backtest/june/`:
- `download_es_bars.py` — downloads real 1-min ES bars per day via IBKR (needs
  Gateway running). Data in `data/backtest/es_bars/` (Jun 16–Jul 6 present).
- `levels_loader.py` — per-day newsletter levels (Spanish keys: `soportes`,
  `resistencias`, `nivel_critico`, `content_plan`, `bias`), with fallback.
- `extract_real_entries.py` — heuristic "real entries" CSV from tweets (deliverable 1).
- `backtest_harness.py` — replays the REAL production decision code minute-by-minute
  (real-time vs 15-min-delayed), FB-gated LLM calls, writes
  `data/backtest_june/signals_{realtime,delayed}.csv`. `--no-llm` dry-run counts calls.
  `--days YYYY-MM-DD,...` or `--day`.
- `crossref_and_summary.py` — matches signals to real entries, writes `summary.md`.

**Cost note:** the harness uses `generar_señal_llm` → Sonnet 5. For cheap
calibration set env `LLM_DECISION_MODEL=claude-haiku-4-5` (the user is
credit-conscious — always estimate LLM calls and confirm before big runs).

## CRITICAL context — what live trading (Jul 8) revealed (this is what Phase 4 must fix)

Live paper day: **3 long signals, 1 small win + 2 stop-outs = net-losing day**,
while Adam had a green day. Root causes, in priority order:

1. **It traded mid-range chop and missed the A+ low.** Adam's winning trade was the
   deep Failed Breakdown of the day's significant low (**7482**). The bot never
   took it (suppressed early at 0.55 before acceptance), then entered the
   **mid-range** levels above it (7506/7511/7521) — the "tested-to-death" chop his
   method says to avoid. Clean A/B (Phase 3.3): a deep FB of the day's low
   reconstructs at **0.75 confidence**; the 7511 mid-range chop at **0.65** — both
   clear the 0.60 gate, so the bad trades still fire. Few-shot narrowed but did NOT
   close this gap.
2. **It broke his hard rules.** After a winning first trade it even sent
   "First trade is a win — stop trading" to Telegram… then took a 2nd morning trade
   (and lost). His rules ("first win → stop until 2pm", "1–3 trades max, 2nd only
   after a loss", "never go red") are **not enforced in code** — left to the LLM,
   which ignored them.
3. **Data bug:** during active trades the log spammed `Error 162 HMDS "query
   returned no data: ESU6"` — the 1-min bar fetch for trade management
   (`get_bars(1,3)` in `_gestionar_trade_activo`) fails on the paper account, so
   stop/T1 detection ran on spot price only.

## Phase 4 — Calibration & Validation (your task)

Goal: make the bot *measurably* match his real trades before it's trusted, and fix
the failure modes above.

**STATUS (as of this commit):** the free / no-credit work is DONE and committed;
only the paid Sonnet backtest + the final threshold pick remain.

1. **Confidence-threshold calibration.** ⏳ **DEFERRED — needs credits.** Must be
   run on **Sonnet 5** (production's model), NOT Haiku — `confianza` is
   model-self-reported and doesn't transfer across models. Full 13-day both-view
   run ≈ 764 Sonnet calls (~$15–19 on the intro pricing); real-time-only ≈ $8–10;
   a focused ~5-day RT subset ≈ $3–4. The scorer's LLM precision/recall section
   (`backtest/june/score_vs_ground_truth.py`) lights up once `signals_realtime.csv`
   has real `entrar` verdicts. Don't guess the threshold — the level veto below is
   the real chop fix; the threshold is a secondary lever.
2. **Deterministic level-ranking veto (code):** ✅ **DONE.**
   `signals.signal_engine.update_significant_low` / `is_midrange_chop_veto`
   (`DEEP_FLUSH_PTS=20`, `MIDRANGE_BUFFER_PTS=10`), mirrored in the harness.
   Validated free: 70 chop vetoes over 13 days, **0 false vetoes** on Adam's real
   entries, Jun-26 7405 winner spared.
3. **Deterministic day-state rules (code):** ✅ **DONE.** Day-state machine in
   `SignalEngine` (`_entrada_permitida_por_estado` / `_registrar_resultado_trade`):
   cap 3/day, first WIN → stop until the post-2pm session, first non-win → one
   retry only ("never more"). From Adam's own methodology. Covered by
   `tests/test_phase4_deterministic.py` (incl. the exact July 8 violation).
4. **Fix the Error 162 data issue:** ✅ **DONE & verified LIVE.**
   `market_data/ibkr_feed.py` floors the 1-min window past the 15-min delay gap
   (`get_bars(1,3)`: 6min → 23min). Reproduced the exact error with the old window,
   confirmed 14 bars with the new one against the live gateway.
5. **Ground-truth + scoring:** ✅ **DONE (free part).** Hand-reviewed real entries in
   `backtest/june/ground_truth_entries.csv` + `GROUND_TRUTH.md` (tracked); scorer
   `backtest/june/score_vs_ground_truth.py`. Key finding: only ~5–7 of his entries
   are RTH-intraday-evaluable; many are overnight / post-2pm and structurally out
   of the RTH window's reach. Structural-recall ceiling ≈ 5/7; the 2 misses
   (6/22 7528, 7/7 7540) are Phase-1 **detector** gaps, not threshold/LLM.

**Remaining free work not yet done:** wiring the day-state rules + a deterministic
trade-outcome simulator into `backtest_harness.py` so the *paid* backtest reflects
Adam's trade cap and computes real precision/recall — best built alongside the paid
run (step 1), its only consumer.

## Gotchas

- IBKR Gateway must be running (paper, 4002) for any bar download or live run; it's
  often OFF between sessions — check first (`socket connect 127.0.0.1:4002`).
- Timezone: newsletter/levels are NY time; IBKR bars come in Chicago time (convert
  to NY, store tz-naive — past bugs from UTC-aware pandas).
- Daily JSON uses **Spanish keys** (`soportes`/`resistencias`/`nivel_critico`/
  `content_plan`); `get_all_levels()` reads them — keep it.
- today.json `date` is the newsletter's *publish* date (e.g. "07-07" = the July 8
  plan); that's expected.
- The user works step-by-step, is credit-conscious (estimate LLM spend first), and
  values honest assessments over optimistic ones.

Full plan/roadmap context is in the conversation history; Phases 5 (production
hardening — mostly exists) and 6 (paper-trade discipline) come after Phase 4.
