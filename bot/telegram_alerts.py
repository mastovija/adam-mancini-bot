"""
bot/telegram_alerts.py — Telegram alert system
=========================================================
Sends three types of messages to the phone:

1. 📋 Morning briefing  → summary of Adam's newsletter at market open
2. ⚡ Signal alert       → when the engine detects an actionable setup
3. 🐦 Adam's tweet       → when Adam posts something actionable on X

USAGE (quick test):
    python bot/telegram_alerts.py
"""

import asyncio
import html
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.append(str(Path(__file__).parent.parent))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MARKET_TIMEZONE

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    from telegram.error import TelegramError
except ImportError:
    print("❌ python-telegram-bot no instalado.")
    print("   Ejecuta: pip install python-telegram-bot")
    sys.exit(1)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _format_levels(levels: list, max_show: int = 12) -> str:
    """
    Formats a list of levels for Telegram.
    Caps it at max_show levels to avoid cluttering the message,
    noting how many are hidden if there are more.
    """
    if not levels:
        return ''
    visible = [str(int(n)) for n in levels[:max_show] if n]
    result  = ' · '.join(visible)
    if len(levels) > max_show:
        result += f" <i>(+{len(levels) - max_show} more)</i>"
    return result


def _fecha_legible(fecha_raw: str) -> str:
    """Converts '2026-06-09' into 'Tue 9 Jun'."""
    try:
        fecha_obj = datetime.strptime(fecha_raw, '%Y-%m-%d')
        dias  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        meses = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        return f"{dias[fecha_obj.weekday()]} {fecha_obj.day} {meses[fecha_obj.month-1]}"
    except Exception:
        return fecha_raw


# ─────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────

