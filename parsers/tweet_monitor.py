"""
parsers/tweet_monitor.py — Real-time tweet monitor with Playwright
===========================================================================
Checks every 3 minutes whether Adam Mancini has posted new tweets.
During market hours, on a new tweet:
  - If it's a NEW actionable signal → immediate Telegram alert
  - If it's a levels update → logged for context
  - If it's general commentary → logged for context

USAGE:
    python parsers/tweet_monitor.py

HOW IT WORKS:
    1. Every 3 minutes it opens Chromium briefly (~5 seconds)
    2. Navigates to x.com/AdamMancini4 with your cookies
    3. Intercepts the UserTweets call the browser makes
    4. Compares against the last tweet seen
    5. If there are new ones → classifies them with Claude Haiku
    6. Closes the browser and waits 3 minutes

FIXES APPLIED:
  - The LLM now distinguishes between "a new signal now" vs "Adam commenting
    on a past trade" (yesterday, ystd, last night, etc.). It used to classify
    "We got a long YESTERDAY at 3:45PM" as an actionable signal for today.
  - Mathematical validation: stop < entry for LONG, stop > entry for SHORT.
    If the LLM produces stop=7535 for a long at 7485, the signal is discarded.
"""

import asyncio
import json
import sys
# import re  ← removed C-14: unused after replacing regex with str.find/rfind
from datetime import datetime  # timezone removed C-14: only pytz.timezone() is used here
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import anthropic
import pytz

from bot.telegram_alerts import TelegramAlerter

from config import (
    TWITTER_TARGET,
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RAW_DIR ← removed C-14:
    # they were used by enviar_telegram(), which was removed in C-11.
    # TelegramAlerter reads its own credentials from config internally.
    DATA_DIR,
    MARKET_TIMEZONE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)
from parsers.playwright_utils import extract_tweets, crear_contexto_con_cookies

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright not installed.")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
PROFILE_URL   = f'https://x.com/{TWITTER_TARGET}'
POLL_INTERVAL = 180   # seconds between checks (3 minutes)
STATE_FILE    = DATA_DIR / 'tweet_monitor_state.json'

# Words that indicate Adam is talking about a PAST trade, not a new one now.
# If the tweet contains any of these words → it is NOT an actionable signal.
PALABRAS_PASADO = [
    'yesterday', 'ystd', 'last night', 'last week', 'this morning (was)',
    'earlier today', 'we got', 'we were', 'triggered yesterday',
    'posted yesterday', 'given in advance', 'as posted', 'as i posted',
    'in the books', 'paid', 'hit our target', 'already', 'was a great',
]


# ─────────────────────────────────────────────
# Monitor state
# ─────────────────────────────────────────────

