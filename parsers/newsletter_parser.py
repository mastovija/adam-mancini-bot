"""
parsers/newsletter_parser.py — Parser del newsletter diario de Adam Mancini
=============================================================================
Cada mañana descarga el artículo más reciente de Trade Companion
(tradecompanion.substack.com), extrae el mapa del día con Claude Haiku
y guarda el resultado en data/daily/today.json.

Este archivo es lo que el motor de señales (Fase 5) consulta durante
toda la sesión para saber el bias, los niveles clave y las condiciones.

CAMPOS CLAVE en today.json:
  - soportes, resistencias, nivel_critico, bias: extraídos por Haiku
  - content_plan: últimos 8000 chars del artículo = sección Trade Plan
    (contiene "I'd bid direct", "Bull/Bear case", contexto de niveles)
    Este es el campo más importante — el motor de señales lo pasa completo al LLM.

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

from config import SUBSTACK_URL, DATA_DIR, SUBSTACK_COOKIES
from knowledge_base.processor import extract_trading_info


# ─────────────────────────────────────────────
# Rutas de archivos
# ─────────────────────────────────────────────
DAILY_DIR   = DATA_DIR / 'daily'
TODAY_FILE  = DAILY_DIR / 'today.json'
HISTORY_DIR = DAILY_DIR / 'history'


# ─────────────────────────────────────────────
# Cookies de suscripción de pago
# ─────────────────────────────────────────────

def get_substack_cookies() -> dict:
    """
    Parsea SUBSTACK_COOKIES del .env a un dict para requests.
    Con substack.sid, Substack sirve el artículo completo (30,000+ chars).
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


# ─────────────────────────────────────────────
# Obtener el artículo más reciente
# ─────────────────────────────────────────────

def get_latest_article() -> dict | None:
    """Consulta la API pública de Substack para el artículo más reciente."""
    try:
        url = f"{SUBSTACK_URL}/api/v1/archive?sort=new&offset=0&limit=1"
        r   = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
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
            'published_at': a.get('post_date', '')[:10],
            'is_free':      a.get('audience') == 'everyone',
        }
    except Exception as e:
        print(f"❌ Error querying Substack: {e}")
        return None


def scrape_article_content(url: str) -> str:
    """
    Descarga el contenido completo del artículo usando las cookies de suscripción.
    Sin cookies solo obtiene el preview (2,000-3,000 chars).
    """
    cookies = get_substack_cookies()

    if cookies:
        print("   🔑 Using subscription cookies (full content)")
    else:
        print("   🆓 No cookies — public preview only")

    try:
        r = requests.get(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'
                )
            },
            cookies=cookies,
            timeout=15
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        for selector in ['div.available-content', 'div.body.markup', 'article']:
            el = soup.select_one(selector)
            if el:
                text = el.get_text('\n', strip=True)
                if len(text) > 200:
                    return text

        return ''
    except Exception as e:
        print(f"  ⚠️  Error downloading: {e}")
        return ''


# ─────────────────────────────────────────────
# Guardar el mapa del día
# ─────────────────────────────────────────────

