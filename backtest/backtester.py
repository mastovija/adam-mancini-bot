"""
backtest/backtester.py — Simulates the bot over historical data
=============================================================
Measures the bot's edge using FORWARD RETURNS, not comparison with tweets.

The real edge metric: when the bot detects a Failed Breakdown at one of
Adam's levels, how many ES points does it rise in the next 15/30/60 min?
If the average is positive and >55% of signals are positive, the bot has edge.

This metric is more honest than "matches with Adam" because:
- Adam doesn't publish his trades anywhere
- The tweets are a very noisy and incomplete proxy
- The forward return directly measures whether the timing is correct

USAGE:
    python backtest/backtester.py

OUTPUT:
    - Edge analysis with returns at +15, +30 and +60 minutes
    - JSON report in data/historical/backtest_report.json
    - CSV with all signals in data/historical/backtest_signals.csv
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
# Paths
# ─────────────────────────────────────────────
HISTORICAL_DIR    = DATA_DIR / 'historical'
BACKTEST_BARS_DIR = DATA_DIR / 'backtest' / 'spy_bars'
SPY_15MIN_FILE    = HISTORICAL_DIR / 'spy_15min.csv'
REPORT_FILE       = HISTORICAL_DIR / 'backtest_report.json'
SIGNALS_FILE      = HISTORICAL_DIR / 'backtest_signals.csv'

# Only analyze levels within ±100 pts of the day's median price.
RANGO_NIVELES_DIA = 100  # ES points

# Minimum flush to consider a real FB.
# 3 pts caused 69/70 positive days (pure noise).
# The correct sequence must also be: above → flush → recovery.
MIN_FLUSH_PTS = 10.0


# ─────────────────────────────────────────────
# Load historical data
# ─────────────────────────────────────────────

def load_spy_data() -> pd.DataFrame:
    """Loads the 15-minute SPY bars, falling back to 1-min JSON."""
    if SPY_15MIN_FILE.exists():
        df = pd.read_csv(SPY_15MIN_FILE)
    else:
        print("⚠️ spy_15min.csv does not exist. Trying to load from 1-min JSON...")
        df = load_spy_data_from_json()
        if df is None or df.empty:
            print("❌ No historical data. Run first:")
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
    """Loads the daily 1-min JSON files from data/backtest/spy_bars."""
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
    """Converts 1-min data to 15-minute bars."""
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
    """Loads all processed newsletters indexed by date."""
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
    """Loads all of Adam's tweets indexed by date (context, not a metric)."""
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
# Level extraction
# ─────────────────────────────────────────────

def get_levels_from_newsletter(newsletter: dict) -> list:
    """
    Extracts the levels from the newsletter, preserving the type.
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
# Failed Breakdown detection
# ─────────────────────────────────────────────

def detect_fb_historical(bars_day: pd.DataFrame, bar_idx: int,
                          nivel_es: float) -> dict:
    """
    Detects a REAL Failed Breakdown by verifying the correct sequence.

    A valid FB requires these three conditions IN chronological ORDER:
      1. Price was ABOVE the level (+5 pts at least)       ← approach
      2. Flush BELOW the level (at least MIN_FLUSH_PTS pts)
      3. Current price recovered ABOVE the level           ← recovery

    Without the order check (previous version) we detected 69/70 days because
    any 3-pt fluctuation satisfied the conditions regardless of whether the
    price came from above or below. Now we require the full
    above→flush→recovery sequence, just like Adam's real setup.

    Args:
        bars_day: DataFrame for the day with es_close, es_low, es_high columns
        bar_idx:  index of the current bar (the "recovery")
        nivel_es: support level in ES points

    Returns:
        dict with 'es_fb', 'flush_size', 'entry_price'
    """
    bars = bars_day.reset_index(drop=True)

    if bar_idx >= len(bars):
        return {'es_fb': False, 'flush_size': 0, 'entry_price': 0}

    precio_actual = bars.iloc[bar_idx]['es_close']

    # Condition 3: current price must be ABOVE the level (recovery complete)
    if precio_actual <= nivel_es:
        return {'es_fb': False, 'flush_size': 0, 'entry_price': precio_actual}

    # Analyze the window of the last 8 bars looking for the above→flush sequence
    # The bars are in chronological order (oldest → newest)
    inicio  = max(0, bar_idx - 8)
    ventana = bars.iloc[inicio:bar_idx]

    was_above  = False  # Step 1: price was clearly above the level
    flush_size = 0

    for _, bar in ventana.iterrows():
        close_es = bar['es_close']
        low_es   = bar['es_low']

        # Step 1: check that the price was at least 5 pts above the level
        if not was_above:
            if close_es > nivel_es + 5:
                was_above = True
            continue  # keep looking even after finding the above

        # Step 2: after being above, look for a flush below MIN_FLUSH_PTS
        # (we only look for it AFTER having seen the price above)
        if was_above and low_es < nivel_es - MIN_FLUSH_PTS:
            flush_size = round(nivel_es - low_es, 1)
            # We have the 3 steps: above → flush → recovery (precio_actual > nivel)
            return {
                'es_fb':       True,
                'flush_size':  flush_size,
                'entry_price': precio_actual,
            }

    return {'es_fb': False, 'flush_size': 0, 'entry_price': precio_actual}


# ─────────────────────────────────────────────
# Return measurement
# ─────────────────────────────────────────────

def measure_forward_returns(bars_day: pd.DataFrame, bar_idx: int,
                             entry_price: float) -> dict:
    """
    Measures the return in ES points over the N bars following the signal.

    1 bar = 15 min → ret_15min = next close - entry
    Positive = ES rose (a correct long signal).
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
# Tweet analysis (context, not a metric)
# ─────────────────────────────────────────────

