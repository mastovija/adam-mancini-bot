"""
knowledge_base/processor.py — Extrae información de trading con Claude Haiku
=============================================================================
Lee un artículo del newsletter y usa el LLM para extraer los datos
estructurados que necesitamos: bias, niveles, setup del día.

No llames esto directamente — lo usa build_kb.py
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
# Diseñado para ser conciso (menos tokens = más barato)
# y devolver JSON limpio sin markdown

EXTRACTION_PROMPT = """Analiza este fragmento de newsletter de trading del S&P 500/ES y extrae los datos clave.

Título: {title}
Fecha: {date}
Contenido:
{content}

Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown:
{{
  "bias": "bullish" | "bearish" | "neutral" | "mixed",
  "condicion_bias": "una frase corta con la condición principal",
  "soportes": [lista de niveles numéricos de soporte mencionados],
  "resistencias": [lista de niveles numéricos de resistencia mencionados],
  "nivel_critico": número más importante del día o null,
  "setup": "descripción del setup principal en máximo 2 frases",
  "invalida_si": "qué condición invalidaría la tesis o null"
}}

Si un campo no se menciona: null para valores, [] para listas."""


# ─────────────────────────────────────────────
# Función principal de extracción
# ─────────────────────────────────────────────

def extract_trading_info(article: dict) -> dict:
    """
    Usa Claude Haiku para extraer información estructurada de un artículo.

    Args:
        article: dict con 'title', 'published_at', 'content'

    Returns:
        dict con bias, soportes, resistencias, setup, etc.
        En caso de error devuelve un dict vacío con valores por defecto.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Limitar el contenido a 1500 chars para reducir coste
    # Los datos clave de Adam siempre están en los primeros párrafos
    content_truncated = article.get('content', '')[:1500]

    prompt = EXTRACTION_PROMPT.format(
        title=article.get('title', ''),
        date=article.get('published_at', ''),
        content=content_truncated
    )

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=400,
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
        "bias": "unknown",
        "condicion_bias": None,
        "soportes": [],
        "resistencias": [],
        "nivel_critico": None,
        "setup": None,
        "invalida_si": None,
    }
