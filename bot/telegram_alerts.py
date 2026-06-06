"""
bot/telegram_alerts.py — Sistema de alertas por Telegram
=========================================================
Envía tres tipos de mensajes al móvil:

1. 📋 Briefing matutino  → resumen del newsletter de Adam al abrir mercado
2. ⚡ Alerta de señal    → cuando el motor detecta un setup accionable
3. 🐦 Tweet de Adam      → cuando Adam postea algo accionable en X

USO (test rápido):
    python bot/telegram_alerts.py

Para conectar al motor de señales, importa TelegramAlerter y úsalo
en signal_engine.py y tweet_monitor.py (ver al final del archivo).
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
# Clase principal
# ─────────────────────────────────────────────

class TelegramAlerter:
    """
    Envía mensajes formateados al chat de Telegram configurado en .env.
    Todos los métodos son async — úsalos con await.
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
        """
        Envía un mensaje al chat configurado.
        Usa HTML para el formato (negrita, cursiva, monospace).
        """
        try:
            await self.bot.send_message(
                chat_id    = self.chat_id,
                text       = text,
                parse_mode = parse_mode,
            )
        except TelegramError as e:
            print(f"  ❌ Error Telegram: {e}")

    # ─────────────────────────────────────────────
    # Tipo 1 — Briefing matutino
    # ─────────────────────────────────────────────

    async def send_morning_briefing(self, today: dict):
        """
        Envía el resumen del newsletter al empezar el día.
        Se llama desde newsletter_parser.py a las 7:30 AM EST.

        Formato:
            📋 PLAN DE ADAM — Lunes 9 Jun
            ─────────────────────────────
            Bias:     🟢 BULLISH
            Crítico:  7,527
            Soportes: 7,326 · 7,410 · 7,458
            ...
        """
        bias     = today.get('bias', 'unknown').upper()
        bias_emo = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪', 'MIXED': '🟡'}.get(bias, '❓')

        # Formatear niveles como lista con punto de separación
        soportes   = ' · '.join(str(int(n)) for n in today.get('soportes', []) if n)
        resistencias = ' · '.join(str(int(n)) for n in today.get('resistencias', []) if n)

        # Fecha legible
        fecha_raw = today.get('date', str(datetime.today().date()))
        try:
            fecha_obj = datetime.strptime(fecha_raw, '%Y-%m-%d')
            dias_es   = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
            meses_es  = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
                         'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
            fecha_str = f"{dias_es[fecha_obj.weekday()]} {fecha_obj.day} {meses_es[fecha_obj.month-1]}"
        except Exception:
            fecha_str = fecha_raw

        titulo  = html.escape(today.get('title', '')[:60])
        setup   = html.escape((today.get('setup') or '')[:150])
        invalida = html.escape((today.get('invalida_si') or '')[:100])

        lineas = [
            f"📋 <b>PLAN DE ADAM — {fecha_str}</b>",
            "─" * 30,
        ]
        if titulo:
            lineas.append(f"📰 <i>{titulo}</i>\n")

        lineas += [
            f"Bias:      {bias_emo} <b>{bias}</b>",
        ]
        if today.get('nivel_critico'):
            lineas.append(f"Crítico:   <b>{int(today['nivel_critico'])}</b>")
        if soportes:
            lineas.append(f"Soportes:  {soportes}")
        if resistencias:
            lineas.append(f"Resists:   {resistencias}")
        if setup:
            lineas += ["", f"💭 {setup}"]
        if invalida:
            lineas.append(f"⚠️ Invalida si: {invalida}")

        if not today.get('is_complete'):
            lineas += ["", "💡 <i>Preview — suscríbete para análisis completo</i>"]

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Tipo 2 — Alerta de señal del motor
    # ─────────────────────────────────────────────

    async def send_signal_alert(self, señal: dict, precio_es: float, nivel: float, today: dict):
        """
        Envía una alerta de trading cuando el motor detecta un setup.
        Incluye entrada, stop loss, targets y ratio R/R.

        Formato:
            🟢 LONG ES — Adam Mancini
            ──────────────────────────
            📍 Entrada:   7,527
            🛑 Stop:      7,508
            🎯 Target 1:  7,604
            🎯 Target 2:  7,620
            📐 R/R:       1:4.1
            ──────────────────────────
            💭 Failed breakdown en 7527...
            🎯 Confianza: 82%  ⏰ 14:32
        """
        # Determinar dirección desde la razón o datos
        razon = (señal.get('razon') or '').lower()
        es_long = 'long' in razon or señal.get('entrada_es', nivel) <= nivel

        dir_emo  = '🟢' if es_long else '🔴'
        dir_text = 'LONG' if es_long else 'SHORT'

        entrada = señal.get('entrada_es', nivel)
        stop    = señal.get('stop_es')
        t1      = señal.get('target1_es')
        t2      = señal.get('target2_es')
        conf    = señal.get('confianza', 0)

        # Ratio R/R
        rr_linea = ''
        if entrada and stop and t1:
            riesgo  = abs(float(entrada) - float(stop))
            reward1 = abs(float(t1) - float(entrada))
            if riesgo > 0:
                rr = reward1 / riesgo
                rr_linea = f"\n📐 R/R:       1:{rr:.1f}"

        # Hora EST
        tz_ny = pytz.timezone(MARKET_TIMEZONE)
        hora  = datetime.now(tz_ny).strftime('%H:%M')

        razon_escaped = html.escape((señal.get('razon') or '')[:150])

        lineas = [
            f"{dir_emo} <b>{dir_text} ES — Adam Mancini</b>",
            "─" * 28,
            f"📍 Entrada:   <b>{entrada}</b>",
        ]
        if stop:
            lineas.append(f"🛑 Stop:      <b>{stop}</b>")
        if t1:
            lineas.append(f"🎯 Target 1:  <b>{t1}</b>")
        if t2:
            lineas.append(f"🎯 Target 2:  <b>{t2}</b>")

        lineas.append("─" * 28)
        if rr_linea:
            lineas.append(rr_linea.strip())

        lineas += [
            f"📊 Nivel:     {int(nivel)} | Bias: {today.get('bias','?').upper()}",
            f"💭 {razon_escaped}",
            f"🎯 Confianza: <b>{conf:.0%}</b>  ⏰ {hora} EST",
        ]

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Tipo 3 — Tweet nuevo de Adam
    # ─────────────────────────────────────────────

    async def send_tweet_alert(self, tweet: dict, clasificacion: dict):
        """
        Envía alerta cuando Adam publica un tweet accionable.
        Incluye el texto original + los niveles extraídos.

        Formato:
            ⚡ ADAM TWEETEA — SEÑAL
            ──────────────────────────
            "ES holding 7527 long here..."
            ──────────────────────────
            🟢 LONG | Entrada: 7527 | SL: 7508 | TP: 7604
        """
        tipo    = clasificacion.get('tipo', 'comentario')
        accion  = clasificacion.get('accionable', False)

        tipo_emo = {'senal': '⚡', 'nivel': '📍', 'comentario': '💬'}.get(tipo, '🐦')
        tipo_txt = {'senal': 'SEÑAL ACCIONABLE', 'nivel': 'ACTUALIZACIÓN NIVELES',
                    'comentario': 'COMENTARIO'}.get(tipo, tipo.upper())

        texto   = html.escape((tweet.get('text') or '')[:300])
        resumen = html.escape((clasificacion.get('resumen') or '')[:100])

        hora_raw = tweet.get('created_at', '')
        hora_str = hora_raw[11:16] if len(hora_raw) > 15 else ''

        lineas = [
            f"{tipo_emo} <b>ADAM TWEETEA — {tipo_txt}</b>",
            "─" * 28,
            f'<i>"{texto}"</i>',
            "─" * 28,
        ]

        if resumen:
            lineas.append(f"📊 {resumen}")

        # Si es señal accionable, añadir detalles
        if accion:
            direccion = (clasificacion.get('direccion') or '').upper()
            entrada   = clasificacion.get('entrada')
            stop      = clasificacion.get('stop')
            target    = clasificacion.get('target')

            if direccion and entrada:
                dir_emo = '🟢' if direccion == 'LONG' else '🔴'
                trade_str = f"{dir_emo} {direccion}"
                if entrada: trade_str += f" | Entrada: {entrada}"
                if stop:    trade_str += f" | SL: {stop}"
                if target:  trade_str += f" | TP: {target}"
                lineas.append(f"\n<b>{trade_str}</b>")

        if hora_str:
            lineas.append(f"\n⏰ {hora_str} UTC")

        await self.send('\n'.join(lineas))

    # ─────────────────────────────────────────────
    # Test
    # ─────────────────────────────────────────────

    async def send_test(self):
        """Envía un mensaje de prueba para verificar la conexión."""
        tz_ny = pytz.timezone(MARKET_TIMEZONE)
        hora  = datetime.now(tz_ny).strftime('%H:%M EST')

        await self.send(
            f"✅ <b>Bot Adam Mancini — Conectado</b>\n\n"
            f"Hora: {hora}\n"
            f"Estado: Todas las fases operativas\n\n"
            f"Recibirás alertas cuando:\n"
            f"• El precio toque un nivel de Adam\n"
            f"• Adam publique un tweet accionable\n"
            f"• Newsletter de mañana esté listo"
        )
        print("✅ Mensaje de prueba enviado a Telegram")


