# Spanish → English Migration Plan

> **Status:** inventory only — nothing edited yet.
> **Generated:** 2026-06-30
> **Scope:** all project source (`.py`, `.md`, `.txt`, `.example`). Excludes `venv/`, `.git/`, `__pycache__/`, and `data/` (price-bar JSON).

## How to read this

Files are grouped by **file**, ordered by the **migration priority** of their highest-value content:

1. **LLM-facing prompts** sent to the Claude API — *correctness-critical, migrate first*
2. **Telegram user-facing messages** — what the human actually reads on their phone
3. **Console / log output** — operator-facing print/log strings
4. **Code comments & docstrings** — developer-facing
5. **Docs / README / config** — supporting material

"Spanish lines" = rough line count of lines containing Spanish (accented chars or ≥2 Spanish keywords), via a heuristic scan. Treat as ±10%. Many files mix categories, so each file lists its internal breakdown.

**Repo total: ~1,050 Spanish lines across 20 files.** `README.md` is already English (0 lines) — no action needed.

---

## TIER 1 — LLM prompts (Claude API)  🔴 highest priority — ✅ COMPLETE

These are `f"""…"""` templates passed as `messages=[{"role":"user","content":prompt}]`. Mistranslation here changes model behavior, not just readability. Each prompt file *also* carries heavy Spanish docstrings/comments (counted separately below — those remain Spanish, handled in Tier 3/4).

**The assembled prompt Claude reads is now fully English in all three call paths, including every string interpolated at runtime. JSON output *key* names are preserved (parsing depends on them); *values* — free-text and enums — are English.**

- [ ] **`knowledge_base/processor.py`** — *~37 Spanish lines*
  - [x] 🔴 `EXTRACTION_PROMPT` (L32–L56, ~25 lines) — newsletter → structured bias/levels extraction ✅ translated to English (keys unchanged)
  - [ ] docstrings/comments (~12 lines): module docstring, `ESTRATEGIA DE EXTRACCIÓN`, fallback comments
- [ ] **`parsers/tweet_monitor.py`** — *~86 Spanish lines*
  - [x] 🔴 `CLASIFICACION_PROMPT` (L166–L223, ~58 lines) — tweet classifier prompt ✅ fully English, incl. `tipo` enum values now `signal`/`level`/`comment`/`other` (was `senal`/`nivel`/`comentario`/`otro`). All comparison/default sites updated: `tweet_monitor.py` (return default L278, `.get` default L384, `== 'level'` L401) **and** consumer `bot/telegram_alerts.py` (lookup-dict keys L227-232 + default L224 + `__main__` sample L409). Keys unchanged.
  - [ ] docstrings/comments (~73 lines): module header `CÓMO FUNCIONA`, market-hours logic, save-state comments
  - [ ] print/log strings (~3 lines)
- [ ] **`signals/signal_engine.py`** — *~209 Spanish lines (largest file in repo)*
  - [x] 🔴 `SIGNAL_PROMPT` (L440–L508, ~69 lines) — the core entry-decision prompt ✅ translated to English (keys unchanged)
  - [x] 🔴 `formatear_tweets_para_prompt()` ✅ translated (LLM-facing return strings)
  - [x] 🔴 Runtime-interpolated prompt fragments ✅ all translated: `get_trading_window()` `ventana`+`criterio` return strings (incl. "FUERA del horario…"), `confirmacion` strings, `detect_failed_breakdown` `descripcion` values + `calidad` fragments (`profundo`/`moderado`→`deep`/`moderate`), `content_plan` fallback (`Plan no disponible`), `fb_descripcion` fallback (`Sin análisis`), and the `tipo_nivel` values `soporte`/`resistencia`/`pivote`→`support`/`resistance`/`pivot` (producers L105/108/112 + consumer `determinar_lado` L314-315). *Note: `entrar: false` left literal inside criterio strings — it references the JSON output key.*
  - [ ] Telegram management-alert strings (T1/T2/stop-hit) — *see Tier 2 note*
  - [ ] docstrings/comments (~129 lines): module header `MEJORAS AÑADIDAS`, state-persistence, cooldown, main-loop docstrings
  - [ ] print/log strings (~5 lines)

