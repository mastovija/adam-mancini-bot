"""
backtest/download_data.py — Downloads historical SPY data from Alpaca
=========================================================================
Downloads 1-minute SPY bars for the period we have tweets for
(Feb 26 - Jun 6, 2026) and saves them locally for backtesting.

USAGE:
    python backtest/download_data.py

RESULT:
    data/backtest/spy_bars/YYYY-MM-DD.json   (one file per trading day)
    data/backtest/spy_bars/index.json         (list of available days)
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.append(str(Path(__file__).parent.parent))

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, MARKET_TICKER, DATA_DIR

try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except ImportError:
    print("❌ alpaca-py not installed. Run: pip install alpaca-py")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
BACKTEST_DIR  = DATA_DIR / 'backtest'
BARS_DIR      = BACKTEST_DIR / 'spy_bars'

# Period to download: matches the tweet period we have
# Adjust these dates to fit your dataset
DATE_START = datetime(2026, 2, 26, tzinfo=pytz.UTC)
DATE_END   = datetime(2026, 6, 7,  tzinfo=pytz.UTC)

# Alpaca rate-limits requests — we download week by week
CHUNK_DAYS = 7


# ─────────────────────────────────────────────
# Functions
# ─────────────────────────────────────────────

def es_dia_laborable(dt: datetime) -> bool:
    """Checks whether a day is Monday to Friday."""
    return dt.weekday() < 5


def bars_to_list(bars) -> list:
    """
    Converts Alpaca's bars object to a serializable list of dicts.
    Handles different versions of alpaca-py.
    """
    result = []
    try:
        bar_list = list(bars[MARKET_TICKER])
    except (KeyError, TypeError):
        try:
            bar_list = list(bars.data.get(MARKET_TICKER, []))
        except Exception:
            return []

    for bar in bar_list:
        result.append({
            'timestamp': bar.timestamp.isoformat(),
            'open':      float(bar.open),
            'high':      float(bar.high),
            'low':       float(bar.low),
            'close':     float(bar.close),
            'volume':    int(bar.volume),
        })
    return result


def descargar_dia(client, fecha: datetime) -> list:
    """
    Downloads the 1-minute SPY bars for a specific day.

    Args:
        fecha: date in UTC (only the date is used, not the time)

    Returns:
        List of OHLCV bars, or an empty list on error
    """
    # Market hours in UTC: 9:30 - 16:00 EST = 13:30 - 20:00 UTC
    # We request a little more just in case
    start = fecha.replace(hour=13, minute=0, second=0)
    end   = fecha.replace(hour=21, minute=0, second=0)

    try:
        request = StockBarsRequest(
            symbol_or_symbols = MARKET_TICKER,
            timeframe          = TimeFrame(1, TimeFrameUnit.Minute),
            start              = start,
            end                = end,
            feed               = 'iex',
        )
        bars_data = client.get_stock_bars(request)
        return bars_to_list(bars_data)
    except Exception as e:
        print(f"  ⚠️  Error downloading {fecha.date()}: {e}")
        return []


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def download_historical_data():
    """
    Downloads historical SPY data for the entire backtesting period.
    Saves one JSON per trading day.
    """
    print("=" * 55)
    print("  Backtesting — Download Historical SPY Data")
    print("=" * 55)
    print(f"📅 Period: {DATE_START.date()} → {DATE_END.date()}")
    print(f"📁 Saving to: {BARS_DIR}\n")

    BARS_DIR.mkdir(parents=True, exist_ok=True)

    client = StockHistoricalDataClient(
        api_key    = ALPACA_API_KEY,
        secret_key = ALPACA_SECRET_KEY,
    )

    # Generate the list of weekdays in the period
    dias = []
    current = DATE_START
    while current < DATE_END:
        if es_dia_laborable(current):
            dias.append(current)
        current += timedelta(days=1)

    print(f"📊 Weekdays in the period: {len(dias)}")

    # See which ones we already have downloaded
    existentes = {f.stem for f in BARS_DIR.glob('*.json') if f.stem != 'index'}
    por_descargar = [d for d in dias if str(d.date()) not in existentes]
    print(f"✅ Already downloaded: {len(existentes)}")
    print(f"🆕 To download: {len(por_descargar)}\n")

    if not por_descargar:
        print("✅ All data already downloaded")
        return

    print("📥 Downloading...")
    print("-" * 40)

    descargados = 0
    vacios      = 0

    for i, fecha in enumerate(por_descargar, 1):
        fecha_str = str(fecha.date())
        print(f"  [{i:3d}/{len(por_descargar)}] {fecha_str}... ", end='', flush=True)

        bars = descargar_dia(client, fecha)

        if bars:
            # Save the day's bars
            output = BARS_DIR / f"{fecha_str}.json"
            with open(output, 'w') as f:
                json.dump(bars, f)
            print(f"{len(bars)} bars ✅")
            descargados += 1
        else:
            print("no data (holiday or error)")
            vacios += 1

        # Pause between requests to avoid hammering the API
        if i % 5 == 0:
            time.sleep(1)

    # Save the index of available days
    dias_disponibles = sorted([f.stem for f in BARS_DIR.glob('*.json') if f.stem != 'index'])
    with open(BARS_DIR / 'index.json', 'w') as f:
        json.dump(dias_disponibles, f, indent=2)

    print("\n" + "=" * 55)
    print(f"✅ Download complete")
    print(f"   Days with data: {descargados}")
    print(f"   Days without data: {vacios} (holidays/weekends)")
    print(f"   Total on disk: {len(dias_disponibles)} days")
    print(f"📁 Directory: {BARS_DIR}")
    print("=" * 55)


if __name__ == '__main__':
    download_historical_data()
