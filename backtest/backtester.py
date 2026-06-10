"""
backtest/backtester.py — Simula el bot sobre datos históricos
=============================================================
Reproduce cada día de trading del período, aplica la lógica del
motor de señales sobre precios históricos de SPY, y compara las
señales generadas con lo que Adam realmente hizo ese día (tweets).

USO:
    python backtest/backtester.py

OUTPUT:
    - Resumen en consola con métricas de precisión
    - Reporte detallado en data/historical/backtest_report.json
    - CSV con todas las señales en data/historical/backtest_signals.csv

MÉTRICAS CLAVE:
    - Match rate: % de señales que coinciden con la dirección de Adam
    - False positives: señales cuando Adam no operó
    - False negatives: Adam operó pero no generamos señal
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from config import (
    DATA_DIR,
    PROCESSED_DIR,
    TWEETS_DIR,
    SPY_TO_ES_MULTIPLIER,
    LEVEL_TOLERANCE_POINTS,
)


# ─────────────────────────────────────────────
# Rutas
# ─────────────────────────────────────────────
HISTORICAL_DIR    = DATA_DIR / 'historical'
BACKTEST_BARS_DIR = DATA_DIR / 'backtest' / 'spy_bars'
SPY_15MIN_FILE    = HISTORICAL_DIR / 'spy_15min.csv'
REPORT_FILE       = HISTORICAL_DIR / 'backtest_report.json'
SIGNALS_FILE      = HISTORICAL_DIR / 'backtest_signals.csv'


# ─────────────────────────────────────────────
# Cargar datos históricos
# ─────────────────────────────────────────────

def load_spy_data() -> pd.DataFrame:
    """Carga las barras de 15 minutos de SPY descargadas, con fallback a JSON de 1-min."""
    if SPY_15MIN_FILE.exists():
        df = pd.read_csv(SPY_15MIN_FILE)
    else:
        print("⚠️ spy_15min.csv no existe. Intentando cargar datos desde JSON 1-min...")
        df = load_spy_data_from_json()
        if df is None or df.empty:
            print("❌ No hay datos históricos. Ejecuta primero:")
            print("   python backtest/download_data.py")
            sys.exit(1)
        df = resample_spy_to_15min(df)

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    # Normalizar a timezone-naive para evitar errores de comparación
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df['date']      = df['timestamp'].dt.date.astype(str)
    df['es_close']  = df['close'] * SPY_TO_ES_MULTIPLIER
    df['es_high']   = df['high']  * SPY_TO_ES_MULTIPLIER
    df['es_low']    = df['low']   * SPY_TO_ES_MULTIPLIER
    return df


def load_spy_data_from_json() -> pd.DataFrame:
    """Carga los archivos JSON diarios de 1-min desde data/backtest/spy_bars."""
    if not BACKTEST_BARS_DIR.exists():
        return pd.DataFrame()

    rows = []
    for path in sorted(BACKTEST_BARS_DIR.glob('*.json')):
        if path.name == 'index.json':
            continue
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        for row in data:
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df.sort_values('timestamp').reset_index(drop=True)


def resample_spy_to_15min(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte datos 1-min a barras de 15 minutos."""
    df = df.set_index('timestamp')
    df = df.resample('15min', label='left', closed='left').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    )
    df = df.dropna(subset=['open']).reset_index()
    return df


def load_newsletters_by_date() -> dict:
    """
    Carga todos los newsletters procesados indexados por fecha.
    Busca primero en data/processed/ (con trading_info) y luego en raw/.
    Returns: {fecha_str: newsletter_dict}
    """
    newsletters = {}

    for f in PROCESSED_DIR.glob('*.json'):
        try:
            data = json.load(open(f, encoding='utf-8'))
            fecha = (
                data.get('published_at') or
                data.get('date') or
                data.get('post_date', '')
            )
            if fecha and len(fecha) >= 10:
                fecha = fecha[:10]
                if fecha not in newsletters:
                    newsletters[fecha] = data
        except Exception:
            continue

    return newsletters


def load_tweets_by_date() -> dict:
    """
    Carga todos los tweets indexados por fecha (YYYY-MM-DD).
    Returns: {fecha_str: [tweet1, tweet2, ...]}
    """
    tweets_by_date = defaultdict(list)
    tweets_file    = TWEETS_DIR / 'adam_mancini_tweets.json'

    if not tweets_file.exists():
        return {}

    with open(tweets_file, encoding='utf-8') as f:
        tweets = json.load(f)

    for tweet in tweets:
        created = tweet.get('created_at', '')
        if not created or tweet.get('is_retweet'):
            continue
        try:
            dt = datetime.strptime(created, '%a %b %d %H:%M:%S +0000 %Y')
            fecha = dt.strftime('%Y-%m-%d')
            tweets_by_date[fecha].append(tweet)
        except Exception:
            continue

    return dict(tweets_by_date)


