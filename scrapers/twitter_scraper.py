"""
scrapers/twitter_scraper.py - Descarga tweets históricos de Adam Mancini
=========================================================================
Versión 3: usa cookies del navegador directamente, sin login programático.
Esto evita el bloqueo de Cloudflare que afecta al login automático.

SETUP (solo la primera vez):
    1. Instala "Cookie-Editor" en Chrome (extensión gratuita)
    2. Ve a x.com con tu sesión iniciada
    3. Clic en Cookie-Editor → Export → Export as JSON
    4. Guarda el texto en: data/raw/cookies.json

USO:
    python scrapers/twitter_scraper.py
"""
# ── Patch completo: reemplaza el objeto de transacción entero ─────────
class _NullTransaction:
    """Transacción vacía que evita todos los errores de KEY_BYTE."""
    def __init__(self):
        self.DEFAULT_ROW_INDEX = 2
        self.DEFAULT_KEY_BYTES_INDICES = [15, 5, 2, 0]
        self.key = ''
        self.home_page_response = ''

    async def init(self, *args, **kwargs):
        pass  # no hace nada, evita todos los errores de parsing

    def __getattr__(self, name):
        # Cualquier método desconocido devuelve una función vacía
        def _noop(*args, **kwargs):
            return ''
        return _noop

from twikit import Client as _TwikitClient
_orig_client_init = _TwikitClient.__init__

def _patched_client_init(self, *args, **kwargs):
    _orig_client_init(self, *args, **kwargs)
    self.client_transaction = _NullTransaction()  # reemplaza después de crear

_TwikitClient.__init__ = _patched_client_init
# ── Fin del patch ─────────────────────────────────────────────────────

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    TWITTER_TARGET,
    TWEETS_DIR,
    RAW_DIR,
)

try:
    from twikit import Client
except ImportError:
    print("❌ twikit not installed. Run: pip install twikit")
    sys.exit(1)


# ─────────────────────────────────────────────
# Rutas de archivos
# ─────────────────────────────────────────────
COOKIES_FILE = RAW_DIR / 'cookies.json'     # cookies exportadas del navegador
OUTPUT_FILE  = TWEETS_DIR / 'adam_mancini_tweets.json'

# Pausa entre páginas para no activar rate limits (segundos)
DELAY_BETWEEN_PAGES = 3


# ─────────────────────────────────────────────
# Funciones de utilidad
# ─────────────────────────────────────────────