LONG_KEYWORDS = ['long here', 'long entry', 'long on', 'buying', 'long trigger',
                 'trap here', 'long setup', 'long this']


def count_adam_tweets(tweets: list) -> int:
    """Counts Adam's tweets that look like actionable entries (informational context)."""
    return sum(
        1 for t in tweets
        if any(kw in t.get('text', '').lower() for kw in LONG_KEYWORDS)
    )


# ─────────────────────────────────────────────
# Edge report
# ─────────────────────────────────────────────

def print_edge_report(señales: list):
    """
    Computes and shows the bot's edge metrics.

    Key metric: when we detect a real FB at one of Adam's levels,
    does ES rise on average over the next 15/30/60 minutes?
    """
    if not señales:
        print("No signals to analyze.")
        return

    fb_señales = [s for s in señales if s.get('es_fb')]
    no_fb      = [s for s in señales if not s.get('es_fb')]

    print(f"\n{'='*60}")
    print(f"  EDGE ANALYSIS — FORWARD RETURNS")
    print(f"{'='*60}")
    print(f"\n📊 Total signals:         {len(señales)}")
    print(f"   With Failed Breakdown: {len(fb_señales)}")
    print(f"   Without FB (reference):{len(no_fb)}")

    for nombre, grupo in [("WITH Failed Breakdown", fb_señales),
                           ("WITHOUT Failed Breakdown (reference)", no_fb)]:
        if not grupo:
            continue

        print(f"\n{'─'*45}")
        print(f"  {nombre} ({len(grupo)} signals)")
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

            print(f"  {signo} +{ventana}:  mean {media:+.1f} pts | "
                  f"{pct_pos:.0f}% positive ({positivos}/{len(valores)})")

        if nombre.startswith('WITH '):
            flushes = [s['flush_size'] for s in grupo if s.get('flush_size', 0) > 0]
            if flushes:
                print(f"\n  📐 Mean flush: {sum(flushes)/len(flushes):.1f} pts ES")
                print(f"     Range: {min(flushes):.0f} – {max(flushes):.0f} pts")

    # Top 5 best by 60-min return
    mejores = sorted(
        [s for s in señales if s.get('ret_60min') is not None],
        key=lambda x: x['ret_60min'], reverse=True
    )[:5]
    if mejores:
        print(f"\n🏆 Top 5 signals (by 60-min return):")
        for s in mejores:
            fb_tag = '✅ FB' if s.get('es_fb') else '⚠️   '
            print(f"   {fb_tag} {s['fecha']} level {s['nivel_es']:.0f} "
                  f"flush:{s.get('flush_size', 0):.0f}pts | "
                  f"+15:{s.get('ret_15min', 0):+.0f} "
                  f"+30:{s.get('ret_30min', 0):+.0f} "
                  f"+60:{s.get('ret_60min', 0):+.0f} pts")

    # Automatic interpretation
    all_ret30 = [s['ret_30min'] for s in fb_señales if s.get('ret_30min') is not None]
    if all_ret30:
        media_global   = sum(all_ret30) / len(all_ret30)
        pct_pos_global = sum(1 for v in all_ret30 if v > 0) / len(all_ret30) * 100
        print(f"\n{'='*60}")
        if media_global > 2 and pct_pos_global >= 60:
            print(f"🎉 EDGE CONFIRMED: +{media_global:.1f} pts mean at 30min, "
                  f"{pct_pos_global:.0f}% positive — the bot has real edge.")
        elif media_global > 0 and pct_pos_global >= 55:
            print(f"✅ Moderate edge: +{media_global:.1f} pts mean at 30min, "
                  f"{pct_pos_global:.0f}% positive — monitor more sessions.")
        elif media_global > 0:
            print(f"⚠️  Weak edge: +{media_global:.1f} pts mean at 30min, "
                  f"{pct_pos_global:.0f}% positive — adjust parameters.")
        else:
            print(f"❌ No edge detected: {media_global:.1f} pts mean at 30min.")
            print(f"   Review level quality or detection parameters.")


