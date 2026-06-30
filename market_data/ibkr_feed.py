"""
market_data/ibkr_feed.py — ES Futures price feed with IBKR Paper Trading
=============================================================================
Replaces the Alpaca/SPY feed with direct data from the ES futures contract
from Interactive Brokers (paper trading).

WHY IBKR INSTEAD OF ALPACA/SPY:
  - Alpaca gave the SPY price and we multiplied ×10 → noise and imprecision
  - The Alpaca IEX feed has NO pre-market data → we missed Adam's prime
    window (7:30-9:30 AM EST where he places most of his entries)
  - IBKR gives the EXACT ES futures price, with no proxy or conversion
  - IBKR has pre-market data with useRTH=False on historical requests

PREREQUISITES (do once):
  1. Install: pip install ib_insync

  2. Download IB Gateway (lightweight version, doesn't require the full TWS):
     https://www.interactivebrokers.com/en/trading/ibgateway.php

  3. In IB Gateway → settings (gear) → API → Settings:
       Socket port: 4002
       ✅ Allow connections from localhost only
       ❌ Read-Only API (uncheck to remove the error 321 warnings)

  4. Add to .env:
     IBKR_ES_EXPIRY=202609    ← September 2026 (ESU2026, the current front month)

IBKR DURATION UNITS (important):
  IBKR only accepts: S (seconds), D (days), W (weeks), M (months), Y (years)
  It does NOT accept "H" (hours) — that was the bug that failed in reqHistoricalData.
  We use seconds for short durations and days for longer ones.

NOTES ON DATA IN A PAPER ACCOUNT:
  - Paper accounts don't include a real-time data feed
  - reqMarketDataType(3) requests DELAYED data (15 min delay, free)
  - The historical bars from reqHistoricalData are real data with no delay
  - To detect levels with ±3 pts tolerance, delayed is sufficient
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    IBKR_HOST,
    IBKR_PORT,
    IBKR_CLIENT_ID,
    IBKR_ES_EXPIRY,
    MARKET_TIMEZONE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)

try:
    from ib_insync import IB, Future, util
except ImportError:
    print("❌ ib_insync not installed.")
    print("   Run: pip install ib_insync")
    sys.exit(1)


# ─────────────────────────────────────────────
# Market hours
# ─────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Determines whether we're in Adam's trading hours (7:30-16:00 EST).
    ES futures trade almost 24h, but Adam only trades in this range.
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


# ─────────────────────────────────────────────
# ES Futures feed with IBKR
# ─────────────────────────────────────────────

class ESFeed:
    """
    Direct ES Futures feed from IBKR paper trading.

    IMPORTANT — get_bars() is ASYNC:
        With asyncio already running (the bot's normal case), the synchronous
        reqHistoricalData() fails with "This event loop is already running".
        We use reqHistoricalDataAsync() → always call it with 'await':
            bars = await self.feed.get_bars(15, 8)

    LIFECYCLE:
        feed = ESFeed()
        await feed.connect_async()     # once at startup
        snapshot = feed.get_snapshot() # synchronous — reads the cached ticker
        bars = await feed.get_bars()   # async — request to IBKR
        feed.disconnect()              # when shutting down the bot
    """

    def __init__(self):
        self.ib = IB()
        self._contract: Optional[Future] = None
        self._ticker = None
        self._last_update: Optional[datetime] = None
        self._connected = False

    async def connect_async(self):
        """
        Connects to IB Gateway and subscribes to delayed market data.
        Call ONCE at bot startup (from signal_engine.run_loop).
        """
        if self._connected:
            return

        print(f"\n🔌 Connecting to IB Gateway ({IBKR_HOST}:{IBKR_PORT}, clientId={IBKR_CLIENT_ID})...")
        print("   (IB Gateway must be running in paper trading mode)")

        try:
            await self.ib.connectAsync(
                host=IBKR_HOST,
                port=IBKR_PORT,
                clientId=IBKR_CLIENT_ID,
                timeout=15,
            )
        except Exception as e:
            print(f"\n❌ Could not connect to IB Gateway: {e}")
            print(f"   1. IB Gateway running on your Mac (paper trading)")
            print(f"   2. API enabled, port {IBKR_PORT}")
            raise

        # Type 3 = DELAYED (~15 min delay, free with a paper account)
        # Without this → error 354 "Requested market data is not subscribed"
        # Type 1 = LIVE (requires a paid subscription at IBKR)
        self.ib.reqMarketDataType(3)
        print("   📡 Data mode: DELAYED (15 min) — enough for signals")

        # Qualify the contract: IBKR fills in the details (conId, localSymbol, etc.)
        contrato_raw = Future(
            symbol='ES',
            exchange='CME',
            currency='USD',
            lastTradeDateOrContractMonth=IBKR_ES_EXPIRY,  # '202609' = sep 2026
        )

        contratos = await self.ib.qualifyContractsAsync(contrato_raw)
        if not contratos:
            raise ValueError(
                f"IBKR did not find the ES contract {IBKR_ES_EXPIRY}.\n"
                f"Valid format: '202609' (sep 2026), '202612' (dec 2026)"
            )

        self._contract = contratos[0]
        print(f"   ✅ Qualified contract: {self._contract.localSymbol} "
              f"(expires {self._contract.lastTradeDateOrContractMonth})")

        # Subscribe to the real-time tick stream (delayed with type 3)
        # The ticker updates automatically when new ticks arrive
        self._ticker = self.ib.reqMktData(
            self._contract,
            genericTickList='',
            snapshot=False,
            regulatorySnapshot=False,
        )

        print("   ⏳ Waiting for first ticks...")
        await asyncio.sleep(3)

        self._connected = True

        precio = self._get_current_price()
        if precio:
            print(f"   📊 ES price: {precio:.2f} (delayed ~15 min)")
        else:
            print("   ⚠️  No price yet — normal if the market is closed")

        print(f"   ✅ IBKR feed active — {self._contract.localSymbol}\n")

    def disconnect(self):
        """Disconnects cleanly from IB Gateway."""
        if self._connected:
            try:
                if self._contract:
                    self.ib.cancelMktData(self._contract)
            except Exception:
                pass
            self.ib.disconnect()
            self._connected = False
            print("🔌 IBKR disconnected cleanly")

    def _ensure_connected(self) -> bool:
        """Verifies the connection is still active."""
        if not self._connected or not self.ib.isConnected():
            print("  ⚠️  IBKR disconnected")
            self._connected = False
            return False
        return True

    def _get_current_price(self) -> Optional[float]:
        """
        Reads the current price from the cached ticker.
        Order of preference: last (last trade) → close (previous close).
        """
        if not self._ticker:
            return None

        price = self._ticker.last
        if price and not util.isNan(price) and price > 100:
            self._last_update = datetime.now()
            return float(price)

        price = self._ticker.close
        if price and not util.isNan(price) and price > 100:
            return float(price)

        return None

    def get_snapshot(self) -> Optional[dict]:
        """
        Gets the current ES price (SYNCHRONOUS — reads the cached ticker).

        The ticker is updated in the background by ib_insync, so
        get_snapshot() is instant: it just reads the last value received.

        Returns a dict compatible with SPYFeed.get_snapshot():
        {
            'timestamp':       '2026-06-18T10:32:00',
            'spy_price':       750.5,     ← es_price / 10 (compatibility only)
            'es_equivalent':   7505.0,    ← real ES price
            'bar':             {ohlcv},
            'in_market_hours': True
        }
        """
        if not self._ensure_connected():
            return None

        precio_es = self._get_current_price()
        if not precio_es:
            print("  ⚠️  No ES price in the IBKR stream")
            return None

        # Guard: more than 10 min without an update → possible silent disconnect
        if self._last_update:
            edad_seg = (datetime.now() - self._last_update).total_seconds()
            if edad_seg > 600:
                print(f"  ⚠️  Last tick {int(edad_seg/60)} min ago")
                return None

        return {
            'timestamp':     datetime.now().isoformat(),
            'spy_price':       round(precio_es / 10.0, 2),  # legacy compatibility
            'es_equivalent':   precio_es,
            'bar': {
                'open': precio_es, 'high': precio_es,
                'low':  precio_es, 'close': precio_es,
                'volume': 0,
            },
            'in_market_hours': is_market_open(),
        }

    async def get_bars(self, timeframe_minutes: int = 15, n: int = 10) -> list:
        """
        Gets the last N ES bars at the given timeframe.

        ASYNC — call with 'await':
            bars = await self.feed.get_bars(15, 8)

        Uses reqHistoricalDataAsync instead of the synchronous wrapper because
        the event loop is already running when the bot is active.

        VALID IBKR DURATION UNITS: S D W M Y (NOT "H" — hours doesn't exist)
        We use seconds ("S") for short durations, days ("D") for long ones.

        useRTH=False: includes pre-market (7:30-9:30 AM) — CRITICAL for Adam.
        The returned prices are in direct ES points (e.g. 7505.25).
        """
        if not self._ensure_connected():
            return []

        # ── Compute the duration in a valid IBKR format ──────────────────
        # IBKR accepts: {number} S/D/W/M/Y — "H" is NOT valid
        # We use seconds to be precise and not request too much
        # (n*2 to leave margin for empty bars from closed-market gaps)
        segundos_necesarios = timeframe_minutes * n * 2 * 60

        if segundos_necesarios <= 86400:        # up to 1 day → use seconds
            duration = f"{segundos_necesarios} S"
        elif segundos_necesarios <= 86400 * 5:  # up to 5 days → use days
            dias = (segundos_necesarios // 86400) + 1
            duration = f"{dias} D"
        else:                                   # more than 5 days → weeks
            semanas = (segundos_necesarios // (86400 * 7)) + 1
            duration = f"{semanas} W"

        # ── Bar format for IBKR ───────────────────────────────────────────
        # IBKR accepts: "1 min", "5 mins", "15 mins", "1 hour", "1 day", etc.
        if timeframe_minutes == 1:
            bar_size = "1 min"
        elif timeframe_minutes < 60:
            bar_size = f"{timeframe_minutes} mins"
        elif timeframe_minutes == 60:
            bar_size = "1 hour"
        else:
            bar_size = f"{timeframe_minutes // 60} hours"

        try:
            # reqHistoricalDataAsync → awaitable, doesn't block the event loop
            # endDateTime='' → up to "now"
            # whatToShow='TRADES' → real trade prices
            # useRTH=False → INCLUDES pre-market (essential for Adam 7:30-9:30 AM)
            # keepUpToDate=False → one-off request, not a continuous subscription
            bars_ibkr = await self.ib.reqHistoricalDataAsync(
                contract=self._contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=False,
                formatDate=1,
                keepUpToDate=False,
            )

            if not bars_ibkr:
                return []

            bars = [
                {
                    'open':      float(b.open),
                    'high':      float(b.high),
                    'low':       float(b.low),
                    'close':     float(b.close),
                    'volume':    int(b.volume),
                    'timestamp': str(b.date),
                }
                for b in bars_ibkr
                if b.open > 100 and b.close > 100  # filter out empty bars
            ]

            return bars[-n:]  # last N, oldest to newest

        except Exception as e:
            print(f"  ⚠️  Error fetching IBKR {timeframe_minutes}min bars: {e}")
            return []

    def spy_to_es(self, price: float) -> float:
        """With IBKR the price is already ES. Kept for compatibility."""
        return float(price)


# ─────────────────────────────────────────────
# Standalone connection test
# ─────────────────────────────────────────────

async def _test_feed():
    """
    Tests the connection with IB Gateway and shows ES data.
    Run with: python market_data/ibkr_feed.py
    """
    print("=" * 60)
    print("  IBKR Feed Test — ES Futures")
    print("=" * 60)
    print(f"⏰ Market (Adam window): {'🟢 OPEN' if is_market_open() else '🔴 CLOSED'}")
    print(f"📋 Target contract: ES {IBKR_ES_EXPIRY}")

    feed = ESFeed()

    try:
        await feed.connect_async()

        print("\n📊 Fetching current snapshot...")
        snapshot = feed.get_snapshot()  # synchronous

        if snapshot:
            print(f"\n✅ Snapshot OK:")
            print(f"   ES price:    {snapshot['es_equivalent']:.2f} points")
            print(f"   Timestamp:   {snapshot['timestamp'][:19]}")
        else:
            print("⚠️  No snapshot (market closed or no recent ticks)")

        print(f"\n📈 Last 5 15-minute bars (pre-market included):")
        bars = await feed.get_bars(15, 5)
        if bars:
            for b in bars:
                print(f"   {b['timestamp']} | "
                      f"O:{b['open']:.2f}  H:{b['high']:.2f}  "
                      f"L:{b['low']:.2f}  C:{b['close']:.2f}  "
                      f"Vol:{b['volume']:,}")
        else:
            print("   (No bars)")

        print(f"\n📈 Last 3 1-minute bars:")
        bars_1m = await feed.get_bars(1, 3)
        if bars_1m:
            for b in bars_1m:
                print(f"   {b['timestamp']} | C:{b['close']:.2f}")
        else:
            print("   (No 1-minute bars)")

    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        feed.disconnect()

    print("\n✅ Test complete")


if __name__ == '__main__':
    asyncio.run(_test_feed())
