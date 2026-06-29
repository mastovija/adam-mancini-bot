"""
signals/signal_engine.py — Motor de señales: el corazón del bot
================================================================
Une todas las piezas del proyecto para detectar señales de Adam Mancini.

MEJORAS AÑADIDAS (junio 2026):
  1. Estado de trade activo: bloquea nuevas señales hasta que el trade se cierre.
  2. Alertas de gestión: T1, T2, stop hit.
  3. Ventanas de trading corregidas: chop eleva el listón (no bloquea), segunda
     ventana 3pm-4pm en lugar de 2pm-4pm.
  4. detect_failed_breakdown corregido: ahora detecta FB intra-barra (cuando el
     flush y la recovery ocurren dentro de la misma vela de 15 minutos, caso
     frecuente en mercados de alta volatilidad como hoy 25 junio con elevator
     down de 96 pts en 14 minutos).
"""

import asyncio
import json
# import re  ← eliminado C-14: los regex del JSON se reemplazaron por str.find/rfind
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import anthropic
import pytz

from bot.telegram_alerts import TelegramAlerter

from config import (
    DATA_DIR,
    DATA_SOURCE,
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    SPY_TO_ES_MULTIPLIER,
    LEVEL_TOLERANCE_POINTS,
    MARKET_TIMEZONE,
)

if DATA_SOURCE == 'ibkr':
    from market_data.ibkr_feed import ESFeed as MarketFeed, is_market_open
    print(f"📡 Feed: IBKR ES Futures (multiplicador={SPY_TO_ES_MULTIPLIER})")
else:
    from market_data.alpaca_feed import SPYFeed as MarketFeed, is_market_open
    print(f"📡 Feed: Alpaca SPY (multiplicador={SPY_TO_ES_MULTIPLIER})")


# ─────────────────────────────────────────────
# Rutas
# ─────────────────────────────────────────────
TODAY_FILE  = DATA_DIR / 'daily' / 'today.json'
STATE_FILE  = DATA_DIR / 'signal_engine_state.json'
TWEETS_FILE = DATA_DIR / 'tweet_monitor_state.json'


# ─────────────────────────────────────────────
# Cargar contexto del día
# ─────────────────────────────────────────────

def load_today() -> dict | None:
    """Carga el mapa del día generado por el newsletter parser."""
    if not TODAY_FILE.exists():
        print("  ⚠️  today.json no existe — ejecuta primero newsletter_parser.py")
        return None

    with open(TODAY_FILE) as f:
        data = json.load(f)

    if data.get('date'):
        dias = (datetime.now().date() -
                datetime.strptime(data['date'], '%Y-%m-%d').date()).days
        if dias > 2:
            print(f"  ⚠️  today.json tiene {dias} días de antigüedad")

    if len(data.get('content_plan', '')) < 500:
        print("  ⚠️  content_plan muy corto — ejecuta newsletter_parser.py --force")

    return data


def get_all_levels(today: dict) -> list:
    """
    Extrae todos los niveles del día (soportes + resistencias + pivote)
    y los deduplica por valor numérico.

    Las resistencias se incluyen por el setup Level Reclaim de Adam.

    FIX B-5 — deduplicación entre listas:
    El extractor LLM (processor.py) a veces clasifica el mismo precio en
    soportes Y resistencias (p. ej. 7425 en el today.json del 24 jun).
    Sin deduplicar, check_once consulta al LLM DOS veces para ese nivel
    (una como soporte, otra como resistencia) y las listas del prompt
    también mezclan tipos para el mismo precio, confundiendo al modelo.

    CRITERIO: si un mismo nivel (redondeado a entero) aparece varias veces,
    nos quedamos con la PRIMERA ocurrencia en orden: soportes primero,
    luego resistencias, luego pivote. El tipo que 'gana' es el que el LLM
    extrajo en primer lugar, que suele ser el más representativo.
    """
    niveles = []
    for n in today.get('soportes', []):
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'soporte'})
    for n in today.get('resistencias', []):
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'resistencia'})
    if today.get('nivel_critico'):
        nc = float(today['nivel_critico'])
        if not any(abs(x['nivel'] - nc) < 0.5 for x in niveles):
            niveles.append({'nivel': nc, 'tipo': 'pivote'})

    # Deduplicar por valor redondeado al entero más cercano.
    # round(7425.0) == round(7425.3) == 7425 → se queda la primera entrada.
    # Esto evita que el motor consulte al LLM dos veces para el mismo nivel
    # y que el prompt incluya tipos contradictorios para el mismo precio.
    vistos: dict[int, dict] = {}
    for entry in niveles:
        clave = round(entry['nivel'])
        if clave not in vistos:
            vistos[clave] = entry

    deduplicados = sorted(vistos.values(), key=lambda x: x['nivel'], reverse=True)

    n_total      = len(niveles)
    n_dedup      = len(deduplicados)
    if n_total != n_dedup:
        print(f"  🔧 B-5: {n_total - n_dedup} nivel(es) duplicado(s) eliminado(s) "
              f"({n_total} → {n_dedup})")

    return deduplicados


