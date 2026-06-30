"""
market_data/alpaca_feed.py — SPY price feed with Alpaca Markets
====================================================================
Fetches the SPY price in real time using Alpaca's free API.
Kept as a FALLBACK in case the IBKR Gateway has problems.

TO USE ALPACA: set DATA_SOURCE = 'alpaca' in config.py

The IEX feed used by the free account does NOT have a 15-minute delay —
it's a real price but only covers ~2-3% of the volume. To detect whether
the price is near one of Adam's levels (±3 ES points tolerance),
it's completely sufficient.

If you want full SIP data: Alpaca Basic ($9/month)

STRUCTURE OF WHAT IT RETURNS:
    {
        "timestamp":     "2026-06-06T14:32:00",
        "spy_price":     540.27,
        "es_equivalent": 5402.7,      ← SPY * 10 ≈ the ES level Adam mentions
        "bar": {
            "open": 540.10, "high": 540.45,
            "low": 539.95,  "close": 540.27,
            "volume": 123456
        },
        "in_market_hours": True
    }
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    MARKET_TICKER,
    SPY_TO_ES_MULTIPLIER,
    MARKET_TIMEZONE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import (
        StockLatestBarRequest,
        StockBarsRequest,
    )
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except ImportError:
    print("❌ alpaca-py not installed. Run: pip install alpaca-py")
    sys.exit(1)


# ─────────────────────────────────────────────
# Market hours
# ─────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Checks whether the NYSE market is open right now.
    Hours: Monday-Friday, 7:30-16:00 EST (configured in config.py)
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz)

    if ahora.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    apertura = ahora.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
    )
    cierre = ahora.replace(
        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0
    )
    return apertura <= ahora <= cierre


def tiempo_hasta_apertura() -> int:
    """Returns the seconds until the next market open."""
    tz = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz)

    apertura_hoy = ahora.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
    )

    if ahora < apertura_hoy and ahora.weekday() < 5:
        return int((apertura_hoy - ahora).total_seconds())

    dias_hasta = 1
    while True:
        proximo = ahora + timedelta(days=dias_hasta)
        if proximo.weekday() < 5:
            apertura = proximo.replace(
                hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
            )
            return int((apertura - ahora).total_seconds())
        dias_hasta += 1


# ─────────────────────────────────────────────
# SPY price feed
# ─────────────────────────────────────────────

class SPYFeed:
    """
    Fetches SPY price data from Alpaca Markets.
    Fallback when the IBKR Gateway is unavailable.

    NOTE: get_bars() has an ASYNC version for compatibility with signal_engine,
    which uses 'await self.feed.get_bars()' (required by ESFeed/IBKR).
    """

    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise ValueError(
                "Faltan credenciales de Alpaca en .env\n"
                "  ALPACA_API_KEY=...\n  ALPACA_SECRET_KEY=..."
            )
        self.client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

    def get_latest_bar(self) -> Optional[dict]:
        """Fetches the most recent 1-minute SPY bar."""
        try:
            request = StockLatestBarRequest(
                symbol_or_symbols=MARKET_TICKER,
                feed='iex',
            )
            bars = self.client.get_stock_latest_bar(request)
            bar = bars.get(MARKET_TICKER)

            if not bar:
                return None

            return {
                'open':      float(bar.open),
                'high':      float(bar.high),
                'low':       float(bar.low),
                'close':     float(bar.close),
                'volume':    int(bar.volume),
                'timestamp': bar.timestamp.isoformat() if bar.timestamp else '',
            }
        except Exception as e:
            print(f"  ⚠️  Error fetching bar: {e}")
            return None

    def get_recent_bars(self, n: int = 20) -> list:
        """Fetches the last N 1-minute SPY bars."""
        try:
            utc   = pytz.UTC
            start = datetime.now(utc) - timedelta(minutes=n + 10)
            end   = datetime.now(utc)

            request = StockBarsRequest(
                symbol_or_symbols=MARKET_TICKER,
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=start, end=end, feed='iex',
            )
            bars_data = self.client.get_stock_bars(request)
            try:
                bars = list(bars_data[MARKET_TICKER])
            except (KeyError, TypeError):
                bars = list(bars_data.data.get(MARKET_TICKER, []))

            return [
                {
                    'open':      float(b.open),
                    'high':      float(b.high),
                    'low':       float(b.low),
                    'close':     float(b.close),
                    'volume':    int(b.volume),
                    'timestamp': b.timestamp.isoformat() if b.timestamp else '',
                }
                for b in bars[-n:]
            ]
        except Exception as e:
            print(f"  ⚠️  Error fetching recent bars: {e}")
            return []

    def _get_bars_sync(self, timeframe_minutes: int = 15, n: int = 10) -> list:
        """
        Internal synchronous version of get_bars.
        Called from the async get_bars() wrapper and from get_snapshot().
        """
        try:
            utc   = pytz.UTC
            start = datetime.now(utc) - timedelta(minutes=(timeframe_minutes * n) + 30)
            end   = datetime.now(utc)

            request = StockBarsRequest(
                symbol_or_symbols=MARKET_TICKER,
                timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
                start=start, end=end, feed='iex',
            )
            bars_data = self.client.get_stock_bars(request)
            try:
                bars = list(bars_data[MARKET_TICKER])
            except (KeyError, TypeError):
                bars = list(bars_data.data.get(MARKET_TICKER, []))

            return [
                {
                    'open':      float(b.open),
                    'high':      float(b.high),
                    'low':       float(b.low),
                    'close':     float(b.close),
                    'volume':    int(b.volume),
                    'timestamp': b.timestamp.isoformat(),
                }
                for b in bars[-n:]
            ]
        except Exception as e:
            print(f"  ⚠️  Error fetching {timeframe_minutes}min bars: {e}")
            return []

    async def get_bars(self, timeframe_minutes: int = 15, n: int = 10) -> list:
        """
        ASYNC version of get_bars — required by signal_engine which uses 'await'.

        signal_engine calls 'await self.feed.get_bars()' because ESFeed (IBKR)
        needs the async version (it uses reqHistoricalDataAsync internally).
        This wrapper makes SPYFeed compatible without changing signal_engine.

        Alpaca uses synchronous HTTP requests, but wrapping the call in a
        coroutine makes it 'awaitable' without any real behavior change.
        It doesn't block the event loop because the HTTP call is fast (~200ms).
        """
        return self._get_bars_sync(timeframe_minutes, n)

    def spy_to_es(self, spy_price: float) -> float:
        """Converts a SPY price to an ES level (SPY * 10 ≈ ES)."""
        return round(spy_price * SPY_TO_ES_MULTIPLIER, 1)

    def get_snapshot(self) -> Optional[dict]:
        """
        Gets everything the signal engine needs in a single call.
        Includes a stale-price guard: older than 10 min → ignore.
        """
        bar = self.get_latest_bar()
        if not bar:
            return None

        if bar.get('timestamp'):
            try:
                bar_ts = datetime.fromisoformat(bar['timestamp'])
                if bar_ts.tzinfo is None:
                    bar_ts = bar_ts.replace(tzinfo=pytz.UTC)
                edad_segundos = (datetime.now(pytz.UTC) - bar_ts).total_seconds()
                if edad_segundos > 600:
                    print(f"  ⚠️  Stale bar ({edad_segundos/60:.0f} min) — ignoring")
                    return None
            except Exception:
                pass

        spy_price     = bar['close']
        es_equivalent = self.spy_to_es(spy_price)

        return {
            'timestamp':       datetime.now().isoformat(),
            'spy_price':       spy_price,
            'es_equivalent':   es_equivalent,
            'bar':             bar,
            'in_market_hours': is_market_open(),
        }


# ─────────────────────────────────────────────
# Polling loop (legacy)
# ─────────────────────────────────────────────

async def run_market_loop(callback, interval_seconds: int = 60):
    """Market loop for external use with a callback."""
    feed = SPYFeed()
    print(f"📊 Market feed started | {MARKET_TICKER} every {interval_seconds}s")

    while True:
        if not is_market_open():
            espera = min(tiempo_hasta_apertura(), 300)
            print(f"  😴 Market closed — next check in {espera//60} min")
            await asyncio.sleep(espera)
            continue

        snapshot = feed.get_snapshot()
        if snapshot:
            await callback(snapshot)
        else:
            print("  ⚠️  No market data")

        await asyncio.sleep(interval_seconds)


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

def test_feed():
    """
    Tests the connection with Alpaca.
    Run with: python market_data/alpaca_feed.py
    """
    print("=" * 50)
    print("  Alpaca Feed Test — SPY (fallback)")
    print("=" * 50)
    print(f"⏰ Market: {'🟢 OPEN' if is_market_open() else '🔴 CLOSED'}")

    feed = SPYFeed()

    print(f"\n📊 Fetching {MARKET_TICKER} price...")
    snapshot = feed.get_snapshot()

    if snapshot:
        print(f"\n✅ Connection successful")
        print(f"   SPY:           ${snapshot['spy_price']:.2f}")
        print(f"   ES equivalent:  {snapshot['es_equivalent']:.1f}")
        print(f"   Timestamp:     {snapshot['timestamp']}")
    else:
        print("❌ Could not fetch data")
        print("   Check ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")


if __name__ == '__main__':
    test_feed()
