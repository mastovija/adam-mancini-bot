"""
main.py — Punto de entrada del Bot Adam Mancini
================================================
Arranca todos los módulos del bot con un solo comando:
    python main.py

QUÉ HACE AL INICIAR:
    1. Parsea el newsletter de hoy (si no está ya hecho)
    2. Envía el briefing matutino a Telegram
    3. Arranca el motor de señales (cada 60 segundos)
    4. Arranca el monitor de tweets (cada 3 minutos)
    5. Programa el parseo diario del newsletter a las 7:30 AM EST

TODO CORRE EN PARALELO — un solo proceso, un solo comando.
Para parar: Ctrl+C (envía aviso de apagado a Telegram)
"""

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

import pytz

# Añadir raíz al path
sys.path.append(str(Path(__file__).parent))

from config import MARKET_TIMEZONE
from parsers.newsletter_parser import parse_daily_newsletter
from parsers.tweet_monitor import monitorizar
from signals.signal_engine import SignalEngine
from bot.telegram_alerts import TelegramAlerter

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    print("⚠️  APScheduler no disponible — el newsletter no se reparsea automáticamente")
    print("   Instala con: pip install apscheduler")


# ─────────────────────────────────────────────
# Tareas programadas
# ─────────────────────────────────────────────

async def tarea_newsletter_diario():
    """
    Se ejecuta cada día a las 7:30 AM EST (lun-vie).
    Descarga el newsletter de hoy y envía el briefing matutino a Telegram.

    FIX B-9 — doble briefing:
    Si el bot arracó poco antes de las 7:30 AM (caso típico: arrancas a
    las 7:15-7:25 EST), inicializar() ya habrá parseado el newsletter y
    enviado el briefing. Sin esta guarda, el scheduler disparaba igualmente
    a las 7:30 y mandaba un segundo briefing idéntico a Telegram.

    Solución: si today.json fue modificado hace menos de GRACE_MINUTES,
    inicializar() ya lo hizo — saltamos este ciclo.
    """
    GRACE_MINUTES = 20   # ventana de gracia: si el archivo tiene <20 min, ya está hecho

    from config import DATA_DIR
    today_file = DATA_DIR / 'daily' / 'today.json'

    if today_file.exists():
        minutos = (datetime.now().timestamp() - today_file.stat().st_mtime) / 60
        if minutos < GRACE_MINUTES:
            print(f"\n⏰ [7:30 AM] Newsletter ya parseado hace {minutos:.0f} min "
                  f"— omitiendo (inicializar() lo hizo al arrancar)")
            return

    print("\n⏰ [7:30 AM] Parseando newsletter diario...")
    alerter = TelegramAlerter()

    try:
        today = parse_daily_newsletter(force=True)
        if today:
            await alerter.send_morning_briefing(today)
            print("✅ Newsletter parseado y briefing enviado")
        else:
            await alerter.send("⚠️ No se pudo obtener el newsletter de hoy")
    except Exception as e:
        print(f"❌ Error en tarea newsletter: {e}")
        # parse_mode=None: este aviso es texto plano (sin etiquetas HTML) y la excepción {e}
        # puede traer <, > o & (rutas, URLs con &, fragmentos del scrape). Sin esto, ese
        # carácter rompería el parser HTML de Telegram y el aviso de error se perdería.
        await alerter.send(f"❌ Error newsletter: {e}", parse_mode=None)


# ─────────────────────────────────────────────
# Inicialización
# ─────────────────────────────────────────────