> **Out of scope / not touched:** `backtest/backtester.py` keeps its own independent `soporte`/`resistencia`/`pivote` level dicts — it shares no runtime data with `signal_engine.py` and never feeds `SIGNAL_PROMPT` (offline tool, Tier 3). The Spanish display strings in `telegram_alerts.py` (`SEÑAL ACCIONABLE`, etc.) and the Spanish docstrings/comments in all three files remain — those are Tier 2/3/4.

---

## TIER 2 — Telegram user-facing messages  🟠 — ✅ COMPLETE

Strings sent to the user's phone via `bot.send_message(...)` / `alerter.send(...)`.

**All user-facing Telegram message templates are now English.** Only displayed string *values* were translated — internal dict keys (`signal`/`level`/`comment`, `bias`, `nivel_critico`, etc.), variable names (`señal`, `lineas`, `soportes_str`…), f-string interpolations, emoji, and HTML tags are untouched. Docstrings/comments and operator-facing `print()`/log strings remain Spanish (Tier 3/4). Both touched files pass `py_compile`.

- [x] **`bot/telegram_alerts.py`** — *~65 Spanish lines*
  - [x] 🟠 Message templates ✅ translated: `_fecha_legible` day/month abbrevs (`Lun`→`Mon`, `Ene`→`Jan`, …), `_format_levels` "+N más"→"+N more", 📋 briefing (`PLAN DE ADAM`→`ADAM'S PLAN`, `Crítico`→`Critical`, `Soportes`→`Supports`, `Invalida si`→`Invalidated if`, preview line), ⚡ signal alert (`Entrada`→`Entry`, `Nivel`→`Level`, `Confianza`→`Confidence`), 🐦 tweet alert (`tipo_txt` values `SEÑAL ACCIONABLE`→`ACTIONABLE SIGNAL`/`ACTUALIZACIÓN NIVELES`→`LEVELS UPDATE`/`COMENTARIO`→`COMMENT`, `ADAM TWEETEA`→`ADAM TWEETS`, `Entrada`→`Entry`), T1/T2/stop management alerts (`ALCANZADO`→`REACHED`, `Ganancia`→`gain`, `cobrado`→`banked`, `TOCADO`→`HIT`, `CERRADO EN BREAKEVEN`→`CLOSED AT BREAKEVEN`, etc.), and `send_test` connection message.
  - [ ] docstrings/comments (~29 lines): module header "Envía tres tipos de mensajes al móvil", `USO (test rápido)` — *Tier 3/4, left Spanish*
  - [ ] print/log (~1 line) — *Tier 3, left Spanish*
- [x] **`main.py`** — *~43 Spanish lines*
  - [x] 🟠 User-facing alert text via `alerter.send(...)` ✅ translated: "Could not fetch today's newsletter", "Newsletter error: {e}", startup message (`Bot Adam Mancini iniciado`→`Adam Mancini Bot started`, `No hay newsletter…`→`No newsletter available today — monitoring only`), shutdown message (`detenido`→`stopped`).
  - [ ] docstrings/comments (~34 lines): orchestration flow docstrings — *Tier 3/4, left Spanish*
  - [ ] print/log (~5 lines) — *Tier 3, left Spanish*
- [x] **`signals/signal_engine.py`** *(cross-ref)* — management-alert Telegram strings: the rendered alerts live in `telegram_alerts.py` (`send_t1/t2/stop_alert`, translated above); no user-facing template strings remain in `signal_engine.py` itself.

---

## TIER 3 — Console / log output  🟡 — ✅ COMPLETE

Operator-facing `print(...)` / `logging` strings. Listed by print/log line weight.

**All operator-facing `print()` strings across these 10 files are now English.** Scope was strictly the natural-language text passed to `print()` — internal dict/variable keys (`señales`, `por_descargar`, `descargados`, `dias_con_nl`, the `RANGO_NIVELES`/`MIN_FLUSH_PTS` constant labels…), f-string interpolations, emoji, separators, and ChromaDB document/metadata content (`add_tweets_to_kb.py` `doc_texto`) were left untouched. Code comments and docstrings remain Spanish (Tier 4 — handled per-file in a later pass). All 10 files pass `py_compile`.

