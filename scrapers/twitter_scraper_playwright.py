"""
scrapers/twitter_scraper_playwright.py — Descarga historial de tweets con Playwright
=====================================================================================
Usa Playwright para abrir un navegador real, cargar el perfil de Adam Mancini,
hacer scroll automático hasta el fondo y capturar todos los tweets disponibles
(hasta ~3200, límite que impone Twitter en su interfaz web).

USO:
    python scrapers/twitter_scraper_playwright.py

CÓMO FUNCIONA:
    1. Abre Chromium con tus cookies de sesión (sin login)
    2. Navega a x.com/AdamMancini4
    3. Intercepta las llamadas GraphQL UserTweets que hace Twitter
    4. Hace scroll al fondo para que Twitter cargue más tweets
    5. Repite hasta no haber más tweets nuevos
    6. Guarda todo en data/raw/tweets/adam_mancini_tweets.json

TIEMPO ESTIMADO: ~15-20 minutos para ~3000 tweets
REANUDABLE: sí, los tweets ya descargados se saltan automáticamente
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import TWEETS_DIR, TWITTER_TARGET
from parsers.playwright_utils import extract_tweets, crear_contexto_con_cookies

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────
OUTPUT_FILE      = TWEETS_DIR / 'adam_mancini_tweets.json'
PROFILE_URL      = f'https://x.com/{TWITTER_TARGET}'
SCROLL_DELAY_MS  = 2000   # ms entre scrolls (tiempo para que carguen tweets)
MAX_SCROLLS      = 150    # máx scrolls = ~3000 tweets (20 por scroll)
MAX_SIN_NUEVOS   = 5      # parar si N scrolls consecutivos sin tweets nuevos


# ─────────────────────────────────────────────
# Utilidades de archivo
# ─────────────────────────────────────────────

def cargar_tweets_existentes() -> tuple[list, set]:
    """Carga tweets ya descargados para no repetir trabajo."""
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            tweets = json.load(f)
        return tweets, {t['id'] for t in tweets}
    return [], set()


def guardar_tweets(tweets: list):
    """Guarda todos los tweets en el archivo JSON."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(tweets, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

async def scrape_historico():
    """
    Descarga todos los tweets disponibles de @AdamMancini4 usando Playwright.

    El proceso de scroll:
    - Cada vez que scrollas al fondo, Twitter hace una llamada UserTweets
    - Playwright intercepta esa llamada y extrae los tweets
    - Repetimos hasta que no haya tweets nuevos o lleguemos al límite
    """
    print("=" * 60)
    print("  Adam Mancini Bot — Tweet Scraper (Playwright)")
    print("=" * 60)
    print(f"🎯 Target: @{TWITTER_TARGET}")
    print(f"📁 Saving to: {OUTPUT_FILE}\n")

    # Cargar tweets ya descargados
    todos_tweets, ids_existentes = cargar_tweets_existentes()
    if todos_tweets:
        print(f"📂 Resuming: {len(todos_tweets)} tweets already downloaded\n")

    nuevos_total     = 0
    scrolls_sin_nuevos = 0

    async with async_playwright() as p:
        browser, context = await crear_contexto_con_cookies(p)
        page = await context.new_page()

        # ── Interceptar respuestas de la API ──────────────────────────────
        # Cada vez que Twitter devuelve tweets, los procesamos inmediatamente
        async def procesar_respuesta(response):
            nonlocal nuevos_total, scrolls_sin_nuevos

            if 'UserTweets' not in response.url or response.status != 200:
                return

            try:
                data = await response.json()
                tweets_batch = extract_tweets(data.get('data', {}))

                nuevos_en_batch = 0
                for tweet in tweets_batch:
                    if tweet['id'] and tweet['id'] not in ids_existentes:
                        # Añadir metadatos de descarga
                        tweet['scraped_at'] = datetime.now().isoformat()
                        todos_tweets.append(tweet)
                        ids_existentes.add(tweet['id'])
                        nuevos_en_batch += 1
                        nuevos_total    += 1

                if nuevos_en_batch > 0:
                    print(f"  ✅ Batch: +{nuevos_en_batch} tweets | "
                          f"Total: {len(todos_tweets):,}")
                    guardar_tweets(todos_tweets)  # Guardar después de cada batch
                    scrolls_sin_nuevos = 0
                else:
                    scrolls_sin_nuevos += 1

            except Exception as e:
                print(f"  ⚠️  Error processing batch: {e}")

        page.on('response', procesar_respuesta)

        # ── Cargar perfil ─────────────────────────────────────────────────
        print("🌐 Loading Adam Mancini's profile...")
        try:
            await page.goto(PROFILE_URL, wait_until='load', timeout=20000)
        except Exception:
            pass  # El timeout de load es normal en Twitter

        # Esperar a que carguen los primeros tweets
        await page.wait_for_timeout(3000)
        print(f"📥 Starting scroll ({MAX_SCROLLS} max)...\n")

        # ── Scroll automático ─────────────────────────────────────────────
        for scroll_num in range(MAX_SCROLLS):

            # Parar si demasiados scrolls sin tweets nuevos
            if scrolls_sin_nuevos >= MAX_SIN_NUEVOS:
                print(f"\n⏹️  {MAX_SIN_NUEVOS} scrolls with no new tweets — reached the end")
                break

            # Scroll al fondo de la página
            # Esto hace que Twitter cargue el siguiente batch de tweets
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(SCROLL_DELAY_MS)

            # Mostrar progreso cada 10 scrolls
            if (scroll_num + 1) % 10 == 0:
                print(f"  📜 Scroll {scroll_num + 1}/{MAX_SCROLLS} | "
                      f"Total accumulated: {len(todos_tweets):,}")

        await browser.close()

    # ── Guardar resultado final ───────────────────────────────────────────
    guardar_tweets(todos_tweets)

    print("\n" + "=" * 60)
    print(f"✅ Download complete")
    print(f"📊 Total tweets: {len(todos_tweets):,}")
    print(f"🆕 New this session: {nuevos_total:,}")

    if todos_tweets:
        # Estadísticas — datetime ya está importado al inicio del módulo
        def _parse(d):
            try: return datetime.strptime(d, '%a %b %d %H:%M:%S +0000 %Y')
            except: return datetime.min
        fechas = sorted([t['created_at'] for t in todos_tweets if t.get('created_at')], key=_parse)
        if fechas:
            print(f"📅 Oldest: {fechas[0]}")
            print(f"📅 Newest: {fechas[-1]}")

        originales = sum(1 for t in todos_tweets if not t.get('is_retweet'))
        print(f"📝 Original tweets: {originales:,}")

    print(f"📁 File: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == '__main__':
    asyncio.run(scrape_historico())