# ─────────────────────────────────────────────
# Lógica de señal simplificada (sin LLM)
# ─────────────────────────────────────────────

def get_levels_from_newsletter(newsletter: dict) -> list:
    """
    Extrae los niveles del newsletter CONSERVANDO el tipo (soporte/resistencia).
    
    Devuelve lista de dicts: {'nivel': float, 'tipo': 'soporte'|'resistencia'|'pivote'}
    Igual que get_all_levels() en signal_engine.py para que el backtest
    mida exactamente la misma lógica que corre en producción.
    """
    trading_info = newsletter.get('trading_info', newsletter)
    niveles = []

    # Soportes → long
    for n in trading_info.get('soportes', []) or []:
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'soporte'})

    # Resistencias → short
    for n in trading_info.get('resistencias', []) or []:
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'resistencia'})

    # Nivel crítico como pivote (si no está ya en soportes/resistencias)
    nc = trading_info.get('nivel_critico')
    if nc:
        nc = float(nc)
        if not any(abs(x['nivel'] - nc) < 0.5 for x in niveles):
            niveles.append({'nivel': nc, 'tipo': 'pivote'})

    return sorted(niveles, key=lambda x: x['nivel'], reverse=True)


def check_level_touch(bars_day: pd.DataFrame, nivel_es: float,
                      tolerancia: float = None) -> list:
    """
    Detecta cuándo el precio ES toca o pasa cerca de un nivel.
    Returns: lista de dicts con timestamp y tipo de toque
    """
    tol    = tolerancia or LEVEL_TOLERANCE_POINTS
    toques = []

    for _, bar in bars_day.iterrows():
        if abs(bar['es_close'] - nivel_es) <= tol:
            # Determinar dirección probable: ¿venía de arriba o de abajo?
            if bar['es_close'] <= nivel_es:
                tipo = 'at_support'  # precio en soporte → posible long
            else:
                tipo = 'at_resistance'  # precio en resistencia → posible short

            toques.append({
                'timestamp': str(bar['timestamp']),
                'price_es':  round(bar['es_close'], 1),
                'nivel_es':  nivel_es,
                'tipo':      tipo,
            })

    return toques[:1]  # Solo el primer toque del día (como haría Adam)


def candle_confirms(bars_day: pd.DataFrame, timestamp_str: str,
                    direction: str) -> bool:
    """
    Verifica si la vela de 15 minutos SIGUIENTE confirma la dirección.

    FIX: bars_day conserva el índice del DataFrame global (ej. filas 750-780),
    así que mezclar .index (etiquetas) con posiciones rompía la lógica y
    devolvía False siempre. Solución: trabajar con posiciones reales
    usando reset_index() al principio.
    """
    try:
        # Reseteamos el índice → ahora las posiciones van de 0 a N-1
        bars = bars_day.reset_index(drop=True)

        ts  = pd.Timestamp(timestamp_str)
        idx = bars.index[bars['timestamp'] == ts]   # posición real dentro del día

        # Si no encontramos la vela, o es la última del día (no hay "siguiente")
        if len(idx) == 0 or idx[0] + 1 >= len(bars):
            return False

        # La vela SIGUIENTE al toque del nivel
        next_bar = bars.iloc[idx[0] + 1]

        if direction == 'long':
            # bool() convierte numpy.bool_ a bool nativo — necesario para json.dump
            return bool(next_bar['close'] > next_bar['open'])
        else:
            return bool(next_bar['close'] < next_bar['open'])
    except Exception:
        return False


# ─────────────────────────────────────────────
# Análisis de tweets de Adam
# ─────────────────────────────────────────────

# Patrones para detectar operaciones en tweets de Adam
LONG_KEYWORDS  = ['long here', 'long entry', 'long on', 'buying', 'long trigger',
                   'trap here', 'long setup', 'long this']
SHORT_KEYWORDS = ['short here', 'short entry', 'short on', 'selling', 'short trigger',
                  'short setup', 'sell here', 'short this']

def extract_adam_trades_from_tweets(tweets: list) -> list:
    """
    Extrae operaciones concretas de los tweets de Adam para un día.
    Busca menciones explícitas de 'long' o 'short' con niveles de precio.

    Returns: lista de dicts con direction y nivel
    """
    operaciones = []

    for tweet in tweets:
        texto = tweet.get('text', '').lower()

        # Detectar dirección
        es_long  = any(kw in texto for kw in LONG_KEYWORDS)
        es_short = any(kw in texto for kw in SHORT_KEYWORDS)

        if not es_long and not es_short:
            continue

        # Extraer niveles mencionados
        numeros = re.findall(r'\b(\d{4,5}(?:\.\d+)?)\b', tweet.get('text', ''))
        niveles = [float(n) for n in numeros if 3000 <= float(n) <= 10000]

        if not niveles:
            continue

        operaciones.append({
            'direction': 'long' if es_long else 'short',
            'niveles':   niveles,
            'texto':     tweet.get('text', '')[:100],
        })

    return operaciones


