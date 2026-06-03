"""
config.py - Configuración central del bot Adam Mancini
=======================================================
Este archivo es el único sitio donde se definen rutas y parámetros.
El resto de módulos importan desde aquí, nunca hardcodean valores.

Para configurar el bot: copia .env.example a .env y rellena los valores.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carga variables desde .env (en desarrollo)
# En producción (Railway/servidor) vienen directamente del entorno
load_dotenv()


# ─────────────────────────────────────────────
# Rutas del proyecto
# ─────────────────────────────────────────────
# Path(__file__).parent apunta al directorio donde está este archivo (la raíz)
BASE_DIR        = Path(__file__).parent
DATA_DIR        = BASE_DIR / 'data'
RAW_DIR         = DATA_DIR / 'raw'
TWEETS_DIR      = RAW_DIR  / 'tweets'      # JSON con tweets de Adam
NEWSLETTER_DIR  = RAW_DIR  / 'newsletter'  # JSON con artículos del newsletter
PROCESSED_DIR   = DATA_DIR / 'processed'   # Datos ya procesados y limpios
CHROMA_DIR      = DATA_DIR / 'chromadb'    # Base vectorial persistente

# Crear directorios automáticamente si no existen
for _dir in [TWEETS_DIR, NEWSLETTER_DIR, PROCESSED_DIR, CHROMA_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Twitter/X
# ─────────────────────────────────────────────
TWITTER_USERNAME = os.getenv('TWITTER_USERNAME')    # tu @usuario sin @
TWITTER_EMAIL    = os.getenv('TWITTER_EMAIL')       # email de tu cuenta
TWITTER_PASSWORD = os.getenv('TWITTER_PASSWORD')    # contraseña

TWITTER_TARGET        = 'AdamMancini4'  # perfil de Adam que monitorizamos
TWITTER_POLL_INTERVAL = 180             # segundos entre checks en tiempo real (3 min)


# ─────────────────────────────────────────────
# Substack - Newsletter de Adam
# ─────────────────────────────────────────────
SUBSTACK_URL     = 'https://tradecompanion.substack.com'
SUBSTACK_COOKIES = os.getenv('SUBSTACK_COOKIES', '')  # para contenido de pago (futuro)


# ─────────────────────────────────────────────
# Anthropic API (Claude Haiku para parseo)
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# Haiku es suficiente para extraer niveles y bias de texto
# Solo usamos modelos más caros si la precisión falla
LLM_MODEL = 'claude-haiku-4-5-20251001'


# ─────────────────────────────────────────────
# Alpaca - Datos de mercado SPY (gratis)
# ─────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_PAPER      = True  # True = paper trading (gratis con datos reales)

MARKET_TICKER           = 'SPY'   # proxy del ES futures que sigue Adam
SPY_TO_ES_MULTIPLIER    = 10.0    # SPY * 10 ≈ nivel ES/SPX (p.ej. SPY 540 ≈ ES 5400)
LEVEL_TOLERANCE_POINTS  = 3.0     # margen para decir "precio está en el nivel" (en puntos ES)


# ─────────────────────────────────────────────
# Telegram Bot
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')


# ─────────────────────────────────────────────
# ChromaDB - Base vectorial
# ─────────────────────────────────────────────
CHROMA_COLLECTION = 'adam_mancini_knowledge'  # nombre de la colección


# ─────────────────────────────────────────────
# Horario de mercado (NYSE, zona horaria NY)
# ─────────────────────────────────────────────
MARKET_TIMEZONE   = 'America/New_York'
MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0
