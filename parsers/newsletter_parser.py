"""
parsers/newsletter_parser.py — Parser del newsletter diario de Adam Mancini
=============================================================================
Cada mañana descarga el artículo más reciente de Trade Companion
(tradecompanion.substack.com), extrae el mapa del día con Claude Haiku
y guarda el resultado en data/daily/today.json.

Este archivo es lo que el motor de señales (Fase 5) consulta durante
toda la sesión para saber el bias, los niveles clave y las condiciones.

USO manual:
    python parsers/newsletter_parser.py

Con --force re-parsea aunque ya exista el de hoy:
    python parsers/newsletter_parser.py --force

USO automático: llamado por el scheduler a las 7:30 AM EST
"""

import json
import sys
import requests
from datetime import datetime, date
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent.parent))

from config import SUBSTACK_URL, DATA_DIR
from knowledge_base.processor import extract_trading_info


# ─────────────────────────────────────────────
# Rutas de archivos
# ─────────────────────────────────────────────
DAILY_DIR    = DATA_DIR / 'daily'
TODAY_FILE   = DAILY_DIR / 'today.json'      # sobreescrito cada día
HISTORY_DIR  = DAILY_DIR / 'history'         # copia de cada día guardada


# ─────────────────────────────────────────────
# Obtener el artículo más reciente
# ─────────────────────────────────────────────

def get_latest_article() -> dict | None:
    """
    Consulta la API pública de Substack para obtener el artículo más reciente.
    No requiere suscripción — devuelve metadatos de todos los artículos.

    Returns:
        dict con slug, title, url, published_at, is_free
        o None si hay error de conexión
    """
    try:
        url = f"{SUBSTACK_URL}/api/v1/archive?sort=new&offset=0&limit=1"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.raise_for_status()
        articles = r.json()

        if not articles:
            return None

        a = articles[0]
        return {
            'slug':         a.get('slug', ''),
            'title':        a.get('title', ''),
            'subtitle':     a.get('subtitle', ''),
            'url':          f"{SUBSTACK_URL}/p/{a.get('slug', '')}",
            'published_at': a.get('post_date', '')[:10],  # solo fecha YYYY-MM-DD
            'is_free':      a.get('audience') == 'everyone',
        }
    except Exception as e:
        print(f"❌ Error consultando Substack: {e}")
        return None


def scrape_article_content(url: str) -> str:
    """
    Descarga el contenido visible del artículo.
    Para artículos gratuitos obtiene el texto completo.
    Para artículos de pago obtiene solo el preview visible.

    Args:
        url: URL del artículo en Substack

    Returns:
        Texto del artículo (lo que sea visible sin suscripción)
    """
    try:
        r = requests.get(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'
                )
            },
            timeout=15
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        # Substack ha cambiado su estructura varias veces — probamos selectores
        for selector in ['div.available-content', 'div.body.markup', 'article']:
            el = soup.select_one(selector)
            if el:
                text = el.get_text('\n', strip=True)
                if len(text) > 200:
                    return text

        return ''
    except Exception as e:
        print(f"  ⚠️  Error descargando: {e}")
        return ''


# ─────────────────────────────────────────────
# Guardar el mapa del día
# ─────────────────────────────────────────────

