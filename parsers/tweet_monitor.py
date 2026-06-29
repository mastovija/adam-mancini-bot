"""
parsers/tweet_monitor.py — Monitor de tweets en tiempo real con Playwright
===========================================================================
Comprueba cada 3 minutos si Adam Mancini ha publicado tweets nuevos.
En horario de mercado, ante un tweet nuevo:
  - Si es una señal accionable NUEVA → alerta Telegram inmediata
  - Si es una actualización de niveles → registra para contexto
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

FIXES APLICADOS:
  - El LLM ahora distingue entre "señal nueva ahora" vs "Adam comentando
    un trade pasado" (ayer, ystd, last night, etc.). Antes clasificaba
    "We got a long YESTERDAY at 3:45PM" como señal accionable de hoy.
  - Validación matemática: stop < entry para LONG, stop > entry para SHORT.
    Si el LLM genera stop=7535 para un long en 7485, la señal se descarta.
"""

import asyncio
import json
import sys
# import re  ← eliminado C-14: sin uso tras reemplazar regex por str.find/rfind
from datetime import datetime  # timezone eliminado C-14: sólo pytz.timezone() se usa aquí
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import anthropic
import pytz

from bot.telegram_alerts import TelegramAlerter

from config import (
    TWITTER_TARGET,
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RAW_DIR ← eliminados C-14:
    # los usaba enviar_telegram() que se eliminó en C-11.
    # TelegramAlerter lee sus propias credenciales desde config internamente.
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
PROFILE_URL   = f'https://x.com/{TWITTER_TARGET}'
POLL_INTERVAL = 180   # segundos entre checks (3 minutos)
STATE_FILE    = DATA_DIR / 'tweet_monitor_state.json'

# Palabras que indican que Adam habla de un trade PASADO, no uno nuevo ahora.
# Si el tweet contiene alguna de estas palabras → NO es señal accionable.
PALABRAS_PASADO = [
    'yesterday', 'ystd', 'last night', 'last week', 'this morning (was)',
    'earlier today', 'we got', 'we were', 'triggered yesterday',
    'posted yesterday', 'given in advance', 'as posted', 'as i posted',
    'in the books', 'paid', 'hit our target', 'already', 'was a great',
]


# ─────────────────────────────────────────────
# Estado del monitor
# ─────────────────────────────────────────────

def cargar_estado() -> dict:
    """
    Carga el estado del monitor: último tweet visto, tweets del día.
    Persiste entre reinicios del bot.
    """
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        'ultimo_tweet_id': None,
        'ultimo_check':    None,
        'tweets_hoy':      [],
        'fecha_hoy':       None,
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
    """Comprueba si estamos en horario activo (7:30-16:00 EST)."""
    tz_ny = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz_ny)

    if ahora.weekday() >= 5:
        return False

    apertura = ahora.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    cierre   = ahora.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return apertura <= ahora <= cierre


# ─────────────────────────────────────────────
# Obtener tweets recientes
# ─────────────────────────────────────────────

async def obtener_tweets_recientes() -> list:
    """
    Abre Chromium brevemente, carga el perfil de Adam y captura tweets recientes.
    Solo tarda ~5 segundos.
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

CLASIFICACION_PROMPT = """Analiza este tweet de Adam Mancini, trader del S&P 500/ES futures.

Tweet: "{texto}"
Fecha del tweet: {fecha}

═══════════════════════════════════════════════════
DISTINCIÓN CRÍTICA — LEE CON ATENCIÓN:
═══════════════════════════════════════════════════
Solo marcar accionable=true si Adam está anunciando una entrada AHORA MISMO
o dando instrucciones para actuar EN ESTE MOMENTO.

NUNCA marcar accionable=true si Adam está:
  - Describiendo un trade que hizo ayer o antes ("yesterday", "ystd", "last night",
    "we got a long at 3:45PM ystd", "as posted", "in the books", "paid", "triggered")
  - Haciendo recap o resumen de operaciones pasadas
  - Diciendo que ya alcanzó un target ("7558 1st target hit")
  - Comentando o analizando sin dar instrucción de entrada nueva

SEÑALES REALES (accionable=true) suenan así:
  - "Going long here at 7485"
  - "Long trigger at 7502 if ES recovers"
  - "Buy the Failed Breakdown of 7509 NOW"
  - "Entering long at 7478"