# ─────────────────────────────────────────────
# Ventana de trading de Adam
# ─────────────────────────────────────────────

def get_trading_window() -> tuple:
    """
    Determina en qué ventana de trading estamos y el criterio aplicable.

    Devuelve (hora_str, ventana_descripcion, criterio_extra_para_LLM).

    VENTANAS (según newsletter de Adam del 24 jun):
    - 7:30-11:00 AM: principal, criterio normal
    - 11:00 AM-3:00 PM: chop — listón más alto (NO bloqueo automático)
      Solo si: elevator down claro (25+ pts) + nivel mínimo significativo mayor
    - 3:00-4:00 PM: segunda ventana real ("after 3pm")
    """
    tz_est   = pytz.timezone(MARKET_TIMEZONE)
    ahora    = datetime.now(tz_est)
    hora_str = ahora.strftime('%H:%M EST')
    hora_dec = ahora.hour + ahora.minute / 60.0

    if 7.5 <= hora_dec < 11.0:
        ventana  = "🟢 VENTANA PRINCIPAL (7:30-11:00 AM) — mayoría de entradas de Adam"
        criterio = (
            "Estamos en la VENTANA PRINCIPAL (7:30-11:00 AM). "
            "Criterio normal: evalúa el Failed Breakdown/Level Reclaim según el plan."
        )

    elif 11.0 <= hora_dec < 15.0:
        ventana  = "🟡 VENTANA CHOP (11:00 AM-3:00 PM) — listón más alto, no bloqueo"
        criterio = (
            "Estamos en la VENTANA CHOP (11:00 AM-3:00 PM). "
            "Adam 'raramente' opera aquí — pero NO es 'nunca'. "
            # B-7: eliminado ejemplo con fecha y precios hardcodeados ('25 jun', 7409, 7415).
            # Un ejemplo específico en el prompt ancla al LLM hacia esos precios concretos
            # y degrada la calidad de las decisiones confórme pasan los días y el rango
            # del mercado se aleja de esos valores. La regla abstracta es suficiente.
            "Cuando sí opera en esta ventana, siempre es con elevator down pronunciado "
            "que flushea un mínimo significativo mayor, seguido de FB claro. "
            "Nunca entra en chop en niveles menores, resistencias o zonas ya muy testeadas."
            "\n\nCRITERIO ELEVADO — TODOS estos requisitos deben cumplirse:\n"
            "  1. Elevator down CLARO y reciente (mínimo 25-30 pts en caída rápida)\n"
            "  2. El nivel es un MÍNIMO SIGNIFICATIVO MAYOR:\n"
            "     - Daily low del día anterior, O\n"
            "     - Low multi-hora (20+ pts de caída que formó ese low), O\n"
            "     - Shelf de lows de varias horas\n"
            "     NO vale: niveles menores, resistencias, niveles 'tested to death'\n"
            "  3. Recovery clara del nivel\n"
            "\nSi alguno de estos tres no se cumple claramente → entrar: false."
        )

    elif 15.0 <= hora_dec < 16.0:
        ventana  = "🟠 SEGUNDA VENTANA (3:00-4:00 PM) — solo si primer trade fue ganador"
        criterio = (
            "Estamos en la SEGUNDA VENTANA (3:00-4:00 PM, 'after 3pm' según Adam). "
            "Solo si el primer trade del día fue ganador. "
            "Solo con A+ setup: elevator down limpio + FB de nivel mayor. "
            "NO operar si Adam ya cerró plataforma o está en modo runner."
        )

    else:
        ventana  = "⚪ FUERA DE VENTANA DE TRADING"
        criterio = "FUERA del horario de trading de Adam → entrar: false."

    return hora_str, ventana, criterio


# ─────────────────────────────────────────────
# Tweets del día
# ─────────────────────────────────────────────

