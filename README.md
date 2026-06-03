# Adam Mancini Trading Bot 🤖📈

Bot que replica la metodología de trading de [Adam Mancini](https://x.com/AdamMancini4) en el S&P 500.

Estudia sus miles de tweets históricos y su newsletter diario, monitoriza el SPY en tiempo real
y envía alertas por Telegram cuando detecta los setups característicos de Adam (entrada, SL, TP).

---

## Coste mensual

| Componente | Herramienta | Coste/mes |
|---|---|---|
| Tweets (histórico + live) | twikit (scraping libre) | $0 |
| Newsletter | Substack suscripción | $10-15 |
| LLM para parseo | Claude Haiku API | ~$3-5 |
| Datos de mercado | Alpaca free (SPY) | $0 |
| Base vectorial | ChromaDB local | $0 |
| Alertas | Telegram Bot | $0 |
| Servidor | Railway Hobby | $5 |
| **Total** | | **~$18-25/mes** |

---

## Fases del proyecto

- [x] **Fase 1** — Recopilación de datos (tweets + newsletter)
- [ ] **Fase 2** — Base de conocimiento vectorial (ChromaDB)
- [ ] **Fase 3** — Parser diario (newsletter + tweets en vivo)
- [ ] **Fase 4** — Feed de mercado (Alpaca SPY 1 min)
- [ ] **Fase 5** — Motor de señales (detección + LLM árbitro)
- [ ] **Fase 6** — Bot Telegram (alertas formateadas)
- [ ] **Fase 7** — Backtesting y validación
- [ ] **Fase 8** — Deploy producción 24/7

---

## Instalación y uso

### 1. Clonar el repositorio
```bash
git clone https://github.com/tuusuario/adam-mancini-bot.git
cd adam-mancini-bot
```

### 2. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno
```bash
cp .env.example .env
# Edita .env con tu editor favorito y rellena las credenciales
```

### 4. Fase 1 — Descargar datos históricos
```bash
# Descarga todos los tweets de @AdamMancini4 (~3200 disponibles)
python scrapers/twitter_scraper.py

# Descarga el newsletter gratuito de Substack
python scrapers/substack_scraper.py
```

---

## Estructura del proyecto

```
adam-mancini-bot/
├── config.py              # Configuración central (rutas, API keys, parámetros)
├── requirements.txt       # Dependencias Python
├── .env.example           # Template de variables de entorno
│
├── scrapers/              # FASE 1: Descarga de datos
│   ├── twitter_scraper.py     # Tweets históricos de @AdamMancini4
│   └── substack_scraper.py    # Newsletter Trade Companion
│
├── knowledge_base/        # FASE 2: Base vectorial ChromaDB
├── parsers/               # FASE 3: Parser diario newsletter + tweets live
├── market_data/           # FASE 4: Feed Alpaca SPY en tiempo real
├── signals/               # FASE 5: Motor de detección de señales
├── bot/                   # FASE 6: Bot Telegram
├── backtest/              # FASE 7: Backtesting y validación
│
└── data/                  # Datos locales (no se sube a GitHub)
    ├── raw/
    │   ├── tweets/            # JSON con tweets descargados
    │   └── newsletter/        # JSON con artículos del newsletter
    └── processed/             # Datos procesados listos para ChromaDB
```

---

## Deploy en Railway

Railway es la opción más sencilla para tener el bot corriendo 24/7:

1. Sube el código a GitHub
2. Crea cuenta en [Railway](https://railway.app)
3. New Project → Deploy from GitHub repo
4. Variables → añade todas las del `.env`
5. El bot se despliega automáticamente en cada `git push`

**Plan:** Railway Hobby ($5/mes) para servicio siempre activo.
