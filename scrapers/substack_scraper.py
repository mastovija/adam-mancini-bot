"""
scrapers/substack_scraper.py - Descarga el newsletter Trade Companion
======================================================================
Scraper para tradecompanion.substack.com, el newsletter diario de Adam Mancini.
Usa la API pública de Substack para listar artículos y requests/beautifulsoup
para descargar el contenido de los artículos gratuitos.

USO:
    python scrapers/substack_scraper.py

QUÉ HACE:
    1. Obtiene el listado completo de artículos via API de Substack
    2. Guarda el índice en data/raw/newsletter/index.json
    3. Descarga el contenido completo de los artículos gratuitos
    4. Para los de pago, guarda solo metadatos (título, fecha, extracto)
    5. Cada artículo se guarda como un JSON individual

COSTE: $0 — usa la API pública (no requiere suscripción para gratuitos)
NOTA: Para artículos de pago, suscríbete y actualiza SUBSTACK_COOKIES en .env
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Añade la raíz del proyecto al path para importar config.py
sys.path.append(str(Path(__file__).parent.parent))


from config import SUBSTACK_URL, NEWSLETTER_DIR, SUBSTACK_COOKIES


# ─────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────
# Headers para parecer un navegador normal
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html',
}

# Pausa entre requests para no sobrecargar el servidor
DELAY_BETWEEN_REQUESTS = 1.5  # segundos

# Artículos por página en la API de Substack
BATCH_SIZE = 12
PREVIEW_MAX_LENGTH = 500  # articles shorter than this are considered previews

def get_cookies_dict() -> dict:
    """
    Parses SUBSTACK_COOKIES from .env into a dict for requests.
    With a valid substack.sid cookie, Substack serves the full
    paid article instead of the public preview.
    """
    cookies = {}
    if not SUBSTACK_COOKIES:
        return cookies
    for part in SUBSTACK_COOKIES.split(';'):
        part = part.strip()
        if '=' in part:
            name, value = part.split('=', 1)
            cookies[name.strip()] = value.strip()
    return cookies

def has_paid_access() -> bool:
    """Returns True if paid cookies are configured in .env."""
    return bool(get_cookies_dict())

# ─────────────────────────────────────────────
# Fase 1: Obtener listado de artículos
# ─────────────────────────────────────────────

def get_all_post_metadata() -> list:
    """
    Obtiene los metadatos de todos los artículos del newsletter
    usando la API pública de Substack (/api/v1/archive).

    La API devuelve artículos en lotes de 12, ordenados por fecha.
    Paginamos hasta que no haya más artículos.

    Devuelve: lista de dicts con id, title, slug, date, is_free
    """
    print("📋 Obteniendo listado completo de artículos...")

    all_posts = []
    offset    = 0

    while True:
        # Endpoint público de Substack para listar artículos
        url = (f"{SUBSTACK_URL}/api/v1/archive"
               f"?sort=new&search=&offset={offset}&limit={BATCH_SIZE}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            batch = response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Error en API: {e}")
            break

        if not batch:
            break  # No hay más artículos

        for post in batch:
            # 'audience': 'everyone' = gratuito, 'paid' = de pago
            is_free = post.get('audience') == 'everyone'

            all_posts.append({
                'id':           post.get('id'),
                'title':        post.get('title', ''),
                'subtitle':     post.get('subtitle', ''),
                'slug':         post.get('slug', ''),
                'published_at': post.get('post_date', '')[:10],  # solo la fecha
                'is_free':      is_free,
                'url':          f"{SUBSTACK_URL}/p/{post.get('slug', '')}",
            })

        print(f"  Encontrados: {len(all_posts)} artículos...")

        # Si la respuesta tiene menos del tamaño de página, ya no hay más
        if len(batch) < BATCH_SIZE:
            break

        offset += BATCH_SIZE
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"✅ Total artículos encontrados: {len(all_posts)}\n")
    return all_posts


# ─────────────────────────────────────────────
# Fase 2: Descargar contenido de cada artículo
# ─────────────────────────────────────────────

def scrape_post_content(post: dict) -> dict:
    """
    Descarga y extrae el texto completo de un artículo.

    Para artículos gratuitos: extrae el contenido completo.
    Para artículos de pago: solo guarda el preview visible.

    Args:
        post: dict con metadatos del artículo (de get_all_post_metadata)

    Returns:
        dict con todos los metadatos + el campo 'content' con el texto
    """
    cookies = get_cookies_dict()   # session cookie for paid content
    try:
        response = requests.get(
            post['url'],
            headers={**HEADERS, 'Accept': 'text/html'},
            cookies=cookies,       # sends substack.sid → full content
            timeout=15
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {**post, 'content': '', 'scrape_status': 'error', 'error': str(e)}

    soup = BeautifulSoup(response.text, 'html.parser')

    # ── Detectar paywall ──────────────────────────────────────────────────
    # Substack añade clases específicas cuando el contenido requiere suscripción
    paywall_classes = ['paywall', 'subscribe-paywall', 'subscription-required']
    has_paywall = any(
        soup.find(attrs={'class': lambda c: c and any(p in str(c) for p in paywall_classes)})
        for _ in [1]  # truco para hacer el any() más limpio
    )

    # ── Extraer el contenido del artículo ────────────────────────────────
    # Substack ha cambiado su estructura HTML varias veces,
    # probamos múltiples selectores para ser compatibles con versiones antiguas
    content_selectors = [
        'div.available-content',          # versiones recientes
        'div.body.markup',                # versiones anteriores
        'div[class*="available-content"]', # variante con clase compuesta
        'article',                         # fallback genérico
    ]

    content_text = ''
    for selector in content_selectors:
        element = soup.select_one(selector)
        if element:
            # get_text con '\n' como separador para mantener estructura de párrafos
            content_text = element.get_text('\n', strip=True)
            break

    # Previews: 900-2500 chars | Artículos completos: 5000-40000 chars
    # No usamos has_paywall porque Substack muestra elementos de paywall
    # incluso cuando el usuario está autenticado y tiene el contenido completo.
    is_complete = len(content_text) > 3000

    return {
        **post,
        'content':              content_text,
        'content_length':       len(content_text),
        'is_complete':          is_complete,
        'has_paywall_detected': has_paywall,
        'scraped_at':           datetime.now().isoformat(),
        'scrape_status':        'ok',
    }


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

def scrape_newsletter():
    """
    Función principal que orquesta todo el proceso de scraping del newsletter.

    Flujo completo:
    1. Obtener lista de todos los artículos via API
    2. Guardar índice completo en index.json
    3. Descargar artículos gratuitos con contenido completo
    4. Guardar metadatos de artículos de pago (para saber qué hay)
    5. Cada artículo = un archivo JSON individual (slug.json)
    """
    print("=" * 55)
    print("  Bot Adam Mancini — Scraper de Newsletter")
    print("=" * 55)
    print(f"🌐 URL: {SUBSTACK_URL}")
    print(f"📁 Guardando en: {NEWSLETTER_DIR}\n")

    # Crear directorio
    NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Obtener listado completo ───────────────────────────────────────
    all_posts = get_all_post_metadata()
    if not all_posts:
        print("❌ No se encontraron artículos. ¿Está bien la URL en config.py?")
        return

    # ── 2. Guardar índice ─────────────────────────────────────────────────
    # El índice es útil para saber qué existe aunque no hayamos descargado el contenido
    index_file = NEWSLETTER_DIR / 'index.json'
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(all_posts, f, indent=2, ensure_ascii=False)
    print(f"📑 Índice guardado: {index_file}")

    # ── 3. Estadísticas ───────────────────────────────────────────────────
    free_posts  = [p for p in all_posts if p['is_free']]
    paid_posts  = [p for p in all_posts if not p['is_free']]
    print(f"📊 Artículos gratuitos: {len(free_posts)}")
    print(f"🔒 Artículos de pago:   {len(paid_posts)}\n")

    # ── 4. Filtrar los ya descargados ─────────────────────────────────────
    # Leemos los slugs de los JSON ya existentes para no repetir trabajo
    existing_slugs = {f.stem for f in NEWSLETTER_DIR.glob('*.json')
                      if f.stem != 'index'}

    def needs_download(post: dict) -> bool:
        """
        Re-downloads paid articles if they only have preview content.
        Key: with cookies configured, replaces all short previews with
        the full article text automatically.
        """
        if post['slug'] not in existing_slugs:
            return True             # never downloaded → always download
        if post['is_free']:
            return False            # free articles are already complete
        if not has_paid_access():
            return False            # no cookies → can't improve the preview
        # Paid article + cookies → re-download if the file is just a preview
        f = NEWSLETTER_DIR / f"{post['slug']}.json"
        try:
            data = json.load(open(f, encoding='utf-8'))
            return (not data.get('is_complete', False) or
                    data.get('content_length', 0) < PREVIEW_MAX_LENGTH)
        except Exception:
            return True

    to_download_free = [p for p in free_posts if needs_download(p)]
    to_download_paid = [p for p in paid_posts if needs_download(p)]

    if has_paid_access():
        est_min = (len(to_download_paid) * DELAY_BETWEEN_REQUESTS) / 60
        print(f"🆕 Por descargar: {len(to_download_free)} gratuitos, "
              f"{len(to_download_paid)} de pago (contenido completo con cookies)")
        print(f"⏱️  Tiempo estimado: ~{est_min:.0f} minutos")
    else:
        print(f"🆕 Por descargar: {len(to_download_free)} gratuitos, "
              f"{len(to_download_paid)} de pago (solo metadatos/preview)")

    print(f"🆕 Por descargar: {len(to_download_free)} gratuitos, "
          f"{len(to_download_paid)} de pago (solo metadatos)")

    # ── 5. Descargar artículos gratuitos con contenido ────────────────────
    print("\n📥 Descargando artículos gratuitos...")
    print("-" * 40)

    downloaded_ok  = 0
    downloaded_err = 0

    for i, post in enumerate(to_download_free, 1):
        date_str  = post['published_at']
        title_str = post['title'][:55] + ('...' if len(post['title']) > 55 else '')
        print(f"  [{i:3d}/{len(to_download_free)}] {date_str} — {title_str}")

        article = scrape_post_content(post)

        # Guardar como JSON con el slug como nombre de archivo
        output_file = NEWSLETTER_DIR / f"{post['slug']}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(article, f, indent=2, ensure_ascii=False)

        # Informar resultado
        if article['scrape_status'] == 'ok':
            downloaded_ok += 1
            status = "✅ completo" if article.get('is_complete') else "⚠️  parcial"
            print(f"       → {status} ({article['content_length']:,} chars)")
        else:
            downloaded_err += 1
            print(f"       → ❌ Error: {article.get('error', 'desconocido')}")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── 6. Guardar metadatos de artículos de pago ─────────────────────────
    # Aunque no tenemos el contenido, guardamos los metadatos para saber qué existe
    if to_download_paid:
        print(f"\n📥 Descargando preview de {len(to_download_paid)} artículos de pago...")
        print("-" * 40)
        for i, post in enumerate(to_download_paid, 1):
            date_str  = post['published_at']
            title_str = post['title'][:55] + ('...' if len(post['title']) > 55 else '')
            print(f"  [{i:4d}/{len(to_download_paid)}] {date_str} — {title_str}")

            # Descargamos igual que los gratuitos — captura lo que sea visible
            article = scrape_post_content(post)
            output_file = NEWSLETTER_DIR / f"{post['slug']}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(article, f, indent=2, ensure_ascii=False)

            time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── Resumen final ─────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"✅ Scraping completado")
    print(f"📥 Descargados con contenido: {downloaded_ok}")
    print(f"❌ Errores:                   {downloaded_err}")
    print(f"🔒 Sin contenido (de pago):   {len(to_download_paid)}")
    print(f"📁 Directorio: {NEWSLETTER_DIR}")
    print("=" * 55)
    print()
    print("💡 Para descargar los artículos de pago en el futuro:")
    print("   1. Suscríbete al newsletter en tradecompanion.substack.com")
    print("   2. Abre Chrome → F12 → Application → Cookies → substack.com")
    print("   3. Copia las cookies y ponlas en SUBSTACK_COOKIES en tu .env")


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────
if __name__ == '__main__':
    scrape_newsletter()
