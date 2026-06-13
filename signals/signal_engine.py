"""
signals/signal_engine.py — Motor de señales: el corazón del bot
================================================================
Une todas las piezas del proyecto para detectar señales de Adam Mancini.

FUENTES DE INFORMACIÓN (por orden de importancia):
  1. Newsletter de hoy (content_plan) — las palabras EXACTAS de Adam
     sobre qué niveles son accionables y en qué condiciones
  2. Tweets de Adam del día — actualizaciones en tiempo real
  3. Metodología de Adam (en el prompt) — base fija siempre aplicada
  4. Precio SPY/ES en tiempo real — para detectar cuándo se acerca a un nivel
  5. Velas de 15min — confirmación técnica y detección de Failed Breakdown

VENTANA DE TRADING DE ADAM:
  - 7:30-11:00 AM EST: ventana principal, mayoría de entradas
  - 11:00-14:00 EST: chop, raramente opera
  - 14:00-16:00 EST: segunda ventana

LÓGICA DE ENTRADA:
  - Detecta cuando el precio está cerca de un nivel del newsletter
    (tanto soportes COMO resistencias — estas pueden actuar como soportes
    cuando el precio hace backtest desde arriba)
  - Analiza si hay un Failed Breakdown real (flush + recovery, no chop)
  - Pasa el plan completo de Adam + tweets + hora del día al LLM
  - El LLM (con las palabras exactas de Adam) decide si es una entrada válida
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# IMPORTANTE: añadir la raíz del proyecto al path ANTES de importar módulos propios
sys.path.append(str(Path(__file__).parent.parent))

import anthropic
import pytz

from bot.telegram_alerts import TelegramAlerter

from config import (
    DATA_DIR,
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    SPY_TO_ES_MULTIPLIER,
    LEVEL_TOLERANCE_POINTS,
    MARKET_TIMEZONE,
)
from market_data.alpaca_feed import SPYFeed, is_market_open


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
    """
    Carga el mapa del día generado por el newsletter parser.
    El campo más importante es 'content_plan': el Trade Plan completo
    en las palabras exactas de Adam.
    """
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

    content_len = len(data.get('content_plan', ''))
    if content_len < 500:
        print(f"  ⚠️  content_plan muy corto ({content_len} chars) — "
              f"ejecuta: python parsers/newsletter_parser.py --force")

    return data


def get_all_levels(today: dict) -> list:
    """
    Extrae los niveles del mapa del día conservando su tipo.
    Incluye soportes, resistencias Y pivote.

    Las resistencias también se incluyen porque pueden actuar como soporte
    cuando el precio hace backtest desde arriba (setup explícito de Adam).
    El LLM con el newsletter completo decide si son accionables.

    Returns: [{'nivel': float, 'tipo': 'soporte'|'resistencia'|'pivote'}, ...]
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

    return sorted(niveles, key=lambda x: x['nivel'], reverse=True)


# ─────────────────────────────────────────────
# Ventana de trading de Adam
# ─────────────────────────────────────────────

def get_trading_window() -> tuple:
    """
    Determina en qué ventana de trading de Adam estamos.

    Adam opera principalmente en dos ventanas:
    - 7:30-11:00 AM EST: ventana principal, mayoría de entradas
    - 11:00-14:00 EST: chop, raramente opera ("I rarely trade this window")
    - 14:00-16:00 EST: segunda ventana, solo si el primer trade fue ganador

    Returns: (hora_str, descripcion_ventana)
    """
    tz_est   = pytz.timezone(MARKET_TIMEZONE)
    ahora    = datetime.now(tz_est)
    hora_str = ahora.strftime('%H:%M EST')
    hora_dec = ahora.hour + ahora.minute / 60.0

    if 7.5 <= hora_dec < 11.0:
        ventana = "🟢 VENTANA PRINCIPAL (7:30-11:00 AM) — óptima, mayoría de entradas de Adam"
    elif 11.0 <= hora_dec < 14.0:
        ventana = ("🔴 VENTANA CHOP (11:00 AM-2:00 PM) — Adam raramente opera aquí. "
                   "Solo entrar en A+ setups con FB muy claro.")
    elif 14.0 <= hora_dec < 16.0:
        ventana = ("🟡 VENTANA TARDE (2:00-4:00 PM) — segunda ventana de Adam. "
                   "Solo si el primer trade del día fue ganador.")
    else:
        ventana = "⚪ FUERA DE VENTANA DE TRADING"

    return hora_str, ventana