def get_todays_tweets() -> list:
    """
    Lee tweets de Adam del día de DOS fuentes (monitor + scraper).

    FIX B-8 — zona horaria:
    Antes usaba datetime.now() local (España, UTC+2 en verano). Cualquier
    tweet de Adam después de las 18:00 EST (medianoche en España) se
    comparaba contra la fecha del día SIGUIENTE en España y quedaba excluido,
    aunque Adam siga en plena sesión de trading (hasta las 16:00 EST).

    Ahora el 'día de trading' se ancla a New York time (MARKET_TIMEZONE),
    igual que el resto de la lógica del bot. Y los timestamps UTC de los
    tweets se convierten a NY antes de comparar la fecha.
    """
    tz_ny = pytz.timezone(MARKET_TIMEZONE)   # America/New_York
    # 'hoy' en tiempo de Nueva York — alineado con la sesión de trading de Adam
    hoy   = datetime.now(tz_ny).strftime('%Y-%m-%d')
    tweets     = []
    ids_vistos = set()

    if TWEETS_FILE.exists():
        try:
            state = json.load(open(TWEETS_FILE, encoding='utf-8'))
            # Nota: tweet_monitor.py guarda fecha_hoy en hora local (España).
            # Durante el horario de mercado (07:30-16:00 EST = 13:30-22:00 España)
            # ambas fechas coinciden, así que esta comparación es correcta.
            # El desajuste solo ocurriría entre las 22:00 y medianoche España
            # (16:00-18:00 EST, after-hours), donde los tweets son historial y
            # el filtro de palabras pasado los rechazaría de todas formas.
            if state.get('fecha_hoy') == hoy:
                for item in state.get('tweets_hoy', []):
                    tweet = item.get('tweet', item) if isinstance(item, dict) else item
                    tid = tweet.get('id')
                    if tweet.get('text') and not tweet.get('is_retweet') and tid:
                        tweets.append(tweet)
                        ids_vistos.add(tid)
        except Exception:
            pass

    raw_file = DATA_DIR / 'raw' / 'tweets' / 'adam_mancini_tweets.json'
    if raw_file.exists():
        try:
            all_tweets = json.load(open(raw_file, encoding='utf-8'))
            for tweet in all_tweets:
                created = tweet.get('created_at', '')
                tid     = tweet.get('id')
                if not created or tweet.get('is_retweet') or tid in ids_vistos:
                    continue
                try:
                    # Parsear el timestamp UTC de Twitter y convertir a NY time
                    # antes de comparar la fecha. Así '2026-06-26 22:30 UTC'
                    # (= 18:30 EST, after-close del 26) no se clasifica como
                    # tweet del 27 solo porque en España ya son las 00:30 del 27.
                    dt_utc = datetime.strptime(created, '%a %b %d %H:%M:%S +0000 %Y')
                    dt_ny  = pytz.utc.localize(dt_utc).astimezone(tz_ny)
                    if dt_ny.strftime('%Y-%m-%d') == hoy:
                        tweets.append(tweet)
                        ids_vistos.add(tid)
                except Exception:
                    pass
        except Exception:
            pass

    def _ts(t):
        try:
            # Ordenar por timestamp UTC (misma base para todos los tweets)
            dt_utc = datetime.strptime(t.get('created_at', ''), '%a %b %d %H:%M:%S +0000 %Y')
            return pytz.utc.localize(dt_utc)
        except Exception:
            return datetime.min.replace(tzinfo=pytz.utc)

    return sorted(tweets, key=_ts)


def formatear_tweets_para_prompt(tweets: list) -> str:
    """Formatea los tweets de Adam del día para el prompt del LLM."""
    if not tweets:
        return "No hay tweets de Adam todavía hoy."
    lines = []
    for tweet in tweets[-12:]:
        texto = tweet.get('text', '').strip()
        if not texto:
            continue
        hora = ''
        created = tweet.get('created_at', '')
        if created:
            try:
                dt   = datetime.strptime(created, '%a %b %d %H:%M:%S +0000 %Y')
                hora = f" [{dt.strftime('%H:%M')} UTC]"
            except Exception:
                pass
        lines.append(f"• {texto}{hora}")
    return '\n'.join(lines) if lines else "No hay tweets de Adam todavía hoy."


# ─────────────────────────────────────────────
# Detección de nivel
# ─────────────────────────────────────────────

def is_price_at_level(precio_es: float, nivel: float, tolerancia: float = None) -> bool:
    """¿Está el precio ES dentro de la tolerancia del nivel?"""
    tol = tolerancia or LEVEL_TOLERANCE_POINTS
    return abs(precio_es - nivel) <= tol


def determinar_lado(precio_es: float, nivel_info: dict, bias: str) -> str | None:
    """Adam no va short → solo 'long' o None."""
    tipo = nivel_info['tipo']
    if tipo in ('soporte', 'resistencia', 'pivote'):
        if tipo == 'pivote' and precio_es < nivel_info['nivel']:
            return None
        return 'long' if bias != 'bearish' else None
    return None


def confirmar_con_vela_15min(bars_15: list, nivel: float, direccion: str) -> bool:
    """Confirmación técnica con la última vela de 15 minutos."""
    if not bars_15:
        return False
    v     = bars_15[-1]
    m     = SPY_TO_ES_MULTIPLIER
    close = v['close'] * m
    open_ = v['open']  * m
    if direccion == 'long':
        return close > open_ and close >= nivel
    return False


