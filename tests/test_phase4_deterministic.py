"""
tests/test_phase4_deterministic.py — Phase 4 deterministic-fix regression tests
==============================================================================
Covers the two code-only fixes surfaced by the July 8 live-trading loss:

  A. Level-ranking veto (signals.signal_engine.update_significant_low /
     is_midrange_chop_veto) — kill tested mid-range chop above the day's
     established significant low, spare the deep-flush A+ low.
  B. Day-state machine (SignalEngine._entrada_permitida_por_estado /
     _registrar_resultado_trade) — enforce Adam's own rules: 1–3 trades/day,
     first WIN → stop until the post-2pm session, first non-win → one retry only.

No pytest dependency — run directly:  python tests/test_phase4_deterministic.py
Exit code 0 = all pass, 1 = a failure (CI-friendly).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import signals.signal_engine as se
from signals.signal_engine import (
    SignalEngine, update_significant_low, is_midrange_chop_veto,
    DEEP_FLUSH_PTS, MIDRANGE_BUFFER_PTS,
)

_failures = []


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        _failures.append(name)


def _fb(flush, es_fb=True):
    return {'es_fb': es_fb, 'flush_size': flush}


def test_veto():
    print("A. Level-ranking veto")
    # July 8 reconstruction: 7482 deep low, then shallow chop above it.
    sl = None
    sl = update_significant_low(sl, 7482, _fb(28))     # deep low anchors
    sl = update_significant_low(sl, 7511, _fb(7))      # shallow must NOT overwrite
    check("deep FB sets significant low to 7482", sl and sl['price'] == 7482)

    def v(level, flush):
        return is_midrange_chop_veto(level, _fb(flush), sl)

    check("7511 shallow chop -> VETO", v(7511, 7) is True)
    check("7506 moderate chop -> VETO", v(7506, 12) is True)
    check("7482 the low itself -> pass", v(7482, 28) is False)
    check("level at low+BUFFER -> pass (boundary)", v(7482 + MIDRANGE_BUFFER_PTS, 5) is False)
    check("higher level w/ fresh DEEP flush -> pass", v(7560, DEEP_FLUSH_PTS) is False)
    check("no significant low yet -> pass",
          is_midrange_chop_veto(7511, _fb(7), None) is False)
    # a lower deep FB later must re-anchor
    sl2 = update_significant_low(sl, 7450, _fb(25))
    check("a lower deep FB re-anchors the significant low", sl2['price'] == 7450)


def _engine():
    """Day-state-only SignalEngine (bypass __init__ to avoid feed/Telegram)."""
    e = object.__new__(SignalEngine)
    e._dia = '2026-07-08'
    e._significant_low = None
    e._trades_hoy = 0
    e._resultados_hoy = []
    e._primer_resultado = None
    return e


def test_day_state():
    print("B. Day-state machine (Adam's rules)")

    def gate(e, hora):
        return e._entrada_permitida_por_estado(hora)[0]

    # First trade WIN -> the exact July 8 violation must now be blocked.
    e = _engine()
    check("n=0 first entry allowed", gate(e, 10.0) is True)
    e._trades_hoy = 1
    e._registrar_resultado_trade('win')
    check("first WIN -> 2nd morning entry BLOCKED [Jul-8 bug]", gate(e, 10.0) is False)
    check("first WIN -> post-2pm entry ALLOWED", gate(e, 14.5) is True)

    # First LOSS -> exactly one retry.
    e = _engine(); e._trades_hoy = 1; e._registrar_resultado_trade('loss')
    check("first LOSS -> 2nd entry ALLOWED", gate(e, 10.0) is True)
    e._trades_hoy = 2; e._registrar_resultado_trade('loss')
    check("loss-day 3rd entry BLOCKED (never more)", gate(e, 10.0) is False)

    # First LOSS, second WIN -> still no 3rd.
    e = _engine(); e._trades_hoy = 1; e._registrar_resultado_trade('loss')
    e._trades_hoy = 2; e._registrar_resultado_trade('win')
    check("loss-then-win -> 3rd still BLOCKED", gate(e, 10.0) is False)

    # Hard cap 3 regardless of outcomes.
    e = _engine(); e._trades_hoy = 3
    e._resultados_hoy = ['scratch'] * 3; e._primer_resultado = 'scratch'
    check("hard cap 3 -> BLOCKED", gate(e, 10.0) is False)

    # Scratch first behaves like a non-win (one retry).
    e = _engine(); e._trades_hoy = 1; e._registrar_resultado_trade('scratch')
    check("first SCRATCH -> retry ALLOWED", gate(e, 10.0) is True)

    # New-day reset clears everything.
    e = _engine(); e._trades_hoy = 2; e._primer_resultado = 'win'
    e._significant_low = {'price': 7482, 'flush': 28}
    e._reset_day_state_if_new_day('2026-07-09')
    check("new-day reset clears trade + significant-low state",
          e._trades_hoy == 0 and e._primer_resultado is None and e._significant_low is None)


if __name__ == '__main__':
    test_veto()
    test_day_state()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL PASS")