def cargar_estado() -> dict:
    """
    Loads the monitor state: last tweet seen, today's tweets.
    Persists across bot restarts.
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
    """Saves the monitor state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(estado, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Market hours
# ─────────────────────────────────────────────

def en_horario_mercado() -> bool:
    """Checks whether we're in active hours (7:30-16:00 EST)."""
    tz_ny = pytz.timezone(MARKET_TIMEZONE)
    ahora = datetime.now(tz_ny)

    if ahora.weekday() >= 5:
        return False

    apertura = ahora.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    cierre   = ahora.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return apertura <= ahora <= cierre


# ─────────────────────────────────────────────
# Fetch recent tweets
# ─────────────────────────────────────────────

async def obtener_tweets_recientes() -> list:
    """
    Opens Chromium briefly, loads Adam's profile and captures recent tweets.
    Only takes ~5 seconds.
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
# Classification with the LLM
# ─────────────────────────────────────────────

CLASIFICACION_PROMPT = """Analyze this tweet from Adam Mancini, an S&P 500/ES futures trader.

Tweet: "{texto}"
Tweet date: {fecha}

═══════════════════════════════════════════════════
CRITICAL DISTINCTION — READ CAREFULLY:
═══════════════════════════════════════════════════
Only set accionable=true if Adam is announcing an entry RIGHT NOW
or giving instructions to act AT THIS MOMENT.

NEVER set accionable=true if Adam is:
  - Describing a trade he took yesterday or earlier ("yesterday", "ystd", "last night",
    "we got a long at 3:45PM ystd", "as posted", "in the books", "paid", "triggered")
  - Recapping or summarizing past trades
  - Saying a target was already hit ("7558 1st target hit")
  - Commenting or analyzing without giving a new entry instruction

REAL SIGNALS (accionable=true) sound like this:
  - "Going long here at 7485"
  - "Long trigger at 7502 if ES recovers"
  - "Buy the Failed Breakdown of 7509 NOW"
  - "Entering long at 7478"

NON-SIGNALS (accionable=false) sound like this:
  - "We got a massive long yesterday at 7485" ← PAST
  - "Longs triggered yesterday at 3:45PM" ← PAST
  - "7558 1st target hit" ← RESULT of a past trade
  - "Don't trade, OPEX noise" ← general advice, no entry
  - "Watch 7535 as support" ← a level, not an entry

═══════════════════════════════════════════════════
MANDATORY MATHEMATICAL VALIDATION:
═══════════════════════════════════════════════════
If accionable=true and direccion="long":
  - stop MUST be LOWER than entrada (a long's stop is below)
  - Correct example: entrada=7485, stop=7470 ✓
  - INCORRECT example: entrada=7485, stop=7535 ✗ (impossible — that would be a short)

If accionable=true and direccion="short":
  - stop MUST be HIGHER than entrada
  - Correct example: entrada=7558, stop=7570 ✓

If the tweet's levels do not allow this rule to be satisfied → accionable=false.

═══════════════════════════════════════════════════
Respond ONLY with valid JSON, no extra text:
{{
  "tipo": "signal" | "level" | "comment" | "other",
  "accionable": true | false,
  "es_referencia_pasada": true | false,
  "direccion": "long" | "short" | null,
  "entrada": number or null,
  "stop": number or null,
  "target": number or null,
  "niveles_mencionados": [list of ES numbers],
  "resumen": "a short sentence describing what Adam says in this tweet"
}}"""


def tweet_es_referencia_pasada(texto: str) -> bool:
    """
    Fast keyword filter BEFORE calling the LLM.
    If the tweet clearly talks about yesterday or earlier, we discard it
    as an actionable signal without spending tokens on the LLM.

    Returns True if the tweet appears to reference a past trade.
    """
    texto_lower = texto.lower()
    return any(palabra in texto_lower for palabra in PALABRAS_PASADO)


async def clasificar_tweet(texto: str, fecha: str) -> dict:
    """
    Uses Claude Haiku to classify one of Adam's tweets.

    FIX C-13: clasificar_tweet is called from monitorizar() (async).
    The synchronous LLM call blocked Playwright's event loop.
    asyncio.to_thread() runs it in a thread without blocking.

    Includes two layers of protection against false positives:
    1. Past-keyword filter (before the LLM, free)
    2. LLM prompt with explicit instructions about past vs present
    3. Mathematical validation of stop < entry for longs (post-LLM)
    """
    # Layer 1: fast past-keyword filter
    # If the tweet mentions "yesterday", "ystd", etc. → not an actionable signal
    # We still call the LLM to classify type and levels, but we force
    # accionable=False before it can trigger an alert
    es_pasado = tweet_es_referencia_pasada(texto)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = CLASIFICACION_PROMPT.format(texto=texto[:600], fecha=fecha)

    try:
        # C-13: to_thread frees Playwright's event loop during the LLM call.
        response = await asyncio.to_thread(
            client.messages.create,
            model      = LLM_MODEL,
            max_tokens = 350,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Same fix as generar_señal_llm: extract the first JSON object from
        # the text so it doesn't break on extra text or markdown from the LLM.
        inicio = raw.find('{')
        fin    = raw.rfind('}')
        if inicio != -1 and fin != -1 and fin > inicio:
            raw = raw[inicio:fin + 1]
        clasificacion = json.loads(raw)
    except Exception as e:
        return {
            "tipo": "other",
            "accionable": False,
            "resumen": texto[:100],
            "error": str(e)
        }

    # Layer 2: if our filter detected past words, force accionable=False
    # even if the LLM said true (the LLM can be wrong)
    if es_pasado or clasificacion.get('es_referencia_pasada'):
        clasificacion['accionable'] = False
        clasificacion['_motivo_rechazo'] = 'referencia a trade pasado'
        return clasificacion

    # Layer 3: mathematical validation of stop vs entry
    # A long with a stop higher than the entry is mathematically impossible
    if clasificacion.get('accionable'):
        entrada   = clasificacion.get('entrada')
        stop      = clasificacion.get('stop')
        direccion = (clasificacion.get('direccion') or '').lower()

        if entrada and stop:
            if direccion == 'long' and float(stop) >= float(entrada):
                clasificacion['accionable'] = False
                clasificacion['_motivo_rechazo'] = (
                    f'stop ({stop}) >= entry ({entrada}) in LONG — mathematically impossible'
                )
            elif direccion == 'short' and float(stop) <= float(entrada):
                clasificacion['accionable'] = False
                clasificacion['_motivo_rechazo'] = (
                    f'stop ({stop}) <= entry ({entrada}) in SHORT — mathematically impossible'
                )

    return clasificacion


# ─────────────────────────────────────────────
# Main monitor loop
# ─────────────────────────────────────────────

async def monitorizar():
    """
    Main loop: every 3 minutes it checks for new tweets from Adam.
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Live Tweet Monitor")
    print("=" * 55)
    print(f"🎯 Monitoring: @{TWITTER_TARGET}")
    print(f"⏱️  Interval: every {POLL_INTERVAL // 60} minutes")
    print(f"🕐 Active hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MIN:02d}–"
          f"{MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MIN:02d} EST\n")
    print("Ctrl+C to stop\n")

    estado = cargar_estado()

    while True:
        ahora_str  = datetime.now().strftime('%H:%M:%S')
        en_mercado = en_horario_mercado()

        # Reset tweets_hoy if the day changed
        hoy = datetime.now().strftime('%Y-%m-%d')
        if estado.get('fecha_hoy') != hoy:
            estado['tweets_hoy'] = []
            estado['fecha_hoy']  = hoy

        print(f"[{ahora_str}] Checking tweets... "
              f"({'🟢 market open' if en_mercado else '🔴 market closed'})")

        try:
            tweets_recientes = await obtener_tweets_recientes()

            if not tweets_recientes:
                print(f"  ⚠️  No data — possible connection problem")
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
                    print(f"  🆕 {len(tweets_nuevos)} new tweet(s)")

                    for tweet in tweets_nuevos:
                        texto = tweet.get('text', '')
                        fecha = tweet.get('created_at', '')

                        if texto.startswith('RT @'):
                            continue

                        print(f"  📝 [{fecha}] {texto[:80]}...")

                        if en_mercado:
                            # C-13: clasificar_tweet is now async
                            clasificacion = await clasificar_tweet(texto, fecha)
                            tipo = clasificacion.get('tipo', 'other')

                            # ── If it was rejected → show the reason in the log ──
                            motivo = clasificacion.get('_motivo_rechazo')
                            if motivo:
                                print(f"     ⚠️  Discarded: {motivo}")

                            if clasificacion.get('accionable'):
                                # C-11: use the alerter's send_tweet_alert
                                # (escaped HTML, format consistent with the other alerts)
                                # instead of building the plain text here.
                                alerter = TelegramAlerter()
                                await alerter.send_tweet_alert(tweet, clasificacion)
                                direccion = (clasificacion.get('direccion') or '').upper()
                                entrada   = clasificacion.get('entrada')
                                print(f"     ✅ Signal: {direccion} | entry {entrada}")

                            elif tipo == 'level':
                                print(f"     📍 Levels: {clasificacion.get('niveles_mencionados', [])}")

                        # Save all of the day's tweets for LLM context
                        estado.setdefault('tweets_hoy', []).append({
                            'tweet':        tweet,
                            'clasificacion': clasificacion if en_mercado else {}
                        })

                else:
                    print(f"  ✓ No new tweets")

                guardar_estado(estado)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        print(f"  ⏳ Next check in {POLL_INTERVAL // 60} minutes\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        asyncio.run(monitorizar())
    except KeyboardInterrupt:
        print("\n⏹️  Monitor stopped")
