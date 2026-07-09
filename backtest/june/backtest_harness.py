"""
backtest/june/backtest_harness.py — Deliverable 2: real-time vs delayed replay
==============================================================================
Replays the production detection logic over every minute of every trading day in
the window (Jun 16 → Jul 6, 2026), TWICE:

  a) REAL-TIME   — the engine sees all bars up to the decision minute t
  b) DELAYED     — the engine sees only bars up to t − 15 min (the IBKR paper
                   account's 15-minute delay). The delayed "current price" is the
                   close from 15 min ago, exactly what a delayed feed reports.

The real wall clock is t in BOTH views (only the DATA is delayed, not the clock),
so the trading-window criterion and hora_est are anchored at t for both — matching
production, where `datetime.now()` is the true time while the feed lags.

STRUCTURAL FILTER → LLM GATE
----------------------------
At each (minute, level) we run the reused `detect_failed_breakdown()`. The LLM is
called ONLY when that detector fires (plus the production guards: price at level,
valid long side, not in cooldown). This is a deliberate, cost-driven pre-filter
requested for this task; it is applied IDENTICALLY to both views, so the real-time
vs delayed comparison stays fair. Every other step reuses production code as-is:
`get_all_levels`, `is_price_at_level`, `determinar_lado`,
`confirmar_con_vela_15min`, `generar_señal_llm` (same prompt + content_plan) and
the production cooldown constants.

Cooldowns / active-trade block (per view, mirroring signal_engine.SignalEngine):
  - after an LLM reject:  COOLDOWN_NO_ENTRY_MIN (15 min) on that level
  - after an ENTER:       COOLDOWN_SEÑAL_MIN (60 min) on that level AND a global
                          60-min block on new entries (proxy for "one active
                          trade at a time"; Adam's 2nd trade is a 3pm-window
                          setup hours later, so 60 min never hides it).

OUTPUT (per LLM evaluation, one row each):
  data/backtest_june/signals_realtime.csv
  data/backtest_june/signals_delayed.csv
    columns: datetime, level, tipo, price, fb_flush_pts, fb_desc,
             entrar, confianza, razon
  data/backtest_june/triggers_summary.json  (structural-trigger + LLM-call counts)

USAGE
-----
  python backtest/june/backtest_harness.py            # full run (calls the LLM)
  python backtest/june/backtest_harness.py --no-llm    # dry run: count LLM calls only
  python backtest/june/backtest_harness.py --day 2026-06-25 [--no-llm]
"""

import argparse
import asyncio
import csv
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, MARKET_TIMEZONE, LEVEL_TOLERANCE_POINTS
import signals.signal_engine as se
from signals.signal_engine import (
    get_all_levels, is_price_at_level, determinar_lado,
    detect_failed_breakdown, generar_señal_llm,
    COOLDOWN_SEÑAL_MIN, COOLDOWN_NO_ENTRY_MIN, FB_ENTRY_ZONE_PTS,
    update_significant_low, is_midrange_chop_veto,   # Phase 4.A — mid-range veto
)
import levels_loader

NY = pytz.timezone(MARKET_TIMEZONE)
ES_BARS_DIR = DATA_DIR / 'backtest' / 'es_bars'
TWEETS_FILE = DATA_DIR / 'raw' / 'tweets' / 'adam_mancini_tweets.json'
OUT_DIR = DATA_DIR / 'backtest_june'

SESSION_START = '07:30:00'
SESSION_END = '16:00:00'
NEARBY_POINTS = 60.0          # production's "near price" band in check_once
DELAY_MIN = 15                # IBKR paper-account delay
BLOCK_MINUTES = COOLDOWN_SEÑAL_MIN   # global post-signal block per view


# ─────────────────────────────────────────────
# Simulated clock (so reused window logic uses backtest time, not now())
# ─────────────────────────────────────────────

class _FrozenClock:
    """Drop-in for signal_engine.datetime that pins now() to the backtest minute.

    get_trading_window() calls datetime.now(tz); formatear_tweets_para_prompt()
    calls datetime.strptime(). We serve a fixed now() and delegate everything else
    to the real datetime so the reused window/prompt code runs unchanged.
    """
    _frozen_ny: datetime | None = None

    @staticmethod
    def now(tz=None):
        base = _FrozenClock._frozen_ny
        if tz is None:
            return base.replace(tzinfo=None)
        return base.astimezone(tz)

    strptime = staticmethod(datetime.strptime)
    min = datetime.min


