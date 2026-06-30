"""
knowledge_base/processor.py — Extracts trading information with Claude Haiku
=============================================================================
Reads a newsletter article and uses the LLM to extract the structured
data we need: bias, levels, setup of the day.

EXTRACTION STRATEGY:
- First 2000 chars → bias, context and narrative analysis
- Last 6000 chars  → Trade Plan section with the level lists
  (Adam always writes "Supports are: X, Y, Z" and "Resistances are: X, Y, Z"
  at the end of the article. With the old 1500-char limit we never reached
  this section and extracted 0-3 levels instead of the real 20-50.)

Don't call this directly — it's used by build_kb.py and newsletter_parser.py
"""

import json
import re
import sys
from pathlib import Path

import anthropic

sys.path.append(str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY, LLM_MODEL


# ─────────────────────────────────────────────
# Extraction prompt
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
# Main extraction function
# ─────────────────────────────────────────────

def extract_trading_info(article: dict) -> dict:
    """
    Uses Claude Haiku to extract structured information from an article.

    Sends two sections of the article to the LLM:
    - content_start: first 2000 chars for bias and narrative context
    - content_end:   last 6000 chars for the Trade Plan levels

    This solves the previous problem where the 1500-char limit cut the
    article off before reaching the 'Supports are:' and 'Resistances are:'
    section, which Adam always places at the end.

    Args:
        article: dict with 'title', 'published_at', 'content'

    Returns:
        dict with bias, soportes, resistencias, nivel_critico, setup, invalida_si
        On error it returns an empty dict with default values.
    """
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content = article.get('content', '')

    # First 2000 chars: contain the narrative analysis and the day's bias
    content_start = content[:2000]

    # Last 6000 chars: contain the Trade Plan with the level lists.
    # If the article is short (preview) content_end will be empty or identical to start.
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
            max_tokens=1000,  # increased: we need room for 20-50 levels in the JSON
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()

        # Strip markdown if the LLM adds it despite the instructions
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)

        return json.loads(raw)

    except json.JSONDecodeError:
        # LLM returned something that is not valid JSON
        return _empty_trading_info()
    except Exception as e:
        raise RuntimeError(f"Error en API: {e}")


def _empty_trading_info() -> dict:
    """Default values when extraction fails."""
    return {
        "bias":           "unknown",
        "condicion_bias": None,
        "soportes":       [],
        "resistencias":   [],
        "nivel_critico":  None,
        "setup":          None,
        "invalida_si":    None,
    }
