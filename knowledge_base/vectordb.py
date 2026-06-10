"""
knowledge_base/vectordb.py — Base vectorial con ChromaDB
=========================================================
Gestiona la colección ChromaDB donde se almacena todo el conocimiento
de Adam Mancini. Cada documento es un artículo del newsletter.

Dos operaciones principales:
  - add_article(): indexa un artículo con sus metadatos
  - query_similar(): busca situaciones similares a la actual
"""

import json
import sys
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

sys.path.append(str(Path(__file__).parent.parent))
from config import CHROMA_DIR, CHROMA_COLLECTION


# ─────────────────────────────────────────────
# Inicialización de la colección
# ─────────────────────────────────────────────

def get_collection():
    """
    Obtiene (o crea si no existe) la colección ChromaDB.

    La colección persiste en disco en data/chromadb/
    Usa all-MiniLM-L6-v2 como modelo de embeddings (se descarga ~80MB la primera vez)
    """
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # DefaultEmbeddingFunction usa ONNX (all-MiniLM-L6-v2 cuantizado).
    # Es la alternativa a sentence-transformers, que no soporta Python 3.13.
    ef = embedding_functions.DefaultEmbeddingFunction()


    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}  # similitud coseno para textos
    )

    return collection


# ─────────────────────────────────────────────
# Añadir artículos
# ─────────────────────────────────────────────

def add_article(collection, article: dict, trading_info: dict):
    """
    Indexa un artículo en ChromaDB.

    Qué se vectoriza (el 'documento'):
      Combinamos título, bias, setup y contenido para que la búsqueda
      semántica encuentre artículos similares por contexto de mercado.

    Qué va en metadatos:
      Campos estructurados para filtrar (bias, fecha, niveles).
      ChromaDB permite filtrar por metadatos antes de buscar por similitud.

    Args:
        collection: colección ChromaDB (de get_collection())
        article: dict del newsletter (slug, title, content, published_at...)
        trading_info: dict extraído por processor.py (bias, soportes...)
    """

    # ── Texto a vectorizar ────────────────────────────────────────────────
    # Combinamos la info estructurada con el contenido original.
    # Esto mejora la búsqueda semántica: podemos buscar "SPX rebotando en soporte bullish"
    # y encontrar artículos donde Adam describía esa situación.
    bias_str   = trading_info.get('bias', 'unknown') or 'unknown'
    setup_str  = trading_info.get('setup', '') or ''
    content    = article.get('content', '')[:1500]  # máx 1500 chars

    document_text = (
        f"Fecha: {article.get('published_at', '')}\n"
        f"Título: {article.get('title', '')}\n"
        f"Bias: {bias_str}\n"
        f"Setup: {setup_str}\n\n"
        f"{content}"
    )

    # ── Metadatos para filtrado ───────────────────────────────────────────
    # ChromaDB solo acepta strings, ints, floats y bools en metadatos.
    # Las listas las serializamos como string JSON.
    soportes      = trading_info.get('soportes', []) or []
    resistencias  = trading_info.get('resistencias', []) or []
    nivel_critico = trading_info.get('nivel_critico')

    metadata = {
        "date":           article.get('published_at', ''),
        "title":          article.get('title', '')[:100],
        "bias":           bias_str,
        "nivel_critico":  float(nivel_critico) if nivel_critico else 0.0,
        "soportes":       json.dumps(soportes),       # lista como string
        "resistencias":   json.dumps(resistencias),   # lista como string
        "setup":          (setup_str or '')[:200],
        "is_complete":    bool(article.get('is_complete', False)),
        "content_length": len(article.get('content', '')),
    }

    # ── Añadir a la colección ─────────────────────────────────────────────
    # El ID es el slug del artículo (único por artículo)
    collection.upsert(  # upsert = add si no existe, update si existe
        documents=[document_text],
        metadatas=[metadata],
        ids=[article.get('slug', article.get('id', str(hash(article.get('title', '')))))]
    )


# ─────────────────────────────────────────────
# Consultar la base de conocimiento
# ─────────────────────────────────────────────

def query_similar(
    collection,
    query: str,
    n_results: int = 5,
    bias_filter: str = None,
    min_content_length: int = 200
) -> list:
    """
    Busca en el historial de Adam situaciones similares a la actual.

    Ejemplo de uso:
        results = query_similar(
            collection,
            "ES en 5400 rechazando resistencia con momentum bajista",
            bias_filter="bearish"
        )

    Args:
        query: descripción de la situación actual del mercado
        n_results: cuántos artículos similares devolver
        bias_filter: 'bullish', 'bearish', 'neutral', 'mixed' o None
        min_content_length: filtrar artículos muy cortos

    Returns:
        Lista de dicts con: date, title, bias, setup, content, distance
    """
    # Construir filtro de metadatos
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

    # Ejecutar búsqueda semántica
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"]
    )

    # Formatear resultados de forma legible
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
# Utilidades
# ─────────────────────────────────────────────

def get_stats(collection) -> dict:
    """Devuelve estadísticas básicas de la colección."""
    total = collection.count()

    # Contar por bias (pequeña muestra para no cargar todo)
    sample = collection.get(limit=total, include=["metadatas"])
    biases = {}
    for meta in sample['metadatas']:
        b = meta.get('bias', 'unknown')
        biases[b] = biases.get(b, 0) + 1

    return {
        "total_documentos": total,
        "distribucion_bias": biases,
    }
