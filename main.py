"""
main.py — Entry point of the Adam Mancini Bot
================================================
Starts every bot module with a single command:
    python main.py

WHAT IT DOES ON STARTUP:
    1. Parses today's newsletter (if not already done)
    2. Sends the morning briefing to Telegram
    3. Starts the signal engine (every 60 seconds)
    4. Starts the tweet monitor (every 3 minutes)
    5. Schedules the daily newsletter parse at 7:30 AM EST

EVERYTHING RUNS IN PARALLEL — a single process, a single command.
To stop: Ctrl+C (sends a shutdown notice to Telegram)
"""

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

import pytz

# Add the root to the path
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
    print("⚠️  APScheduler not available — the newsletter is not re-parsed automatically")
    print("   Install with: pip install apscheduler")


# ─────────────────────────────────────────────
# Scheduled tasks
# ─────────────────────────────────────────────

async def tarea_newsletter_diario():
    """
    Runs every day at 7:30 AM EST (Mon-Fri).
    Downloads today's newsletter and sends the morning briefing to Telegram.

    FIX B-9 — double briefing:
    If the bot started shortly before 7:30 AM (typical case: you start at
    7:15-7:25 EST), inicializar() will already have parsed the newsletter
    and sent the briefing. Without this guard, the scheduler would still
    fire at 7:30 and send a second identical briefing to Telegram.

    Solution: if today.json was modified less than GRACE_MINUTES ago,
    inicializar() already did it — we skip this cycle.
    """
    GRACE_MINUTES = 20   # grace window: if the file is <20 min old, it's already done

    from config import DATA_DIR
    today_file = DATA_DIR / 'daily' / 'today.json'

    if today_file.exists():
        minutos = (datetime.now().timestamp() - today_file.stat().st_mtime) / 60
        if minutos < GRACE_MINUTES:
            print(f"\n⏰ [7:30 AM] Newsletter already parsed {minutos:.0f} min ago "
                  f"— skipping (inicializar() did it at startup)")
            return

    print("\n⏰ [7:30 AM] Parsing daily newsletter...")
    alerter = TelegramAlerter()

    try:
        today = parse_daily_newsletter(force=True)
        if today:
            await alerter.send_morning_briefing(today)
            print("✅ Newsletter parsed and briefing sent")
        else:
            await alerter.send("⚠️ Could not fetch today's newsletter")
    except Exception as e:
        print(f"❌ Error in newsletter task: {e}")
        # parse_mode=None: this notice is plain text (no HTML tags) and the exception {e}
        # may contain <, > or & (paths, URLs with &, scrape fragments). Without this, that
        # character would break Telegram's HTML parser and the error notice would be lost.
        await alerter.send(f"❌ Newsletter error: {e}", parse_mode=None)


# ─────────────────────────────────────────────
# Initialization
# ─────────────────────────────────────────────

async def inicializar():
    """
    Startup tasks: parse newsletter, send briefing, verify connections.
    Runs only once when the bot starts.
    """
    alerter = TelegramAlerter()
    tz_ny   = pytz.timezone(MARKET_TIMEZONE)
    hora    = datetime.now(tz_ny).strftime('%H:%M EST')

    print("\n🚀 Starting Adam Mancini Bot...")

    # ── 1. Parse newsletter ───────────────────────────────────────────────
    print("📰 Parsing newsletter...")
    try:
        today = parse_daily_newsletter()
    except Exception as e:
        print(f"  ⚠️  Newsletter error: {e}")
        today = None

    # ── 2. Morning briefing to Telegram ───────────────────────────────────
    try:
        if today:
            await alerter.send_morning_briefing(today)
            print("✅ Briefing sent to Telegram")
        else:
            await alerter.send(
                f"🤖 <b>Adam Mancini Bot started</b>\n"
                f"⏰ {hora}\n"
                f"⚠️ No newsletter available today — monitoring only"
            )
    except Exception as e:
        print(f"  ⚠️  Error sending briefing: {e}")

    print("✅ Initialization complete\n")
    return today


# ─────────────────────────────────────────────
# Main program
# ─────────────────────────────────────────────

async def main():
    """
    Main function that runs the whole bot in parallel.

    Uses asyncio.gather to run concurrently:
    - Signal engine (checks every 60 seconds)
    - Tweet monitor (checks every 3 minutes)

    The scheduler adds the daily newsletter parse at 7:30 AM.
    """

    # ── Initialization ────────────────────────────────────────────────────
    await inicializar()

    # ── Daily scheduler ───────────────────────────────────────────────────
    scheduler = None
    if HAS_SCHEDULER:
        tz_ny     = pytz.timezone(MARKET_TIMEZONE)
        scheduler = AsyncIOScheduler(timezone=tz_ny)

        # Newsletter: every weekday at 7:30 AM EST
        scheduler.add_job(
            tarea_newsletter_diario,
            trigger     = 'cron',
            hour        = 7,
            minute      = 30,
            day_of_week = 'mon-fri',
            id          = 'newsletter_diario',
        )

        scheduler.start()
        print("⏰ Scheduler active: newsletter at 7:30 AM EST (Mon-Fri)")

    # ── Modules in parallel ───────────────────────────────────────────────
    print("\n▶️  Starting modules:")
    print("   • Signal engine (every 60s)")
    print("   • Tweet monitor (every 3 min)")
    print("   • Waiting for market hours...\n")

    engine = SignalEngine()

    try:
        # Run the signal engine and tweet monitor concurrently
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
                pass  # The event loop is already closed — cosmetic error, doesn't affect behavior


# ─────────────────────────────────────────────
# Clean shutdown handling
# ─────────────────────────────────────────────

def handle_shutdown(loop, alerter):
    """
    Clean shutdown with Ctrl+C.

    FIX: you can't call run_until_complete() on a loop that is already
    running (it raises RuntimeError and the notice was never sent).
    Instead, we schedule the send as a task inside the active loop
    and stop the loop when it finishes.
    """
    print("\n⏹️  Shutting down bot...")

    async def _despedida_y_stop():
        try:
            await alerter.send("⏹️ <b>Adam Mancini Bot stopped</b>")
        except Exception:
            pass  # If Telegram fails, we stop anyway
        loop.stop()

    # create_task: runs inside the loop that is already running
    loop.create_task(_despedida_y_stop())


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  Adam Mancini Bot — Trading Intelligence")
    print("=" * 55)
    print("  Ctrl+C to stop\n")

    loop    = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    alerter = TelegramAlerter()

    # Capture Ctrl+C for a clean shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown, loop, alerter)

    try:
        loop.run_until_complete(main())
    except RuntimeError:
        pass  # Loop stopped by the signal handler
    finally:
        loop.close()
        print("👋 Bot stopped")