def detect_failed_breakdown(bars_15: list, nivel: float) -> dict:
    """
    Detecta la secuencia de Failed Breakdown en el nivel.

    SECUENCIA CRONOLÓGICA REQUERIDA:
      1. Precio estuvo ENCIMA del nivel (+5 pts)
      2. Flush POR DEBAJO del nivel (-5 pts) — elevator down
      3. Precio actual recuperado ENCIMA — recovery

    CORRECCIÓN (junio 2026) — FB INTRA-BARRA:
      En mercados muy rápidos (como el elevator down de 96 pts del 25 jun),
      el flush y la recovery pueden ocurrir dentro de la MISMA vela de 15min.
      La barra más reciente tiene:
        - close > nivel (recovery)
        - low < nivel - 5 (flush intra-barra)

      El algoritmo anterior fallaba en este caso porque en i=1 activaba
      was_above=True y luego buscaba el flush en i=2, i=3... sin mirar que
      el flush YA estaba en i=1 (en su low).

      FIX: verificamos primero si la barra más reciente contiene un FB intra-barra
      antes de ejecutar el bucle estándar.
    """
    if not bars_15 or len(bars_15) < 2:
        return {'es_fb': False, 'flush_size': 0, 'bars_ago': 0,
                'descripcion': 'Sin datos de velas suficientes'}

    m                = SPY_TO_ES_MULTIPLIER
    precio_actual_es = bars_15[-1]['close'] * m

    # Si el precio sigue por debajo del nivel, la recovery no ha ocurrido aún
    if precio_actual_es <= nivel:
        return {'es_fb': False, 'flush_size': 0, 'bars_ago': 0,
                'descripcion': (f'Precio {precio_actual_es:.0f} por debajo de '
                                f'{nivel:.0f} — recovery aún no completada')}

    # ── CHECK 1: FB intra-barra ───────────────────────────────────────────
    barra_actual    = bars_15[-1]
    close_actual_es = barra_actual['close'] * m
    low_actual_es   = barra_actual['low']   * m

    if close_actual_es > nivel and low_actual_es < nivel - 5:
        precio_estaba_encima = any(
            b['close'] * m > nivel + 5
            for b in bars_15[-5:-1]
        )
        if precio_estaba_encima:
            flush_size = round(nivel - low_actual_es, 1)
            if flush_size >= 20:
                calidad = f'profundo ({flush_size:.0f} pts) — alta probabilidad institucional'
            elif flush_size >= 10:
                calidad = f'moderado ({flush_size:.0f} pts)'
            else:
                calidad = f'shallow ({flush_size:.0f} pts)'
            return {
                'es_fb':       True,
                'flush_size':  flush_size,
                'bars_ago':    1,
                'descripcion': (
                    f'✅ FAILED BREAKDOWN intra-barra: {calidad}, '
                    f'flush hasta {low_actual_es:.0f} y recovery en la misma vela, '
                    f'precio {precio_actual_es:.0f} encima de {nivel:.0f}'
                )
            }

    # ── CHECK 2: FB estándar (flush en barra anterior) ────────────────────
    was_above = False
    for i in range(1, min(9, len(bars_15))):
        bar      = bars_15[-i]
        close_es = bar['close'] * m
        low_es   = bar['low']   * m

        if not was_above:
            if close_es > nivel + 5:
                was_above = True
            continue

        if low_es < nivel - 5:
            flush_size = round(nivel - low_es, 1)
            if flush_size >= 20:
                calidad = f'profundo ({flush_size:.0f} pts) — alta probabilidad institucional'
            elif flush_size >= 10:
                calidad = f'moderado ({flush_size:.0f} pts)'
            else:
                calidad = f'shallow ({flush_size:.0f} pts)'
            return {
                'es_fb':       True,
                'flush_size':  flush_size,
                'bars_ago':    i,
                'descripcion': (
                    f'✅ FAILED BREAKDOWN: {calidad}, hace {i} velas ({i*15} min), '
                    f'precio {precio_actual_es:.0f} recuperado encima de {nivel:.0f}'
                )
            }

    return {'es_fb': False, 'flush_size': 0, 'bars_ago': 0,
            'descripcion': (
                f'⚠️ Sin FB: precio {precio_actual_es:.0f} encima de {nivel:.0f} '
                f'pero sin secuencia elevator down → flush → recovery'
            )}


# ─────────────────────────────────────────────
# Generación de señal con LLM
# ─────────────────────────────────────────────