# ─────────────────────────────────────────────
# Main backtesting
# ─────────────────────────────────────────────

def run_backtest():
    """
    Runs the full backtest measuring forward returns.

    For each trading day with an available newsletter:
    1. Extracts support levels from the newsletter (full content)
    2. Filters to ±100 pts of the day's median price
    3. Scans bar by bar looking for real FBs (above→flush→recovery sequence)
    4. Measures returns at +15, +30 and +60 minutes
    5. Reports whether the bot has real statistical edge
    """
    print("=" * 60)
    print("  Adam Mancini Bot — Backtesting Phase 7")
    print("=" * 60)

    print("\n📥 Loading data...")
    df_spy         = load_spy_data()
    newsletters    = load_newsletters_by_date()
    tweets_by_date = load_tweets_by_date()

    print(f"   SPY 15min:   {len(df_spy):,} bars")
    print(f"   Newsletters: {len(newsletters)} days")
    print(f"   Tweets:      {sum(len(v) for v in tweets_by_date.values())} tweets "
          f"across {len(tweets_by_date)} days")

    dias_trading = sorted(df_spy['date'].unique())
    dias_con_nl  = [d for d in dias_trading if d in newsletters]

    print(f"\n📅 Days for backtest: {len(dias_con_nl)} "
          f"({dias_con_nl[0] if dias_con_nl else '?'} → "
          f"{dias_con_nl[-1] if dias_con_nl else '?'})")
    print(f"   MIN_FLUSH_PTS: {MIN_FLUSH_PTS} pts")
    print(f"   RANGO_NIVELES: ±{RANGO_NIVELES_DIA} pts from median price")

    if not dias_con_nl:
        print("❌ No days to test. Check the data.")
        return

    print("\n🔄 Simulating...\n")

    señales_generadas = []

    for fecha in dias_con_nl:
        bars_day   = df_spy[df_spy['date'] == fecha].copy().reset_index(drop=True)
        newsletter = newsletters[fecha]
        bias       = (newsletter.get('trading_info', newsletter)).get('bias', 'unknown')

        niveles = get_levels_from_newsletter(newsletter)
        if not niveles:
            continue

        precio_mediano = float(bars_day['es_close'].median())

        # Only use levels within range AND only the most important ones:
        # the day's critical level + the 3 supports closest to the price.
        # This replicates Adam's real selection, who says "bid direct" on
        # 2-4 key levels, not all of the ones he lists for context.
        candidatos = [
            n for n in niveles
            if abs(n['nivel'] - precio_mediano) <= RANGO_NIVELES_DIA
            and n['tipo'] != 'resistencia'
        ]
        # Sort by closeness to the median price and take the 3 closest
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

    # ── Report ────────────────────────────────────────────────────────────
    total_dias     = len(dias_con_nl)
    total_señales  = len(señales_generadas)
    dias_sin_señal = total_dias - total_señales

    print("=" * 60)
    print("  BACKTESTING REPORT")
    print("=" * 60)
    print(f"\n📅 Period:              {dias_con_nl[0]} → {dias_con_nl[-1]}")
    print(f"📊 Days tested:         {total_dias}")
    print(f"📊 Days with FB signal: {total_señales} "
          f"({total_señales/total_dias*100:.0f}% of days)")
    print(f"📊 Days without FB (chop): {dias_sin_señal}")

    print_edge_report(señales_generadas)

    if señales_generadas:
        print("\n📍 Top levels with FB detected:")
        contador = Counter(int(s['nivel_es']) for s in señales_generadas)
        for nivel, count in contador.most_common(5):
            rets = [s.get('ret_30min') for s in señales_generadas
                    if int(s['nivel_es']) == nivel and s.get('ret_30min') is not None]
            media = sum(rets) / len(rets) if rets else 0
            signo = '🟢' if media > 0 else '🔴'
            print(f"   {signo} {nivel}: {count}x | mean 30min ret: {media:+.1f} pts")

        print("\n📋 Last 5 signals:")
        for s in señales_generadas[-5:]:
            r15 = f"{s['ret_15min']:+.0f}" if s.get('ret_15min') is not None else '?'
            r30 = f"{s['ret_30min']:+.0f}" if s.get('ret_30min') is not None else '?'
            r60 = f"{s['ret_60min']:+.0f}" if s.get('ret_60min') is not None else '?'
            tw  = f" tweets:{s['adam_tweets']}" if s.get('adam_tweets') else ''
            print(f"   {s['fecha']} level {s['nivel_es']:.0f} "
                  f"flush:{s['flush_size']:.0f}pts | "
                  f"+15:{r15} +30:{r30} +60:{r60}{tw}")

    # ── Save ──────────────────────────────────────────────────────────────
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

    print(f"\n💾 Report: {REPORT_FILE}")
    print(f"💾 CSV:     {SIGNALS_FILE}")
    print("=" * 60)


if __name__ == '__main__':
    run_backtest()
