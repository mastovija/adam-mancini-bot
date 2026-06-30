"""
knowledge_base/build_kb.py — Builds Adam Mancini's knowledge base
================================================================================
Main Phase 2 script. Reads every newsletter article, extracts the
trading information with Claude Haiku, and indexes them in ChromaDB.

USAGE:
    python knowledge_base/build_kb.py

ESTIMATED TIME:
    ~30-45 minutes for 1415 articles (1-2 seconds per API call)
    Estimated cost: ~$0.10-0.15 with Claude Haiku

RESUMABLE:
    If interrupted, run again and it continues from where it left off.
    Already-processed articles are saved in data/processed/processed_slugs.json
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
# Progress management
# ─────────────────────────────────────────────
# We save which articles we already processed so we can resume
PROGRESS_FILE = PROCESSED_DIR / 'processed_slugs.json'


def load_progress() -> set:
    """Loads the slugs of already-processed articles."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return set(json.load(f))
    return set()


def save_progress(processed: set):
    """Saves the processed slugs to disk."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(list(processed), f)


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

def build_knowledge_base():
    """
    Full knowledge-base build process:

    1. Loads every newsletter article from data/raw/newsletter/
    2. For each article with enough content:
       a. Calls Claude Haiku to extract bias, levels, setup
       b. Saves the result to data/processed/
       c. Indexes it in ChromaDB with semantic embeddings
    3. Saves progress every 10 articles (so it can resume)
    """
    print("=" * 60)
    print("  Adam Mancini Bot — Phase 2: Knowledge Base")
    print("=" * 60)

    # ── Initialize ChromaDB ───────────────────────────────────────────────
    print("\n📚 Initializing ChromaDB...")
    print("   (First run downloads the embeddings model ~80MB)")
    collection = get_collection()
    print(f"   ✅ Collection ready. Current documents: {collection.count()}")

    # ── Load available articles ───────────────────────────────────────────
    article_files = sorted([
        f for f in NEWSLETTER_DIR.glob('*.json')
        if f.stem != 'index'
    ])
    print(f"\n📰 Articles in newsletter/: {len(article_files)}")

    # ── Filter out already-processed ──────────────────────────────────────
    processed = load_progress()
    to_process = [f for f in article_files if f.stem not in processed]

    print(f"✅ Already processed: {len(processed)}")
    print(f"🆕 To process:  {len(to_process)}")

    if not to_process:
        print("\n🎉 Everything already indexed. The knowledge base is complete.")
        _print_stats(collection)
        return

    # ── Estimate cost and time ────────────────────────────────────────────
    est_minutes = len(to_process) * 1.5 / 60
    est_cost    = len(to_process) * 0.00008  # ~$0.00008 per article with Haiku
    print(f"\n⏱️  Estimated time: ~{est_minutes:.0f} minutes")
    print(f"💰 Estimated cost:  ~${est_cost:.2f}")
    print()

    # ── Processing ────────────────────────────────────────────────────────
    print("🤖 Processing articles with Claude Haiku...")
    print("-" * 60)

    ok     = 0
    skipped = 0
    errors = 0

    for i, article_file in enumerate(to_process, 1):

        # Load article
        with open(article_file, 'r', encoding='utf-8') as f:
            article = json.load(f)

        title_short = article.get('title', '')[:50]
        date_str    = article.get('published_at', '')

        # ── Skip articles without enough content ──────────────────────────
        content_len = len(article.get('content', ''))
        if content_len < 150:
            processed.add(article_file.stem)
            skipped += 1
            continue

        print(f"  [{i:4d}/{len(to_process)}] {date_str} — {title_short}...")

        try:
            # ── Extract info with the LLM ─────────────────────────────────
            trading_info = extract_trading_info(article)

            # ── Save the processed result to disk ─────────────────────────
            # Useful for debugging and to avoid re-processing if ChromaDB is wiped
            output_file = PROCESSED_DIR / f"{article_file.stem}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(
                    {**article, 'trading_info': trading_info},
                    f, indent=2, ensure_ascii=False
                )

            # ── Index in ChromaDB ─────────────────────────────────────────
            add_article(collection, article, trading_info)

            processed.add(article_file.stem)
            ok += 1

            # Show what was extracted to confirm it works
            bias = trading_info.get('bias', '?')
            sop  = trading_info.get('soportes', [])
            res  = trading_info.get('resistencias', [])
            print(f"       → {bias} | supports: {sop} | resistances: {res}")

        except Exception as e:
            errors += 1
            print(f"       ❌ Error: {e}")
            # We don't add it to processed so it gets retried next time

        # ── Save progress every 10 articles ───────────────────────────────
        if i % 10 == 0:
            save_progress(processed)
            print(f"\n  💾 Progress saved ({len(processed)} processed)\n")

        # Small pause to avoid hammering the API
        time.sleep(0.5)

    # Save final progress
    save_progress(processed)

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"✅ Processing complete")
    print(f"   Indexed:  {ok}")
    print(f"   Skipped:  {skipped} (insufficient content)")
    print(f"   Errors:   {errors}")
    _print_stats(collection)
    print("=" * 60)
    print()
    print("💡 Next step: Phase 3 — Daily newsletter parser")


def _print_stats(collection):
    """Prints statistics for the collection."""
    try:
        stats = get_stats(collection)
        print(f"\n📊 Knowledge base status:")
        print(f"   Total documents: {stats['total_documentos']}")
        print(f"   Bias distribution: {stats['distribucion_bias']}")
    except Exception:
        print(f"\n📊 Total documents in ChromaDB: {collection.count()}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == '__main__':
    build_knowledge_base()