async def inicializar():
    """
    Tareas de inicio: parsear newsletter, enviar briefing, verificar conexiones.
    Se ejecuta una sola vez al arrancar el bot.
    """
    alerter = TelegramAlerter()
    tz_ny   = pytz.timezone(MARKET_TIMEZONE)
    hora    = datetime.now(tz_ny).strftime('%H:%M EST')

    print("\n🚀 Iniciando Bot Adam Mancini...")

    # ── 1. Parsear newsletter ─────────────────────────────────────────────
    print("📰 Parseando newsletter...")
    try:
        today = parse_daily_newsletter()
    except Exception as e:
        print(f"  ⚠️  Error newsletter: {e}")
        today = None

    # ── 2. Briefing matutino a Telegram ───────────────────────────────────
    try:
        if today:
            await alerter.send_morning_briefing(today)
            print("✅ Briefing enviado a Telegram")
        else:
            await alerter.send(
                f"🤖 <b>Bot Adam Mancini iniciado</b>\n"
                f"⏰ {hora}\n"
                f"⚠️ No hay newsletter disponible hoy — solo monitoring activo"
            )
    except Exception as e:
        print(f"  ⚠️  Error enviando briefing: {e}")

    print("✅ Inicialización completada\n")
    return today


# ─────────────────────────────────────────────
# Programa principal
# ─────────────────────────────────────────────

async def main():
    """
    Función principal que corre todo el bot en paralelo.

    Usa asyncio.gather para ejecutar concurrentemente:
    - Motor de señales (checks cada 60 segundos)
    - Monitor de tweets (checks cada 3 minutos)

    El scheduler añade el parseo diario del newsletter a las 7:30 AM.
    """

    # ── Inicialización ────────────────────────────────────────────────────
    await inicializar()

    # ── Scheduler diario ──────────────────────────────────────────────────
    scheduler = None
    if HAS_SCHEDULER:
        tz_ny     = pytz.timezone(MARKET_TIMEZONE)
        scheduler = AsyncIOScheduler(timezone=tz_ny)

        # Newsletter: cada día laborable a las 7:30 AM EST
        scheduler.add_job(
            tarea_newsletter_diario,
            trigger     = 'cron',
            hour        = 7,
            minute      = 30,
            day_of_week = 'mon-fri',
            id          = 'newsletter_diario',
        )

        scheduler.start()
        print("⏰ Scheduler activo: newsletter a las 7:30 AM EST (lun-vie)")

    # ── Módulos en paralelo ───────────────────────────────────────────────
    print("\n▶️  Arrancando módulos:")
    print("   • Motor de señales (cada 60s)")
    print("   • Monitor de tweets (cada 3 min)")
    print("   • Esperando horario de mercado...\n")

    engine = SignalEngine()

    try:
        # Ejecutar señal engine y tweet monitor concurrentemente
        await asyncio.gather(
            engine.run_loop(interval_seconds=60),
            monitorizar(),
        )
    except asyncio.CancelledError:
        pass
    finally:
        if scheduler:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass  # El event loop ya está cerrado — error cosmético, no afecta al funcionamiento


# ─────────────────────────────────────────────
# Manejo de apagado limpio
# ─────────────────────────────────────────────

def handle_shutdown(loop, alerter):
    """
    Apagado limpio con Ctrl+C.

    FIX: no se puede llamar a run_until_complete() sobre un loop que ya
    está corriendo (lanza RuntimeError y el aviso nunca se enviaba).
    En su lugar, programamos el envío como tarea dentro del loop activo
    y paramos el loop cuando termine.
    """
    print("\n⏹️  Apagando bot...")

    async def _despedida_y_stop():
        try:
            await alerter.send("⏹️ <b>Bot Adam Mancini detenido</b>")
        except Exception:
            pass  # Si Telegram falla, paramos igualmente
        loop.stop()

    # create_task: se ejecuta dentro del loop que ya está corriendo
    loop.create_task(_despedida_y_stop())


# ─────────────────────────────────────────────
# Entrada
# ─────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  Bot Adam Mancini — Trading Intelligence")
    print("=" * 55)
    print("  Ctrl+C para parar\n")

    loop    = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    alerter = TelegramAlerter()

    # Capturar Ctrl+C para apagado limpio
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown, loop, alerter)

    try:
        loop.run_until_complete(main())
    except RuntimeError:
        pass  # Loop detenido por el signal handler
    finally:
        loop.close()
        print("👋 Bot detenido")