*Behavioral note:* in `backtester.py` `print_edge_report`, the group labels `"CON Failed Breakdown"`/`"SIN Failed Breakdown (referencia)"` became `"WITH …"`/`"WITHOUT …"`; the downstream discriminator `if 'CON' in nombre` was rewritten to `if nombre.startswith('WITH ')` (a bare `'WITH' in nombre` would have matched `"WITHOUT"`), preserving the original flush-stats logic.

- [x] **`backtest/backtester.py`** ✅ print/log strings translated (edge report, backtest report, top-levels/last-signals tables, status messages) — heaviest non-prompt file
- [x] **`scrapers/substack_scraper.py`** ✅ print/log strings translated (article listing, download progress, final summary, cookie tips)
- [x] **`scrapers/twitter_scraper.py`** ✅ print/log strings translated (cookie loading/errors, pagination, summary)
- [x] **`market_data/ibkr_feed.py`** ✅ print/log strings translated (connect/disconnect, data-mode, snapshot/bars, `_test_feed`)
- [x] **`parsers/newsletter_parser.py`** ✅ print/log strings translated (fetch/parse flow, `_mostrar_resumen` DAY MAP box)
- [x] **`backtest/download_data.py`** ✅ print/log strings translated (period/download progress, final summary)
- [x] **`knowledge_base/build_kb.py`** ✅ print/log strings translated (init, progress, summary, `_print_stats`)
- [x] **`knowledge_base/add_tweets_to_kb.py`** ✅ print/log strings translated (load/index/summary). *ChromaDB `doc_texto`/`metadata` content left as data — not console output.*
- [x] **`market_data/alpaca_feed.py`** ✅ print/log strings translated (feed errors, polling loop, `test_feed`)
- [x] **`scrapers/twitter_scraper_playwright.py`** ✅ print/log strings translated (scroll progress, batch errors, summary)

---

## TIER 4 — Code comments & docstrings (comment-dominant files)  🟢

Files whose Spanish is almost entirely developer-facing.

- [ ] **`knowledge_base/vectordb.py`** — *~34 lines* (docstrings/comments ~30, strings ~3)
- [ ] **`parsers/playwright_utils.py`** — *~14 lines* (comments ~12, docstrings ~4)
- [ ] **`clear_recent_slugs.py`** — *minimal* (small utility; spot-check comments, ~0–3 lines)

> Note: comment/docstring Spanish is also embedded in every Tier-1/2/3 file above — handle it in the same pass as that file rather than separately.

---

## TIER 5 — Docs / config  ⚪

- [ ] **`config.py`** — *~33 lines* (almost all comments: module header `Configuración central`, `SPY_TO_ES_MULTIPLIER` notes, IBKR/Alpaca fallback comments; ~2 strings)
- [ ] **`.env.example`** — *~8 lines* (Spanish setup comments, 7 comment lines)
- [ ] **`requirements.txt`** — *~10 lines* (Spanish section comments, ~3 comment lines)
- [ ] **`README.md`** — ✅ **already English** (0 Spanish lines) — no action

---

## Suggested execution order

1. Tier 1 prompt blocks (`EXTRACTION_PROMPT`, `CLASIFICACION_PROMPT`, `SIGNAL_PROMPT`) — verify model output unchanged after translating.
2. Tier 2 Telegram templates — eyeball rendered messages.
3. Sweep each file end-to-end for Tiers 3–5 (logs + comments + docstrings) so each file is touched once.
4. Update `config.py` / `.env.example` / `requirements.txt` comments last.

**Watch-outs**
- All variable/function names are English **except** a few Spanish-named helpers: `formatear_tweets_para_prompt()` (signal_engine.py) and constants like `CLASIFICACION_PROMPT`. Renaming these is optional and requires updating call sites.
- Emoji and Telegram HTML tags (`parse_mode=ParseMode.HTML`) must survive translation.
- Prompt output schemas (JSON keys the model must emit) are English already — translate only the natural-language instructions, not the field names, or downstream parsing breaks.
