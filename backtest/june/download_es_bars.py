"""
backtest/june/download_es_bars.py — Download real ES 1-min bars for the window
==============================================================================
Downloads 1-minute ES futures bars for every trading day in the backtest window
(Jun 16 → Jul 6, 2026) directly from IBKR and stores one JSON per day. These are
REAL ES-point bars — the whole reason we do NOT reuse the Phase-7 SPY×10 data
(that multiplicative proxy drops the drifting cost-of-carry basis; see the plan).

SINGLE CONTRACT
---------------
The window is entirely AFTER the June front-month roll, so every day uses one
contract: ESU2026 (`202609`), which is the IBKR_ES_EXPIRY default in config.py.
No roll splice, no expired-contract data needed.

REUSE
-----
Same connection recipe as market_data/ibkr_feed.py (connectAsync,
reqMarketDataType(3), qualifyContractsAsync). We do NOT reuse ESFeed.get_bars()
because it always ends at "now"; a backtest needs an explicit per-day
endDateTime, so we call reqHistoricalDataAsync directly here.

PREREQUISITE
------------
IB Gateway must be running in paper mode on port 4002 (config.IBKR_PORT).
If it is offline this script exits with a clear message and downloads nothing.

TIMEZONE
--------
IBKR returns bar times in CME/Chicago exchange time; we convert to
America/New_York (Adam's session is defined in NY time) and store NAIVE NY
wall-clock strings ('YYYY-MM-DD HH:MM:SS') to avoid the known UTC-aware pandas
bug downstream.

DURATION (IBKR gotcha)
----------------------
We request '2 D' ending at 20:00 NY on the target day, NOT '1 D'. With '1 D',
when the end time falls in the evening globex IBKR anchors the window to the
session open and truncates to a couple of hours (empirically ~120 bars). '2 D'
reliably spans the full target session plus the prior trading day, giving the
harness a realistic 15-min lookback at the 07:30 open (production's useRTH=False
feed sees overnight bars too). Coverage of the 07:30–16:00 NY window is then
validated per day; any day missing session bars is flagged, not trusted.

OUTPUT
------
data/backtest/es_bars/<YYYY-MM-DD>.json   (list of OHLCV dicts, oldest→newest)
data/backtest/es_bars/index.json          (list of days successfully saved)
"""

import asyncio
import json
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ES_EXPIRY, MARKET_TIMEZONE, DATA_DIR,
)

# Reuse the exact trading-day list the rest of the backtest uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from levels_loader import TRADING_DAYS  # noqa: E402

try:
    from ib_insync import IB, Future
except ImportError:
    print("❌ ib_insync not installed. Run: pip install ib_insync")
    sys.exit(1)

OUT_DIR = DATA_DIR / 'backtest' / 'es_bars'
NY = pytz.timezone(MARKET_TIMEZONE)


