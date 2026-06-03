"""
scrapers/twitter_scraper.py - Descarga tweets históricos de Adam Mancini
=========================================================================
Usa twikit para scrapear Twitter/X sin necesitar API key oficial.
Twikit simula la app de Twitter usando cookies de una cuenta normal.

USO:
    python scrapers/twitter_scraper.py

QUÉ HACE:
    1. Loguea en Twitter (o carga cookies guardadas de sesiones anteriores)
    2. Localiza el perfil de @AdamMancini4
    3. Descarga todos los tweets disponibles (máx ~3200 histórico de Twitter)
    4. Guarda los tweets en data/raw/tweets/adam_mancini_tweets.json
    5. Si se interrumpe, al relanzar continúa sin re-descargar lo ya guardado

COSTE: $0 — no requiere API key de Twitter
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Añade la raíz del proyecto al path para poder importar config.py
sys.path.append(str(Path(__file__).parent.parent))

from config import (
    TWITTER_USERNAME,
    TWITTER_EMAIL,
    TWITTER_PASSWORD,
    TWITTER_TARGET,
    TWEETS_DIR,
    RAW_DIR,
)

try:
    from twikit import Client, TooManyRequests
except ImportError:
    print("❌ twikit no instalado. Ejecuta: pip install twikit")
    sys.exit(1)


# ─────────────────────────────────────────────
# Rutas de archivos
# ─────────────────────────────────────────────
# Las cookies guardan tu sesión para no tener que loguearte cada vez
COOKIES_FILE = RAW_DIR / 'cookies.json'

# Archivo principal donde se acumulan todos los tweets
OUTPUT_FILE  = TWEETS_DIR / 'adam_mancini_tweets.json'


# ─────────────────────────────────────────────
# Parámetros de scraping
# ─────────────────────────────────────────────
# Pausa entre páginas para no ser detectado como bot por Twitter
DELAY_BETWEEN_PAGES = 3   # segundos

# Cuánto esperar si Twitter nos da rate limit (15 minutos)
DELAY_RATE_LIMIT    = 900  # segundos


# ─────────────────────────────────────────────
# Funciones de utilidad
# ─────────────────────────────────────────────

def load_existing_tweets() -> list:
    """
    Carga los tweets ya descargados para no volver a descargarlos.
    Devuelve una lista vacía si es la primera vez.
    """
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_tweets(tweets: list):
    """
    Guarda la lista de tweets en el archivo JSON principal.
    Se llama después de cada página para preservar progreso.
    """
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(tweets, f, indent=2, ensure_ascii=False)


def tweet_to_dict(tweet) -> dict:
    """
    Convierte un objeto Tweet de twikit a un diccionario simple y serializable.
    Solo guardamos los campos útiles para el análisis de trading.
    """
    return {
        'id':            tweet.id,
        'text':          tweet.text,
        'created_at':    str(tweet.created_at),
        'favorite_count': getattr(tweet, 'favorite_count', 0),
        'retweet_count':  getattr(tweet, 'retweet_count', 0),
        'reply_count':    getattr(tweet, 'reply_count', 0),
        # Marcamos si es retweet para filtrarlos después (Adam no suele retweetear análisis)
        'is_retweet':    tweet.text.startswith('RT @'),
        # Fecha de descarga para saber qué datos son recientes
        'scraped_at':    datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
# Función principal de scraping
# ─────────────────────────────────────────────

async def scrape_adam_tweets():
    """
    Descarga todos los tweets disponibles de @AdamMancini4.

    Twitter limita el historial a ~3200 tweets por su app,
    así que ese es el máximo que podemos obtener aunque Adam tenga más.
    Para los tweets más antiguos habría que usar servicios de archivo como Apify.
    """
    print("=" * 55)
    print("  Bot Adam Mancini — Scraper de Tweets")
    print("=" * 55)
    print(f"🎯 Objetivo: @{TWITTER_TARGET}")
    print(f"📁 Guardando en: {OUTPUT_FILE}\n")

    # ── Crear cliente de twikit ───────────────────────────────────────────
    client = Client('en-US')

    # ── Login o cargar sesión guardada ────────────────────────────────────
    # Usar cookies guardadas evita hacer login cada vez,
    # lo que reduce el riesgo de que Twitter detecte actividad automatizada
    if COOKIES_FILE.exists():
        print("🍪 Cargando sesión guardada (cookies)...")
        client.load_cookies(str(COOKIES_FILE))
    else:
        print("🔑 Iniciando sesión en Twitter...")

        # Verificar que tenemos credenciales
        if not all([TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD]):
            print("❌ Faltan credenciales en .env:")
            print("   TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD")
            return

        await client.login(
            auth_info_1=TWITTER_USERNAME,
            auth_info_2=TWITTER_EMAIL,
            password=TWITTER_PASSWORD
        )
        # Guardar cookies para no tener que loguearse en el futuro
        COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        client.save_cookies(str(COOKIES_FILE))
        print("✅ Login exitoso. Cookies guardadas para próximas ejecuciones.\n")

    # ── Obtener perfil de Adam ────────────────────────────────────────────
    print(f"🔍 Buscando perfil de @{TWITTER_TARGET}...")
    user = await client.get_user_by_screen_name(TWITTER_TARGET)
    print(f"✅ Encontrado: {user.name} (seguidores: {user.followers_count:,})\n")

    # ── Cargar tweets ya descargados ──────────────────────────────────────
    existing_tweets = load_existing_tweets()
    existing_ids    = {t['id'] for t in existing_tweets}
    all_tweets      = existing_tweets.copy()

    if existing_tweets:
        print(f"📂 Reanudando: {len(existing_tweets)} tweets ya descargados.\n")

    # ── Descarga paginada ─────────────────────────────────────────────────
    # twikit devuelve ~40 tweets por página
    # Iteramos con .next() hasta que no queden más páginas

    print("📥 Descargando tweets...")
    print("-" * 40)

    page_num        = 1
    new_this_run    = 0

    try:
        # Primera página
        tweets_page = await client.get_user_tweets(
            user.id,
            tweet_type='Tweets',  # 'Tweets' = solo tweets propios (sin replies)
            count=40
        )

        while True:
            new_this_page = 0

            for tweet in tweets_page:
                # Ignorar retweets (Adam los usa poco y no tienen análisis)
                if tweet.text.startswith('RT @'):
                    continue

                # Ignorar si ya lo tenemos
                if tweet.id in existing_ids:
                    continue

                all_tweets.append(tweet_to_dict(tweet))
                existing_ids.add(tweet.id)
                new_this_page += 1
                new_this_run  += 1

            print(f"  Página {page_num:3d}: +{new_this_page:3d} nuevos | "
                  f"Total acumulado: {len(all_tweets):,}")

            # Guardar después de cada página (por si se interrumpe)
            save_tweets(all_tweets)

            # Comprobar si hay más páginas disponibles
            if not hasattr(tweets_page, 'next_cursor') or not tweets_page.next_cursor:
                print("\n✅ No hay más páginas disponibles.")
                break

            # Pausa entre páginas (para no ser detectado)
            await asyncio.sleep(DELAY_BETWEEN_PAGES)

            # Cargar siguiente página
            tweets_page = await tweets_page.next()
            page_num   += 1

    except TooManyRequests:
        # Twitter nos ha puesto rate limit — esperamos y guardamos lo que tenemos
        print(f"\n⚠️  Rate limit de Twitter. Esperando {DELAY_RATE_LIMIT // 60} minutos...")
        save_tweets(all_tweets)
        await asyncio.sleep(DELAY_RATE_LIMIT)
        print("🔄 Puedes relanzar el script para continuar.")

    except Exception as e:
        print(f"\n❌ Error inesperado: {e}")
        print("💾 Guardando progreso actual...")
        save_tweets(all_tweets)

    # ── Resumen final ─────────────────────────────────────────────────────
    save_tweets(all_tweets)

    print("\n" + "=" * 55)
    print(f"✅ Scraping completado")
    print(f"📊 Total tweets guardados: {len(all_tweets):,}")
    print(f"🆕 Nuevos en esta sesión:  {new_this_run:,}")

    # Estadísticas de fecha si hay tweets
    if all_tweets:
        dates = sorted([t['created_at'][:10] for t in all_tweets if t.get('created_at')])
        if dates:
            print(f"📅 Más antiguo: {dates[0]}")
            print(f"📅 Más reciente: {dates[-1]}")

    retweets = sum(1 for t in all_tweets if t.get('is_retweet'))
    originals = len(all_tweets) - retweets
    print(f"📝 Tweets originales: {originals:,} | Retweets: {retweets:,}")
    print(f"📁 Archivo: {OUTPUT_FILE}")
    print("=" * 55)


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────
if __name__ == '__main__':
    asyncio.run(scrape_adam_tweets())
