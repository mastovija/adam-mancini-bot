"""
market_data/ibkr_feed.py — Feed de precios ES Futures con IBKR Paper Trading
=============================================================================
Reemplaza el feed de Alpaca/SPY por datos directos del contrato ES futures
desde Interactive Brokers (paper trading).

POR QUÉ IBKR EN LUGAR DE ALPACA/SPY:
  - Alpaca daba el precio de SPY y multiplicábamos ×10 → ruido e imprecisión
  - Alpaca IEX feed NO tiene datos pre-mercado → nos perdíamos la ventana
    prime de Adam (7:30-9:30 AM EST donde pone la mayoría de sus entradas)
  - IBKR da el precio EXACTO del ES futures, sin proxy ni conversión
  - IBKR tiene datos pre-mercado con useRTH=False en las peticiones históricas

REQUISITOS PREVIOS (hacer una vez):
  1. Instalar: pip install ib_insync

  2. Descargar IB Gateway (versión ligera, no requiere TWS completo):
     https://www.interactivebrokers.com/en/trading/ibgateway.php

  3. En IB Gateway → configuración (engranaje) → API → Settings:
       Puerto (Socket port): 4002
       ✅ Allow connections from localhost only
       ❌ Read-Only API (desmarcar para eliminar los warnings de error 321)

  4. Añadir al .env:
     IBKR_ES_EXPIRY=202609    ← Septiembre 2026 (ESU2026, el front month actual)

UNIDADES DE DURACIÓN IBKR (importante):
  IBKR solo acepta: S (segundos), D (días), W (semanas), M (meses), Y (años)
  NO acepta "H" (horas) — ese era el bug que fallaba en reqHistoricalData.
  Usamos segundos para duraciones cortas y días para más largas.

NOTAS SOBRE DATOS EN CUENTA PAPER:
  - Las cuentas paper no incluyen feed de datos en tiempo real
  - reqMarketDataType(3) solicita datos DELAYED (15 min de delay, gratuito)
  - Las barras históricas de reqHistoricalData sí son datos reales sin delay
  - Para detectar niveles con ±3 pts de tolerancia, delayed es suficiente
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
    print("❌ ib_insync no instalado.")
    print("   Ejecuta: pip install ib_insync")
    sys.exit(1)


# ─────────────────────────────────────────────
# Horario de mercado
# ─────────────────────────────────────────────

def is_market_open() -> bool:
    """
    Determina si estamos en horario de trading de Adam (7:30-16:00 EST).
    ES futures opera casi 24h, pero Adam solo opera en este rango.
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


# ─────────────────────────────────────────────
# Feed ES Futures con IBKR
# ─────────────────────────────────────────────

