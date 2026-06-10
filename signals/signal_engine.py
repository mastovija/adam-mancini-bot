"""
signals/signal_engine.py — Motor de señales: el corazón del bot
================================================================
Une todas las piezas del proyecto para detectar señales de Adam Mancini:

1. Lee today.json → niveles y bias del newsletter de hoy
2. Obtiene precio SPY cada 60 segundos → convierte a ES
3. Detecta cuando el precio está en un nivel de Adam
4. Pide confirmación a la vela de 15 minutos (timeframe de Adam)
5. Consulta ChromaDB → busca situaciones históricas similares
6. Pregunta al LLM: "¿entraría Adam aquí?" con todo el contexto
7. Si sí → genera alerta con entrada, stop y target

La lógica de entrada replica el estilo de Adam:
  - LONG:  precio llega a soporte, vela 15min cierra por encima → entrada
  - SHORT: precio llega a resistencia, vela 15min cierra por debajo → entrada
  - Stop:  siguiente nivel en contra
  - Target: siguiente nivel a favor

USO:
    python signals/signal_engine.py
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
# IMPORTANTE: añadir la raíz del proyecto al path ANTES de importar
# cualquier módulo propio (bot, config, market_data...). Si no,
# ejecutar este archivo directamente falla con ModuleNotFoundError.
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
from knowledge_base.vectordb import get_collection, query_similar


# ─────────────────────────────────────────────
# Rutas
# ─────────────────────────────────────────────
TODAY_FILE   = DATA_DIR / 'daily' / 'today.json'
STATE_FILE   = DATA_DIR / 'signal_engine_state.json'


# ─────────────────────────────────────────────
# Cargar contexto del día
# ─────────────────────────────────────────────

def load_today() -> dict | None:
    """
    Carga el mapa del día generado por el newsletter parser.
    Contiene: bias, nivel_critico, soportes, resistencias, setup.
    """
    if not TODAY_FILE.exists():
        print("  ⚠️  today.json no existe — ejecuta primero newsletter_parser.py")
        return None

    with open(TODAY_FILE) as f:
        data = json.load(f)

    # Avisar si el newsletter es de hace más de 2 días
    if data.get('date'):
        dias = (datetime.now().date() -
                datetime.strptime(data['date'], '%Y-%m-%d').date()).days
        if dias > 2:
            print(f"  ⚠️  today.json tiene {dias} días de antigüedad")

    return data


def get_all_levels(today: dict) -> list:
    """
    Extrae los niveles del mapa del día CONSERVANDO su tipo.

    Antes devolvíamos una lista plana de números y perdíamos la información
    de si cada nivel era soporte o resistencia — eso provocaba longs en
    resistencias. Ahora cada nivel es un dict: {'nivel': 7527.0, 'tipo': 'soporte'}

    El nivel crítico se marca como 'pivote': actúa de soporte si el precio
    está por encima, y de resistencia si está por debajo.
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
        # Evitar duplicar si ya está en soportes/resistencias
        if not any(abs(x['nivel'] - nc) < 0.5 for x in niveles):
            niveles.append({'nivel': nc, 'tipo': 'pivote'})

    # Ordenados de mayor a menor para facilitar buscar el siguiente nivel
    return sorted(niveles, key=lambda x: x['nivel'], reverse=True)



# ─────────────────────────────────────────────
# Detección de nivel
# ─────────────────────────────────────────────

def is_price_at_level(precio_es: float, nivel: float, tolerancia: float = None) -> bool:
    """
    Comprueba si el precio ES está dentro de la tolerancia de un nivel.
    Tolerancia por defecto: LEVEL_TOLERANCE_POINTS de config.py (3 puntos).
    """
    tol = tolerancia or LEVEL_TOLERANCE_POINTS
    return abs(precio_es - nivel) <= tol


