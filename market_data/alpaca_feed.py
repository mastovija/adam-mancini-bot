"""
market_data/alpaca_feed.py — Feed de precios SPY con Alpaca Markets
====================================================================
Obtiene el precio de SPY en tiempo real usando la API gratuita de Alpaca.

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
    print("❌ alpaca-py no instalado. Ejecuta: pip install alpaca-py")
    sys.exit(1)


# ─────────────────────────────────────────────
# Horario de mercado
# ─────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Comprueba si el mercado NYSE está abierto ahora mismo.
    Horario: lunes-viernes, 9:30-16:00 EST (o el que tengas en config.py)
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

    # Calcular próxima apertura
    apertura_hoy = ahora.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
    )

    if ahora < apertura_hoy and ahora.weekday() < 5:
        return int((apertura_hoy - ahora).total_seconds())

    # Buscar el próximo día de semana
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

    Uso básico:
        feed = SPYFeed()
        snapshot = feed.get_snapshot()
        print(snapshot['es_equivalent'])  # nivel ES equivalente
    """

    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise ValueError(
                "Faltan credenciales de Alpaca en .env\n"
                "Crea una cuenta gratuita en alpaca.markets y añade:\n"
                "  ALPACA_API_KEY=...\n  ALPACA_SECRET_KEY=..."
            )
        # El cliente de datos históricos y latest — funciona con cuenta paper gratuita
        self.client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

    def get_latest_bar(self) -> Optional[dict]:
        """
        Obtiene la barra de 1 minuto más reciente de SPY.

        Returns:
            Dict con open, high, low, close, volume, timestamp
            o None si hay error o mercado cerrado
        """
        try:
            request = StockLatestBarRequest(
                symbol_or_symbols=MARKET_TICKER,
                feed='iex',  # feed gratuito — real-time con ~2-3% del volumen
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
            print(f"  ⚠️  Error obteniendo barra: {e}")
            return None

    def get_recent_bars(self, n: int = 20) -> list:
        """
        Obtiene las últimas N barras de 1 minuto de SPY.
        Útil para calcular tendencia y momentum a corto plazo.

        Args:
            n: número de barras (minutos hacia atrás)

        Returns:
            Lista de dicts con OHLCV, ordenados del más antiguo al más nuevo
        """
        try:
            utc = pytz.UTC
            # Pedimos un poco más para asegurar que tenemos N barras completas
            start = datetime.now(utc) - timedelta(minutes=n + 10)
            end   = datetime.now(utc)

            request = StockBarsRequest(
                symbol_or_symbols=MARKET_TICKER,
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=start,
                end=end,
                feed='iex',
            )
            bars_data = self.client.get_stock_bars(request)
            # alpaca-py cambió el formato — manejamos ambas versiones
            try:
                bars = list(bars_data[MARKET_TICKER])
            except (KeyError, TypeError):
                try:
                    bars = list(bars_data.data.get(MARKET_TICKER, []))
                except Exception:
                    bars = []

            return [
                {
                    'open':      float(b.open),
                    'high':      float(b.high),
                    'low':       float(b.low),
                    'close':     float(b.close),
                    'volume':    int(b.volume),
                    'timestamp': b.timestamp.isoformat() if b.timestamp else '',
                }
                for b in bars[-n:]  # Solo las últimas N
            ]
        except Exception as e:
            print(f"  ⚠️  Error obteniendo barras recientes: {e}")
            return []
    
    def get_bars(self, timeframe_minutes: int = 15, n: int = 10) -> list:
        """
        Obtiene las últimas N barras del timeframe indicado.
        Por defecto 15 minutos — el timeframe principal de Adam.
        """
        try:
            utc = pytz.UTC
            start = datetime.now(utc) - timedelta(minutes=(timeframe_minutes * n) + 30)
            end   = datetime.now(utc)

            request = StockBarsRequest(
                symbol_or_symbols=MARKET_TICKER,
                timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
                start=start,
                end=end,
                feed='iex',
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
            print(f"  ⚠️  Error obteniendo barras {timeframe_minutes}min: {e}")
            return []

    def spy_to_es(self, spy_price: float) -> float:
        """
        Convierte precio de SPY a nivel equivalente del ES futures.

        Adam habla de niveles como 7527, 7474, etc. (ES/SPX).
        SPY cotiza ~10x más bajo: SPY 540 ≈ ES 5400.
        Multiplicamos por SPY_TO_ES_MULTIPLIER (10.0 en config.py).
        """
        return round(spy_price * SPY_TO_ES_MULTIPLIER, 1)

    def get_snapshot(self) -> Optional[dict]:
        """
        Obtiene todo lo necesario para el motor de señales en una sola llamada.

        Incluye guarda anti-precio-obsoleto: si la última barra de Alpaca
        tiene más de 10 minutos (festivo NYSE no detectado, sin volumen IEX
        en premarket, etc.), devuelve None para no disparar señales falsas.
        """
        bar = self.get_latest_bar()
        if not bar:
            return None

        # ── Guarda contra precios obsoletos ──────────────────────────────
        # Alpaca devuelve la última barra disponible aunque sea de hace horas.
        # Más de 10 minutos de antigüedad = dato no fiable para señales.
        if bar.get('timestamp'):
            try:
                bar_ts = datetime.fromisoformat(bar['timestamp'])
                # Hacer timezone-aware si no lo es
                if bar_ts.tzinfo is None:
                    bar_ts = bar_ts.replace(tzinfo=pytz.UTC)
                ahora_utc = datetime.now(pytz.UTC)
                edad_segundos = (ahora_utc - bar_ts).total_seconds()
                if edad_segundos > 600:  # 10 minutos
                    print(f"  ⚠️  Barra obsoleta ({edad_segundos/60:.0f} min) — ignorando")
                    return None
            except Exception:
                pass  # Si no podemos parsear el timestamp, continuamos

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
# Loop de polling para el motor de señales
# ─────────────────────────────────────────────

async def run_market_loop(callback, interval_seconds: int = 60):
    """
    Ejecuta un callback cada 'interval_seconds' segundos con el snapshot del mercado.
    Fuera de horario de mercado espera en modo eficiente.

    Cómo usarlo desde la Fase 5:
        async def mi_callback(snapshot):
            precio_es = snapshot['es_equivalent']
            # ... detectar si está en un nivel de Adam

        asyncio.run(run_market_loop(mi_callback))

    Args:
        callback:         función async que recibe el snapshot de mercado
        interval_seconds: segundos entre cada lectura (default: 60 = 1 minuto)
    """
    feed = SPYFeed()
    print(f"📊 Feed de mercado iniciado | {MARKET_TICKER} cada {interval_seconds}s")

    while True:
        if not is_market_open():
            espera = min(tiempo_hasta_apertura(), 300)  # máx 5 min de espera
            minutos = espera // 60
            print(f"  😴 Mercado cerrado — próxima comprobación en {minutos} min")
            await asyncio.sleep(espera)
            continue

        # Obtener datos y ejecutar callback
        snapshot = feed.get_snapshot()
        if snapshot:
            await callback(snapshot)
        else:
            print("  ⚠️  Sin datos de mercado")

        await asyncio.sleep(interval_seconds)


# ─────────────────────────────────────────────
# Test rápido
# ─────────────────────────────────────────────

def test_feed():
    """
    Prueba la conexión con Alpaca y muestra el precio actual de SPY.
    Ejecuta con: python market_data/alpaca_feed.py
    """
    print("=" * 50)
    print("  Test Feed Alpaca — SPY")
    print("=" * 50)
    print(f"⏰ Mercado: {'🟢 ABIERTO' if is_market_open() else '🔴 CERRADO'}")

    feed = SPYFeed()

    print(f"\n📊 Obteniendo precio de {MARKET_TICKER}...")
    snapshot = feed.get_snapshot()

    if snapshot:
        print(f"\n✅ Conexión exitosa")
        print(f"   SPY:          ${snapshot['spy_price']:.2f}")
        print(f"   ES equivalent: {snapshot['es_equivalent']:.1f}")
        print(f"   Timestamp:    {snapshot['timestamp']}")
        print(f"\n   Barra 1min:")
        bar = snapshot['bar']
        print(f"   O:{bar['open']:.2f} H:{bar['high']:.2f} "
              f"L:{bar['low']:.2f} C:{bar['close']:.2f} "
              f"Vol:{bar['volume']:,}")

        # Barras recientes
        print(f"\n📈 Últimas 5 barras de 1 minuto:")
        bars = feed.get_recent_bars(5)
        if bars:
            for b in bars:
                print(f"   {b['timestamp'][11:16]} | "
                      f"C:{b['close']:.2f} | "
                      f"ES:{feed.spy_to_es(b['close']):.1f} | "
                      f"Vol:{b['volume']:,}")
        else:
            print("   (Sin barras — puede que el mercado esté cerrado)")
    else:
        print("❌ No se pudo obtener datos")
        print("   Verifica ALPACA_API_KEY y ALPACA_SECRET_KEY en .env")


if __name__ == '__main__':
    test_feed()
