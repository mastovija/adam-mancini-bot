"""
parsers/tweet_monitor.py — Monitor de tweets en tiempo real con Playwright
===========================================================================
Comprueba cada 3 minutos si Adam Mancini ha publicado tweets nuevos.
En horario de mercado (9:30-16:00 EST), ante un tweet nuevo:
  - Si es una señal accionable → alerta Telegram inmediata (Fase 6)
  - Si es una actualización de niveles → actualiza el contexto del día
  - Si es comentario general → registra para contexto

USO:
    python parsers/tweet_monitor.py

CÓMO FUNCIONA:
    1. Cada 3 minutos abre Chromium brevemente (~5 segundos)
    2. Navega a x.com/AdamMancini4 con tus cookies
    3. Intercepta la llamada UserTweets que hace el navegador
    4. Compara con el último tweet visto
    5. Si hay nuevos → los clasifica con Claude Haiku
    6. Cierra el navegador y espera 3 minutos
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from bot.telegram_alerts import TelegramAlerter


import anthropic
import pytz

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    TWITTER_TARGET,
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    RAW_DIR,
    DATA_DIR,
    MARKET_TIMEZONE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)
from parsers.playwright_utils import extract_tweets, crear_contexto_con_cookies

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright no instalado.")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────
PROFILE_URL      = f'https://x.com/{TWITTER_TARGET}'
POLL_INTERVAL    = 180   # segundos entre checks (3 minutos)
STATE_FILE       = DATA_DIR / 'tweet_monitor_state.json'


# ─────────────────────────────────────────────
# Estado del monitor
# ─────────────────────────────────────────────

def cargar_estado() -> dict:
    """
    Carga el estado del monitor: último tweet visto, contexto del día.
    Persiste entre reinicios del bot.
    """
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        'ultimo_tweet_id': None,
        'ultimo_check': None,
        'tweets_hoy': [],
    }


def guardar_estado(estado: dict):
    """Guarda el estado del monitor en disco."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(estado, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Horario de mercado
# ─────────────────────────────────────────────

def en_horario_mercado() -> bool:
    """
    Comprueba si estamos en horario de mercado NYSE.
    El monitor solo corre activamente de 9:30 a 16:00 EST.
    """
    tz_ny = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz_ny)

    # Solo días de semana (lunes=0 ... viernes=4)
    if ahora.weekday() >= 5:
        return False

    apertura = ahora.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    cierre   = ahora.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)

    return apertura <= ahora <= cierre


# ─────────────────────────────────────────────
# Obtener tweets más recientes
# ─────────────────────────────────────────────

async def obtener_tweets_recientes() -> list:
    """
    Abre Chromium brevemente, carga el perfil de Adam y captura los tweets más recientes.
    Solo tarda ~5 segundos — lo justo para interceptar la llamada UserTweets.

    Returns:
        Lista de tweets recientes (los ~20 más nuevos)
    """
    tweets_capturados = []

    async with async_playwright() as p:
        browser, context = await crear_contexto_con_cookies(p)
        page = await context.new_page()

        async def capturar(response):
            if 'UserTweets' in response.url and response.status == 200:
                try:
                    data = await response.json()
                    tweets = extract_tweets(data.get('data', {}))
                    tweets_capturados.extend(tweets)
                except Exception:
                    pass

        page.on('response', capturar)

        try:
            await page.goto(PROFILE_URL, wait_until='load', timeout=20000)
        except Exception:
            pass

        await page.wait_for_timeout(3000)
        await browser.close()

    return tweets_capturados


# ─────────────────────────────────────────────
# Clasificación con LLM
# ─────────────────────────────────────────────

CLASIFICACION_PROMPT = """Analiza este tweet de Adam Mancini, trader del S&P 500/ES.

Tweet: "{texto}"
Fecha: {fecha}

Clasifícalo respondiendo SOLO con JSON válido:
{{
  "tipo": "senal" | "nivel" | "comentario" | "otro",
  "accionable": true | false,
  "direccion": "long" | "short" | null,
  "entrada": número o null,
  "stop": número o null,
  "target": número o null,
  "niveles_mencionados": [lista de números],
  "resumen": "una frase corta de lo que dice"
}}

"senal": entrada específica con dirección y niveles
"nivel": actualización de soporte/resistencia sin entrada
"comentario": análisis general sin niveles específicos
"otro": irrelevante para trading"""