def determinar_lado(precio_es: float, nivel_info: dict, bias: str) -> str | None:
    """
    Decide la dirección del trade según el TIPO de nivel y el bias.

    Lógica level-to-level de Adam:
      - Soporte tocado     → long  (si el bias no es bearish)
      - Resistencia tocada → short (si el bias no es bullish)
      - Pivote (crítico)   → según de qué lado esté el precio

    Esto reemplaza la lógica anterior que solo miraba si precio <= nivel,
    lo que generaba longs en resistencias.
    """
    tipo  = nivel_info['tipo']
    nivel = nivel_info['nivel']

    if tipo == 'soporte':
        # Long en soporte, salvo que el día sea claramente bajista
        return 'long' if bias != 'bearish' else None

    if tipo == 'resistencia':
        # Short en resistencia, salvo que el día sea claramente alcista
        return 'short' if bias != 'bullish' else None

    if tipo == 'pivote':
        # El nivel crítico funciona como bisagra:
        # precio por encima → actúa de soporte; por debajo → de resistencia
        if precio_es >= nivel:
            return 'long' if bias != 'bearish' else None
        else:
            return 'short' if bias != 'bullish' else None

    return None


def confirmar_con_vela_15min(bars_15: list, nivel: float, direccion: str) -> bool:
    """
    Confirma el setup usando la vela de 15 minutos.
    Este es el timeframe principal de Adam ("98% de mi tiempo").

    Criterios de confirmación:
    - LONG:  último cierre 15min > apertura (vela verde) Y precio por encima del nivel
    - SHORT: último cierre 15min < apertura (vela roja) Y precio por debajo del nivel

    Args:
        bars_15:   lista de barras de 15 minutos (más reciente al final)
        nivel:     nivel de precio en ES
        direccion: 'long' o 'short'

    Returns:
        True si la vela confirma el setup
    """
    if not bars_15:
        return False  # Sin datos, no confirmamos

    ultima_vela = bars_15[-1]
    close = ultima_vela['close'] * SPY_TO_ES_MULTIPLIER
    open_ = ultima_vela['open'] * SPY_TO_ES_MULTIPLIER

    if direccion == 'long':
        # Vela alcista (cierre > apertura) y precio por encima del soporte
        return close > open_ and close >= nivel

    elif direccion == 'short':
        # Vela bajista (cierre < apertura) y precio por debajo de resistencia
        return close < open_ and close <= nivel

    return False


# ─────────────────────────────────────────────
# Generación de señal con LLM
# ─────────────────────────────────────────────

SIGNAL_PROMPT = """Eres un experto en la metodología de trading de Adam Mancini en ES/SPX.
Adam opera "level to level": entra en niveles clave, stop justo al otro lado, target en el siguiente nivel.

SITUACIÓN ACTUAL:
- Precio ES: {precio_es}
- Nivel clave: {nivel}
- Dirección propuesta: {direccion}
- Bias del día: {bias}
- Nivel crítico del newsletter: {nivel_critico}
- Soportes del día: {soportes}
- Resistencias del día: {resistencias}
- Setup del newsletter: {setup}
- Invalida si: {invalida_si}

VELA 15 MINUTOS (timeframe principal de Adam):
- Open: {open_15} | High: {high_15} | Low: {low_15} | Close: {close_15}
- Confirmación técnica: {confirmacion}

SITUACIONES HISTÓRICAS SIMILARES (de newsletters pasados):
{historico}

¿Adam entraría en {direccion} aquí? Responde SOLO con JSON válido:
{{
  "entrar": true o false,
  "razon": "una frase explicando la decisión",
  "entrada_es": nivel ES de entrada (número),
  "stop_es": nivel ES de stop loss (número),
  "target1_es": primer target ES (número),
  "target2_es": segundo target ES o null,
  "confianza": valor entre 0.0 y 1.0
}}"""


