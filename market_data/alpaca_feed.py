"""
market_data/alpaca_feed.py — Feed de precios SPY con Alpaca Markets
====================================================================
Obtiene el precio de SPY en tiempo real usando la API gratuita de Alpaca.
Mantenido como FALLBACK por si IBKR Gateway tiene problemas.

PARA USAR ALPACA: cambiar DATA_SOURCE = 'alpaca' en config.py

El feed IEX que usa la cuenta gratuita NO tiene 15 minutos de delay —
es precio real pero solo cubre ~2-3% del volumen. Para detectar si el
precio está cerca de un nivel de Adam (tolerancia de ±3 puntos ES),
es completamente suficiente.

Si quieres datos SIP completos: Alpaca Basic ($9/mes)

ESTRUCTURA DE LO QUE DEVUELVE:
    {
        "timestamp":     "2026-06-06T14:32:00",
        "spy_price":     540.27,
        "es_equivalent": 5402.7,      ← SPY * 10 ≈ nivel ES que menciona Adam
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
# Horario de mercado
# ─────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Comprueba si el mercado NYSE está abierto ahora mismo.
    Horario: lunes-viernes, 7:30-16:00 EST (configurado en config.py)
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz)

    if ahora.weekday() >= 5:  # sábado=5, domingo=6
        return False

    apertura = ahora.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
    )
    cierre = ahora.replace(
        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0
    )
    return apertura <= ahora <= cierre


def tiempo_hasta_apertura() -> int:
    """Devuelve los segundos hasta la próxima apertura del mercado."""
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
# Feed de precios SPY
# ─────────────────────────────────────────────

class SPYFeed:
    """
    Obtiene datos de precio de SPY desde Alpaca Markets.
    Fallback cuando IBKR Gateway no está disponible.

    NOTA: get_bars() tiene versión ASYNC para compatibilidad con signal_engine,
    que usa 'await self.feed.get_bars()' (requerido por ESFeed/IBKR).
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
        """Obtiene la barra de 1 minuto más reciente de SPY."""
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
        """Obtiene las últimas N barras de 1 minuto de SPY."""
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
        Versión síncrona interna de get_bars.
        Llamada desde el wrapper async get_bars() y desde get_snapshot().
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
        Versión ASYNC de get_bars — requerida por signal_engine que usa 'await'.

        signal_engine llama 'await self.feed.get_bars()' porque ESFeed (IBKR)
        necesita la versión async (usa reqHistoricalDataAsync internamente).
        Este wrapper hace que SPYFeed sea compatible sin cambiar signal_engine.

        Alpaca usa requests HTTP síncronos, pero envolver la llamada en una
        corutina la hace 'awaitable' sin ningún cambio de comportamiento real.
        No bloquea el event loop porque la llamada HTTP es rápida (~200ms).
        """
        return self._get_bars_sync(timeframe_minutes, n)

    def spy_to_es(self, spy_price: float) -> float:
        """Convierte precio de SPY a nivel ES (SPY * 10 ≈ ES)."""
        return round(spy_price * SPY_TO_ES_MULTIPLIER, 1)

    def get_snapshot(self) -> Optional[dict]:
        """
        Obtiene todo lo necesario para el motor de señales en una sola llamada.
        Incluye guarda anti-precio-obsoleto: más de 10 min → ignorar.
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
# Loop de polling (legacy)
# ─────────────────────────────────────────────

async def run_market_loop(callback, interval_seconds: int = 60):
    """Loop de mercado para uso externo con callback."""
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
# Test rápido
# ─────────────────────────────────────────────

def test_feed():
    """
    Prueba la conexión con Alpaca.
    Ejecuta con: python market_data/alpaca_feed.py
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