def clasificar_tweet(texto: str, fecha: str) -> dict:
    """
    Usa Claude Haiku para clasificar un tweet de Adam.
    Determina si es una señal accionable, actualización de niveles, o comentario.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = CLASIFICACION_PROMPT.format(texto=texto[:500], fecha=fecha)

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        # Limpiar markdown si aparece
        import re
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        return {
            "tipo": "otro",
            "accionable": False,
            "resumen": texto[:100],
            "error": str(e)
        }


# ─────────────────────────────────────────────
# Telegram (placeholder hasta Fase 6)
# ─────────────────────────────────────────────

async def enviar_telegram(mensaje: str):
    print(f"📱 {mensaje}")
    try:
        alerter = TelegramAlerter()
        await alerter.send(mensaje)
    except Exception as e:
        print(f"  ⚠️  Error Telegram: {e}")


# ─────────────────────────────────────────────
# Loop principal del monitor
# ─────────────────────────────────────────────

async def monitorizar():
    """
    Loop principal: cada 3 minutos comprueba tweets nuevos de Adam.
    En horario de mercado, clasifica y alerta si hay señales.
    """
    print("=" * 55)
    print("  Bot Adam Mancini — Monitor de Tweets en Directo")
    print("=" * 55)
    print(f"🎯 Monitorizando: @{TWITTER_TARGET}")
    print(f"⏱️  Intervalo: cada {POLL_INTERVAL // 60} minutos")
    print(f"🕐 Horario activo: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MIN:02d}–"
          f"{MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MIN:02d} EST\n")
    print("Ctrl+C para parar\n")

    estado = cargar_estado()

    while True:
        ahora_str = datetime.now().strftime('%H:%M:%S')
        en_mercado = en_horario_mercado()

        print(f"[{ahora_str}] Comprobando tweets... "
              f"({'🟢 mercado abierto' if en_mercado else '🔴 fuera de mercado'})")

        try:
            # ── Obtener tweets recientes ──────────────────────────────────
            tweets_recientes = await obtener_tweets_recientes()

            if not tweets_recientes:
                print(f"  ⚠️  Sin datos — posible problema de conexión")
            else:
                # ── Filtrar tweets nuevos ─────────────────────────────────
                ultimo_id = estado.get('ultimo_tweet_id')
                tweets_nuevos = []

                for tweet in tweets_recientes:
                    if not tweet.get('id'):
                        continue
                    if ultimo_id and tweet['id'] <= ultimo_id:
                        break  # Los tweets vienen ordenados del más nuevo al más viejo
                    tweets_nuevos.append(tweet)

                # Actualizar último tweet visto
                if tweets_recientes:
                    estado['ultimo_tweet_id'] = tweets_recientes[0]['id']
                    estado['ultimo_check']     = datetime.now().isoformat()

                # ── Procesar tweets nuevos ────────────────────────────────
                if tweets_nuevos:
                    print(f"  🆕 {len(tweets_nuevos)} tweet(s) nuevo(s)")

                    for tweet in tweets_nuevos:
                        texto = tweet.get('text', '')
                        fecha = tweet.get('created_at', '')

                        # Ignorar retweets
                        if texto.startswith('RT @'):
                            continue

                        print(f"  📝 [{fecha}] {texto[:80]}...")

                        # Solo clasificar en horario de mercado
                        if en_mercado:
                            clasificacion = clasificar_tweet(texto, fecha)
                            tipo = clasificacion.get('tipo', 'otro')

                            if clasificacion.get('accionable'):
                                # ── Señal accionable → alerta inmediata ──
                                direccion = clasificacion.get('direccion', '').upper()
                                entrada   = clasificacion.get('entrada')
                                stop      = clasificacion.get('stop')
                                target    = clasificacion.get('target')

                                mensaje = (
                                    f"⚡ TWEET DE ADAM — {tipo.upper()}\n"
                                    f"{'─' * 30}\n"
                                    f"📝 {texto[:200]}\n"
                                    f"{'─' * 30}\n"
                                    f"📊 {clasificacion.get('resumen', '')}\n"
                                )
                                if entrada:
                                    emoji = '🟢' if direccion == 'LONG' else '🔴'
                                    mensaje += (
                                        f"{emoji} {direccion} | "
                                        f"Entrada: {entrada} | "
                                        f"SL: {stop} | "
                                        f"TP: {target}\n"
                                    )

                                await enviar_telegram(mensaje)
                                print(f"     ✅ Señal: {direccion} | entrada {entrada}")

                            elif tipo == 'nivel':
                                print(f"     📍 Niveles: {clasificacion.get('niveles_mencionados', [])}")

                        # Guardar todos los tweets del día para contexto
                        estado.setdefault('tweets_hoy', []).append({
                            'tweet': tweet,
                            'clasificacion': clasificacion if en_mercado else {}
                        })

                else:
                    print(f"  ✓ Sin tweets nuevos")

                guardar_estado(estado)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        # ── Esperar hasta el próximo check ────────────────────────────────
        print(f"  ⏳ Próximo check en {POLL_INTERVAL // 60} minutos\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        asyncio.run(monitorizar())
    except KeyboardInterrupt:
        print("\n⏹️  Monitor detenido")