class TelegramAlerter:
    """
    Sends formatted messages to the Telegram chat configured in .env.
    All methods are async — use them with await.
    """

    def __init__(self):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError(
                "Faltan credenciales de Telegram en .env\n"
                "Necesitas: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID"
            )
        self.bot     = Bot(token=TELEGRAM_BOT_TOKEN)
        self.chat_id = TELEGRAM_CHAT_ID

    async def send(self, text: str, parse_mode: str = ParseMode.HTML):
        """Sends a message to the configured chat."""
        try:
            await self.bot.send_message(
                chat_id    = self.chat_id,
                text       = text,
                parse_mode = parse_mode,
            )
        except TelegramError as e:
            print(f"  ❌ Error Telegram: {e}")

    # ─────────────────────────────────────────────
    # Type 1 — Morning briefing
    # ─────────────────────────────────────────────

    async def send_morning_briefing(self, today: dict):
        """
        Sends the newsletter summary at the start of the day.

        Shows up to 12 supports and 12 resistances to avoid cluttering
        the message (with a "+N more" note if there are more).
        The setup and invalidation text is shown in full — no truncation.
        """
        bias     = today.get('bias', 'unknown').upper()
        bias_emo = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪', 'MIXED': '🟡'}.get(bias, '❓')

        fecha_str    = _fecha_legible(today.get('date', ''))
        titulo       = html.escape(today.get('title', '')[:70])

        # Levels — capped at 12 per type to avoid clutter
        soportes_str    = _format_levels(today.get('soportes', []),    max_show=12)
        resistencias_str = _format_levels(today.get('resistencias', []), max_show=12)

        # Full text — no truncation (Telegram allows up to 4096 chars per message)
        setup    = html.escape(today.get('setup')      or '')
        invalida = html.escape(today.get('invalida_si') or '')

        lineas = [
            f"📋 <b>ADAM'S PLAN — {fecha_str}</b>",
            "─" * 30,
        ]
        if titulo:
            lineas.append(f"📰 <i>{titulo}</i>\n")

        lineas.append(f"Bias:     {bias_emo} <b>{bias}</b>")

        if today.get('nivel_critico'):
            lineas.append(f"Critical: <b>{int(today['nivel_critico'])}</b>")

        if soportes_str:
            lineas.append(f"🟩 Supports:\n{soportes_str}")

        if resistencias_str:
            lineas.append(f"🟥 Resists:\n{resistencias_str}")

        if setup:
            lineas += ["", f"💭 <b>Setup:</b> {setup}"]

        if invalida:
            lineas += ["", f"⚠️ <b>Invalidated if:</b> {invalida}"]

        if not today.get('is_complete'):
            lineas += ["", "💡 <i>Preview — subscribe for full analysis</i>"]

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Type 2 — Engine signal alert
    # ─────────────────────────────────────────────

    async def send_signal_alert(self, señal: dict, precio_es: float, nivel: float, today: dict):
        """
        Sends a trading alert when the engine detects a setup.

        Uses señal['direccion'] (saved by signal_engine.py) to
        determine LONG/SHORT — it doesn't infer it from the reason text,
        which may be in Spanish and unreliable.
        """
        # Real direction from signal_engine.py (not inferred from the text)
        es_long  = señal.get('direccion', 'long') == 'long'
        dir_emo  = '🟢' if es_long else '🔴'
        dir_text = 'LONG' if es_long else 'SHORT'

        entrada = señal.get('entrada_es', nivel)
        stop    = señal.get('stop_es')
        t1      = señal.get('target1_es')
        t2      = señal.get('target2_es')
        conf    = señal.get('confianza', 0)

        # R/R ratio
        rr_str = ''
        if entrada and stop and t1:
            riesgo  = abs(float(entrada) - float(stop))
            reward1 = abs(float(t1) - float(entrada))
            if riesgo > 0:
                rr_str = f"📐 R/R:       1:{reward1/riesgo:.1f}\n"

        # EST time
        tz_ny = pytz.timezone(MARKET_TIMEZONE)
        hora  = datetime.now(tz_ny).strftime('%H:%M')

        # Full reason — no truncation
        razon = html.escape(señal.get('razon') or '')

        lineas = [
            f"{dir_emo} <b>{dir_text} ES — Adam Mancini</b>",
            "─" * 28,
            f"📍 Entry:     <b>{entrada}</b>",
        ]
        if stop:
            lineas.append(f"🛑 Stop:      <b>{stop}</b>")
        if t1:
            lineas.append(f"🎯 Target 1:  <b>{t1}</b>")
        if t2:
            lineas.append(f"🎯 Target 2:  <b>{t2}</b>")

        lineas.append("─" * 28)

        if rr_str:
            lineas.append(rr_str.strip())

        lineas += [
            f"📊 Level: {int(nivel)} | Bias: {today.get('bias','?').upper()}",
            f"💭 {razon}",
            f"🎯 Confidence: <b>{conf:.0%}</b>  ⏰ {hora} EST",
        ]

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Type 3 — New tweet from Adam
    # ─────────────────────────────────────────────

    async def send_tweet_alert(self, tweet: dict, clasificacion: dict):
        """
        Sends an alert when Adam posts an actionable tweet.
        Includes the original text + the extracted levels.
        """
        tipo    = clasificacion.get('tipo', 'comment')
        accion  = clasificacion.get('accionable', False)

        tipo_emo = {'signal': '⚡', 'level': '📍', 'comment': '💬'}.get(tipo, '🐦')
        tipo_txt = {
            'signal':  'ACTIONABLE SIGNAL',
            'level':   'LEVELS UPDATE',
            'comment': 'COMMENT',
        }.get(tipo, tipo.upper())

        texto   = html.escape(tweet.get('text') or '')
        resumen = html.escape(clasificacion.get('resumen') or '')

        hora_raw = tweet.get('created_at', '')
        hora_str = hora_raw[11:16] if len(hora_raw) > 15 else ''

        lineas = [
            f"{tipo_emo} <b>ADAM TWEETS — {tipo_txt}</b>",
            "─" * 28,
            f'<i>"{texto}"</i>',
            "─" * 28,
        ]

        if resumen:
            lineas.append(f"📊 {resumen}")

        if accion:
            direccion = (clasificacion.get('direccion') or '').upper()
            entrada   = clasificacion.get('entrada')
            stop      = clasificacion.get('stop')
            target    = clasificacion.get('target')

            if direccion and entrada:
                dir_emo   = '🟢' if direccion == 'LONG' else '🔴'
                trade_str = f"{dir_emo} {direccion}"
                if entrada: trade_str += f" | Entry: {entrada}"
                if stop:    trade_str += f" | SL: {stop}"
                if target:  trade_str += f" | TP: {target}"
                lineas += ["", f"<b>{trade_str}</b>"]

        if hora_str:
            lineas += ["", f"⏰ {hora_str} UTC"]

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Test
    # ─────────────────────────────────────────────

    # C-11: these three methods complete the alert consolidation.
    # signal_engine.py no longer needs its own formatear_alerta_* functions
    # or enviar_alerta — all formatting lives here, with escaped HTML.

    async def send_t1_alert(self, trade: dict, precio_es: float):
        """
        T1 reached — close 75%, move stop to breakeven, leave the runner.
        Receives the active trade dict exactly as signal_engine.py saves it.
        """
        ganancia = precio_es - trade['entrada']
        t2_str   = str(int(trade['t2'])) if trade.get('t2') else 'max possible'
        await self.send(
            f"✅ <b>T1 REACHED — {int(trade['t1'])}</b>\n"
            f"{'─' * 28}\n"
            f"📈 Current gain: <b>+{ganancia:.0f} pts</b>\n"
            f"{'─' * 28}\n"
            f"📋 <b>ACTION (Adam's methodology):</b>\n"
            f"   1️⃣ Close 75% now\n"
            f"   2️⃣ Move stop to BREAKEVEN <b>{trade['entrada']:.0f}</b>\n"
            f"   3️⃣ Leave runner → T2: {t2_str}\n"
            f"{'─' * 28}\n"
            f"🏃 Runner active | Stop: <b>{trade['entrada']:.0f}</b> (breakeven)"
        )

    async def send_t2_alert(self, trade: dict, precio_es: float):
        """
        T2 reached — close the runner. Trade complete.
        """
        ganancia_runner = precio_es - trade['entrada']
        ganancia_t1     = trade['t1'] - trade['entrada']
        await self.send(
            f"🏆 <b>T2 REACHED — {int(trade['t2'])}</b>\n"
            f"{'─' * 28}\n"
            f"📈 Runner gain: <b>+{ganancia_runner:.0f} pts</b>\n"
            f"📈 T1 banked:   <b>+{ganancia_t1:.0f} pts</b> (75%)\n"
            f"{'─' * 28}\n"
            f"📋 Close runner or trail stop very tight\n"
            f"✅ Trade completed per Adam's methodology"
        )

    async def send_stop_alert(self, trade: dict, precio_es: float):
        """
        Stop hit — distinguishes the original stop (loss) vs breakeven (runner).
        """
        if trade.get('t1_alcanzado'):
            ganancia_t1 = trade['t1'] - trade['entrada']
            await self.send(
                f"🔄 <b>RUNNER CLOSED AT BREAKEVEN</b>\n"
                f"{'─' * 28}\n"
                f"📊 Entry: {trade['entrada']:.0f} | Runner exit: {precio_es:.0f}\n"
                f"💰 Runner: ≈0 pts (breakeven)\n"
                f"✅ T1 banked: <b>+{ganancia_t1:.0f} pts</b> on the 75%\n"
                f"{'─' * 28}\n"
                f"📋 Adam: <i>'First trade is a win — stop trading'</i>"
            )
        else:
            perdida = precio_es - trade['entrada']
            await self.send(
                f"🛑 <b>STOP LOSS HIT</b>\n"
                f"{'─' * 28}\n"
                f"📊 Entry: {trade['entrada']:.0f} | Stop: {precio_es:.0f}\n"
                f"📉 Result: <b>{perdida:.0f} pts</b>\n"
                f"{'─' * 28}\n"
                f"📋 Adam: <i>'First trade loss → one more attempt allowed today'</i>"
            )

    async def send_test(self):
        """Sends a test message to verify the connection."""
        tz_ny = pytz.timezone(MARKET_TIMEZONE)
        hora  = datetime.now(tz_ny).strftime('%H:%M EST')
        await self.send(
            f"✅ <b>Adam Mancini Bot — Connected</b>\n\n"
            f"Time: {hora}\n"
            f"Status: All phases operational\n\n"
            f"You'll receive alerts when:\n"
            f"• Price touches one of Adam's levels\n"
            f"• Adam posts an actionable tweet\n"
            f"• Tomorrow's newsletter is ready"
        )
        print("✅ Mensaje de prueba enviado a Telegram")