def signals_match(nuestra_señal: dict, adam_trades: list,
                  tolerancia_nivel: float = 15.0) -> bool:
    """
    Comprueba si nuestra señal coincide con alguna operación de Adam.
    Criterio: misma dirección Y nivel dentro de tolerancia.
    """
    if not adam_trades:
        return False

    for trade in adam_trades:
        if trade['direction'] != nuestra_señal['direction']:
            continue

        for nivel_adam in trade['niveles']:
            if abs(nivel_adam - nuestra_señal['nivel_es']) <= tolerancia_nivel:
                return True

    return False


# ─────────────────────────────────────────────
# Backtesting principal
# ─────────────────────────────────────────────

def run_backtest():
    """
    Ejecuta el backtest completo:
    1. Para cada día de trading con newsletter disponible
    2. Detecta si el precio tocó algún nivel de Adam
    3. Compara con los tweets reales de Adam ese día
    4. Genera métricas de precisión
    """
    print("=" * 60)
    print("  Bot Adam Mancini — Backtesting Fase 7")
    print("=" * 60)

    # ── Cargar datos ──────────────────────────────────────────────────────
    print("\n📥 Cargando datos...")
    df_spy         = load_spy_data()
    newsletters    = load_newsletters_by_date()
    tweets_by_date = load_tweets_by_date()

    print(f"   SPY 15min:   {len(df_spy):,} barras")
    print(f"   Newsletters: {len(newsletters)} días")
    print(f"   Tweets:      {sum(len(v) for v in tweets_by_date.values())} tweets "
          f"en {len(tweets_by_date)} días")

    # ── Obtener días de trading disponibles ───────────────────────────────
    dias_trading = sorted(df_spy['date'].unique())
    dias_con_nl  = [d for d in dias_trading if d in newsletters]

    print(f"\n📅 Días para backtest: {len(dias_con_nl)} "
          f"({dias_con_nl[0] if dias_con_nl else '?'} → "
          f"{dias_con_nl[-1] if dias_con_nl else '?'})")

    if not dias_con_nl:
        print("❌ Sin días para testear. Revisa los datos.")
        return

    # ── Bucle principal ───────────────────────────────────────────────────
    print("\n🔄 Simulando...\n")

    señales_generadas = []
    dias_sin_señal    = []

    for fecha in dias_con_nl:
        bars_day   = df_spy[df_spy['date'] == fecha].copy()
        newsletter = newsletters[fecha]
        tweets_dia = tweets_by_date.get(fecha, [])

        # Niveles del newsletter
        niveles = get_levels_from_newsletter(newsletter)
        bias    = (newsletter.get('trading_info', newsletter)).get('bias', 'unknown')

        if not niveles:
            continue

        # Operaciones reales de Adam ese día
        adam_trades = extract_adam_trades_from_tweets(tweets_dia)

        señal_dia = None

        # Comprobar cada nivel (ahora nivel_info es {'nivel': float, 'tipo': str})
        for nivel_info in niveles:
            nivel = nivel_info['nivel']
            tipo  = nivel_info['tipo']

            toques = check_level_touch(bars_day, nivel)
            if not toques:
                continue

            toque = toques[0]

            # La dirección viene del TIPO de nivel, no de la posición del precio.
            # Mismo criterio que signal_engine.py → backtest y producción son consistentes.
            if tipo == 'soporte':
                direction = 'long'
            elif tipo == 'resistencia':
                direction = 'short'
            else:  # pivote
                direction = 'long' if toque['price_es'] >= nivel else 'short'

            # Ajustar por bias del día
            if bias == 'bullish' and direction == 'short':
                continue
            if bias == 'bearish' and direction == 'long':
                continue

            # Confirmar con siguiente vela 15min
            confirmado = candle_confirms(bars_day, toque['timestamp'], direction)

            señal_dia = {
                'fecha':       fecha,
                'nivel_es':    float(nivel),      # numpy.float64 → float nativo
                'direction':   direction,
                'bias':        bias,
                'price_es':    float(toque['price_es']),  # ídem
                'timestamp':   toque['timestamp'],
                'confirmada':  bool(confirmado),   # numpy.bool_ → bool nativo
                'adam_trades': len(adam_trades),
                'match':       False,
            }
            break  # Solo la primera señal del día

        if señal_dia:
            # Verificar si coincide con Adam
            señal_dia['match'] = signals_match(señal_dia, adam_trades)
            señales_generadas.append(señal_dia)
        else:
            dias_sin_señal.append({
                'fecha':       fecha,
                'adam_trades': len(adam_trades),
                'niveles':     niveles[:3],
            })

    # ── Calcular métricas ─────────────────────────────────────────────────
    total_dias      = len(dias_con_nl)
    total_señales   = len(señales_generadas)
    señales_match   = sum(1 for s in señales_generadas if s['match'])
    dias_adam_opero = len([d for d in (señales_generadas + dias_sin_señal)
                           if (d.get('adam_trades', 0) > 0)])

    # Días con tweets de Adam donde no generamos señal (falsos negativos)
    falsos_neg = sum(1 for d in dias_sin_señal if d['adam_trades'] > 0)

    # Señales nuestras donde Adam no operó (falsos positivos)
    falsos_pos = sum(1 for s in señales_generadas
                     if s['adam_trades'] == 0 and not s['match'])

    match_rate = señales_match / total_señales * 100 if total_señales > 0 else 0

    # ── Imprimir reporte ──────────────────────────────────────────────────
    print("=" * 60)
    print("  REPORTE DE BACKTESTING")
    print("=" * 60)
    print(f"\n📅 Período:            {dias_con_nl[0]} → {dias_con_nl[-1]}")
    print(f"📊 Días testeados:     {total_dias}")
    print(f"📊 Señales generadas:  {total_señales}")
    print(f"📊 Días Adam operó:    {dias_adam_opero}")
    print()
    print(f"✅ Matches con Adam:   {señales_match}/{total_señales} "
          f"({match_rate:.1f}%)")
    print(f"⚠️  Falsos positivos:   {falsos_pos} "
          f"(señalamos, Adam no operó)")
    print(f"⚠️  Falsos negativos:   {falsos_neg} "
          f"(Adam operó, no lo detectamos)")

    # Desglose por dirección
    longs  = [s for s in señales_generadas if s['direction'] == 'long']
    shorts = [s for s in señales_generadas if s['direction'] == 'short']

    if longs:
        long_match = sum(1 for s in longs if s['match'])
        print(f"\n🟢 LONG:  {len(longs)} señales, "
              f"{long_match} matches ({long_match/len(longs)*100:.1f}%)")
    if shorts:
        short_match = sum(1 for s in shorts if s['match'])
        print(f"🔴 SHORT: {len(shorts)} señales, "
              f"{short_match} matches ({short_match/len(shorts)*100:.1f}%)")

    # Top niveles
    print("\n📍 Top niveles detectados:")
    from collections import Counter
    contador_niveles = Counter(int(s['nivel_es']) for s in señales_generadas)
    for nivel, count in contador_niveles.most_common(5):
        matches_nivel = sum(1 for s in señales_generadas
                           if int(s['nivel_es']) == nivel and s['match'])
        print(f"   {nivel}: {count} señales, {matches_nivel} matches")

    # Últimas 5 señales generadas
    print("\n📋 Últimas 5 señales:")
    for s in señales_generadas[-5:]:
        emoji = '✅' if s['match'] else '❌'
        print(f"   {emoji} {s['fecha']} {s['direction'].upper():5s} "
              f"en {s['nivel_es']:.0f} | "
              f"{'confirma' if s['confirmada'] else 'no confirma'} | "
              f"Adam tweets: {s['adam_trades']}")

    # ── Guardar resultados ────────────────────────────────────────────────
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)

    # JSON detallado
    report = {
        'periodo':          f"{dias_con_nl[0]} → {dias_con_nl[-1]}",
        'total_dias':       total_dias,
        'señales_generadas': total_señales,
        'matches':          señales_match,
        'match_rate':       round(match_rate, 1),
        'falsos_positivos': falsos_pos,
        'falsos_negativos': falsos_neg,
        'señales':          señales_generadas,
    }
    with open(REPORT_FILE, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # CSV de señales
    if señales_generadas:
        pd.DataFrame(señales_generadas).to_csv(SIGNALS_FILE, index=False)

    print(f"\n💾 Reporte guardado: {REPORT_FILE}")
    print(f"💾 Señales CSV:      {SIGNALS_FILE}")
    print("=" * 60)

    # Interpretación
    print()
    if match_rate >= 70:
        print("🎉 El bot replica bien la metodología de Adam.")
        print("   Match rate >70% es una base sólida para operar.")
    elif match_rate >= 50:
        print("⚠️  Match rate moderado. El bot funciona pero mejorable.")
        print("   Recomendado: ajustar tolerancia de niveles y revisar falsos negativos.")
    else:
        print("❌ Match rate bajo. Revisar la lógica de detección.")
        print("   Posibles causas: tolerancia muy ajustada, niveles incorrectos.")


if __name__ == '__main__':
    run_backtest()
