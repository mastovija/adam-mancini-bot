"""
backtest/download_data.py — Descarga datos históricos de SPY desde Alpaca
=========================================================================
Descarga barras de 1 minuto de SPY para el período que tenemos de tweets
(Feb 26 - Jun 6, 2026) y las guarda localmente para el backtesting.

USO:
    python backtest/download_data.py

RESULTADO:
    data/backtest/spy_bars/YYYY-MM-DD.json   (un archivo por día de trading)
    data/backtest/spy_bars/index.json         (lista de días disponibles)
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
    print("❌ alpaca-py no instalado. Ejecuta: pip install alpaca-py")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────
BACKTEST_DIR  = DATA_DIR / 'backtest'
BARS_DIR      = BACKTEST_DIR / 'spy_bars'

# Período a descargar: coincide con el período de tweets que tenemos
# Ajusta estas fechas según tu dataset
DATE_START = datetime(2026, 2, 26, tzinfo=pytz.UTC)
DATE_END   = datetime(2026, 6, 7,  tzinfo=pytz.UTC)

# Alpaca limita las peticiones — descargamos semana a semana
CHUNK_DAYS = 7


# ─────────────────────────────────────────────
# Funciones
# ─────────────────────────────────────────────

def es_dia_laborable(dt: datetime) -> bool:
    """Comprueba si un día es de lunes a viernes."""
    return dt.weekday() < 5


def bars_to_list(bars) -> list:
    """
    Convierte el objeto de barras de Alpaca a una lista de dicts serializable.
    Maneja diferentes versiones de alpaca-py.
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
    Descarga las barras de 1 minuto de SPY para un día específico.

    Args:
        fecha: fecha en UTC (solo se usa la fecha, no la hora)

    Returns:
        Lista de barras OHLCV, o lista vacía si hay error
    """
    # Horario de mercado en UTC: 9:30 - 16:00 EST = 13:30 - 20:00 UTC
    # Pedimos un poco más por si acaso
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
        print(f"  ⚠️  Error descargando {fecha.date()}: {e}")
        return []


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def download_historical_data():
    """
    Descarga datos históricos de SPY para todo el período de backtesting.
    Guarda un JSON por día de trading.
    """
    print("=" * 55)
    print("  Backtesting — Descarga Datos Históricos SPY")
    print("=" * 55)
    print(f"📅 Período: {DATE_START.date()} → {DATE_END.date()}")
    print(f"📁 Guardando en: {BARS_DIR}\n")

    BARS_DIR.mkdir(parents=True, exist_ok=True)

    client = StockHistoricalDataClient(
        api_key    = ALPACA_API_KEY,
        secret_key = ALPACA_SECRET_KEY,
    )

    # Generar lista de días laborables en el período
    dias = []
    current = DATE_START
    while current < DATE_END:
        if es_dia_laborable(current):
            dias.append(current)
        current += timedelta(days=1)

    print(f"📊 Días laborables en el período: {len(dias)}")

    # Ver cuáles ya tenemos descargados
    existentes = {f.stem for f in BARS_DIR.glob('*.json') if f.stem != 'index'}
    por_descargar = [d for d in dias if str(d.date()) not in existentes]
    print(f"✅ Ya descargados: {len(existentes)}")
    print(f"🆕 Por descargar: {len(por_descargar)}\n")

    if not por_descargar:
        print("✅ Todos los datos ya están descargados")
        return

    print("📥 Descargando...")
    print("-" * 40)

    descargados = 0
    vacios      = 0

    for i, fecha in enumerate(por_descargar, 1):
        fecha_str = str(fecha.date())
        print(f"  [{i:3d}/{len(por_descargar)}] {fecha_str}... ", end='', flush=True)

        bars = descargar_dia(client, fecha)

        if bars:
            # Guardar barras del día
            output = BARS_DIR / f"{fecha_str}.json"
            with open(output, 'w') as f:
                json.dump(bars, f)
            print(f"{len(bars)} barras ✅")
            descargados += 1
        else:
            print("sin datos (festivo o error)")
            vacios += 1

        # Pausa entre requests para no saturar la API
        if i % 5 == 0:
            time.sleep(1)

    # Guardar índice de días disponibles
    dias_disponibles = sorted([f.stem for f in BARS_DIR.glob('*.json') if f.stem != 'index'])
    with open(BARS_DIR / 'index.json', 'w') as f:
        json.dump(dias_disponibles, f, indent=2)

    print("\n" + "=" * 55)
    print(f"✅ Descarga completada")
    print(f"   Días con datos: {descargados}")
    print(f"   Días sin datos: {vacios} (festivos/fines de semana)")
    print(f"   Total en disco: {len(dias_disponibles)} días")
    print(f"📁 Directorio: {BARS_DIR}")
    print("=" * 55)


if __name__ == '__main__':
    download_historical_data()
