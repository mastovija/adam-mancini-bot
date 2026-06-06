"""
parsers/playwright_utils.py — Utilidades compartidas para scraping con Playwright
==================================================================================
Funciones reutilizadas por el scraper histórico y el monitor en tiempo real:
- normalizar_cookies(): convierte formato Cookie-Editor al formato Playwright
- extract_tweets(): parsea la respuesta GraphQL de Twitter y extrae tweets limpios
- get_browser_context(): crea un contexto Playwright con las cookies cargadas
"""

import json
from pathlib import Path
from typing import Optional
import sys

sys.path.append(str(Path(__file__).parent.parent))
from config import RAW_DIR


# ─────────────────────────────────────────────
# Cookies
# ─────────────────────────────────────────────

COOKIES_FILE = RAW_DIR / 'cookies.json'

# Mapeo de valores sameSite de Cookie-Editor a los que acepta Playwright
SAME_SITE_MAP = {
    'no_restriction': 'None',
    'lax':            'Lax',
    'strict':         'Strict',
    'unspecified':    'None',
    'none':           'None',
}


def normalizar_cookies(cookies_list: list) -> list:
    """
    Convierte cookies exportadas de Cookie-Editor al formato que acepta Playwright.

    Cookie-Editor usa: sameSite = "no_restriction" | "lax" | "strict"
    Playwright exige:  sameSite = "None" | "Lax" | "Strict"
    """
    resultado = []
    for c in cookies_list:
        cookie = {
            'name':     c.get('name', ''),
            'value':    c.get('value', ''),
            'domain':   c.get('domain', ''),
            'path':     c.get('path', '/'),
            'secure':   bool(c.get('secure', False)),
            'httpOnly': bool(c.get('httpOnly', False)),
            'sameSite': SAME_SITE_MAP.get(
                str(c.get('sameSite', '')).lower(), 'None'
            ),
        }
        # Cookie-Editor usa 'expirationDate', Playwright usa 'expires'
        if c.get('expirationDate'):
            cookie['expires'] = float(c['expirationDate'])

        if cookie['name']:  # Ignorar cookies sin nombre
            resultado.append(cookie)

    return resultado


def cargar_cookies() -> list:
    """Carga y normaliza las cookies desde el archivo guardado."""
    if not COOKIES_FILE.exists():
        raise FileNotFoundError(
            f"No se encontraron cookies en {COOKIES_FILE}\n"
            "Exporta las cookies de x.com con Cookie-Editor en Chrome."
        )
    with open(COOKIES_FILE, 'r') as f:
        return normalizar_cookies(json.load(f))


# ─────────────────────────────────────────────
# Extracción de tweets del JSON de Twitter
# ─────────────────────────────────────────────

def extract_tweets(api_response: dict) -> list:
    """
    Extrae tweets de la respuesta GraphQL de UserTweets.

    La estructura de Twitter es:
    data → user → result → timeline → timeline → instructions
    → entries → content → itemContent → tweet_results → result → legacy

    Args:
        api_response: dict con la respuesta JSON de la API de Twitter

    Returns:
        Lista de dicts con id, text, created_at, counts, is_retweet
    """
    tweets = []

    try:
        # Navegar hasta las instrucciones del timeline
        instructions = (
            api_response['user']['result']['timeline']['timeline']['instructions']
        )
    except (KeyError, TypeError):
        return tweets

    for instruction in instructions:
        # Solo procesamos las entradas que añaden tweets
        if instruction.get('type') != 'TimelineAddEntries':
            continue

        for entry in instruction.get('entries', []):
            try:
                # Cada entrada puede ser un tweet, un cursor de paginación, etc.
                tweet_result = (
                    entry['content']['itemContent']['tweet_results']['result']
                )
                legacy = tweet_result.get('legacy', {})

                text = legacy.get('full_text', '')
                if not text:
                    continue

                tweets.append({
                    'id':            legacy.get('id_str', ''),
                    'text':          text,
                    'created_at':    legacy.get('created_at', ''),
                    'favorite_count': legacy.get('favorite_count', 0),
                    'retweet_count': legacy.get('retweet_count', 0),
                    'reply_count':   legacy.get('reply_count', 0),
                    'quote_count':   legacy.get('quote_count', 0),
                    'is_retweet':    text.startswith('RT @'),
                })
            except (KeyError, TypeError):
                # Esta entrada no es un tweet (cursor, separador, etc.)
                continue

    return tweets


async def crear_contexto_con_cookies(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled']
    )
    context = await browser.new_context(
        user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    )
    cookies = cargar_cookies()
    await context.add_cookies(cookies)

    # ── Anti-detección ────────────────────────────────────────────────────
    # Sin esto Twitter sirve contenido cacheado a navegadores automatizados
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    """)
    # ─────────────────────────────────────────────────────────────────────

    return browser, context