# ─────────────────────────────────────────────
# Command-line test
# ─────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("  Test Telegram — Bot Adam Mancini")
    print("=" * 50)

    alerter = TelegramAlerter()

    print("📱 Enviando mensaje de prueba...")
    await alerter.send_test()
    await asyncio.sleep(1)

    print("📋 Enviando briefing de ejemplo...")
    today_ejemplo = {
        'date':          '2026-06-10',
        'title':         'Has SPX Moved Into Sell Bounces Mode? June 11 Plan',
        'bias':          'mixed',
        'nivel_critico':  7390,
        'soportes':      [7308, 7280, 7247, 7215, 7200, 7160],
        'resistencias':  [7311, 7320, 7327, 7332, 7344, 7354, 7358, 7364, 7370,
                          7377, 7383, 7390, 7401, 7410, 7415, 7420, 7432, 7438,
                          7451, 7458, 7465, 7472, 7478, 7487, 7495, 7501, 7508,
                          7517, 7527, 7538, 7550, 7558, 7565, 7570, 7577, 7588,
                          7593, 7604, 7620, 7630, 7638, 7643, 7651],
        'setup':         'Mode 2 rangebound (7308-7390). El mercado requiere recuperación de 7390 para confirmar control alcista y acceso a 7527; caída bajo 7296 activa caso bajista con breakdown trades.',
        'invalida_si':   'Cierre sostenido bajo 7296 invalida el bias alcista e inicia caso bajista; o recuperación sostenida sobre 7390 que alcance 7527 confirma ruptura alcista.',
        'is_complete':   True,
    }
    await alerter.send_morning_briefing(today_ejemplo)
    await asyncio.sleep(1)

    print("⚡ Enviando señal de ejemplo...")
    señal_ejemplo = {
        'entrar':     True,
        'direccion':  'long',
        'razon':      'Failed Breakdown en 7308. Bias mixed pero soporte mayor. Vela 15min confirma bounce. Contexto histórico similar muestra 70% de continuación alcista desde este nivel.',
        'entrada_es': 7308,
        'stop_es':    7290,
        'target1_es': 7358,
        'target2_es': 7390,
        'confianza':  0.78,
    }
    await alerter.send_signal_alert(señal_ejemplo, 7310.0, 7308, today_ejemplo)
    await asyncio.sleep(1)

    print("🐦 Enviando alerta de tweet de ejemplo...")
    tweet_ejemplo  = {
        'text':       'ES holding 7308 long here target 7358 stop 7290. Failed breakdown. If loses 7290 adds more risk.',
        'created_at': '2026-06-10T14:32:00+00:00',
    }
    clasif_ejemplo = {
        'tipo':       'signal',
        'accionable': True,
        'direccion':  'long',
        'entrada':    7308,
        'stop':       7290,
        'target':     7358,
        'resumen':    'Long en 7308 con target 7358 y stop 7290',
    }
    await alerter.send_tweet_alert(tweet_ejemplo, clasif_ejemplo)

    print("\n✅ Test completado — revisa tu Telegram")


if __name__ == '__main__':
    asyncio.run(main())