# ─────────────────────────────────────────────
# Tweets del día
# ─────────────────────────────────────────────

def get_todays_tweets() -> list:
    """
    Lee los tweets de Adam del día actual desde el estado del tweet monitor.
    """
    if not TWEETS_FILE.exists():
        return []

    try:
        state = json.load(open(TWEETS_FILE, encoding='utf-8'))
        hoy   = datetime.now().strftime('%Y-%m-%d')

        if state.get('fecha_hoy') != hoy:
            return []

        tweets_hoy = state.get('tweets_hoy', [])
        tweets = []
        for item in tweets_hoy:
            if isinstance(item, dict):
                tweet = item.get('tweet', item)
                if tweet.get('text') and not tweet.get('is_retweet'):
                    tweets.append(tweet)
        return tweets
    except Exception:
        return []


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
    """Comprueba si el precio ES está dentro de la tolerancia de un nivel."""
    tol = tolerancia or LEVEL_TOLERANCE_POINTS
    return abs(precio_es - nivel) <= tol


def determinar_lado(precio_es: float, nivel_info: dict, bias: str) -> str | None:
    """
    Decide la dirección del trade según el tipo de nivel y el bias.

    Soportes → long (soporte directo)
    Resistencias → long (backtest desde arriba, puede actuar como soporte)
      Adam describe este setup: precio rompe una zona, sube, vuelve a testear
      la zona desde arriba como soporte (su "Level Reclaim" o "Backtest entry")
    Pivote → según posición del precio

    El LLM con el newsletter completo decide si la resistencia es accionable
    como soporte para ese día concreto.

    Adam no va short → nunca devolvemos 'short'.
    """
    tipo = nivel_info['tipo']

    if tipo == 'soporte':
        return 'long' if bias != 'bearish' else None

    if tipo == 'resistencia':
        # La resistencia puede ser entrada long cuando el precio la retesta desde arriba
        # (backtest/level reclaim setup de Adam). El LLM decide si aplica hoy.
        return 'long' if bias != 'bearish' else None

    if tipo == 'pivote':
        nivel = nivel_info['nivel']
        if precio_es >= nivel:
            return 'long' if bias != 'bearish' else None
        else:
            return None  # No short

    return None


def confirmar_con_vela_15min(bars_15: list, nivel: float, direccion: str) -> bool:
    """Confirma el setup usando la vela de 15 minutos (timeframe principal de Adam)."""
    if not bars_15:
        return False

    ultima_vela = bars_15[-1]
    close = ultima_vela['close'] * SPY_TO_ES_MULTIPLIER
    open_ = ultima_vela['open'] * SPY_TO_ES_MULTIPLIER

    if direccion == 'long':
        return close > open_ and close >= nivel

    return False