def guardar_mapa_dia(article: dict, trading_info: dict, content: str) -> dict:
    """
    Construye y guarda el mapa del día en dos sitios:
    - data/daily/today.json  → se sobreescribe cada día (el motor lo lee aquí)
    - data/daily/history/YYYY-MM-DD.json  → archivo histórico permanente

    Args:
        article:       metadatos del artículo (de get_latest_article)
        trading_info:  info extraída por LLM (bias, niveles, setup)
        content:       texto del artículo para referencia

    Returns:
        dict con toda la información del día
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    mapa = {
        # Metadatos del artículo
        'date':            article.get('published_at', str(date.today())),
        'title':           article.get('title', ''),
        'url':             article.get('url', ''),
        'is_complete':     article.get('is_free', False),
        'parsed_at':       datetime.now().isoformat(),

        # Mapa de trading extraído por LLM
        'bias':            trading_info.get('bias', 'unknown'),
        'condicion_bias':  trading_info.get('condicion_bias'),
        'nivel_critico':   trading_info.get('nivel_critico'),
        'soportes':        trading_info.get('soportes', []),
        'resistencias':    trading_info.get('resistencias', []),
        'setup':           trading_info.get('setup'),
        'invalida_si':     trading_info.get('invalida_si'),

        # Preview del contenido para debug
        'content_preview': content[:1500] if content else '',
    }

    # Guardar today.json (el motor de señales siempre lee este archivo)
    with open(TODAY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapa, f, indent=2, ensure_ascii=False)

    # Guardar copia histórica con la fecha como nombre
    history_file = HISTORY_DIR / f"{mapa['date']}.json"
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(mapa, f, indent=2, ensure_ascii=False)

    return mapa


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

def parse_daily_newsletter(force: bool = False) -> dict | None:
    """
    Descarga y parsea el newsletter más reciente de Adam Mancini.

    Flujo:
    1. Comprueba si ya tenemos el mapa de hoy (evita reprocesar)
    2. Obtiene el artículo más reciente de Substack
    3. Descarga el contenido (preview o completo si es gratuito)
    4. Extrae bias, niveles y setup con Claude Haiku
    5. Guarda en data/daily/today.json

    Args:
        force: si True, reparsea aunque ya exista el de hoy

    Returns:
        dict con el mapa del día, o None si no hay newsletter disponible
    """
    print("=" * 55)
    print("  Bot Adam Mancini — Newsletter Parser")
    print("=" * 55)

    hoy = str(date.today())

    # ── Comprobar si ya tenemos el de hoy ────────────────────────────────
    if TODAY_FILE.exists() and not force:
        with open(TODAY_FILE) as f:
            existing = json.load(f)
        if existing.get('date') == hoy:
            print(f"✅ Newsletter de hoy ({hoy}) ya procesado.")
            _mostrar_resumen(existing)
            return existing

    # ── Obtener metadatos del artículo más reciente ───────────────────────
    print(f"🔍 Consultando Substack para {hoy}...")
    article = get_latest_article()

    if not article:
        print("❌ No se pudo conectar a Substack")
        return None

    print(f"📰 Artículo: [{article['published_at']}] {article['title'][:60]}")

    # ── Descargar contenido del artículo ─────────────────────────────────
    print("📥 Descargando contenido...")
    content = scrape_article_content(article['url'])

    if content:
        print(f"   ✅ {len(content):,} caracteres obtenidos")
        article['is_free'] = len(content) > 500  # actualizar si es completo
    else:
        print("   ⚠️  Sin contenido (artículo de pago) — usando título")
        content = f"{article['title']}\n{article.get('subtitle', '')}"

    # ── Extraer mapa de trading con LLM ──────────────────────────────────
    print("🤖 Extrayendo mapa del día con Claude Haiku...")

    try:
        trading_info = extract_trading_info({
            'title':        article['title'],
            'published_at': article['published_at'],
            'content':      content,
        })
        print(f"   ✅ Bias: {trading_info.get('bias')} | "
              f"Crítico: {trading_info.get('nivel_critico')} | "
              f"Soportes: {trading_info.get('soportes', [])}")
    except Exception as e:
        print(f"   ❌ Error LLM: {e}")
        trading_info = {}

    # ── Guardar mapa del día ──────────────────────────────────────────────
    mapa = guardar_mapa_dia(article, trading_info, content)
    print(f"\n💾 Guardado en: {TODAY_FILE}")

    # ── Mostrar resumen legible ───────────────────────────────────────────
    _mostrar_resumen(mapa)

    if not article.get('is_free'):
        print("\n💡 Solo hay preview disponible.")
        print("   Al suscribirte el análisis será mucho más detallado.")

    return mapa


def _mostrar_resumen(mapa: dict):
    """Muestra el mapa del día de forma legible en consola."""
    print()
    print("┌─ MAPA DEL DÍA " + "─" * 38)
    print(f"│ Fecha:         {mapa.get('date', '?')}")
    print(f"│ Bias:          {mapa.get('bias', '?').upper()}")

    if mapa.get('nivel_critico'):
        print(f"│ Nivel crítico: {mapa['nivel_critico']}")

    if mapa.get('soportes'):
        print(f"│ Soportes:      {mapa['soportes']}")

    if mapa.get('resistencias'):
        print(f"│ Resistencias:  {mapa['resistencias']}")

    if mapa.get('setup'):
        print(f"│ Setup:         {mapa['setup'][:80]}")

    if mapa.get('invalida_si'):
        print(f"│ Invalida si:   {mapa['invalida_si'][:80]}")

    print("└" + "─" * 53)
    print()


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────

if __name__ == '__main__':
    force = '--force' in sys.argv
    parse_daily_newsletter(force=force)
