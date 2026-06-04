"""
knowledge_base/build_kb.py — Construye la base de conocimiento de Adam Mancini
================================================================================
Script principal de la Fase 2. Lee todos los artículos del newsletter,
extrae la información de trading con Claude Haiku, y los indexa en ChromaDB.

USO:
    python knowledge_base/build_kb.py

TIEMPO ESTIMADO:
    ~30-45 minutos para 1415 artículos (1-2 segundos por llamada a la API)
    Coste estimado: ~$0.10-0.15 con Claude Haiku

REANUDABLE:
    Si se interrumpe, vuelve a ejecutar y continúa desde donde lo dejó.
    Los artículos ya procesados se guardan en data/processed/processed_slugs.json
"""

import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from config import NEWSLETTER_DIR, PROCESSED_DIR
from knowledge_base.processor import extract_trading_info
from knowledge_base.vectordb import get_collection, add_article, get_stats


# ─────────────────────────────────────────────
# Gestión del progreso
# ─────────────────────────────────────────────
# Guardamos qué artículos ya procesamos para poder reanudar
PROGRESS_FILE = PROCESSED_DIR / 'processed_slugs.json'


def load_progress() -> set:
    """Carga los slugs de artículos ya procesados."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return set(json.load(f))
    return set()


def save_progress(processed: set):
    """Guarda los slugs procesados en disco."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(list(processed), f)


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

def build_knowledge_base():
    """
    Proceso completo de construcción de la base de conocimiento:

    1. Carga todos los artículos del newsletter de data/raw/newsletter/
    2. Para cada artículo con contenido suficiente:
       a. Llama a Claude Haiku para extraer bias, niveles, setup
       b. Guarda el resultado en data/processed/
       c. Indexa en ChromaDB con embeddings semánticos
    3. Guarda progreso cada 10 artículos (para poder reanudar)
    """
    print("=" * 60)
    print("  Bot Adam Mancini — Fase 2: Base de Conocimiento")
    print("=" * 60)

    # ── Inicializar ChromaDB ──────────────────────────────────────────────
    print("\n📚 Inicializando ChromaDB...")
    print("   (La primera vez descarga el modelo de embeddings ~80MB)")
    collection = get_collection()
    print(f"   ✅ Colección lista. Documentos actuales: {collection.count()}")

    # ── Cargar artículos disponibles ──────────────────────────────────────
    article_files = sorted([
        f for f in NEWSLETTER_DIR.glob('*.json')
        if f.stem != 'index'
    ])
    print(f"\n📰 Artículos en newsletter/: {len(article_files)}")

    # ── Filtrar ya procesados ─────────────────────────────────────────────
    processed = load_progress()
    to_process = [f for f in article_files if f.stem not in processed]

    print(f"✅ Ya procesados: {len(processed)}")
    print(f"🆕 Por procesar:  {len(to_process)}")

    if not to_process:
        print("\n🎉 Todo ya está indexado. La base de conocimiento está completa.")
        _print_stats(collection)
        return

    # ── Estimar coste y tiempo ────────────────────────────────────────────
    est_minutes = len(to_process) * 1.5 / 60
    est_cost    = len(to_process) * 0.00008  # ~$0.00008 por artículo con Haiku
    print(f"\n⏱️  Tiempo estimado: ~{est_minutes:.0f} minutos")
    print(f"💰 Coste estimado:  ~${est_cost:.2f}")
    print()

    # ── Procesamiento ─────────────────────────────────────────────────────
    print("🤖 Procesando artículos con Claude Haiku...")
    print("-" * 60)

    ok     = 0
    skipped = 0
    errors = 0

    for i, article_file in enumerate(to_process, 1):

        # Cargar artículo
        with open(article_file, 'r', encoding='utf-8') as f:
            article = json.load(f)

        title_short = article.get('title', '')[:50]
        date_str    = article.get('published_at', '')

        # ── Saltar artículos sin contenido suficiente ─────────────────────
        content_len = len(article.get('content', ''))
        if content_len < 150:
            processed.add(article_file.stem)
            skipped += 1
            continue

        print(f"  [{i:4d}/{len(to_process)}] {date_str} — {title_short}...")

        try:
            # ── Extraer info con LLM ──────────────────────────────────────
            trading_info = extract_trading_info(article)

            # ── Guardar resultado procesado en disco ──────────────────────
            # Útil para debug y para no re-procesar si ChromaDB se borra
            output_file = PROCESSED_DIR / f"{article_file.stem}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(
                    {**article, 'trading_info': trading_info},
                    f, indent=2, ensure_ascii=False
                )

            # ── Indexar en ChromaDB ───────────────────────────────────────
            add_article(collection, article, trading_info)

            processed.add(article_file.stem)
            ok += 1

            # Mostrar lo extraído para ver que funciona
            bias = trading_info.get('bias', '?')
            sop  = trading_info.get('soportes', [])
            res  = trading_info.get('resistencias', [])
            print(f"       → {bias} | soportes: {sop} | resistencias: {res}")

        except Exception as e:
            errors += 1
            print(f"       ❌ Error: {e}")
            # No añadimos al processed para que se reintente la próxima vez

        # ── Guardar progreso cada 10 artículos ────────────────────────────
        if i % 10 == 0:
            save_progress(processed)
            print(f"\n  💾 Progreso guardado ({len(processed)} procesados)\n")

        # Pequeña pausa para no saturar la API
        time.sleep(0.5)

    # Guardar progreso final
    save_progress(processed)

    # ── Resumen ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"✅ Procesamiento completado")
    print(f"   Indexados:  {ok}")
    print(f"   Saltados:   {skipped} (contenido insuficiente)")
    print(f"   Errores:    {errors}")
    _print_stats(collection)
    print("=" * 60)
    print()
    print("💡 Próximo paso: Fase 3 — Parser diario del newsletter")


def _print_stats(collection):
    """Imprime estadísticas de la colección."""
    try:
        stats = get_stats(collection)
        print(f"\n📊 Estado de la base de conocimiento:")
        print(f"   Total documentos: {stats['total_documentos']}")
        print(f"   Distribución bias: {stats['distribucion_bias']}")
    except Exception:
        print(f"\n📊 Total documentos en ChromaDB: {collection.count()}")


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────

if __name__ == '__main__':
    build_knowledge_base()