def _gateway_reachable() -> bool:
    """Quick TCP probe so we fail fast with a helpful message if Gateway is down."""
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((IBKR_HOST, IBKR_PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _normalize_bar(b) -> dict | None:
    """Convert an ib_insync BarData to our dict with a naive NY timestamp."""
    if b.open <= 100 or b.close <= 100:      # skip empty/placeholder bars
        return None
    d = b.date
    if isinstance(d, datetime):
        dt_ny = d.astimezone(NY) if d.tzinfo else NY.localize(d)
    else:                                    # a plain date (shouldn't happen for 1-min)
        return None
    return {
        'timestamp': dt_ny.replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'),
        'open': float(b.open), 'high': float(b.high),
        'low': float(b.low), 'close': float(b.close),
        'volume': int(b.volume),
    }


async def _download_day(ib: IB, contract, day: str) -> list[dict]:
    """Download one trading day's 1-min ES bars (incl. overnight/pre-market)."""
    end_ny = NY.localize(datetime.strptime(day + ' 20:00:00', '%Y-%m-%d %H:%M:%S'))
    try:
        raw = await ib.reqHistoricalDataAsync(
            contract=contract,
            endDateTime=end_ny,          # tz-aware → ib_insync anchors correctly
            durationStr='2 D',           # '1 D' truncates in the evening globex; see module docstring
            barSizeSetting='1 min',
            whatToShow='TRADES',
            useRTH=False,                # include Adam's 7:30 pre-market window
            formatDate=1,
            keepUpToDate=False,
        )
    except Exception as e:
        print(f"    ⚠️  request error: {e}")
        return []

    bars = [nb for nb in (_normalize_bar(b) for b in raw) if nb]
    return bars


# Adam's decision window is 07:30–16:00 NY = 510 minutes. We require most of it.
SESSION_START = '07:30:00'
SESSION_END = '16:00:00'
MIN_SESSION_BARS = 480          # ≥ ~94% of the 510-minute window present


def _session_coverage(day: str, bars: list[dict]) -> dict:
    """Measure 07:30–16:00 NY coverage for the target day (sanity + validation)."""
    session = [b for b in bars
               if f"{day} {SESSION_START}" <= b['timestamp'] <= f"{day} {SESSION_END}"]
    if not session:
        return {'n_session': 0, 'ok': False, 'summary': 'no in-session bars ⚠️'}
    lo = min(b['low'] for b in session)
    hi = max(b['high'] for b in session)
    first = session[0]['timestamp'][11:16]
    last = session[-1]['timestamp'][11:16]
    ok = len(session) >= MIN_SESSION_BARS and first <= '07:35'
    return {
        'n_session': len(session), 'ok': ok,
        'first': first, 'last': last, 'lo': lo, 'hi': hi,
        'summary': f"{len(session)} session bars {first}-{last} | ES range {lo:.0f}-{hi:.0f}",
    }


async def _run():
    print("=" * 68)
    print(f"  Download ES 1-min bars — ES {IBKR_ES_EXPIRY} — {TRADING_DAYS[0]} → {TRADING_DAYS[-1]}")
    print("=" * 68)

    if not _gateway_reachable():
        print(f"❌ IB Gateway not reachable at {IBKR_HOST}:{IBKR_PORT}.")
        print("   Start IB Gateway (paper trading, API enabled) and re-run.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ib = IB()
    await ib.connectAsync(host=IBKR_HOST, port=IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=15)
    ib.reqMarketDataType(3)

    qualified = await ib.qualifyContractsAsync(
        Future(symbol='ES', exchange='CME', currency='USD',
               lastTradeDateOrContractMonth=IBKR_ES_EXPIRY)
    )
    if not qualified:
        print(f"❌ Could not qualify ES {IBKR_ES_EXPIRY}.")
        ib.disconnect()
        sys.exit(1)
    contract = qualified[0]
    print(f"✅ Contract: {contract.localSymbol} (expires {contract.lastTradeDateOrContractMonth})\n")

    manifest: dict[str, dict] = {}
    for day in TRADING_DAYS:
        out_path = OUT_DIR / f"{day}.json"
        print(f"  {day} | requesting...", flush=True)
        bars = await _download_day(ib, contract, day)
        if not bars:
            print(f"  {day} | ❌ no data returned")
            manifest[day] = {'n_bars': 0, 'ok': False, 'summary': 'no data'}
            continue

        cov = _session_coverage(day, bars)
        json.dump(bars, open(out_path, 'w'))
        flag = '✅' if cov['ok'] else '⚠️ REDUCED'
        print(f"  {day} | {flag} {len(bars)} bars | {cov['summary']}")
        manifest[day] = {'n_bars': len(bars), **{k: cov[k] for k in cov if k != 'summary'}}
        await asyncio.sleep(1.5)          # gentle pacing between requests

    # Manifest = the source of truth for which days the harness may evaluate.
    json.dump(manifest, open(OUT_DIR / 'manifest.json', 'w'), indent=2)
    good = [d for d, m in manifest.items() if m.get('ok')]
    json.dump(sorted(good), open(OUT_DIR / 'index.json', 'w'), indent=2)
    ib.disconnect()

    print("\n" + "=" * 68)
    print(f"✅ Done — {len(good)}/{len(TRADING_DAYS)} days with FULL session coverage")
    reduced = [d for d, m in manifest.items() if not m.get('ok')]
    if reduced:
        print(f"⚠️  Reduced/again-needed days: {reduced}")
    print(f"📁 {OUT_DIR}  (manifest.json = per-day coverage)")
    print("=" * 68)


if __name__ == '__main__':
    asyncio.run(_run())