class ESFeed:
    """
    Feed directo de ES Futures desde IBKR paper trading.

    IMPORTANTE — get_bars() es ASYNC:
        Con asyncio ya corriendo (el caso normal del bot), reqHistoricalData()
        síncrono falla con "This event loop is already running".
        Usamos reqHistoricalDataAsync() → siempre llamar con 'await':
            bars = await self.feed.get_bars(15, 8)

    CICLO DE VIDA:
        feed = ESFeed()
        await feed.connect_async()     # una sola vez al arrancar
        snapshot = feed.get_snapshot() # síncrono — lee ticker cacheado
        bars = await feed.get_bars()   # async — petición a IBKR
        feed.disconnect()              # al apagar el bot
    """

    def __init__(self):
        self.ib = IB()
        self._contract: Optional[Future] = None
        self._ticker = None
        self._last_update: Optional[datetime] = None
        self._connected = False

    async def connect_async(self):
        """
        Conecta a IB Gateway y suscribe a datos de mercado delayed.
        Llamar UNA SOLA VEZ al arrancar el bot (desde signal_engine.run_loop).
        """
        if self._connected:
            return

        print(f"\n🔌 Conectando a IB Gateway ({IBKR_HOST}:{IBKR_PORT}, clientId={IBKR_CLIENT_ID})...")
        print("   (IB Gateway debe estar corriendo en modo paper trading)")

        try:
            await self.ib.connectAsync(
                host=IBKR_HOST,
                port=IBKR_PORT,
                clientId=IBKR_CLIENT_ID,
                timeout=15,
            )
        except Exception as e:
            print(f"\n❌ No se pudo conectar a IB Gateway: {e}")
            print(f"   1. IB Gateway corriendo en tu Mac (paper trading)")
            print(f"   2. API habilitada, puerto {IBKR_PORT}")
            raise

        # Tipo 3 = DELAYED (~15 min de retraso, gratuito con cuenta paper)
        # Sin esto → error 354 "Requested market data is not subscribed"
        # Tipo 1 = LIVE (requiere suscripción de pago en IBKR)
        self.ib.reqMarketDataType(3)
        print("   📡 Modo de datos: DELAYED (15 min) — suficiente para señales")

        # Calificar el contrato: IBKR completa los detalles (conId, localSymbol, etc.)
        contrato_raw = Future(
            symbol='ES',
            exchange='CME',
            currency='USD',
            lastTradeDateOrContractMonth=IBKR_ES_EXPIRY,  # '202609' = sep 2026
        )

        contratos = await self.ib.qualifyContractsAsync(contrato_raw)
        if not contratos:
            raise ValueError(
                f"IBKR no encontró el contrato ES {IBKR_ES_EXPIRY}.\n"
                f"Formato válido: '202609' (sep 2026), '202612' (dic 2026)"
            )

        self._contract = contratos[0]
        print(f"   ✅ Contrato calificado: {self._contract.localSymbol} "
              f"(expira {self._contract.lastTradeDateOrContractMonth})")

        # Suscribir a stream de ticks en tiempo real (delayed con tipo 3)
        # El ticker se actualiza automáticamente cuando llegan nuevos ticks
        self._ticker = self.ib.reqMktData(
            self._contract,
            genericTickList='',
            snapshot=False,
            regulatorySnapshot=False,
        )

        print("   ⏳ Esperando primeros ticks...")
        await asyncio.sleep(3)

        self._connected = True

        precio = self._get_current_price()
        if precio:
            print(f"   📊 Precio ES: {precio:.2f} (delayed ~15 min)")
        else:
            print("   ⚠️  Sin precio aún — normal si el mercado está cerrado")

        print(f"   ✅ Feed IBKR activo — {self._contract.localSymbol}\n")

    def disconnect(self):
        """Desconecta limpiamente de IB Gateway."""
        if self._connected:
            try:
                if self._contract:
                    self.ib.cancelMktData(self._contract)
            except Exception:
                pass
            self.ib.disconnect()
            self._connected = False
            print("🔌 IBKR desconectado correctamente")

    def _ensure_connected(self) -> bool:
        """Verifica que la conexión sigue activa."""
        if not self._connected or not self.ib.isConnected():
            print("  ⚠️  IBKR desconectado")
            self._connected = False
            return False
        return True

    def _get_current_price(self) -> Optional[float]:
        """
        Lee el precio actual del ticker cacheado.
        Orden de preferencia: last (última transacción) → close (cierre anterior).
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
        Obtiene el precio actual del ES (SÍNCRONO — lee ticker cacheado).

        El ticker se actualiza en background por ib_insync, así que
        get_snapshot() es instantáneo: solo lee el último valor recibido.

        Devuelve dict compatible con SPYFeed.get_snapshot():
        {
            'timestamp':       '2026-06-18T10:32:00',
            'spy_price':       750.5,     ← es_price / 10 (solo compatibilidad)
            'es_equivalent':   7505.0,    ← precio ES real
            'bar':             {ohlcv},
            'in_market_hours': True
        }
        """
        if not self._ensure_connected():
            return None

        precio_es = self._get_current_price()
        if not precio_es:
            print("  ⚠️  Sin precio ES en el stream IBKR")
            return None

        # Guardia: más de 10 min sin actualización → posible desconexión silenciosa
        if self._last_update:
            edad_seg = (datetime.now() - self._last_update).total_seconds()
            if edad_seg > 600:
                print(f"  ⚠️  Último tick hace {int(edad_seg/60)} min")
                return None

        return {
            'timestamp':     datetime.now().isoformat(),
            'spy_price':       round(precio_es / 10.0, 2),  # compatibilidad legacy
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
        Obtiene las últimas N barras del ES en el timeframe indicado.

        ASYNC — llamar con 'await':
            bars = await self.feed.get_bars(15, 8)

        Usa reqHistoricalDataAsync en lugar del wrapper síncrono porque
        el event loop ya está corriendo cuando el bot está activo.

        UNIDADES DE DURACIÓN IBKR VÁLIDAS: S D W M Y (NO "H" — horas no existe)
        Usamos segundos ("S") para duraciones cortas, días ("D") para largas.

        useRTH=False: incluye pre-mercado (7:30-9:30 AM) — CRÍTICO para Adam.
        Los precios devueltos son en puntos ES directos (ej: 7505.25).
        """
        if not self._ensure_connected():
            return []

        # ── Calcular duración en formato válido IBKR ─────────────────────
        # IBKR acepta: {número} S/D/W/M/Y — "H" NO es válido
        # Usamos segundos para ser precisos y no pedir demasiado
        # (n*2 para tener margen de barras vacías por gaps de mercado cerrado)
        segundos_necesarios = timeframe_minutes * n * 2 * 60

        if segundos_necesarios <= 86400:        # hasta 1 día → usar segundos
            duration = f"{segundos_necesarios} S"
        elif segundos_necesarios <= 86400 * 5:  # hasta 5 días → usar días
            dias = (segundos_necesarios // 86400) + 1
            duration = f"{dias} D"
        else:                                   # más de 5 días → semanas
            semanas = (segundos_necesarios // (86400 * 7)) + 1
            duration = f"{semanas} W"

        # ── Formato de barra para IBKR ────────────────────────────────────
        # IBKR acepta: "1 min", "5 mins", "15 mins", "1 hour", "1 day", etc.
        if timeframe_minutes == 1:
            bar_size = "1 min"
        elif timeframe_minutes < 60:
            bar_size = f"{timeframe_minutes} mins"
        elif timeframe_minutes == 60:
            bar_size = "1 hour"
        else:
            bar_size = f"{timeframe_minutes // 60} hours"

        try:
            # reqHistoricalDataAsync → awaitable, no bloquea el event loop
            # endDateTime='' → hasta "ahora"
            # whatToShow='TRADES' → precios reales de transacciones
            # useRTH=False → INCLUYE pre-mercado (esencial para Adam 7:30-9:30 AM)
            # keepUpToDate=False → petición puntual, no suscripción continua
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
                if b.open > 100 and b.close > 100  # filtrar barras vacías
            ]

            return bars[-n:]  # últimas N, de más antigua a más reciente

        except Exception as e:
            print(f"  ⚠️  Error obteniendo barras IBKR {timeframe_minutes}min: {e}")
            return []

    def spy_to_es(self, price: float) -> float:
        """Con IBKR el precio ya es ES. Mantenido para compatibilidad."""
        return float(price)


# ─────────────────────────────────────────────
# Test de conexión independiente
# ─────────────────────────────────────────────

async def _test_feed():
    """
    Prueba la conexión con IB Gateway y muestra datos del ES.
    Ejecutar con: python market_data/ibkr_feed.py
    """
    print("=" * 60)
    print("  Test Feed IBKR — ES Futures")
    print("=" * 60)
    print(f"⏰ Mercado (ventana Adam): {'🟢 ABIERTO' if is_market_open() else '🔴 CERRADO'}")
    print(f"📋 Contrato objetivo: ES {IBKR_ES_EXPIRY}")

    feed = ESFeed()

    try:
        await feed.connect_async()

        print("\n📊 Obteniendo snapshot actual...")
        snapshot = feed.get_snapshot()  # síncrono

        if snapshot:
            print(f"\n✅ Snapshot OK:")
            print(f"   ES price:    {snapshot['es_equivalent']:.2f} puntos")
            print(f"   Timestamp:   {snapshot['timestamp'][:19]}")
        else:
            print("⚠️  Sin snapshot (mercado cerrado o sin ticks recientes)")

        print(f"\n📈 Últimas 5 barras de 15 minutos (pre-market incluido):")
        bars = await feed.get_bars(15, 5)
        if bars:
            for b in bars:
                print(f"   {b['timestamp']} | "
                      f"O:{b['open']:.2f}  H:{b['high']:.2f}  "
                      f"L:{b['low']:.2f}  C:{b['close']:.2f}  "
                      f"Vol:{b['volume']:,}")
        else:
            print("   (Sin barras)")

        print(f"\n📈 Últimas 3 barras de 1 minuto:")
        bars_1m = await feed.get_bars(1, 3)
        if bars_1m:
            for b in bars_1m:
                print(f"   {b['timestamp']} | C:{b['close']:.2f}")
        else:
            print("   (Sin barras de 1 minuto)")

    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        feed.disconnect()

    print("\n✅ Test completado")


if __name__ == '__main__':
    asyncio.run(_test_feed())