# ─────────────────────────────────────────────
# Test desde línea de comandos
# ─────────────────────────────────────────────

async def main():
    """
    Prueba la conexión y todos los tipos de mensaje.
    Ejecuta: python bot/telegram_alerts.py
    """
    print("=" * 50)
    print("  Test Telegram — Bot Adam Mancini")
    print("=" * 50)

    alerter = TelegramAlerter()

    # 1. Test básico
    print("📱 Enviando mensaje de prueba...")
    await alerter.send_test()

    await asyncio.sleep(1)

    # 2. Simular briefing matutino
    print("📋 Enviando briefing de ejemplo...")
    today_ejemplo = {
        'date':         '2026-06-09',
        'title':        'SPX Continues To Coil. Another Big Move Coming?',
        'bias':         'bullish',
        'nivel_critico': 7527,
        'soportes':     [7326, 7410, 7458],
        'resistencias': [7527, 7604, 7620],
        'setup':        'Failed Breakdown en 7527. Mantener abre camino a 7604.',
        'invalida_si':  'Perder 7326 de forma sostenida',
        'is_complete':  False,
    }
    await alerter.send_morning_briefing(today_ejemplo)

    await asyncio.sleep(1)

    # 3. Simular señal
    print("⚡ Enviando señal de ejemplo...")
    señal_ejemplo = {
        'entrar':     True,
        'razon':      'Long en soporte 7527. Bias bullish del newsletter. Vela 15min confirma bounce.',
        'entrada_es': 7527,
        'stop_es':    7508,
        'target1_es': 7604,
        'target2_es': 7620,
        'confianza':  0.82,
    }
    await alerter.send_signal_alert(señal_ejemplo, 7530.0, 7527, today_ejemplo)

    await asyncio.sleep(1)

    # 4. Simular tweet
    print("🐦 Enviando alerta de tweet de ejemplo...")
    tweet_ejemplo = {
        'text':       'ES holding 7527 long here target 7604 stop 7508. Failure loses 7410',
        'created_at': '2026-06-09T14:32:00+00:00',
    }
    clasif_ejemplo = {
        'tipo':       'senal',
        'accionable': True,
        'direccion':  'long',
        'entrada':    7527,
        'stop':       7508,
        'target':     7604,
        'resumen':    'Long en 7527 con target 7604 y stop 7508',
    }
    await alerter.send_tweet_alert(tweet_ejemplo, clasif_ejemplo)

    print("\n✅ Test completado — revisa tu Telegram")


if __name__ == '__main__':
    asyncio.run(main())
