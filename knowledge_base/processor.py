"""
knowledge_base/processor.py — Extrae información de trading con Claude Haiku
=============================================================================
Lee un artículo del newsletter y usa el LLM para extraer los datos
estructurados que necesitamos: bias, niveles, setup del día.

ESTRATEGIA DE EXTRACCIÓN:
- Primeros 2000 chars → bias, contexto y análisis narrativo
- Últimos 6000 chars  → sección Trade Plan con las listas de niveles
  (Adam siempre escribe "Supports are: X, Y, Z" y "Resistances are: X, Y, Z"
  al final del artículo. Con el límite antiguo de 1500 chars nunca llegábamos
  a esta sección y extraíamos 0-3 niveles en vez de los 20-50 reales.)

No llames esto directamente — lo usa build_kb.py y newsletter_parser.py
"""

import json
import re
import sys
from pathlib import Path

import anthropic

sys.path.append(str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY, LLM_MODEL


# ─────────────────────────────────────────────
# Prompt de extracción
# ─────────────────────────────────────────────

EXTRACTION_PROMPT = """Analyze this S&P 500/ES futures trading newsletter.

Title: {title}
Date: {date}

START OF THE ARTICLE (analysis and context):
{content_start}

TRADE PLAN SECTION (levels for the day):
{content_end}

Respond ONLY with valid JSON, no extra text, no markdown:
{{
  "bias": "bullish" | "bearish" | "neutral" | "mixed",
  "condicion_bias": "a short sentence with the main condition",
  "soportes": [ALL numeric support levels that appear under 'Supports are:'],
  "resistencias": [ALL numeric resistance levels that appear under 'Resistances are:'],
  "nivel_critico": the most important level of the day or null,
  "setup": "description of the main setup in at most 2 sentences",
  "invalida_si": "what condition would invalidate the thesis or null"
}}

Extract ALL levels from the 'Supports are:' and 'Resistances are:' lists, not just the (major) ones.
If there are no explicit lists, extract the levels mentioned in the narrative text.
If a field does not appear: null for simple values, [] for lists."""


# ─────────────────────────────────────────────
# Función principal de extracción
# ─────────────────────────────────────────────

def extract_trading_info(article: dict) -> dict:
    """
    Usa Claude Haiku para extraer información estructurada de un artículo.

    Envía dos secciones del artículo al LLM:
    - content_start: primeros 2000 chars para bias y contexto narrativo
    - content_end:   últimos 6000 chars para los niveles del Trade Plan

    Esto resuelve el problema anterior donde el límite de 1500 chars
    cortaba el artículo antes de llegar a la sección 'Supports are:'
    y 'Resistances are:', que Adam siempre coloca al final.

    Args:
        article: dict con 'title', 'published_at', 'content'

    Returns:
        dict con bias, soportes, resistencias, nivel_critico, setup, invalida_si
        En caso de error devuelve un dict vacío con valores por defecto.
    """
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content = article.get('content', '')

    # Primeros 2000 chars: contienen el análisis narrativo y el bias del día
    content_start = content[:2000]

    # Últimos 6000 chars: contienen el Trade Plan con las listas de niveles.
    # Si el artículo es corto (preview) el contenido_end será vacío o idéntico al start.
    content_end = content[-6000:] if len(content) > 6000 else ''

    prompt = EXTRACTION_PROMPT.format(
        title=article.get('title', ''),
        date=article.get('published_at', ''),
        content_start=content_start,
        content_end=content_end
    )

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,  # aumentado: necesitamos espacio para 20-50 niveles en el JSON
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()

        # Limpiar markdown si el LLM lo añade a pesar de las instrucciones
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)

        return json.loads(raw)

    except json.JSONDecodeError:
        # LLM devolvió algo que no es JSON válido
        return _empty_trading_info()
    except Exception as e:
        raise RuntimeError(f"Error en API: {e}")


def _empty_trading_info() -> dict:
    """Valores por defecto cuando la extracción falla."""
    return {
        "bias":           "unknown",
        "condicion_bias": None,
        "soportes":       [],
        "resistencias":   [],
        "nivel_critico":  None,
        "setup":          None,
        "invalida_si":    None,
    }