def guardar_mapa_dia(article: dict, trading_info: dict, content: str) -> dict:
    """
    Construye y guarda el mapa del día en dos sitios:
    - data/daily/today.json              → sobreescrito cada día (motor lo lee aquí)
    - data/daily/history/YYYY-MM-DD.json → copia histórica permanente

    El campo más importante es 'content_plan': los últimos 8000 chars del
    artículo, que corresponden a la sección "Trade Plan" donde Adam escribe:
    - "In terms of lvls I'd bid direct:" (los niveles realmente accionables)
    - "Bull case tomorrow:" / "Bear case tomorrow:"
    - Contexto específico de cada nivel ("tested to death", "obvious FB", etc.)

    El motor de señales pasa este texto completo al LLM para que tome
    decisiones con las palabras exactas de Adam, no solo con listas de números.
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    mapa = {
        'date':           article.get('published_at', str(date.today())),
        'title':          article.get('title', ''),
        'url':            article.get('url', ''),
        'is_complete':    len(content) > 5000,
        'parsed_at':      datetime.now().isoformat(),

        # Campos extraídos por Haiku (niveles estructurados)
        'bias':           trading_info.get('bias', 'unknown'),
        'condicion_bias': trading_info.get('condicion_bias'),
        'nivel_critico':  trading_info.get('nivel_critico'),
        'soportes':       trading_info.get('soportes', []),
        'resistencias':   trading_info.get('resistencias', []),
        'setup':          trading_info.get('setup'),
        'invalida_si':    trading_info.get('invalida_si'),

        # Artículo completo — el LLM lo lee entero para entender el plan de Adam
        # Coste: ~$0.007 por llamada al LLM, solo cuando el precio toca un nivel
        'content_plan':   content,
    }

    with open(TODAY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapa, f, indent=2, ensure_ascii=False)

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
    3. Descarga el contenido completo (con cookies de suscripción)
    4. Extrae bias y niveles con Claude Haiku
    5. Guarda en data/daily/today.json con content_plan completo
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Newsletter Parser")
    print("=" * 55)

    hoy = str(date.today())

    if TODAY_FILE.exists() and not force:
        with open(TODAY_FILE) as f:
            existing = json.load(f)
        if existing.get('date') == hoy:
            print(f"✅ Today's newsletter ({hoy}) already processed.")
            _mostrar_resumen(existing)
            return existing

    print(f"🔍 Querying Substack for {hoy}...")
    article = get_latest_article()

    if not article:
        print("❌ Could not connect to Substack")
        return None

    print(f"📰 Article: [{article['published_at']}] {article['title'][:60]}")

    print("📥 Downloading content...")
    content = scrape_article_content(article['url'])

    if content:
        print(f"   ✅ {len(content):,} characters fetched")
    else:
        print("   ⚠️  No content — using title")
        content = f"{article['title']}\n{article.get('subtitle', '')}"

    print("🤖 Extracting day map with Claude Haiku...")

    try:
        trading_info = extract_trading_info({
            'title':        article['title'],
            'published_at': article['published_at'],
            'content':      content,
        })
        soportes     = trading_info.get('soportes', [])
        resistencias = trading_info.get('resistencias', [])
        print(f"   ✅ Bias: {trading_info.get('bias')} | "
              f"Critical: {trading_info.get('nivel_critico')} | "
              f"Supports: {len(soportes)} | "
              f"Resistances: {len(resistencias)}")
    except Exception as e:
        print(f"   ❌ LLM error: {e}")
        trading_info = {}

    mapa = guardar_mapa_dia(article, trading_info, content)
    print(f"\n💾 Saved to: {TODAY_FILE}")
    print(f"   content_plan: {len(mapa.get('content_plan', ''))} chars (full Trade Plan)")
    _mostrar_resumen(mapa)
    return mapa


def _mostrar_resumen(mapa: dict):
    """Muestra el mapa del día de forma legible en consola."""
    print()
    print("┌─ DAY MAP " + "─" * 43)
    print(f"│ Date:          {mapa.get('date', '?')}")
    print(f"│ Bias:          {mapa.get('bias', '?').upper()}")
    print(f"│ Complete:      {'✅ Yes' if mapa.get('is_complete') else '⚠️  Preview only'}")
    print(f"│ Full plan:     {'✅ Yes' if mapa.get('content_plan') else '❌ No'} "
          f"({len(mapa.get('content_plan', ''))} chars)")

    if mapa.get('nivel_critico'):
        print(f"│ Critical level: {mapa['nivel_critico']}")

    if mapa.get('soportes'):
        print(f"│ Supports ({len(mapa['soportes'])}):  {mapa['soportes']}")

    if mapa.get('resistencias'):
        print(f"│ Resists ({len(mapa['resistencias'])}):   {mapa['resistencias'][:8]}...")

    if mapa.get('setup'):
        texto = mapa['setup']
        print(f"│ Setup:")
        for linea in texto.split('.'):
            linea = linea.strip()
            if linea:
                print(f"│   {linea}.")

    if mapa.get('invalida_si'):
        print(f"│ Invalidated if: {mapa['invalida_si']}")

    print("└" + "─" * 53)
    print()


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────

if __name__ == '__main__':
    force = '--force' in sys.argv
    parse_daily_newsletter(force=force)