SIGNAL_PROMPT = """Eres Adam Mancini analizando si debes entrar en un trade ahora mismo en ES futures.

══════════════════════════════════════════════════════════
METODOLOGÍA (base fija, siempre aplica):
══════════════════════════════════════════════════════════
Tu ÚNICO setup de entrada es el Failed Breakdown:
  1. Elevator down: precio cae vertical hacia un mínimo significativo
  2. Flush DEBAJO del mínimo (trampa a los shorts, institucionales acumulan)
  3. Recovery ENCIMA del mínimo → trigger de entrada
  4. Esperar ACEPTACIÓN o protocolo no-aceptación (5+ pts encima, 2 min)

Mínimo significativo válido SOLO:
  - Low del día anterior (daily low)
  - Low multi-hora (mínimo que tardó 2+ horas en formarse, caída 20+ pts)
  - Shelf de lows (varios lows en la misma zona durante varias horas)

También tienes el Level Reclaim / Backtest:
  Resistencia rota al alza que se convierte en soporte → long en el backtest.

NUNCA: knife catch, niveles menores "tested to death", ir short.

══════════════════════════════════════════════════════════
TU PLAN PARA HOY:
══════════════════════════════════════════════════════════
{content_plan}

══════════════════════════════════════════════════════════
TUS TWEETS DE HOY:
══════════════════════════════════════════════════════════
{tweets_hoy}

══════════════════════════════════════════════════════════
SITUACIÓN ACTUAL:
══════════════════════════════════════════════════════════
Hora EST:            {hora_est}
Precio ES actual:    {precio_es}
Nivel bajo análisis: {nivel} ({tipo_nivel})
Bias del día:        {bias}

VENTANA DE TRADING Y CRITERIO APLICABLE:
{criterio_ventana}

ANÁLISIS DE FAILED BREAKDOWN:
{fb_descripcion}

VELA 15 MINUTOS: O:{open_15} H:{high_15} L:{low_15} C:{close_15}
Confirmación técnica: {confirmacion}

══════════════════════════════════════════════════════════
EVALUACIÓN:
══════════════════════════════════════════════════════════
1. ¿Este nivel ({nivel}) es accionable según tu plan de hoy?
2. ¿Hay un Failed Breakdown real con elevator down + flush + recovery?
   ¿El nivel es un mínimo significativo mayor (daily low, multi-hora, shelf)?
3. Aplica el criterio de la ventana horaria indicado arriba.
4. ¿Tus tweets de hoy confirman o contraindican?

REGLA DE STOP: stop DEBE ser MENOR que entrada para LONG (máx 15 pts de riesgo).

Responde SOLO con JSON válido:
{{
  "entrar": true o false,
  "razon": "explica ventana, si hay FB real con elevator down claro, y el nivel",
  "entrada_es": número,
  "stop_es": número (MENOR que entrada_es),
  "target1_es": número (siguiente nivel del newsletter arriba),
  "target2_es": número o null,
  "confianza": 0.0 a 1.0
}}"""


