"""
knowledge_base/add_tweets_to_kb.py — Indexa tweets históricos en ChromaDB
==========================================================================
Añade los tweets descargados de @AdamMancini4 a la base vectorial.
Los tweets complementan los artículos del newsletter con perspectiva
intradía y comentarios de mercado en tiempo real.

USO:
    python knowledge_base/add_tweets_to_kb.py

Nota: los tweets son cortos (280 chars) así que NO usamos LLM para extraer
info — directamente extraemos números (niveles) con regex y detectamos
palabras clave de dirección. Más rápido y más barato.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import TWEETS_DIR, PROCESSED_DIR
from knowledge_base.vectordb import get_collection


# ─────────────────────────────────────────────
# Extracción simple de niveles sin LLM
# ─────────────────────────────────────────────

# Palabras clave que indican dirección
PALABRAS_BULLISH  = ['long', 'bull', 'bullish', 'long here', 'buy', 'support', 'bounce', 'rally', 'upside', 'higher']
PALABRAS_BEARISH  = ['short', 'bear', 'bearish', 'sell', 'resistance', 'breakdown', 'lower', 'downside', 'failed']


def extraer_niveles(texto: str) -> list:
    """
    Extrae números que parecen niveles de precio del ES (4000-9999).
    Adam siempre menciona sus niveles como números: "7527 support", "7474 below".
    """
    numeros = re.findall(r'\b(\d{4,5}(?:\.\d+)?)\b', texto)
    niveles = []
    for n in numeros:
        try:
            nivel = float(n)
            # Rango válido para ES futures (evitar años, porcentajes, etc.)
            if 3000 <= nivel <= 10000:
                niveles.append(nivel)
        except ValueError:
            pass
    return list(set(niveles))  # Eliminar duplicados


def detectar_bias(texto: str) -> str:
    """
    Detecta el sesgo del tweet basándose en palabras clave.
    Simple pero efectivo para tweets cortos de trading.
    """
    texto_lower = texto.lower()

    puntos_bull = sum(1 for p in PALABRAS_BULLISH if p in texto_lower)
    puntos_bear = sum(1 for p in PALABRAS_BEARISH if p in texto_lower)

    if puntos_bull > puntos_bear:
        return 'bullish'
    elif puntos_bear > puntos_bull:
        return 'bearish'
    else:
        return 'neutral'


def tweet_es_relevante(tweet: dict) -> bool:
    """
    Filtra tweets que no aportan información de trading.
    Excluye: retweets, tweets muy cortos, respuestas sin niveles.
    """
    texto = tweet.get('text', '')

    # Excluir retweets
    if texto.startswith('RT @'):
        return False

    # Excluir tweets muy cortos (sin información de trading)
    if len(texto) < 50:
        return False

    # Solo incluir tweets que mencionen niveles de precio
    niveles = extraer_niveles(texto)
    if not niveles:
        return False

    return True


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

def add_tweets_to_kb():
    """
    Lee todos los tweets descargados y los añade a ChromaDB.

    Para cada tweet relevante:
    1. Extrae niveles de precio con regex
    2. Detecta bias con palabras clave
    3. Añade a ChromaDB con metadatos para filtrado
    """
    print("=" * 55)
    print("  Bot Adam Mancini — Añadiendo Tweets a ChromaDB")
    print("=" * 55)

    # ── Cargar tweets ─────────────────────────────────────────────────────
    tweets_file = TWEETS_DIR / 'adam_mancini_tweets.json'
    if not tweets_file.exists():
        print("❌ No se encontraron tweets.")
        print(f"   Ejecuta primero: python scrapers/twitter_scraper_playwright.py")
        return

    with open(tweets_file, 'r', encoding='utf-8') as f:
        todos_tweets = json.load(f)

    print(f"📊 Tweets cargados: {len(todos_tweets)}")

    # ── Inicializar ChromaDB ──────────────────────────────────────────────
    print("📚 Conectando a ChromaDB...")
    collection = get_collection()
    print(f"   Documentos actuales: {collection.count()}")

    # ── Obtener IDs ya indexados ──────────────────────────────────────────
    # Evitar duplicados si corremos el script varias veces
    existentes = set()
    try:
        resultado = collection.get(include=[])
        existentes = set(resultado['ids'])
    except Exception:
        pass

    # ── Procesar e indexar tweets ─────────────────────────────────────────
    print("\n📥 Indexando tweets relevantes...")
    print("-" * 40)

    indexados  = 0
    filtrados  = 0
    duplicados = 0

    for tweet in todos_tweets:
        tweet_id = f"tweet_{tweet.get('id', '')}"

        # Saltar duplicados
        if tweet_id in existentes:
            duplicados += 1
            continue

        # Filtrar tweets sin información de trading
        if not tweet_es_relevante(tweet):
            filtrados += 1
            continue

        texto   = tweet.get('text', '')
        fecha   = tweet.get('created_at', '')
        niveles = extraer_niveles(texto)
        bias    = detectar_bias(texto)

        # ── Construir documento para ChromaDB ────────────────────────────
        # Formato similar al de los artículos del newsletter
        doc_texto = (
            f"Fecha: {fecha}\n"
            f"Tipo: Tweet de Adam Mancini\n"
            f"Bias: {bias}\n"
            f"Niveles: {niveles}\n\n"
            f"{texto}"
        )

        metadata = {
            "date":           fecha[:10] if fecha else '',
            "title":          texto[:80],
            "bias":           bias,
            "nivel_critico":  float(niveles[0]) if niveles else 0.0,
            "soportes":       json.dumps([n for n in niveles if n < (niveles[0] if niveles else 9999)]),
            "resistencias":   json.dumps([n for n in niveles if n >= (niveles[0] if niveles else 0)]),
            "setup":          texto[:200],
            "is_complete":    True,
            "content_length": len(texto),
            "source":         "tweet",  # diferencia tweets de newsletter
        }

        try:
            collection.upsert(
                documents=[doc_texto],
                metadatas=[metadata],
                ids=[tweet_id]
            )
            indexados += 1

            if indexados <= 5 or indexados % 50 == 0:
                print(f"  [{indexados}] {fecha[:10]} — {bias:8s} | niveles: {niveles[:3]} | {texto[:60]}...")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # ── Resumen ───────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"✅ Tweets indexados:  {indexados}")
    print(f"🔕 Filtrados (sin niveles): {filtrados}")
    print(f"⏭️  Duplicados saltados: {duplicados}")
    print(f"📚 Total en ChromaDB: {collection.count()}")
    print("=" * 55)
    print()
    print("La base de conocimiento ahora incluye:")
    print("  • Newsletter: ~1403 artículos (2021-2026)")
    print(f"  • Tweets: {indexados} tweets con niveles de precio")


if __name__ == '__main__':
    add_tweets_to_kb()