NO SEÑALES (accionable=false) suenan así:
  - "We got a massive long yesterday at 7485" ← PASADO
  - "Longs triggered yesterday at 3:45PM" ← PASADO
  - "7558 1st target hit" ← RESULTADO de trade pasado
  - "Don't trade, OPEX noise" ← consejo general sin entrada
  - "Watch 7535 as support" ← nivel, no entrada

═══════════════════════════════════════════════════
VALIDACIÓN MATEMÁTICA OBLIGATORIA:
═══════════════════════════════════════════════════
Si accionable=true y direccion="long":
  - stop DEBE ser MENOR que entrada (el stop de un long es abajo)
  - Ejemplo correcto: entrada=7485, stop=7470 ✓
  - Ejemplo INCORRECTO: entrada=7485, stop=7535 ✗ (imposible — sería un short)

Si accionable=true y direccion="short":
  - stop DEBE ser MAYOR que entrada
  - Ejemplo correcto: entrada=7558, stop=7570 ✓

Si los niveles del tweet no permiten cumplir esta regla → accionable=false.

═══════════════════════════════════════════════════
Responde SOLO con JSON válido, sin texto adicional:
{{
  "tipo": "senal" | "nivel" | "comentario" | "otro",
  "accionable": true | false,
  "es_referencia_pasada": true | false,
  "direccion": "long" | "short" | null,
  "entrada": número o null,
  "stop": número o null,
  "target": número o null,
  "niveles_mencionados": [lista de números ES],
  "resumen": "una frase corta de lo que dice Adam en este tweet"
}}"""


def tweet_es_referencia_pasada(texto: str) -> bool:
    """
    Filtro rápido de palabras clave ANTES de llamar al LLM.
    Si el tweet claramente habla de ayer o antes, lo descartamos
    como señal accionable sin gastar tokens en el LLM.

    Devuelve True si el tweet parece ser una referencia a un trade pasado.
    """
    texto_lower = texto.lower()
    return any(palabra in texto_lower for palabra in PALABRAS_PASADO)


async def clasificar_tweet(texto: str, fecha: str) -> dict:
    """
    Usa Claude Haiku para clasificar un tweet de Adam.

    FIX C-13: clasificar_tweet es llamada desde monitorizar() (async).
    La llamada síncrona al LLM bloqueaba el event loop de Playwright.
    asyncio.to_thread() la corre en un thread sin bloquear.

    Incluye dos capas de protección contra falsos positivos:
    1. Filtro de palabras clave de pasado (antes del LLM, gratis)
    2. Prompt del LLM con instrucciones explícitas sobre pasado vs presente
    3. Validación matemática de stop < entry para longs (post-LLM)
    """
    # Capa 1: filtro rápido de palabras de pasado
    # Si el tweet menciona "yesterday", "ystd", etc. → no es señal accionable
    # Aun así llamamos al LLM para clasificar tipo y niveles, pero forzamos
    # accionable=False antes de que pueda disparar una alerta
    es_pasado = tweet_es_referencia_pasada(texto)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = CLASIFICACION_PROMPT.format(texto=texto[:600], fecha=fecha)

    try:
        # C-13: to_thread libera el event loop de Playwright durante la llamada LLM.
        response = await asyncio.to_thread(
            client.messages.create,
            model      = LLM_MODEL,
            max_tokens = 350,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Mismo fix que generar_señal_llm: extraer el primer objeto JSON
        # del texto para no romperse con texto extra o markdown del LLM.
        inicio = raw.find('{')
        fin    = raw.rfind('}')
        if inicio != -1 and fin != -1 and fin > inicio:
            raw = raw[inicio:fin + 1]
        clasificacion = json.loads(raw)
    except Exception as e:
        return {
            "tipo": "otro",
            "accionable": False,
            "resumen": texto[:100],
            "error": str(e)
        }

    # Capa 2: si nuestro filtro detectó palabras de pasado, forzar accionable=False
    # aunque el LLM haya dicho true (el LLM puede equivocarse)
    if es_pasado or clasificacion.get('es_referencia_pasada'):
        clasificacion['accionable'] = False
        clasificacion['_motivo_rechazo'] = 'referencia a trade pasado'
        return clasificacion

    # Capa 3: validación matemática de stop vs entry
    # Un long con stop mayor que la entrada es matemáticamente imposible
    if clasificacion.get('accionable'):
        entrada   = clasificacion.get('entrada')
        stop      = clasificacion.get('stop')
        direccion = (clasificacion.get('direccion') or '').lower()

        if entrada and stop:
            if direccion == 'long' and float(stop) >= float(entrada):
                clasificacion['accionable'] = False
                clasificacion['_motivo_rechazo'] = (
                    f'stop ({stop}) >= entrada ({entrada}) en LONG — matemáticamente imposible'
                )
            elif direccion == 'short' and float(stop) <= float(entrada):
                clasificacion['accionable'] = False
                clasificacion['_motivo_rechazo'] = (
                    f'stop ({stop}) <= entrada ({entrada}) en SHORT — matemáticamente imposible'
                )

    return clasificacion


# ─────────────────────────────────────────────
# Loop principal del monitor
# ─────────────────────────────────────────────

async def monitorizar():
    """
    Loop principal: cada 3 minutos comprueba tweets nuevos de Adam.
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
        ahora_str  = datetime.now().strftime('%H:%M:%S')
        en_mercado = en_horario_mercado()

        # Resetear tweets_hoy si cambió el día
        hoy = datetime.now().strftime('%Y-%m-%d')
        if estado.get('fecha_hoy') != hoy:
            estado['tweets_hoy'] = []
            estado['fecha_hoy']  = hoy

        print(f"[{ahora_str}] Comprobando tweets... "
              f"({'🟢 mercado abierto' if en_mercado else '🔴 fuera de mercado'})")

        try:
            tweets_recientes = await obtener_tweets_recientes()

            if not tweets_recientes:
                print(f"  ⚠️  Sin datos — posible problema de conexión")
            else:
                tweets_recientes.sort(
                    key=lambda t: int(t.get('id', 0)), reverse=True
                )

                ultimo_id = estado.get('ultimo_tweet_id')
                tweets_nuevos = []

                for tweet in tweets_recientes:
                    if not tweet.get('id'):
                        continue
                    if ultimo_id and int(tweet['id']) <= int(ultimo_id):
                        break
                    tweets_nuevos.append(tweet)

                if tweets_recientes:
                    estado['ultimo_tweet_id'] = str(tweets_recientes[0]['id'])
                    estado['ultimo_check']     = datetime.now().isoformat()

                if tweets_nuevos:
                    print(f"  🆕 {len(tweets_nuevos)} tweet(s) nuevo(s)")

                    for tweet in tweets_nuevos:
                        texto = tweet.get('text', '')
                        fecha = tweet.get('created_at', '')

                        if texto.startswith('RT @'):
                            continue

                        print(f"  📝 [{fecha}] {texto[:80]}...")

                        if en_mercado:
                            # C-13: clasificar_tweet es ahora async
                            clasificacion = await clasificar_tweet(texto, fecha)
                            tipo = clasificacion.get('tipo', 'otro')

                            # ── Si fue rechazado → mostrar motivo en log ──
                            motivo = clasificacion.get('_motivo_rechazo')
                            if motivo:
                                print(f"     ⚠️  Descartado: {motivo}")

                            if clasificacion.get('accionable'):
                                # C-11: usar send_tweet_alert del alerter
                                # (HTML escapado, formato consistente con las demás alertas)
                                # en lugar de construir el texto plano aquí.
                                alerter = TelegramAlerter()
                                await alerter.send_tweet_alert(tweet, clasificacion)
                                direccion = (clasificacion.get('direccion') or '').upper()
                                entrada   = clasificacion.get('entrada')
                                print(f"     ✅ Señal: {direccion} | entrada {entrada}")

                            elif tipo == 'nivel':
                                print(f"     📍 Niveles: {clasificacion.get('niveles_mencionados', [])}")

                        # Guardar todos los tweets del día para contexto del LLM
                        estado.setdefault('tweets_hoy', []).append({
                            'tweet':        tweet,
                            'clasificacion': clasificacion if en_mercado else {}
                        })

                else:
                    print(f"  ✓ Sin tweets nuevos")

                guardar_estado(estado)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        print(f"  ⏳ Próximo check en {POLL_INTERVAL // 60} minutos\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        asyncio.run(monitorizar())
    except KeyboardInterrupt:
        print("\n⏹️  Monitor detenido")
