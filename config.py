"""
config.py - Configuración central del bot Adam Mancini
=======================================================
Este archivo es el único sitio donde se definen rutas y parámetros.
El resto de módulos importan desde aquí, nunca hardcodean valores.

Para configurar el bot: copia .env.example a .env y rellena los valores.

CAMBIO PRINCIPAL (junio 2026):
  - DATA_SOURCE = 'ibkr' → usamos ES futures directo desde IBKR paper trading
  - SPY_TO_ES_MULTIPLIER = 1.0 → IBKR devuelve precios ES directos, sin conversión
  - Alpaca/SPY mantenido como fallback por si hay problemas con IBKR Gateway
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carga variables desde .env (en desarrollo)
# En producción (servidor) vienen directamente del entorno
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
CHROMA_DIR      = DATA_DIR / 'chromadb'    # Base vectorial (ya no se usa activamente)

# Crear directorios automáticamente si no existen
for _dir in [TWEETS_DIR, NEWSLETTER_DIR, PROCESSED_DIR, CHROMA_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Fuente de datos de mercado
# ─────────────────────────────────────────────
# 'ibkr'   → ES Futures directo desde IBKR paper trading (RECOMENDADO)
#              Ventajas: precio exacto, pre-mercado 7:30-9:30 AM, sin proxy
#              Requiere: IB Gateway corriendo en mac con API habilitada
#
# 'alpaca' → SPY desde Alpaca Markets (fallback)
#              Desventajas: proxy SPY*10, sin pre-mercado, ruido en conversión
DATA_SOURCE = 'ibkr'


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
SUBSTACK_COOKIES = os.getenv('SUBSTACK_COOKIES', '')  # para contenido de pago


# ─────────────────────────────────────────────
# Anthropic API (Claude Haiku para parseo y señales)
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# Haiku es suficiente para extraer niveles/bias y evaluar setups
LLM_MODEL = 'claude-haiku-4-5-20251001'


# ─────────────────────────────────────────────
# IBKR - ES Futures (fuente principal)
# ─────────────────────────────────────────────
# IB Gateway paper trading — corre en tu Mac y expone la API en localhost
# Puerto paper de IB Gateway: 4002
# Puerto paper de TWS:        7497
# (IB Gateway es más ligero — usar ese, no TWS)
IBKR_HOST      = os.getenv('IBKR_HOST', '127.0.0.1')
IBKR_PORT      = int(os.getenv('IBKR_PORT', '4002'))
IBKR_CLIENT_ID = int(os.getenv('IBKR_CLIENT_ID', '1'))

# Mes de expiración del contrato ES activo (front month)
# Formato IBKR: YYYYMM
#   202609 = Septiembre 2026 (ESU2026) — front month actual tras el roll del 14 junio
#   202612 = Diciembre 2026 (ESZ2026)  — siguiente cuando expire sep en sept
# Adam confirmó en newsletter que hizo el roll a ESU2026 el domingo 14 junio 2026
IBKR_ES_EXPIRY = os.getenv('IBKR_ES_EXPIRY', '202609')


# ─────────────────────────────────────────────
# Alpaca - Datos de mercado SPY (fallback / legacy)
# ─────────────────────────────────────────────
# Mantenido por si IBKR Gateway tiene problemas y necesitas fallback rápido.
# Para usar: cambiar DATA_SOURCE = 'alpaca' arriba
ALPACA_API_KEY    = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_PAPER      = True

MARKET_TICKER = 'SPY'  # solo se usa si DATA_SOURCE = 'alpaca'

# MULTIPLICADOR SPY→ES:
#   'ibkr'   → 1.0 (ES futures ya viene en puntos ES directos, sin conversión)
#   'alpaca' → 10.0 (SPY * 10 ≈ nivel ES, p.ej. SPY 750 → ES 7500)
SPY_TO_ES_MULTIPLIER = 1.0 if DATA_SOURCE == 'ibkr' else 10.0

# Tolerancia para considerar que el precio está "en" un nivel de Adam
# Adam usa niveles con ±3 puntos de margen típicamente
LEVEL_TOLERANCE_POINTS = 3.0  # en puntos ES


# ─────────────────────────────────────────────
# Telegram Bot
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')


# ─────────────────────────────────────────────
# ChromaDB - Base vectorial (legacy)
# ─────────────────────────────────────────────
# Ya no se usa activamente para queries de señales
# (eliminado el ruido de niveles históricos de 2021-2024 que no son relevantes
#  para el rango actual de 7000-8000)
CHROMA_COLLECTION = 'adam_mancini_knowledge'


# ─────────────────────────────────────────────
# Horario de mercado (NYSE / ventana de Adam)
# ─────────────────────────────────────────────
MARKET_TIMEZONE   = 'America/New_York'

# Empezamos a las 7:30 AM para cubrir la ventana prime de Adam
# (ES futures tiene datos desde las 6 PM del día anterior, pero Adam
#  empieza a operar a las 7:30 AM)
MARKET_OPEN_HOUR  = 7
MARKET_OPEN_MIN   = 30

# FIX: era 17, corregido a 16 (4 PM EST = cierre NYSE)
# Con 17 se generaban warnings de 'barra obsoleta' en el after-hours
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0