def generar_señal_llm(
    precio_es: float,
    nivel: float,
    direccion: str,
    today: dict,
    bars_15: list,
    historico: list
) -> dict:
    """
    Pregunta a Claude Haiku si Adam entraría en esta situación.

    Proporciona todo el contexto disponible:
    - Mapa del día (newsletter)
    - Vela de 15 minutos actual
    - 3 situaciones históricas similares del ChromaDB

    Returns:
        Dict con entrar, razon, entrada, stop, targets, confianza
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Formatear vela 15min
    if bars_15:
        v = bars_15[-1]
        m = SPY_TO_ES_MULTIPLIER
        open_15  = v['open']  * m
        high_15  = v['high']  * m
        low_15   = v['low']   * m
        close_15 = v['close'] * m
        confirmacion = "SÍ (vela en dirección)" if confirmar_con_vela_15min(bars_15, nivel, direccion) else "NO (vela contra dirección)"
    else:
        open_15 = high_15 = low_15 = close_15 = precio_es
        confirmacion = "Sin datos de vela"

    # Formatear contexto histórico
    if historico:
        historico_str = "\n".join([
            f"- [{h['date']}] Bias:{h['bias']} | Setup: {h['setup'][:80]}"
            for h in historico[:3]
        ])
    else:
        historico_str = "No hay situaciones similares en el historial"

    prompt = SIGNAL_PROMPT.format(
        precio_es      = f"{precio_es:.1f}",
        nivel          = f"{nivel:.1f}",
        direccion      = direccion.upper(),
        bias           = today.get('bias', 'unknown'),
        nivel_critico  = today.get('nivel_critico', 'N/A'),
        soportes       = today.get('soportes', []),
        resistencias   = today.get('resistencias', []),
        setup          = (today.get('setup') or '')[:200],
        invalida_si    = (today.get('invalida_si') or 'N/A')[:100],
        open_15        = f"{open_15:.1f}",
        high_15        = f"{high_15:.1f}",
        low_15         = f"{low_15:.1f}",
        close_15       = f"{close_15:.1f}",
        confirmacion   = confirmacion,
        historico      = historico_str,
    )

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=400,
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
# Formato de alerta (placeholder Telegram)
# ─────────────────────────────────────────────

def formatear_alerta(señal: dict, precio_es: float, nivel: float, today: dict) -> str:
    """
    Formatea la señal como un mensaje de Telegram.
    Mismo formato que usaremos en la Fase 6.
    """
    # Usar la dirección real guardada en la señal, no adivinarla del texto
    es_long   = señal.get('direccion', 'long') == 'long'
    dir_emoji = '🟢' if es_long else '🔴'
    dir_texto = 'LONG' if es_long else 'SHORT'

    entrada = señal.get('entrada_es', nivel)
    stop    = señal.get('stop_es', '')
    t1      = señal.get('target1_es', '')
    t2      = señal.get('target2_es', '')
    conf    = señal.get('confianza', 0)

    # Calcular ratio R/R si tenemos todos los datos
    rr_str = ''
    if entrada and stop and t1:
        riesgo  = abs(float(entrada) - float(stop))
        reward1 = abs(float(t1) - float(entrada))
        if riesgo > 0:
            rr = reward1 / riesgo
            rr_str = f"\n📐 R/R: 1:{rr:.1f}"

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
        f"📊 Nivel clave: {nivel}\n"
        f"🧠 Bias hoy: {today.get('bias', '?').upper()}\n"
        f"💭 {señal.get('razon', '')[:120]}\n"
        f"🎯 Confianza: {conf:.0%}"
    )
    if rr_str:
        mensaje += rr_str

    return mensaje


async def enviar_alerta(mensaje: str):
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
    Motor de señales que combina todos los módulos del proyecto.
    Corre en un loop continuo durante el horario de mercado.
    """

    def __init__(self):
        self.feed       = SPYFeed()
        self.collection = get_collection()
        # Registro de últimas señales para evitar duplicados
        # {nivel: datetime del último alerta}
        self._last_signal: dict = {}

    def _esta_en_cooldown(self, nivel: float, horas: float = 1.0) -> bool:
        """
        Evita señalar el mismo nivel más de una vez por hora.
        Adam no repite entradas en el mismo nivel dentro de la misma sesión.
        """
        key = f"{nivel:.0f}"
        last = self._last_signal.get(key)
        if last is None:
            return False
        return (datetime.now() - last).total_seconds() < horas * 3600

    def _marcar_señalado(self, nivel: float):
        self._last_signal[f"{nivel:.0f}"] = datetime.now()

    async def check_once(self) -> bool:
        """
        Ejecuta un ciclo completo de comprobación del mercado.
        Returns True si se generó alguna señal.
        """
        # ── 1. Precio actual ──────────────────────────────────────────────
        snapshot = self.feed.get_snapshot()
        if not snapshot:
            return False

        precio_spy = snapshot['spy_price']
        precio_es  = snapshot['es_equivalent']
        ahora      = snapshot['timestamp'][:19]

        # ── 2. Mapa del día ───────────────────────────────────────────────
        today = load_today()
        if not today:
            return False

        niveles = get_all_levels(today)
        bias    = today.get('bias', 'unknown')

        print(f"[{ahora}] SPY:{precio_spy:.2f} ES:{precio_es:.1f} | "
              f"Bias:{bias} | Niveles:{niveles[:4]}")

       # ── 3. Comprobar cada nivel (ahora son dicts con tipo) ────────────
        señal_enviada = False  # inicializar ANTES del bucle para evitar UnboundLocalError
        for nivel_info in niveles:
            nivel = nivel_info['nivel']

            if not is_price_at_level(precio_es, nivel):
                continue
            if self._esta_en_cooldown(nivel):
                continue

            # Dirección según tipo de nivel + bias (no solo posición del precio)
            direccion = determinar_lado(precio_es, nivel_info, bias)
            if not direccion:
                continue

            # Velas de 15 minutos para confirmación técnica
            bars_15 = self.feed.get_bars(15, 5)

            # Verificar confirmación técnica (no obligatoria, pero suma)
            confirmado = confirmar_con_vela_15min(bars_15, nivel, direccion)
            print(f"  📊 {direccion.upper()} | "
                  f"Vela 15min: {'✅ confirma' if confirmado else '⚠️ no confirma'}")

            # Contexto histórico del ChromaDB
            query_text = (
                f"ES en {nivel:.0f}, precio {'en soporte' if direccion=='long' else 'en resistencia'}, "
                f"bias {bias}"
            )
            historico = query_similar(self.collection, query_text, n_results=3)

            # Consultar LLM
            print("  🤖 Consultando LLM...")
            señal = generar_señal_llm(
                precio_es, nivel, direccion, today, bars_15, historico
            )

            # Guardar la dirección real en la señal (no deducirla del texto LLM)
            señal['direccion'] = direccion


            if señal.get('entrar'):
                confianza = señal.get('confianza', 0)
                print(f"  ✅ LLM: ENTRAR ({confianza:.0%} confianza)")
                mensaje = formatear_alerta(señal, precio_es, nivel, today)
                await enviar_alerta(mensaje)
                self._marcar_señalado(nivel)  # cooldown largo: 1 hora
                señal_enviada = True
            else:
                print(f"  ❌ LLM: No entrar — {señal.get('razon', '')[:80]}")
                # Cooldown corto tras un "no": evita llamar al LLM cada 60s
                # mientras el precio flota en el mismo nivel sin confirmación.
                # Restamos 45 min → el cooldown efectivo es 15 min (= 1 vela de Adam)
                self._last_signal[f"{nivel:.0f}"] = (
                    datetime.now() - timedelta(minutes=45)
                )

        return señal_enviada

    async def run_loop(self, interval_seconds: int = 60):
        """
        Loop principal: comprueba el mercado cada 'interval_seconds'.
        Fuera de mercado espera eficientemente.
        """
        print("=" * 55)
        print("  Bot Adam Mancini — Motor de Señales")
        print("=" * 55)
        print(f"⏱️  Intervalo: {interval_seconds}s | Tolerancia: ±{LEVEL_TOLERANCE_POINTS}pts ES")
        print("Ctrl+C para parar\n")

        while True:
            if not is_market_open():
                tz = pytz.timezone(MARKET_TIMEZONE)
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
