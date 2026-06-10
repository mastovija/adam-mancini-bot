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

EXTRACTION_PROMPT = """Analiza este newsletter de trading del S&P 500/ES futures.

Título: {title}
Fecha: {date}

INICIO DEL ARTÍCULO (análisis y contexto):
{content_start}

SECCIÓN TRADE PLAN (niveles del día):
{content_end}

Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown:
{{
  "bias": "bullish" | "bearish" | "neutral" | "mixed",
  "condicion_bias": "una frase corta con la condición principal",
  "soportes": [TODOS los niveles numéricos de soporte que aparezcan en 'Supports are:'],
  "resistencias": [TODOS los niveles numéricos de resistencia que aparezcan en 'Resistances are:'],
  "nivel_critico": el nivel más importante del día o null,
  "setup": "descripción del setup principal en máximo 2 frases",
  "invalida_si": "qué condición invalidaría la tesis o null"
}}

Extrae TODOS los niveles de las listas 'Supports are:' y 'Resistances are:', no solo los (major).
Si no hay listas explícitas, extrae los niveles mencionados en el texto narrativo.
Si un campo no aparece: null para valores simples, [] para listas."""


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
