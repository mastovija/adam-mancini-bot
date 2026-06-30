"""
knowledge_base/add_tweets_to_kb.py — Indexes historical tweets in ChromaDB
==========================================================================
Adds the tweets downloaded from @AdamMancini4 to the vector store.
The tweets complement the newsletter articles with intraday perspective
and real-time market commentary.

USAGE:
    python knowledge_base/add_tweets_to_kb.py

Note: tweets are short (280 chars) so we do NOT use the LLM to extract
info — we extract numbers (levels) directly with regex and detect
direction keywords. Faster and cheaper.
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
# Simple level extraction without the LLM
# ─────────────────────────────────────────────

# Keywords that indicate direction
PALABRAS_BULLISH  = ['long', 'bull', 'bullish', 'long here', 'buy', 'support', 'bounce', 'rally', 'upside', 'higher']
PALABRAS_BEARISH  = ['short', 'bear', 'bearish', 'sell', 'resistance', 'breakdown', 'lower', 'downside', 'failed']


def extraer_niveles(texto: str) -> list:
    """
    Extracts numbers that look like ES price levels (4000-9999).
    Adam always mentions his levels as numbers: "7527 support", "7474 below".
    """
    numeros = re.findall(r'\b(\d{4,5}(?:\.\d+)?)\b', texto)
    niveles = []
    for n in numeros:
        try:
            nivel = float(n)
            # Valid range for ES futures (avoid years, percentages, etc.)
            if 3000 <= nivel <= 10000:
                niveles.append(nivel)
        except ValueError:
            pass
    return list(set(niveles))  # Remove duplicates


def detectar_bias(texto: str) -> str:
    """
    Detects the tweet's bias based on keywords.
    Simple but effective for short trading tweets.
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
    Filters out tweets that don't carry trading information.
    Excludes: retweets, very short tweets, replies without levels.
    """
    texto = tweet.get('text', '')

    # Exclude retweets
    if texto.startswith('RT @'):
        return False

    # Exclude very short tweets (no trading information)
    if len(texto) < 50:
        return False

    # Only include tweets that mention price levels
    niveles = extraer_niveles(texto)
    if not niveles:
        return False

    return True


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

def add_tweets_to_kb():
    """
    Reads every downloaded tweet and adds them to ChromaDB.

    For each relevant tweet:
    1. Extracts price levels with regex
    2. Detects bias with keywords
    3. Adds to ChromaDB with metadata for filtering
    """
    print("=" * 55)
    print("  Adam Mancini Bot — Adding Tweets to ChromaDB")
    print("=" * 55)

    # ── Load tweets ───────────────────────────────────────────────────────
    tweets_file = TWEETS_DIR / 'adam_mancini_tweets.json'
    if not tweets_file.exists():
        print("❌ No tweets found.")
        print(f"   Run first: python scrapers/twitter_scraper_playwright.py")
        return

    with open(tweets_file, 'r', encoding='utf-8') as f:
        todos_tweets = json.load(f)

    print(f"📊 Tweets loaded: {len(todos_tweets)}")

    # ── Initialize ChromaDB ───────────────────────────────────────────────
    print("📚 Connecting to ChromaDB...")
    collection = get_collection()
    print(f"   Current documents: {collection.count()}")

    # ── Get already-indexed IDs ───────────────────────────────────────────
    # Avoid duplicates if we run the script several times
    existentes = set()
    try:
        resultado = collection.get(include=[])
        existentes = set(resultado['ids'])
    except Exception:
        pass

    # ── Process and index tweets ──────────────────────────────────────────
    print("\n📥 Indexing relevant tweets...")
    print("-" * 40)

    indexados  = 0
    filtrados  = 0
    duplicados = 0

    for tweet in todos_tweets:
        tweet_id = f"tweet_{tweet.get('id', '')}"

        # Skip duplicates
        if tweet_id in existentes:
            duplicados += 1
            continue

        # Filter out tweets without trading information
        if not tweet_es_relevante(tweet):
            filtrados += 1
            continue

        texto   = tweet.get('text', '')
        fecha   = tweet.get('created_at', '')
        niveles = extraer_niveles(texto)
        bias    = detectar_bias(texto)

        # ── Build the document for ChromaDB ──────────────────────────────
        # Format similar to the newsletter articles
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
            "source":         "tweet",  # distinguishes tweets from newsletter
        }

        try:
            collection.upsert(
                documents=[doc_texto],
                metadatas=[metadata],
                ids=[tweet_id]
            )
            indexados += 1

            if indexados <= 5 or indexados % 50 == 0:
                print(f"  [{indexados}] {fecha[:10]} — {bias:8s} | levels: {niveles[:3]} | {texto[:60]}...")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print(f"✅ Tweets indexed:  {indexados}")
    print(f"🔕 Filtered (no levels): {filtrados}")
    print(f"⏭️  Duplicates skipped: {duplicados}")
    print(f"📚 Total in ChromaDB: {collection.count()}")
    print("=" * 55)
    print()
    print("The knowledge base now includes:")
    print("  • Newsletter: ~1403 articles (2021-2026)")
    print(f"  • Tweets: {indexados} tweets with price levels")


if __name__ == '__main__':
    add_tweets_to_kb()