async def generar_señal_llm(precio_es, nivel, tipo_nivel, direccion,
                      today, bars_15, fb_info=None, tweets=None,
                      criterio_ventana='') -> dict:
    """
    Consulta a Claude Haiku si Adam entraría en esta situación.

    FIX C-13 — async:
    client.messages.create() es síncrona y tarda 1-3 s. Dentro de async
    bloquea el event loop de ib_insync: ningún tick puede procesarse.
    asyncio.to_thread() corre la llamada en un thread del SO y devuelve
    el control al event loop sin parar ningún otro proceso.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    content_plan = (
        today.get('content_plan') or
        today.get('setup', '') + '\n' + today.get('invalida_si', '') or
        'Plan no disponible'
    )
    hora_est, _, _ = get_trading_window()

    if bars_15:
        v    = bars_15[-1]
        m    = SPY_TO_ES_MULTIPLIER
        open_15  = f"{v['open']  * m:.1f}"
        high_15  = f"{v['high']  * m:.1f}"
        low_15   = f"{v['low']   * m:.1f}"
        close_15 = f"{v['close'] * m:.1f}"
        confirmacion = ("SÍ — vela alcista confirma"
                        if confirmar_con_vela_15min(bars_15, nivel, direccion)
                        else "NO — vela no confirma")
    else:
        open_15 = high_15 = low_15 = close_15 = f"{precio_es:.1f}"
        confirmacion = "Sin datos de vela 15min"

    prompt = SIGNAL_PROMPT.format(
        content_plan     = content_plan,
        tweets_hoy       = formatear_tweets_para_prompt(tweets or []),
        hora_est         = hora_est,
        precio_es        = f"{precio_es:.1f}",
        nivel            = f"{nivel:.1f}",
        tipo_nivel       = tipo_nivel,
        direccion        = direccion.upper(),
        bias             = today.get('bias', 'unknown'),
        criterio_ventana = criterio_ventana,
        fb_descripcion   = (fb_info or {}).get('descripcion', '⚠️ Sin análisis'),
        open_15          = open_15,
        high_15          = high_15,
        low_15           = low_15,
        close_15         = close_15,
        confirmacion     = confirmacion,
    )

    try:
        # C-13: to_thread libera el event loop de ib_insync durante los 1-3s de la llamada LLM.
        response = await asyncio.to_thread(
            client.messages.create,
            model      = LLM_MODEL,
            max_tokens = 500,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Fix JSON parsing: el LLM a veces añade texto antes/después del JSON
        # causando 'Extra data' en json.loads. Extraer entre el primer '{' y
        # el último '}' ignora markdown y texto extra.
        inicio = raw.find('{')
        fin    = raw.rfind('}')
        if inicio != -1 and fin != -1 and fin > inicio:
            raw = raw[inicio:fin + 1]
        else:
            raise ValueError(f"No se encontró JSON en la respuesta LLM: {raw[:200]}")
        resultado = json.loads(raw)

        # Validación matemática: stop < entrada para longs
        if resultado.get('entrar'):
            entrada = resultado.get('entrada_es')
            stop    = resultado.get('stop_es')
            if entrada and stop and float(stop) >= float(entrada):
                resultado['entrar'] = False
                resultado['razon']  = f"Descartado: stop ({stop}) >= entrada ({entrada}) en LONG"

        return resultado
    except Exception as e:
        print(f"  ❌ Error LLM: {e}")
        return {"entrar": False, "razon": f"Error: {e}"}


# ─────────────────────────────────────────────
# Motor principal
# ─────────────────────────────────────────────

# FIX C-10: constantes de cooldown en un único lugar.
# Antes el '60' estaba duplicado en _marcar_cooldown (dos veces) y
# _esta_en_cooldown (horas=1.0). Cambiar uno sin los otros rompe el
# cooldown en silencio. Con constantes nombradas hay un solo punto de verdad.
COOLDOWN_SEÑAL_MIN    = 60   # minutos de cooldown tras señal enviada
COOLDOWN_NO_ENTRY_MIN = 15   # minutos de cooldown tras LLM rechaza entrada

class SignalEngine:
    """
    Motor de señales con gestión activa del trade abierto.

    ESTADO DE TRADE ACTIVO (self._trade_activo):
    Una vez enviada la señal:
    {
        'direccion':     'long',
        'entrada':       float,
        'stop':          float,
        'stop_efectivo': float,   <- se mueve a breakeven tras T1
        't1':            float,
        't2':            float | None,
        'nivel':         float,
        'hora':          datetime,
        't1_alcanzado':  bool,
        't2_alcanzado':  bool,
    }
    Mientras hay trade activo NO se buscan nuevas entradas.
    """

    def __init__(self):
        self.feed    = MarketFeed()
        # C-11: alerter como atributo — una sola instancia reutilizada en todos
        # los eventos (entrada, T1, T2, stop).
        self.alerter = TelegramAlerter()
        self._trade_activo: dict | None = None
        self._last_signal: dict = {}
        # Restaura el trade y los cooldowns de una sesión anterior si existen en disco.
        self._cargar_estado()

    # ─────────────────────────────────────────────
    # Persistencia del estado en disco (A-2)
    # ─────────────────────────────────────────────

    def _guardar_estado(self):
        """Vuelca a disco el trade activo y los cooldowns por nivel."""
        try:
            trade = None
            if self._trade_activo:
                trade = dict(self._trade_activo)
                if isinstance(trade.get('hora'), datetime):
                    trade['hora'] = trade['hora'].isoformat()
            data = {
                'guardado_en':  datetime.now().isoformat(),
                'trade_activo': trade,
                'last_signal':  {k: v.isoformat() for k, v in self._last_signal.items()},
            }
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"  ⚠️  No se pudo guardar el estado del motor: {e}")

    def _cargar_estado(self):
        """Restaura el estado desde STATE_FILE al arrancar, con guardas de antigüedad."""
        if not STATE_FILE.exists():
            return
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️  No se pudo leer el estado guardado: {e}")
            return

        ahora = datetime.now()

        # ── Trade activo (con guardas de antigüedad) ──────────────────
        trade = data.get('trade_activo')
        if trade and trade.get('hora'):
            try:
                hora_trade   = datetime.fromisoformat(trade['hora'])
                antiguedad_h = (ahora - hora_trade).total_seconds() / 3600
                mismo_dia    = hora_trade.date() == ahora.date()
                if antiguedad_h < 3 and mismo_dia:
                    trade['hora'] = hora_trade
                    self._trade_activo = trade
                    print(f"  🔄 Trade activo restaurado: entrada {trade.get('entrada')} | "
                          f"T1 {'✓' if trade.get('t1_alcanzado') else '✗'} | "
                          f"stop {trade.get('stop_efectivo')}")
                else:
                    print(f"  🗑️  Trade guardado descartado "
                          f"(antigüedad {antiguedad_h:.1f}h, mismo_día={mismo_dia})")
            except Exception:
                pass

        # ── Cooldowns todavía activos (C-10: guarda expiración, no inicio) ──
        for nivel_str, ts_str in (data.get('last_signal') or {}).items():
            try:
                expiry = datetime.fromisoformat(ts_str)
                if expiry > ahora:
                    self._last_signal[nivel_str] = expiry
            except Exception:
                pass

    async def _gestionar_trade_activo(self, precio_es: float) -> str:
        """
        Monitoriza T1, T2, stop y timeout del trade activo.
        Returns 'bloqueado' si trade abierto, 'libre' si cerrado.

        FIX A-3: usa high/low de velas de 1 min para detectar toques
        que el precio puntual (delayed 15 min, muestreado cada 60s) perdería.
        T1 se comprueba ANTES que stop: si ambos ocurren en la misma barra,
        T1 gana → día verde ('first trade is a win').
        """
        if not self._trade_activo:
            return 'libre'

        trade     = self._trade_activo
        entrada   = trade['entrada']
        t1        = trade['t1']
        t2        = trade.get('t2')
        stop_ef   = trade['stop_efectivo']
        direccion = trade['direccion']

        if direccion != 'long':
            self._trade_activo = None
            return 'libre'

        # ── A-3: alto y bajo reales de las últimas 3 velas de 1 min ─────────
        alto_reciente = precio_es
        bajo_reciente = precio_es
        try:
            bars_1m = await self.feed.get_bars(1, 3)
            if bars_1m:
                m             = SPY_TO_ES_MULTIPLIER
                alto_reciente = max(b['high'] * m for b in bars_1m)
                bajo_reciente = min(b['low']  * m for b in bars_1m)
        except Exception:
            pass

        # ── T2 ────────────────────────────────────────────────────────────────
        if trade.get('t1_alcanzado') and t2 and not trade.get('t2_alcanzado'):
            if alto_reciente >= float(t2):
                trade['t2_alcanzado'] = True
                print(f"  🏆 T2 alcanzado: {t2}")
                await self.alerter.send_t2_alert(trade, alto_reciente)
                self._trade_activo = None
                return 'libre'

        # ── T1 (antes que stop) ───────────────────────────────────────────────
        if not trade.get('t1_alcanzado') and alto_reciente >= float(t1):
            trade['t1_alcanzado']  = True
            trade['stop_efectivo'] = entrada
            print(f"  ✅ T1 alcanzado: {t1}")
            await self.alerter.send_t1_alert(trade, alto_reciente)

        # ── Stop efectivo ────────────────────────────────────────────────────
        if bajo_reciente <= stop_ef:
            print(f"  🛑 Stop hit: {bajo_reciente:.0f}")
            await self.alerter.send_stop_alert(trade, bajo_reciente)
            self._trade_activo = None
            return 'libre'

        # ── Timeout 3 horas ───────────────────────────────────────────────────
        if (datetime.now() - trade['hora']).total_seconds() / 3600 >= 3:
            print(f"  ⏰ Trade expirado (3h) — liberando")
            self._trade_activo = None
            return 'libre'

        pts      = precio_es - entrada
        signo    = '+' if pts >= 0 else ''
        t1_label = '✅ T1✓ runner' if trade.get('t1_alcanzado') else f'→T1:{t1:.0f}'
        print(f"  📊 Trade activo: {entrada:.0f} → {precio_es:.1f} "
              f"({signo}{pts:.0f}pts) | "
              f"H:{alto_reciente:.0f} L:{bajo_reciente:.0f} | "
              f"{t1_label} | stop:{stop_ef:.0f}")
        return 'bloqueado'

    def _esta_en_cooldown(self, nivel: float) -> bool:
        """True si el nivel todavía está en cooldown (C-10: expiración directa)."""
        key    = f"{nivel:.0f}"
        expiry = self._last_signal.get(key)
        if expiry is None:
            return False
        return datetime.now() < expiry

    def _marcar_cooldown(self, nivel: float, minutos: int):
        """Pone el nivel en cooldown 'minutos' minutos desde ahora (C-10)."""
        self._last_signal[f"{nivel:.0f}"] = datetime.now() + timedelta(minutes=minutos)

    async def check_once(self) -> bool:
        """Ciclo completo de comprobación del mercado (cada 60s)."""
        snapshot = self.feed.get_snapshot()
        if not snapshot:
            return False

        precio_es = snapshot['es_equivalent']
        ahora     = snapshot['timestamp'][:19]

        estado_trade = await self._gestionar_trade_activo(precio_es)
        self._guardar_estado()  # A-2: captura T1→breakeven y cierres

        today = load_today()
        if not today:
            return False

        niveles  = get_all_levels(today)
        bias     = today.get('bias', 'unknown')

        niveles_cercanos = [
            n for n in niveles
            if abs(n['nivel'] - precio_es) <= 60
        ]

        hora_est, ventana, criterio_ventana = get_trading_window()

        if DATA_SOURCE == 'ibkr':
            print(f"[{ahora}] ES:{precio_es:.1f} | "
                  f"Bias:{bias} | {hora_est} | Niveles cercanos:{len(niveles_cercanos)}")
        else:
            spy_price = snapshot.get('spy_price', precio_es / 10)
            print(f"[{ahora}] SPY:{spy_price:.2f} ES:{precio_es:.1f} | "
                  f"Bias:{bias} | {hora_est} | Niveles cercanos:{len(niveles_cercanos)}")

        tweets_hoy = get_todays_tweets()
        if tweets_hoy:
            print(f"  🐦 {len(tweets_hoy)} tweets de Adam hoy")

        if estado_trade == 'bloqueado':
            return False

        señal_enviada = False

        # B-6: velas de 15 min una sola vez antes del bucle
        bars_15: list = []
        if niveles_cercanos:
            try:
                bars_15 = await self.feed.get_bars(15, 8)
            except Exception as e:
                print(f"  ⚠️  Error obteniendo velas 15min: {e}")

        for nivel_info in niveles_cercanos:
            nivel      = nivel_info['nivel']
            tipo_nivel = nivel_info['tipo']

            if not is_price_at_level(precio_es, nivel):
                continue
            if self._esta_en_cooldown(nivel):
                continue

            direccion = determinar_lado(precio_es, nivel_info, bias)
            if not direccion:
                continue

            fb_info = detect_failed_breakdown(bars_15, nivel)
            print(f"  {'✅ FB' if fb_info['es_fb'] else '⚠️  No FB'} | "
                  f"Nivel:{nivel:.0f} ({tipo_nivel}) | "
                  f"{fb_info['descripcion'][:70]}")

            confirmado = confirmar_con_vela_15min(bars_15, nivel, direccion)
            print(f"  📊 {direccion.upper()} | "
                  f"15min: {'✅' if confirmado else '⚠️'} | "
                  f"Ventana: {ventana[:45]}")

            print("  🤖 Consultando LLM...")
            # C-13: generar_señal_llm es ahora async — usa asyncio.to_thread internamente
            señal = await generar_señal_llm(
                precio_es        = precio_es,
                nivel            = nivel,
                tipo_nivel       = tipo_nivel,
                direccion        = direccion,
                today            = today,
                bars_15          = bars_15,
                fb_info          = fb_info,
                tweets           = tweets_hoy,
                criterio_ventana = criterio_ventana,
            )
            señal['direccion'] = direccion

            if señal.get('entrar'):
                confianza = señal.get('confianza', 0)
                print(f"  ✅ LLM: ENTRAR ({confianza:.0%} confianza)")

                # C-11: send_signal_alert (HTML escapado, R/R calculado)
                await self.alerter.send_signal_alert(señal, precio_es, nivel, today)

                self._trade_activo = {
                    'direccion':     direccion,
                    'entrada':       float(señal.get('entrada_es', nivel)),
                    'stop':          float(señal.get('stop_es', nivel - 15)),
                    'stop_efectivo': float(señal.get('stop_es', nivel - 15)),
                    't1':            float(señal.get('target1_es', nivel + 20)),
                    't2':            float(señal['target2_es']) if señal.get('target2_es') else None,
                    'nivel':         nivel,
                    'hora':          datetime.now(),
                    't1_alcanzado':  False,
                    't2_alcanzado':  False,
                }
                print(f"  🔒 Trade activo — nuevas entradas bloqueadas")

                self._marcar_cooldown(nivel, minutos=COOLDOWN_SEÑAL_MIN)
                señal_enviada = True
                break

            else:
                print(f"  ❌ LLM: No entrar — {señal.get('razon', '')[:100]}")
                self._marcar_cooldown(nivel, minutos=COOLDOWN_NO_ENTRY_MIN)

        # A-2: persistir al final del tick
        self._guardar_estado()
        return señal_enviada

    async def run_loop(self, interval_seconds: int = 60):
        """Loop principal: comprueba el mercado cada 60 segundos."""
        print("=" * 55)
        print("  Bot Adam Mancini — Motor de Señales")
        print("=" * 55)
        print(f"⏱️  Intervalo: {interval_seconds}s | Tolerancia: ±{LEVEL_TOLERANCE_POINTS}pts ES")
        print("Ctrl+C para parar\n")

        if DATA_SOURCE == 'ibkr' and hasattr(self.feed, 'connect_async'):
            try:
                await self.feed.connect_async()
            except Exception as e:
                print(f"\n❌ No se pudo conectar a IBKR: {e}")
                return

        try:
            while True:
                if not is_market_open():
                    tz    = pytz.timezone(MARKET_TIMEZONE)
                    ahora = datetime.now(tz).strftime('%H:%M')
                    print(f"[{ahora}] 😴 Mercado cerrado — esperando 5 min")
                    await asyncio.sleep(300)
                    continue

                await self.check_once()
                await asyncio.sleep(interval_seconds)

        finally:
            if DATA_SOURCE == 'ibkr' and hasattr(self.feed, 'disconnect'):
                self.feed.disconnect()


if __name__ == '__main__':
    engine = SignalEngine()
    try:
        asyncio.run(engine.run_loop(interval_seconds=60))
    except KeyboardInterrupt:
        print("\n⏹️  Motor detenido")