@contextmanager
def freeze_clock(t_ny: datetime):
    """Temporarily anchor signal_engine's clock at t_ny (tz-aware NY)."""
    real = se.datetime
    _FrozenClock._frozen_ny = t_ny
    se.datetime = _FrozenClock
    try:
        yield
    finally:
        se.datetime = real


# ─────────────────────────────────────────────
# Data loading / bar aggregation
# ─────────────────────────────────────────────

def load_day_bars(day: str) -> list[dict]:
    """Load a day's 1-min ES bars (sorted ascending by naive-NY timestamp)."""
    path = ES_BARS_DIR / f"{day}.json"
    if not path.exists():
        return []
    bars = json.load(open(path))
    bars.sort(key=lambda b: b['timestamp'])
    return bars


def build_15m(one_min: list[dict]) -> list[dict]:
    """Aggregate 1-min bars into 15-min OHLC bins aligned to :00/:15/:30/:45.

    The final bin may be in-progress (partial), exactly as IBKR returns the
    current 15-min bar to the live engine.
    """
    bins: dict[str, dict] = {}
    order: list[str] = []
    for b in one_min:
        dt = datetime.strptime(b['timestamp'], '%Y-%m-%d %H:%M:%S')
        key_dt = dt.replace(minute=(dt.minute // 15) * 15, second=0)
        key = key_dt.strftime('%Y-%m-%d %H:%M:%S')
        agg = bins.get(key)
        if agg is None:
            bins[key] = {'open': b['open'], 'high': b['high'], 'low': b['low'],
                         'close': b['close'], 'volume': b['volume'], 'timestamp': key}
            order.append(key)
        else:
            agg['high'] = max(agg['high'], b['high'])
            agg['low'] = min(agg['low'], b['low'])
            agg['close'] = b['close']
            agg['volume'] += b['volume']
    return [bins[k] for k in order]


def load_day_tweets(day: str) -> list[dict]:
    """Tweets posted on `day` (NY date), each tagged with its NY datetime.

    Used to feed the LLM only the tweets known up to the decision minute — tweets
    are not delayed (a separate monitor), so both views see the same set at t.
    """
    tweets = json.load(open(TWEETS_FILE, encoding='utf-8'))
    out = []
    for t in tweets:
        if t.get('is_retweet') or not t.get('created_at'):
            continue
        try:
            dt_utc = datetime.strptime(t['created_at'], '%a %b %d %H:%M:%S +0000 %Y')
        except Exception:
            continue
        dt_ny = pytz.utc.localize(dt_utc).astimezone(NY)
        if dt_ny.strftime('%Y-%m-%d') == day:
            out.append({**t, '_dt_ny': dt_ny})
    out.sort(key=lambda x: x['_dt_ny'])
    return out


# ─────────────────────────────────────────────
# Per-view evaluation
# ─────────────────────────────────────────────

class ViewState:
    """Per-view cooldowns and post-signal block (mirrors SignalEngine state)."""
    def __init__(self):
        self.cooldown: dict[int, datetime] = {}   # level → expiry (NY)
        self.block_until: datetime | None = None  # global new-entry block (NY)
        self.significant_low: dict | None = None  # Phase 4.A — day's deepest-FB low

    def in_cooldown(self, nivel: float, now_ny: datetime) -> bool:
        exp = self.cooldown.get(round(nivel))
        return exp is not None and now_ny < exp

    def set_cooldown(self, nivel: float, now_ny: datetime, minutes: int):
        self.cooldown[round(nivel)] = now_ny + timedelta(minutes=minutes)

    def blocked(self, now_ny: datetime) -> bool:
        return self.block_until is not None and now_ny < self.block_until


async def evaluate_view(view_name, price, bars_15, levels, bias, tweets_upto,
                        t_ny, state: ViewState, today, use_llm, rows, counters):
    """Run the production structural filter + FB-gated LLM for one view at t.

    Appends a CSV row per LLM evaluation (or per structural trigger in --no-llm).
    Returns True if an ENTER signal was produced.
    """
    if state.blocked(t_ny):
        return False

    near = [lv for lv in levels if abs(lv['nivel'] - price) <= NEARBY_POINTS]

    # Phase 4.A — pre-pass: anchor the day's significant low from ALL nearby levels
    # (mirrors production check_once) so the mid-range veto is order-independent.
    for lv in near:
        state.significant_low = update_significant_low(
            state.significant_low, lv['nivel'], detect_failed_breakdown(bars_15, lv['nivel']))

    for lv in near:
        nivel, tipo = lv['nivel'], lv['tipo']
        fb = detect_failed_breakdown(bars_15, nivel)
        # Phase 1.3 engagement zone (mirrors production check_once): engage when
        # price is AT the level OR when a fresh FB fired and price is in the
        # acceptance/entry band just above the low.
        at_level   = is_price_at_level(price, nivel)
        en_zona_fb = fb['es_fb'] and (0 < price - nivel <= FB_ENTRY_ZONE_PTS)
        if not (at_level or en_zona_fb):
            continue
        if state.in_cooldown(nivel, t_ny):
            continue
        direccion = determinar_lado(price, lv, bias)
        if not direccion:
            continue
        if not fb['es_fb']:                 # ← structural gate: LLM only on a real FB
            continue

        counters['triggers'] += 1
        base_row = {
            'datetime': t_ny.strftime('%Y-%m-%d %H:%M'),
            'level': f"{nivel:.0f}", 'tipo': tipo, 'price': f"{price:.1f}",
            'fb_flush_pts': fb.get('flush_size', 0),
            'fb_desc': fb.get('descripcion', '')[:120],
        }

        # Phase 4.A — deterministic mid-range chop veto: suppress BEFORE the LLM.
        # Counted as a trigger, never as an llm_call. Recorded so the replay shows
        # exactly which (level, minute) points were killed.
        if is_midrange_chop_veto(nivel, fb, state.significant_low):
            counters['vetoes'] += 1
            rows.append({**base_row, 'entrar': 'veto', 'confianza': '',
                         'razon': (f"(midrange-veto: above significant low "
                                   f"{state.significant_low['price']:.0f})")})
            state.set_cooldown(nivel, t_ny, COOLDOWN_NO_ENTRY_MIN)
            continue

        if not use_llm:
            # Dry run: count the LLM call that WOULD happen and dedupe with the
            # reject-cooldown so the estimate matches the real cadence.
            counters['llm_calls'] += 1
            rows.append({**base_row, 'entrar': '', 'confianza': '', 'razon': '(dry-run)'})
            state.set_cooldown(nivel, t_ny, COOLDOWN_NO_ENTRY_MIN)
            continue

        counters['llm_calls'] += 1
        with freeze_clock(t_ny):
            _, _, criterio = se.get_trading_window()
            senal = await generar_señal_llm(
                precio_es=price, nivel=nivel, tipo_nivel=tipo, direccion=direccion,
                today=today, bars_15=bars_15, fb_info=fb, tweets=tweets_upto,
                criterio_ventana=criterio,
            )
        entrar = bool(senal.get('entrar'))
        rows.append({**base_row,
                     'entrar': 'yes' if entrar else 'no',
                     'confianza': senal.get('confianza', ''),
                     'razon': (senal.get('razon', '') or '').replace('\n', ' ')[:300]})

        if entrar:
            counters['signals'] += 1
            state.set_cooldown(nivel, t_ny, COOLDOWN_SEÑAL_MIN)
            state.block_until = t_ny + timedelta(minutes=BLOCK_MINUTES)
            return True
        state.set_cooldown(nivel, t_ny, COOLDOWN_NO_ENTRY_MIN)

    return False


# ─────────────────────────────────────────────
# Day / full run
# ─────────────────────────────────────────────

async def run_day(day, today, use_llm, rt_rows, dl_rows, counters):
    one_min = load_day_bars(day)
    if not one_min:
        print(f"  {day} | ❌ no bars — skipped")
        return False
    bias = today.get('bias', 'unknown')
    levels = get_all_levels(today)
    tweets = load_day_tweets(day)

    session = [b for b in one_min
               if f"{day} {SESSION_START}" <= b['timestamp'] <= f"{day} {SESSION_END}"]
    rt_state, dl_state = ViewState(), ViewState()
    day_counters = {'rt': {'triggers': 0, 'llm_calls': 0, 'signals': 0, 'vetoes': 0},
                    'dl': {'triggers': 0, 'llm_calls': 0, 'signals': 0, 'vetoes': 0}}

    for bar in session:
        t_ny = NY.localize(datetime.strptime(bar['timestamp'], '%Y-%m-%d %H:%M:%S'))
        t_str = bar['timestamp']
        tweets_upto = [tw for tw in tweets if tw['_dt_ny'] <= t_ny]

        # ── Real-time view: everything up to t ──
        rt_1m = [b for b in one_min if b['timestamp'] <= t_str]
        rt_15 = build_15m(rt_1m)[-9:]
        rt_price = rt_1m[-1]['close']
        await evaluate_view('rt', rt_price, rt_15, levels, bias, tweets_upto,
                            t_ny, rt_state, today, use_llm, rt_rows, day_counters['rt'])

        # ── Delayed view: only up to t − 15 min ──
        t_delayed = (t_ny - timedelta(minutes=DELAY_MIN)).strftime('%Y-%m-%d %H:%M:%S')
        dl_1m = [b for b in one_min if b['timestamp'] <= t_delayed]
        if dl_1m:
            dl_15 = build_15m(dl_1m)[-9:]
            dl_price = dl_1m[-1]['close']
            await evaluate_view('dl', dl_price, dl_15, levels, bias, tweets_upto,
                                t_ny, dl_state, today, use_llm, dl_rows, day_counters['dl'])

    for v in ('rt', 'dl'):
        for k in ('triggers', 'llm_calls', 'signals', 'vetoes'):
            counters[v][k] += day_counters[v][k]
    print(f"  {day} | RT trig/llm/sig/veto={day_counters['rt']['triggers']}/"
          f"{day_counters['rt']['llm_calls']}/{day_counters['rt']['signals']}/"
          f"{day_counters['rt']['vetoes']}  "
          f"DL={day_counters['dl']['triggers']}/{day_counters['dl']['llm_calls']}/"
          f"{day_counters['dl']['signals']}/{day_counters['dl']['vetoes']}")
    return True


def _write_csv(path: Path, rows: list[dict]):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ['datetime', 'level', 'tipo', 'price', 'fb_flush_pts', 'fb_desc',
              'entrar', 'confianza', 'razon']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-llm', action='store_true', help="Dry run: count LLM calls, don't spend tokens.")
    ap.add_argument('--day', help="Run a single YYYY-MM-DD (must be in the coverage index).")
    ap.add_argument('--days', help="Comma-separated YYYY-MM-DD list, e.g. one week: "
                                   "2026-06-22,2026-06-23,2026-06-24,2026-06-25,2026-06-26")
    args = ap.parse_args()
    use_llm = not args.no_llm

    index_path = ES_BARS_DIR / 'index.json'
    if not index_path.exists():
        print("❌ No es_bars/index.json — run download_es_bars.py first.")
        sys.exit(1)
    good_days = json.load(open(index_path))
    if args.day:
        days = [args.day]
    elif args.days:
        days = [d.strip() for d in args.days.split(',') if d.strip()]
    else:
        days = good_days
    for d in days:
        if d not in good_days:
            print(f"⚠️  {d} is not in the full-coverage index {good_days}")

    window = levels_loader.get_window_levels()

    print("=" * 68)
    print(f"  Backtest harness — {'LLM ON' if use_llm else 'DRY RUN (no LLM)'} — {len(days)} day(s)")
    print("=" * 68)

    rt_rows, dl_rows = [], []
    counters = {'rt': {'triggers': 0, 'llm_calls': 0, 'signals': 0, 'vetoes': 0},
                'dl': {'triggers': 0, 'llm_calls': 0, 'signals': 0, 'vetoes': 0}}

    evaluated_days = []
    for day in days:
        info = window.get(day)
        if not info or not info['levels']:
            print(f"  {day} | ❌ no levels — skipped")
            continue
        ran = await run_day(day, info['levels'], use_llm, rt_rows, dl_rows, counters)
        if ran:
            evaluated_days.append(day)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Always write the signal logs. In --no-llm mode the 'entrar' column is blank
    # (no catches), which lets the cross-ref plumbing be smoke-tested without tokens.
    _write_csv(OUT_DIR / 'signals_realtime.csv', rt_rows)
    _write_csv(OUT_DIR / 'signals_delayed.csv', dl_rows)
    json.dump({'days_evaluated': evaluated_days, 'llm': use_llm, 'counters': counters},
              open(OUT_DIR / 'triggers_summary.json', 'w'), indent=2)

    print("-" * 68)
    print(f"  TOTAL real-time: triggers={counters['rt']['triggers']} "
          f"llm_calls={counters['rt']['llm_calls']} signals={counters['rt']['signals']} "
          f"vetoes={counters['rt']['vetoes']}")
    print(f"  TOTAL delayed:   triggers={counters['dl']['triggers']} "
          f"llm_calls={counters['dl']['llm_calls']} signals={counters['dl']['signals']} "
          f"vetoes={counters['dl']['vetoes']}")
    total_calls = counters['rt']['llm_calls'] + counters['dl']['llm_calls']
    print(f"  → total LLM calls {'made' if use_llm else 'that WOULD be made'}: {total_calls}")
    if use_llm:
        print(f"  📄 {OUT_DIR}/signals_realtime.csv , signals_delayed.csv")
    print("=" * 68)


if __name__ == '__main__':
    asyncio.run(_main())