def detect_failed_breakdown(bars_15: list, nivel: float) -> dict:
    """
    Detecta si ha habido un Failed Breakdown reciente en el nivel.

    Secuencia requerida (en orden cronológico en las últimas 8 barras):
    1. Precio estuvo ENCIMA del nivel (+5 pts al menos)
    2. Flush por DEBAJO del nivel (al menos 5 pts)
    3. Precio actual recuperado ENCIMA del nivel

    Returns: dict con 'es_fb', 'flush_size', 'bars_ago', 'descripcion'
    """
    if not bars_15 or len(bars_15) < 2:
        return {
            'es_fb': False, 'flush_size': 0, 'bars_ago': 0,
            'descripcion': 'Sin datos de velas suficientes'
        }

    m                = SPY_TO_ES_MULTIPLIER
    precio_actual_es = bars_15[-1]['close'] * m

    if precio_actual_es <= nivel:
        return {
            'es_fb': False, 'flush_size': 0, 'bars_ago': 0,
            'descripcion': (
                f'Precio {precio_actual_es:.0f} por debajo de {nivel:.0f} — '
                f'recovery aún no completada'
            )
        }

    was_above = False

    for i in range(1, min(9, len(bars_15))):
        bar      = bars_15[-i]
        close_es = bar['close'] * m
        low_es   = bar['low']   * m

        if not was_above:
            if close_es > nivel + 5:
                was_above = True
            continue

        if was_above and low_es < nivel - 5:
            flush_size = round(nivel - low_es, 1)

            if flush_size >= 20:
                calidad = f'profundo ({flush_size:.0f} pts) — alta probabilidad'
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

    return {
        'es_fb':       False,
        'flush_size':  0,
        'bars_ago':    0,
        'descripcion': (
            f'⚠️ Sin Failed Breakdown: precio {precio_actual_es:.0f} encima de '
            f'{nivel:.0f} pero sin secuencia elevator down → flush → recovery. '
            f'Adam NO entraría aquí.'
        )
    }


# ─────────────────────────────────────────────
# Generación de señal con LLM
# ─────────────────────────────────────────────

SIGNAL_PROMPT = """Eres Adam Mancini analizando si debes entrar en un trade ahora mismo en ES futures.

══════════════════════════════════════════════════════════
METODOLOGÍA (base fija, siempre aplica sin excepciones):
══════════════════════════════════════════════════════════
Tu ÚNICO setup de entrada es el Failed Breakdown:
  1. Elevator down: precio cae vertical hacia un mínimo significativo
  2. Flush DEBAJO del mínimo (trampa a los shorts, institucionales acumulan)
  3. Recovery ENCIMA del mínimo → trigger de entrada
  4. Esperar ACEPTACIÓN: precio intenta vender en el mínimo, falla, vuelve
     O protocolo no-aceptación: precio 5+ pts encima sin parar

Mínimo significativo válido: low del día anterior, low multi-hora (20+ pts), shelf de lows

Tienes también el Level Reclaim / Backtest setup:
  Cuando una resistencia importante se rompe al alza, el precio vuelve a retestearla
  desde arriba y ese nivel actúa como soporte → entrada long en el backtest.

VENTANA DE TRADING (crítico para evaluar la entrada):
  - 7:30-11:00 AM EST: ventana PRINCIPAL, la mayoría de tus entradas
  - 11:00 AM-2:00 PM EST: CHOP, raramente operas aquí ("I rarely trade this window")
  - 2:00-4:00 PM EST: segunda ventana, solo si el primer trade del día fue ganador

NUNCA:
  - Knife catch (comprar en caída libre — esperar flush completo + recovery)
  - Comprar niveles "tested to death" sin ver el trap y recovery fresco
  - Ir short en ES (solo longs vía Failed Breakdowns)
  - Operar el chop de 11am-2pm salvo setup A+ muy claro

══════════════════════════════════════════════════════════
TU PLAN PARA HOY (newsletter completo — tus propias palabras):
══════════════════════════════════════════════════════════
{content_plan}

══════════════════════════════════════════════════════════
TUS TWEETS DE HOY (actualizaciones en tiempo real):
══════════════════════════════════════════════════════════
{tweets_hoy}

══════════════════════════════════════════════════════════
SITUACIÓN ACTUAL DE MERCADO:
══════════════════════════════════════════════════════════
Hora EST actual:     {hora_est}
Ventana de trading:  {ventana}
Precio ES actual:    {precio_es}
Nivel bajo análisis: {nivel} ({tipo_nivel})
Bias del día:        {bias}

ANÁLISIS DE FAILED BREAKDOWN EN ESTE NIVEL:
{fb_descripcion}

VELA 15 MINUTOS (tu timeframe principal):
O:{open_15} H:{high_15} L:{low_15} C:{close_15}
Confirmación técnica: {confirmacion}

══════════════════════════════════════════════════════════
EVALUACIÓN:
══════════════════════════════════════════════════════════
Basándote en TU newsletter de hoy y TU metodología, evalúa:

1. ¿Este nivel ({nivel}) aparece en tu sección "I'd bid direct" o es accionable según tu plan?
2. ¿Hay un Failed Breakdown real aquí? (elevator down visible + flush + recovery + aceptación)
   O es un Level Reclaim/Backtest setup (resistencia que se convierte en soporte)?
3. ¿La ventana horaria actual ({hora_est}) apoya esta entrada según tu metodología?
4. ¿Qué dicen tus tweets de hoy sobre este nivel o situación?

Si estamos en ventana chop (11am-2pm) → ser muy estricto, solo A+ obvios.
Si el nivel NO está en tu plan, o NO hay FB/Backtest real → entrar: false.

Responde SOLO con JSON válido, sin texto adicional:
{{
  "entrar": true o false,
  "razon": "cita tu newsletter o tweets, menciona la ventana horaria y explica si hay FB/Backtest real",
  "entrada_es": precio ES de entrada (número),
  "stop_es": stop ES (bajo el flush mínimo, máx 15 pts de riesgo),
  "target1_es": primer target (siguiente nivel de tu newsletter),
  "target2_es": segundo target o null,
  "confianza": valor 0.0 a 1.0
}}"""


