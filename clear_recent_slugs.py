import json, sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
from config import NEWSLETTER_DIR, PROCESSED_DIR

PROGRESS_FILE  = PROCESSED_DIR / 'processed_slugs.json'
REPROCESS_FROM = '2025-01-01'

with open(PROGRESS_FILE) as f:
    processed = set(json.load(f))

print(f"Slugs procesados: {len(processed)}")

recientes = set()
for f in NEWSLETTER_DIR.glob('*.json'):
    if f.stem == 'index': continue
    try:
        data = json.load(open(f, encoding='utf-8'))
        if data.get('published_at', '') >= REPROCESS_FROM and f.stem in processed:
            recientes.add(f.stem)
    except: continue

actualizados = processed - recientes
json.dump(list(actualizados), open(PROGRESS_FILE, 'w'))
print(f"A re-procesar (2025+): {len(recientes)}")
print(f"Se mantienen (pre-2025): {len(actualizados)}")
print("\nEjecuta ahora: python knowledge_base/build_kb.py")