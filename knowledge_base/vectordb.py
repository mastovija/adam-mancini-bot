"""
knowledge_base/vectordb.py — Vector store with ChromaDB
=========================================================
Manages the ChromaDB collection that stores all of Adam Mancini's
knowledge. Each document is a newsletter article.

Two main operations:
  - add_article(): indexes an article with its metadata
  - query_similar(): searches for situations similar to the current one
"""

import json
import sys
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

sys.path.append(str(Path(__file__).parent.parent))
from config import CHROMA_DIR, CHROMA_COLLECTION


# ─────────────────────────────────────────────
# Collection initialization
# ─────────────────────────────────────────────

def get_collection():
    """
    Gets (or creates if it doesn't exist) the ChromaDB collection.

    The collection persists to disk in data/chromadb/
    Uses all-MiniLM-L6-v2 as the embedding model (~80MB downloaded on first run)
    """
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # DefaultEmbeddingFunction uses ONNX (quantized all-MiniLM-L6-v2).
    # It's the alternative to sentence-transformers, which doesn't support Python 3.13.
    ef = embedding_functions.DefaultEmbeddingFunction()


    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}  # cosine similarity for text
    )

    return collection


# ─────────────────────────────────────────────
# Add articles
# ─────────────────────────────────────────────

def add_article(collection, article: dict, trading_info: dict):
    """
    Indexes an article in ChromaDB.

    What gets vectorized (the 'document'):
      We combine title, bias, setup and content so that semantic search
      finds similar articles by market context.

    What goes into metadata:
      Structured fields for filtering (bias, date, levels).
      ChromaDB allows filtering by metadata before searching by similarity.

    Args:
        collection: ChromaDB collection (from get_collection())
        article: newsletter dict (slug, title, content, published_at...)
        trading_info: dict extracted by processor.py (bias, soportes...)
    """

    # ── Text to vectorize ─────────────────────────────────────────────────
    # We combine the structured info with the original content.
    # This improves semantic search: we can search "SPX bouncing off bullish support"
    # and find articles where Adam described that situation.
    bias_str   = trading_info.get('bias', 'unknown') or 'unknown'
    setup_str  = trading_info.get('setup', '') or ''
    content    = article.get('content', '')[:1500]  # max 1500 chars

    document_text = (
        f"Fecha: {article.get('published_at', '')}\n"
        f"Título: {article.get('title', '')}\n"
        f"Bias: {bias_str}\n"
        f"Setup: {setup_str}\n\n"
        f"{content}"
    )

    # ── Metadata for filtering ────────────────────────────────────────────
    # ChromaDB only accepts strings, ints, floats and bools in metadata.
    # Lists are serialized as a JSON string.
    soportes      = trading_info.get('soportes', []) or []
    resistencias  = trading_info.get('resistencias', []) or []
    nivel_critico = trading_info.get('nivel_critico')

    metadata = {
        "date":           article.get('published_at', ''),
        "title":          article.get('title', '')[:100],
        "bias":           bias_str,
        "nivel_critico":  float(nivel_critico) if nivel_critico else 0.0,
        "soportes":       json.dumps(soportes),       # list as string
        "resistencias":   json.dumps(resistencias),   # list as string
        "setup":          (setup_str or '')[:200],
        "is_complete":    bool(article.get('is_complete', False)),
        "content_length": len(article.get('content', '')),
    }

    # ── Add to the collection ─────────────────────────────────────────────
    # The ID is the article slug (unique per article)
    collection.upsert(  # upsert = add if it doesn't exist, update if it does
        documents=[document_text],
        metadatas=[metadata],
        ids=[article.get('slug', article.get('id', str(hash(article.get('title', '')))))]
    )


# ─────────────────────────────────────────────
# Query the knowledge base
# ─────────────────────────────────────────────

def query_similar(
    collection,
    query: str,
    n_results: int = 5,
    bias_filter: str = None,
    min_content_length: int = 200
) -> list:
    """
    Searches Adam's history for situations similar to the current one.

    Usage example:
        results = query_similar(
            collection,
            "ES at 5400 rejecting resistance with bearish momentum",
            bias_filter="bearish"
        )

    Args:
        query: description of the current market situation
        n_results: how many similar articles to return
        bias_filter: 'bullish', 'bearish', 'neutral', 'mixed' or None
        min_content_length: filter out very short articles

    Returns:
        List of dicts with: date, title, bias, setup, content, distance
    """
    # Build the metadata filter
    where_clauses = []

    if bias_filter:
        where_clauses.append({"bias": {"$eq": bias_filter}})

    if min_content_length > 0:
        where_clauses.append({"content_length": {"$gte": min_content_length}})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    # Run the semantic search
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"]
    )

    # Format results into a readable shape
    formatted = []
    if results and results['metadatas']:
        for i, meta in enumerate(results['metadatas'][0]):
            formatted.append({
                "date":     meta.get('date', ''),
                "title":    meta.get('title', ''),
                "bias":     meta.get('bias', ''),
                "setup":    meta.get('setup', ''),
                "soportes": json.loads(meta.get('soportes', '[]')),
                "resistencias": json.loads(meta.get('resistencias', '[]')),
                "distance": results['distances'][0][i],
                "content":  results['documents'][0][i][:500],  # preview
            })

    return formatted


# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────

def get_stats(collection) -> dict:
    """Returns basic statistics for the collection."""
    total = collection.count()

    # Count by bias (small sample to avoid loading everything)
    sample = collection.get(limit=total, include=["metadatas"])
    biases = {}
    for meta in sample['metadatas']:
        b = meta.get('bias', 'unknown')
        biases[b] = biases.get(b, 0) + 1

    return {
        "total_documentos": total,
        "distribucion_bias": biases,
    }