def generar_señal_llm(
    precio_es: float,
    nivel: float,
    tipo_nivel: str,
    direccion: str,
    today: dict,
    bars_15: list,
    fb_info: dict | None = None,
    tweets: list | None = None,
) -> dict:
    """
    Pregunta al LLM si Adam entraría en esta situación.

    El LLM recibe:
    - El plan completo del newsletter (content_plan)
    - Los tweets de Adam del día
    - La metodología fija (incluyendo ventanas horarias)
    - La hora actual EST y la ventana de trading
    - La situación de mercado actual
    - El análisis de Failed Breakdown
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    content_plan = (
        today.get('content_plan') or
        today.get('setup', '') + '\n' + today.get('invalida_si', '') or
        'Plan no disponible — ejecuta newsletter_parser.py --force'
    )

    tweets_texto    = formatear_tweets_para_prompt(tweets or [])
    fb_descripcion  = (fb_info or {}).get('descripcion', '⚠️ Sin análisis de FB disponible')
    hora_est, ventana = get_trading_window()

    if bars_15:
        v = bars_15[-1]
        m = SPY_TO_ES_MULTIPLIER
        open_15  = f"{v['open']  * m:.1f}"
        high_15  = f"{v['high']  * m:.1f}"
        low_15   = f"{v['low']   * m:.1f}"
        close_15 = f"{v['close'] * m:.1f}"
        confirmacion = ("SÍ — vela alcista confirma" if
                        confirmar_con_vela_15min(bars_15, nivel, direccion)
                        else "NO — vela no confirma dirección")
    else:
        open_15 = high_15 = low_15 = close_15 = f"{precio_es:.1f}"
        confirmacion = "Sin datos de vela 15min"

    prompt = SIGNAL_PROMPT.format(
        content_plan  = content_plan,
        tweets_hoy    = tweets_texto,
        hora_est      = hora_est,
        ventana       = ventana,
        precio_es     = f"{precio_es:.1f}",
        nivel         = f"{nivel:.1f}",
        tipo_nivel    = tipo_nivel,
        direccion     = direccion.upper(),
        bias          = today.get('bias', 'unknown'),
        fb_descripcion = fb_descripcion,
        open_15       = open_15,
        high_15       = high_15,
        low_15        = low_15,
        close_15      = close_15,
        confirmacion  = confirmacion,
    )

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        print(f"  ❌ Error LLM señal: {e}")
        return {"entrar": False, "razon": f"Error: {e}"}


# ─────────────────────────────────────────────
# Formato de alerta Telegram
# ─────────────────────────────────────────────

def formatear_alerta(señal: dict, precio_es: float, nivel: float, today: dict,
                     hora_est: str = '') -> str:
    """Formatea la señal como mensaje de Telegram."""
    es_long   = señal.get('direccion', 'long') == 'long'
    dir_emoji = '🟢' if es_long else '🔴'
    dir_texto = 'LONG' if es_long else 'SHORT'

    entrada = señal.get('entrada_es', nivel)
    stop    = señal.get('stop_es', '')
    t1      = señal.get('target1_es', '')
    t2      = señal.get('target2_es', '')
    conf    = señal.get('confianza', 0)

    rr_str = ''
    if entrada and stop and t1:
        riesgo  = abs(float(entrada) - float(stop))
        reward1 = abs(float(t1) - float(entrada))
        if riesgo > 0:
            rr_str = f"\n📐 R/R: 1:{reward1/riesgo:.1f}"

    mensaje = (
        f"{dir_emoji} {dir_texto} ES — Señal Adam Mancini\n"
        f"{'─' * 32}\n"
        f"📍 Entrada:   {entrada}\n"
        f"🛑 Stop:      {stop}\n"
        f"🎯 Target 1:  {t1}\n"
    )
    if t2:
        mensaje += f"🎯 Target 2:  {t2}\n"

    mensaje += (
        f"{'─' * 32}\n"
        f"📊 Nivel: {nivel} | Bias: {today.get('bias', '?').upper()}\n"
    )
    if hora_est:
        mensaje += f"⏰ {hora_est}\n"

    mensaje += (
        f"💭 {señal.get('razon', '')[:200]}\n"
        f"🎯 Confianza: {conf:.0%}"
    )
    if rr_str:
        mensaje += rr_str

    return mensaje


async def enviar_alerta(mensaje: str):
    """Envía la alerta por Telegram y la imprime en consola."""
    print(mensaje)
    try:
        alerter = TelegramAlerter()
        await alerter.send(mensaje)
    except Exception as e:
        print(f"  ⚠️  Error Telegram: {e}")


# ─────────────────────────────────────────────
# Motor principal
# ─────────────────────────────────────────────

class SignalEngine:
    """
    Motor de señales que monitoriza el mercado y genera alertas.

    Fuentes de información (por orden de importancia):
    1. Newsletter de hoy (content_plan completo)
    2. Tweets de Adam del día
    3. Metodología + ventanas horarias en el prompt del LLM
    4. Precio SPY/ES en tiempo real
    5. Velas de 15min para detección de Failed Breakdown
    """

    def __init__(self):
        self.feed = SPYFeed()
        self._last_signal: dict = {}

    def _esta_en_cooldown(self, nivel: float, horas: float = 1.0) -> bool:
        """Evita señalar el mismo nivel más de una vez por hora."""
        key  = f"{nivel:.0f}"
        last = self._last_signal.get(key)
        if last is None:
            return False
        return (datetime.now() - last).total_seconds() < horas * 3600

    def _marcar_señalado(self, nivel: float):
        self._last_signal[f"{nivel:.0f}"] = datetime.now()

    async def check_once(self) -> bool:
        """
        Ejecuta un ciclo completo de comprobación del mercado.

        Flujo:
        1. Precio actual (SPY → ES)
        2. Plan del día (newsletter completo)
        3. Tweets de Adam de hoy
        4. Ventana horaria actual
        5. Para cada nivel cercano al precio (soportes Y resistencias):
           a. Detectar Failed Breakdown
           b. Preguntar al LLM con newsletter + tweets + hora del día
           c. Si el LLM dice entrar → alerta Telegram
        """
        # ── 1. Precio actual ──────────────────────────────────────────────
        snapshot = self.feed.get_snapshot()
        if not snapshot:
            return False

        precio_spy = snapshot['spy_price']
        precio_es  = snapshot['es_equivalent']
        ahora      = snapshot['timestamp'][:19]

        # ── 2. Plan del día ───────────────────────────────────────────────
        today = load_today()
        if not today:
            return False

        niveles = get_all_levels(today)
        bias    = today.get('bias', 'unknown')

        # Todos los niveles dentro de ±60 pts (soportes Y resistencias)
        # Las resistencias se incluyen porque pueden actuar como soporte
        # cuando el precio hace backtest desde arriba (setup de Adam)
        niveles_cercanos = [
            n for n in niveles
            if abs(n['nivel'] - precio_es) <= 60
        ]

        # ── 3. Ventana horaria ────────────────────────────────────────────
        hora_est, ventana = get_trading_window()

        print(f"[{ahora}] SPY:{precio_spy:.2f} ES:{precio_es:.1f} | "
              f"Bias:{bias} | {hora_est} | Niveles cercanos:{len(niveles_cercanos)}")

        # ── 4. Tweets de Adam de hoy ──────────────────────────────────────
        tweets_hoy = get_todays_tweets()
        if tweets_hoy:
            print(f"  🐦 {len(tweets_hoy)} tweets de Adam hoy")

        # ── 5. Comprobar cada nivel cercano ───────────────────────────────
        señal_enviada = False

        for nivel_info in niveles_cercanos:
            nivel      = nivel_info['nivel']
            tipo_nivel = nivel_info['tipo']

            if not is_price_at_level(precio_es, nivel):
                continue
            if self._esta_en_cooldown(nivel):
                continue

            # Dirección (solo long — Adam no va short)
            direccion = determinar_lado(precio_es, nivel_info, bias)
            if not direccion:
                continue

            # Velas de 15min
            bars_15 = self.feed.get_bars(15, 8)

            # Detectar Failed Breakdown
            fb_info = detect_failed_breakdown(bars_15, nivel)
            print(f"  {'✅ FB' if fb_info['es_fb'] else '⚠️  No FB'} | "
                  f"Nivel:{nivel:.0f} ({tipo_nivel}) | "
                  f"{fb_info['descripcion'][:70]}")

            confirmado = confirmar_con_vela_15min(bars_15, nivel, direccion)
            print(f"  📊 {direccion.upper()} | "
                  f"15min: {'✅' if confirmado else '⚠️'} | "
                  f"Ventana: {ventana[:40]}")

            # Consultar LLM
            print("  🤖 Consultando LLM...")
            señal = generar_señal_llm(
                precio_es  = precio_es,
                nivel      = nivel,
                tipo_nivel = tipo_nivel,
                direccion  = direccion,
                today      = today,
                bars_15    = bars_15,
                fb_info    = fb_info,
                tweets     = tweets_hoy,
            )

            señal['direccion'] = direccion

            if señal.get('entrar'):
                confianza = señal.get('confianza', 0)
                print(f"  ✅ LLM: ENTRAR ({confianza:.0%} confianza)")
                mensaje = formatear_alerta(señal, precio_es, nivel, today, hora_est)
                await enviar_alerta(mensaje)
                self._marcar_señalado(nivel)
                señal_enviada = True
            else:
                print(f"  ❌ LLM: No entrar — {señal.get('razon', '')[:100]}")
                # Cooldown corto (15 min) tras un "no" para no llamar al LLM cada minuto
                self._last_signal[f"{nivel:.0f}"] = (
                    datetime.now() - timedelta(minutes=45)
                )

        return señal_enviada

    async def run_loop(self, interval_seconds: int = 60):
        """Loop principal: comprueba el mercado cada 60 segundos."""
        print("=" * 55)
        print("  Bot Adam Mancini — Motor de Señales")
        print("=" * 55)
        print(f"⏱️  Intervalo: {interval_seconds}s | Tolerancia: ±{LEVEL_TOLERANCE_POINTS}pts ES")
        print("Ctrl+C para parar\n")

        while True:
            if not is_market_open():
                tz    = pytz.timezone(MARKET_TIMEZONE)
                ahora = datetime.now(tz).strftime('%H:%M')
                print(f"[{ahora}] 😴 Mercado cerrado — esperando 5 min")
                await asyncio.sleep(300)
                continue

            await self.check_once()
            await asyncio.sleep(interval_seconds)


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────

if __name__ == '__main__':
    engine = SignalEngine()
    try:
        asyncio.run(engine.run_loop(interval_seconds=60))
    except KeyboardInterrupt:
        print("\n⏹️  Motor detenido")