def load_existing_tweets() -> list:
    """Carga tweets ya descargados para no repetir trabajo."""
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_tweets(tweets: list):
    """Guarda la lista de tweets en JSON."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(tweets, f, indent=2, ensure_ascii=False)


def load_browser_cookies(client: Client):
    """
    Carga las cookies exportadas desde el navegador.
    Cookie-Editor exporta una lista de objetos con 'name' y 'value'.
    Las convertimos al formato que twikit entiende.
    """
    if not COOKIES_FILE.exists():
        print(f"❌ Cookies file not found: {COOKIES_FILE}")
        print()
        print("To get the cookies:")
        print("  1. Install 'Cookie-Editor' in Chrome (free extension)")
        print("  2. Go to x.com while logged in")
        print("  3. Click Cookie-Editor → Export → Export as JSON")
        print(f"  4. Save the JSON to: {COOKIES_FILE}")
        return False

    with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
        cookies_data = json.load(f)

    # Cookie-Editor puede exportar como lista de objetos o como dict
    # Manejamos ambos formatos
    if isinstance(cookies_data, list):
        # Formato Cookie-Editor: [{"name": "auth_token", "value": "xxx", ...}, ...]
        cookie_dict = {c['name']: c['value'] for c in cookies_data if 'name' in c}
    elif isinstance(cookies_data, dict):
        # Formato simple: {"auth_token": "xxx", ...}
        cookie_dict = cookies_data
    else:
        print("❌ Unrecognized cookie format")
        return False

    # Verificar que tiene las cookies esenciales de Twitter
    if 'auth_token' not in cookie_dict:
        print("❌ The cookies do not contain 'auth_token'")
        print("   Make sure to export the cookies from x.com (not twitter.com)")
        return False

    # Cargar las cookies en el cliente twikit
    client.http.cookies.update(cookie_dict)
    return True


def tweet_to_dict(tweet) -> dict:
    """Convierte un Tweet de twikit a diccionario serializable."""
    return {
        'id':             tweet.id,
        'text':           tweet.text,
        'created_at':     str(tweet.created_at),
        'favorite_count': getattr(tweet, 'favorite_count', 0),
        'retweet_count':  getattr(tweet, 'retweet_count', 0),
        'reply_count':    getattr(tweet, 'reply_count', 0),
        'quote_count':    getattr(tweet, 'quote_count', 0),
        'is_retweet':     tweet.text.startswith('RT @'),
        'scraped_at':     datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

async def scrape_adam_tweets():
    """
    Descarga los tweets de @AdamMancini4 usando cookies del navegador.
    Sin login programático → sin bloqueo de Cloudflare.
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Tweet Scraper (v3)")
    print("=" * 55)
    print(f"🎯 Target: @{TWITTER_TARGET}")
    print(f"📁 Saving to: {OUTPUT_FILE}\n")

    # ── Cargar cookies del navegador ──────────────────────────────────────
    print("🍪 Loading browser cookies...")
    client = Client('en-US')

    if not load_browser_cookies(client):
        return

    print("✅ Cookies loaded\n")

    # ── Obtener perfil de Adam ────────────────────────────────────────────
    print(f"🔍 Looking up profile @{TWITTER_TARGET}...")
    try:
        user = await client.get_user_by_screen_name(TWITTER_TARGET)
        print(f"✅ Found: {user.name} (followers: {user.followers_count:,})\n")
    except Exception as e:
        print(f"❌ Error looking up user: {e}")
        print("   The cookies may have expired. Export them again from Chrome.")
        return

    # ── Cargar tweets ya descargados ──────────────────────────────────────
    existing_tweets = load_existing_tweets()
    existing_ids    = {t['id'] for t in existing_tweets}
    all_tweets      = existing_tweets.copy()

    if existing_tweets:
        print(f"📂 Resuming: {len(existing_tweets)} tweets already downloaded.\n")

    # ── Descarga paginada ─────────────────────────────────────────────────
    print("📥 Downloading tweets...")
    print("-" * 40)

    page_num  = 1
    new_count = 0

    try:
        tweets_page = await client.get_user_tweets(
            user.id,
            tweet_type='Tweets',
            count=40
        )

        while True:
            page_new = 0

            for tweet in tweets_page:
                if tweet.text.startswith('RT @'):
                    continue
                if tweet.id in existing_ids:
                    continue

                all_tweets.append(tweet_to_dict(tweet))
                existing_ids.add(tweet.id)
                page_new  += 1
                new_count += 1

            print(f"  Page {page_num:3d}: +{page_new:3d} new | "
                  f"Total: {len(all_tweets):,}")

            # Guardar progreso tras cada página
            save_tweets(all_tweets)

            if not hasattr(tweets_page, 'next_cursor') or not tweets_page.next_cursor:
                print("\n✅ No more pages.")
                break

            await asyncio.sleep(DELAY_BETWEEN_PAGES)
            tweets_page = await tweets_page.next()
            page_num   += 1

    except Exception as e:
        print(f"\n❌ Error during download: {e}")
        print("💾 Saving current progress...")
        save_tweets(all_tweets)

    # ── Resumen ───────────────────────────────────────────────────────────
    save_tweets(all_tweets)

    print("\n" + "=" * 55)
    print(f"✅ Scraping complete")
    print(f"📊 Total tweets saved: {len(all_tweets):,}")
    print(f"🆕 New this session:   {new_count:,}")

    if all_tweets:
        dates = sorted([t['created_at'][:10] for t in all_tweets if t.get('created_at')])
        if dates:
            print(f"📅 Oldest: {dates[0]}")
            print(f"📅 Newest: {dates[-1]}")

    originals = sum(1 for t in all_tweets if not t.get('is_retweet'))
    print(f"📝 Original tweets: {originals:,}")
    print(f"📁 File: {OUTPUT_FILE}")
    print("=" * 55)


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────
if __name__ == '__main__':
    asyncio.run(scrape_adam_tweets())