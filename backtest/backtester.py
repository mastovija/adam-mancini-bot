"""
backtest/backtester.py — Simula el bot sobre datos históricos
=============================================================
Mide el edge del bot usando FORWARD RETURNS, no comparación con tweets.

La métrica real de edge: cuando el bot detecta un Failed Breakdown en un
nivel de Adam, ¿cuántos puntos ES sube en los siguientes 15/30/60 min?
Si el promedio es positivo y >55% de señales son positivas, el bot tiene edge.

Esta métrica es más honesta que "matches con Adam" porque:
- Adam no publica sus trades en ningún sitio
- Los tweets son una proxy muy ruidosa e incompleta
- El forward return mide directamente si el timing es correcto

USO:
    python backtest/backtester.py

OUTPUT:
    - Análisis de edge con retornos a +15, +30 y +60 minutos
    - Reporte JSON en data/historical/backtest_report.json
    - CSV con todas las señales en data/historical/backtest_signals.csv
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
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

# Solo analizar niveles dentro de ±100 pts del precio mediano del día.
RANGO_NIVELES_DIA = 100  # puntos ES

# Mínimo flush para considerar un FB real.
# 3 pts causaba 69/70 días positivos (ruido puro).
# La secuencia correcta además debe ser: arriba → flush → recovery.
MIN_FLUSH_PTS = 10.0


# ─────────────────────────────────────────────
# Cargar datos históricos
# ─────────────────────────────────────────────

def load_spy_data() -> pd.DataFrame:
    """Carga las barras de 15 minutos de SPY, con fallback a JSON de 1-min."""
    if SPY_15MIN_FILE.exists():
        df = pd.read_csv(SPY_15MIN_FILE)
    else:
        print("⚠️ spy_15min.csv no existe. Intentando cargar desde JSON 1-min...")
        df = load_spy_data_from_json()
        if df is None or df.empty:
            print("❌ No hay datos históricos. Ejecuta primero:")
            print("   python backtest/download_data.py")
            sys.exit(1)
        df = resample_spy_to_15min(df)

    df['timestamp'] = pd.to_datetime(df['timestamp'])
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
    """Convierte datos de 1-min a barras de 15 minutos."""
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
    """Carga todos los newsletters procesados indexados por fecha."""
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
    """Carga todos los tweets de Adam indexados por fecha (contexto, no métrica)."""
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
            dt    = datetime.strptime(created, '%a %b %d %H:%M:%S +0000 %Y')
            fecha = dt.strftime('%Y-%m-%d')
            tweets_by_date[fecha].append(tweet)
        except Exception:
            continue

    return dict(tweets_by_date)


# ─────────────────────────────────────────────
# Extracción de niveles
# ─────────────────────────────────────────────

def get_levels_from_newsletter(newsletter: dict) -> list:
    """
    Extrae los niveles del newsletter conservando el tipo.
    Returns: [{'nivel': float, 'tipo': 'soporte'|'resistencia'|'pivote'}, ...]
    """
    trading_info = newsletter.get('trading_info', newsletter)
    niveles = []

    for n in trading_info.get('soportes', []) or []:
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'soporte'})

    for n in trading_info.get('resistencias', []) or []:
        if n:
            niveles.append({'nivel': float(n), 'tipo': 'resistencia'})

    nc = trading_info.get('nivel_critico')
    if nc:
        nc = float(nc)
        if not any(abs(x['nivel'] - nc) < 0.5 for x in niveles):
            niveles.append({'nivel': nc, 'tipo': 'pivote'})

    return sorted(niveles, key=lambda x: x['nivel'], reverse=True)


# ─────────────────────────────────────────────
# Detección de Failed Breakdown
# ─────────────────────────────────────────────

def detect_fb_historical(bars_day: pd.DataFrame, bar_idx: int,
                          nivel_es: float) -> dict:
    """
    Detecta un Failed Breakdown REAL verificando la secuencia correcta.

    Un FB válido requiere estas tres condiciones EN ORDEN cronológico:
      1. Precio estaba ENCIMA del nivel (+5 pts al menos)  ← enfoque/approach
      2. Flush por DEBAJO del nivel (al menos MIN_FLUSH_PTS pts)
      3. Precio actual recuperado ENCIMA del nivel         ← recovery

    Sin el check de orden (v. anterior) detectábamos 69/70 días porque
    cualquier fluctuación de 3 pts satisfacía las condiciones sin importar
    si el precio venía de arriba o de abajo. Ahora exigimos la secuencia
    completa arriba→flush→recovery, igual que el setup real de Adam.

    Args:
        bars_day: DataFrame del día con columnas es_close, es_low, es_high
        bar_idx:  índice de la barra actual (la "recovery")
        nivel_es: nivel de soporte en puntos ES

    Returns:
        dict con 'es_fb', 'flush_size', 'entry_price'
    """
    bars = bars_day.reset_index(drop=True)

    if bar_idx >= len(bars):
        return {'es_fb': False, 'flush_size': 0, 'entry_price': 0}

    precio_actual = bars.iloc[bar_idx]['es_close']

    # Condición 3: precio actual debe estar ENCIMA del nivel (recovery completada)
    if precio_actual <= nivel_es:
        return {'es_fb': False, 'flush_size': 0, 'entry_price': precio_actual}

    # Analizar la ventana de las últimas 8 barras buscando la secuencia arriba→flush
    # Las barras están en orden cronológico (oldest → newest)
    inicio  = max(0, bar_idx - 8)
    ventana = bars.iloc[inicio:bar_idx]

    was_above  = False  # Paso 1: precio estuvo claramente encima del nivel
    flush_size = 0

    for _, bar in ventana.iterrows():
        close_es = bar['es_close']
        low_es   = bar['es_low']

        # Paso 1: buscar que el precio estuvo al menos 5 pts por encima del nivel
        if not was_above:
            if close_es > nivel_es + 5:
                was_above = True
            continue  # seguir buscando aunque ya encontráramos el above

        # Paso 2: después de estar encima, buscar flush por debajo de MIN_FLUSH_PTS
        # (solo lo buscamos DESPUÉS de haber visto el precio arriba)
        if was_above and low_es < nivel_es - MIN_FLUSH_PTS:
            flush_size = round(nivel_es - low_es, 1)
            # Tenemos los 3 pasos: above → flush → recovery (precio_actual > nivel)
            return {
                'es_fb':       True,
                'flush_size':  flush_size,
                'entry_price': precio_actual,
            }

    return {'es_fb': False, 'flush_size': 0, 'entry_price': precio_actual}


# ─────────────────────────────────────────────
# Medición de retornos
# ─────────────────────────────────────────────

def measure_forward_returns(bars_day: pd.DataFrame, bar_idx: int,
                             entry_price: float) -> dict:
    """
    Mide el retorno en puntos ES en las N barras siguientes a la señal.

    1 barra = 15 min → ret_15min = cierre siguiente - entrada
    Positivo = ES subió (señal long correcta).
    """
    bars = bars_day.reset_index(drop=True)

    def get_return(offset: int) -> float | None:
        idx = bar_idx + offset
        if idx >= len(bars):
            return None
        return round(bars.iloc[idx]['es_close'] - entry_price, 1)

    return {
        'ret_15min': get_return(1),
        'ret_30min': get_return(2),
        'ret_60min': get_return(4),
    }


# ─────────────────────────────────────────────
# Análisis de tweets (contexto, no métrica)
# ─────────────────────────────────────────────

LONG_KEYWORDS = ['long here', 'long entry', 'long on', 'buying', 'long trigger',
                 'trap here', 'long setup', 'long this']


def count_adam_tweets(tweets: list) -> int:
    """Cuenta tweets de Adam que parecen entradas accionables (contexto informativo)."""
    return sum(
        1 for t in tweets
        if any(kw in t.get('text', '').lower() for kw in LONG_KEYWORDS)
    )


# ─────────────────────────────────────────────
# Reporte de edge
# ─────────────────────────────────────────────

def print_edge_report(señales: list):
    """
    Calcula y muestra las métricas de edge del bot.

    Métrica clave: cuando detectamos un FB real en un nivel de Adam,
    ¿sube ES de media en los siguientes 15/30/60 minutos?
    """
    if not señales:
        print("Sin señales para analizar.")
        return

    fb_señales = [s for s in señales if s.get('es_fb')]
    no_fb      = [s for s in señales if not s.get('es_fb')]

    print(f"\n{'='*60}")
    print(f"  ANÁLISIS DE EDGE — FORWARD RETURNS")
    print(f"{'='*60}")
    print(f"\n📊 Total señales:         {len(señales)}")
    print(f"   Con Failed Breakdown:  {len(fb_señales)}")
    print(f"   Sin FB (referencia):   {len(no_fb)}")

    for nombre, grupo in [("CON Failed Breakdown", fb_señales),
                           ("SIN Failed Breakdown (referencia)", no_fb)]:
        if not grupo:
            continue

        print(f"\n{'─'*45}")
        print(f"  {nombre} ({len(grupo)} señales)")
        print(f"{'─'*45}")

        for ventana, campo in [("15 min", 'ret_15min'),
                                ("30 min", 'ret_30min'),
                                ("60 min", 'ret_60min')]:
            valores = [s[campo] for s in grupo if s.get(campo) is not None]
            if not valores:
                continue

            media     = sum(valores) / len(valores)
            positivos = sum(1 for v in valores if v > 0)
            pct_pos   = positivos / len(valores) * 100
            signo     = '🟢' if media > 0 else '🔴'

            print(f"  {signo} +{ventana}:  media {media:+.1f} pts | "
                  f"{pct_pos:.0f}% positivos ({positivos}/{len(valores)})")

        if 'CON' in nombre:
            flushes = [s['flush_size'] for s in grupo if s.get('flush_size', 0) > 0]
            if flushes:
                print(f"\n  📐 Flush medio: {sum(flushes)/len(flushes):.1f} pts ES")
                print(f"     Rango: {min(flushes):.0f} – {max(flushes):.0f} pts")

    # Top 5 mejores por retorno a 60 min
    mejores = sorted(
        [s for s in señales if s.get('ret_60min') is not None],
        key=lambda x: x['ret_60min'], reverse=True
    )[:5]
    if mejores:
        print(f"\n🏆 Top 5 señales (por retorno a 60 min):")
        for s in mejores:
            fb_tag = '✅ FB' if s.get('es_fb') else '⚠️   '
            print(f"   {fb_tag} {s['fecha']} nivel {s['nivel_es']:.0f} "
                  f"flush:{s.get('flush_size', 0):.0f}pts | "
                  f"+15:{s.get('ret_15min', 0):+.0f} "
                  f"+30:{s.get('ret_30min', 0):+.0f} "
                  f"+60:{s.get('ret_60min', 0):+.0f} pts")

    # Interpretación automática
    all_ret30 = [s['ret_30min'] for s in fb_señales if s.get('ret_30min') is not None]
    if all_ret30:
        media_global   = sum(all_ret30) / len(all_ret30)
        pct_pos_global = sum(1 for v in all_ret30 if v > 0) / len(all_ret30) * 100
        print(f"\n{'='*60}")
        if media_global > 2 and pct_pos_global >= 60:
            print(f"🎉 EDGE CONFIRMADO: +{media_global:.1f} pts media a 30min, "
                  f"{pct_pos_global:.0f}% positivos — el bot tiene edge real.")
        elif media_global > 0 and pct_pos_global >= 55:
            print(f"✅ Edge moderado: +{media_global:.1f} pts media a 30min, "
                  f"{pct_pos_global:.0f}% positivos — monitoriza más sesiones.")
        elif media_global > 0:
            print(f"⚠️  Edge débil: +{media_global:.1f} pts media a 30min, "
                  f"{pct_pos_global:.0f}% positivos — ajustar parámetros.")
        else:
            print(f"❌ Sin edge detectado: {media_global:.1f} pts media a 30min.")
            print(f"   Revisar calidad de niveles o parámetros de detección.")


# ─────────────────────────────────────────────
# Backtesting principal
# ─────────────────────────────────────────────

def run_backtest():
    """
    Ejecuta el backtest completo midiendo forward returns.

    Para cada día de trading con newsletter disponible:
    1. Extrae niveles de soporte del newsletter (contenido completo)
    2. Filtra a los ±100 pts del precio mediano del día
    3. Escanea barra a barra buscando FBs reales (secuencia arriba→flush→recovery)
    4. Mide retornos a +15, +30 y +60 minutos
    5. Reporta si el bot tiene edge estadístico real
    """
    print("=" * 60)
    print("  Bot Adam Mancini — Backtesting Fase 7")
    print("=" * 60)

    print("\n📥 Cargando datos...")
    df_spy         = load_spy_data()
    newsletters    = load_newsletters_by_date()
    tweets_by_date = load_tweets_by_date()

    print(f"   SPY 15min:   {len(df_spy):,} barras")
    print(f"   Newsletters: {len(newsletters)} días")
    print(f"   Tweets:      {sum(len(v) for v in tweets_by_date.values())} tweets "
          f"en {len(tweets_by_date)} días")

    dias_trading = sorted(df_spy['date'].unique())
    dias_con_nl  = [d for d in dias_trading if d in newsletters]

    print(f"\n📅 Días para backtest: {len(dias_con_nl)} "
          f"({dias_con_nl[0] if dias_con_nl else '?'} → "
          f"{dias_con_nl[-1] if dias_con_nl else '?'})")
    print(f"   MIN_FLUSH_PTS: {MIN_FLUSH_PTS} pts")
    print(f"   RANGO_NIVELES: ±{RANGO_NIVELES_DIA} pts del precio mediano")

    if not dias_con_nl:
        print("❌ Sin días para testear. Revisa los datos.")
        return

    print("\n🔄 Simulando...\n")

    señales_generadas = []

    for fecha in dias_con_nl:
        bars_day   = df_spy[df_spy['date'] == fecha].copy().reset_index(drop=True)
        newsletter = newsletters[fecha]
        bias       = (newsletter.get('trading_info', newsletter)).get('bias', 'unknown')

        niveles = get_levels_from_newsletter(newsletter)
        if not niveles:
            continue

        precio_mediano = float(bars_day['es_close'].median())

        # Solo usar niveles dentro del rango Y solo los más importantes:
        # el nivel crítico del día + los 3 soportes más cercanos al precio.
        # Esto replica la selección real de Adam, que dice "bid direct" en
        # 2-4 niveles clave, no en todos los que lista para contexto.
        candidatos = [
            n for n in niveles
            if abs(n['nivel'] - precio_mediano) <= RANGO_NIVELES_DIA
            and n['tipo'] != 'resistencia'
        ]
        # Ordenar por cercanía al precio mediano y tomar los 3 más cercanos
        niveles_relevantes = sorted(
            candidatos,
            key=lambda x: abs(x['nivel'] - precio_mediano)
        )[:3]
        if not niveles_relevantes:
            continue

        tweets_dia  = tweets_by_date.get(fecha, [])
        adam_tweets = count_adam_tweets(tweets_dia)

        señal_dia_registrada = False

        for bar_idx in range(8, len(bars_day)):
            if señal_dia_registrada:
                break

            for nivel_info in niveles_relevantes:
                nivel = nivel_info['nivel']

                fb = detect_fb_historical(bars_day, bar_idx, nivel)
                if not fb['es_fb']:
                    continue

                entry_price = fb['entry_price']
                returns     = measure_forward_returns(bars_day, bar_idx, entry_price)

                señal = {
                    'fecha':       fecha,
                    'nivel_es':    float(nivel),
                    'tipo_nivel':  nivel_info['tipo'],
                    'bias':        bias,
                    'price_es':    float(entry_price),
                    'timestamp':   str(bars_day.iloc[bar_idx]['timestamp']),
                    'es_fb':       True,
                    'flush_size':  fb['flush_size'],
                    'adam_tweets': adam_tweets,
                    **returns,
                }
                señales_generadas.append(señal)
                señal_dia_registrada = True
                break

    # ── Reporte ───────────────────────────────────────────────────────────
    total_dias     = len(dias_con_nl)
    total_señales  = len(señales_generadas)
    dias_sin_señal = total_dias - total_señales

    print("=" * 60)
    print("  REPORTE DE BACKTESTING")
    print("=" * 60)
    print(f"\n📅 Período:             {dias_con_nl[0]} → {dias_con_nl[-1]}")
    print(f"📊 Días testeados:      {total_dias}")
    print(f"📊 Días con FB señal:   {total_señales} "
          f"({total_señales/total_dias*100:.0f}% de los días)")
    print(f"📊 Días sin FB (chop):  {dias_sin_señal}")

    print_edge_report(señales_generadas)

    if señales_generadas:
        print("\n📍 Top niveles con FB detectado:")
        contador = Counter(int(s['nivel_es']) for s in señales_generadas)
        for nivel, count in contador.most_common(5):
            rets = [s.get('ret_30min') for s in señales_generadas
                    if int(s['nivel_es']) == nivel and s.get('ret_30min') is not None]
            media = sum(rets) / len(rets) if rets else 0
            signo = '🟢' if media > 0 else '🔴'
            print(f"   {signo} {nivel}: {count}x | ret medio 30min: {media:+.1f} pts")

        print("\n📋 Últimas 5 señales:")
        for s in señales_generadas[-5:]:
            r15 = f"{s['ret_15min']:+.0f}" if s.get('ret_15min') is not None else '?'
            r30 = f"{s['ret_30min']:+.0f}" if s.get('ret_30min') is not None else '?'
            r60 = f"{s['ret_60min']:+.0f}" if s.get('ret_60min') is not None else '?'
            tw  = f" tweets:{s['adam_tweets']}" if s.get('adam_tweets') else ''
            print(f"   {s['fecha']} nivel {s['nivel_es']:.0f} "
                  f"flush:{s['flush_size']:.0f}pts | "
                  f"+15:{r15} +30:{r30} +60:{r60}{tw}")

    # ── Guardar ───────────────────────────────────────────────────────────
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        'periodo':      f"{dias_con_nl[0]} → {dias_con_nl[-1]}",
        'total_dias':   total_dias,
        'dias_con_fb':  total_señales,
        'dias_sin_fb':  dias_sin_señal,
        'min_flush':    MIN_FLUSH_PTS,
        'senales':      señales_generadas,
    }
    with open(REPORT_FILE, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    if señales_generadas:
        pd.DataFrame(señales_generadas).to_csv(SIGNALS_FILE, index=False)

    print(f"\n💾 Reporte: {REPORT_FILE}")
    print(f"💾 CSV:     {SIGNALS_FILE}")
    print("=" * 60)


if __name__ == '__main__':
    run_backtest()
