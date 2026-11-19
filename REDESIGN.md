# Funny Oracle — Redesign Spec ("The Oracle Arena")

> Companion to `ARCHITECTURE.md`. Replaces the Streamlit UI with a **React + FastAPI** web app in a **game show / arena** style.
> The backend (orchestrator, agents, MCP server, quant, scoreboard, cost) is reused, not rewritten.
> **Not investment advice** — the disclaimer stays visible on every screen and on every share card.

---

## 1. Goals

| Goal | How |
|---|---|
| Fun to watch | Agents are characters on a stage; the prediction is a "show" with live progress, a debate, and a verdict reveal |
| Choose model + asset in the UI | Ticker search + model picker with cost/speed estimate before running |
| Live war room | Server-Sent Events stream every agent step, tool call and debate turn |
| Model vs Model | Two models predict the same asset side by side |
| Share card | Export a prediction as a PNG |
| Safe on budget | A mock mode replays stored predictions as fake live streams, so UI work costs ฿0 |

**Keep:** `app.py` (Streamlit) stays working as a fallback until the new UI passes every checkpoint.

---

## 2. Architecture

```
┌──────────────────────────── web/ (React + Vite + TS) ────────────────────────────┐
│  Arena  ·  Duel  ·  Hall of Fame (Scoreboard)  ·  Replay /p/:id                  │
│  Tailwind · Framer Motion · lightweight-charts · html-to-image · canvas-confetti │
└───────────────┬───────────────────────────────────────────────▲──────────────────┘
                │ REST (JSON)                                    │ SSE (text/event-stream)
┌───────────────▼───────────────────── api/ (FastAPI) ───────────┴──────────────────┐
│  routes: models · tickers · quote · history · predictions · duels · scoreboard · │
│          budget · autoscalp                                                       │
│  RunManager: starts runs as asyncio tasks, holds an event queue per run          │
│  MockRunner: replays stored Prediction JSON as timed events (MOCK_LLM=true)      │
└───────────────┬───────────────────────────────────────────────────────────────────┘
                │ run_prediction(..., models=..., on_event=callback)
┌───────────────▼──────── existing backend (unchanged except event hooks) ──────────┐
│  orchestrator.py → agents → core/llm.py → core/mcp_client.py → mcp_server/       │
│  quant/montecarlo.py · core/scoreboard.py · core/cost.py · data/oracle.db         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Backend changes (minimal)

1. **Event hook:** `run_prediction(..., on_event: Callable[[Event], Awaitable[None]] | None = None)`. The orchestrator, agents and `llm.py` emit events at each step. With `on_event=None` behaviour is exactly as today (CLI and Streamlit keep working).
2. **Per-run model override:** `run_prediction(..., analyst_model=None, judge_model=None)`; falls back to `.env` when `None`. The budget guard and cost table use the override.
3. No other changes to agents, MCP, quant or scoring.

### 2.2 Event schema (SSE)

Each SSE message: `event: <type>` + `data: <json>`; every payload has `run_id`, `ts`, `seq`.

| type | payload (besides run_id/ts/seq) | UI reaction |
|---|---|---|
| `run_started` | ticker, asset_type, mode, horizon, models, agents[], est_cost_thb | stage lights up, agents walk on |
| `stage` | name (`news` · `analysts` · `aggregate` · `debate` · `judge` · `quant` · `done`) | progress rail advances |
| `agent_started` | agent | podium → "thinking" |
| `tool_call` | agent, tool, args | speech bubble `📡 get_indicators(BTC-USD, 1h)` |
| `tool_result` | agent, tool, ms, bytes, ok | bubble turns ✓ / ✗ |
| `search` | agent, query | bubble `🔎 "nvidia earnings guidance"` |
| `agent_done` | AgentReport | card flips → score + key points |
| `agent_failed` | agent, reason (`timeout`/`error`) | podium "absent" stamp |
| `aggregate` | s_raw, weights | tug-of-war rope moves |
| `debate_turn` | side (`bull`/`bear`), turn (1–3), text, responding_to | chat bubble with typewriter effect |
| `judge` | Verdict | drumroll → verdict reveal |
| `quant` | price_now, p10, p50, p90, prob_up, fan[] | fan chart draws in |
| `cost_update` | run_cost_thb, total_spent_thb, budget_thb | budget meter |
| `run_done` | prediction_id, Prediction | confetti / rain, share button |
| `error` | message, fatal | toast; fatal → "Show cancelled" |

### 2.3 REST endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/models` | models from `config.PRICES` + capability cache: id, provider, label, price in/out, speed tier (from observed latency), supports_search, **est_cost_thb per mode** (avg of last runs, else default) |
| GET | `/api/tickers/search?q=` | autocomplete: curated list (US mega-caps, top crypto, gold) + yfinance search if available; returns symbol, name, asset_type |
| GET | `/api/quote/{ticker}` | last price, % change, 24h/1d sparkline (cached 60 s) |
| GET | `/api/history/{ticker}?interval=&period=` | OHLCV for candles |
| POST | `/api/predictions` | `{ticker, mode, horizon, analyst_model, judge_model}` → `{run_id}`; budget guard refusal → HTTP 402 `{reason, spent_thb, est_thb}` |
| GET | `/api/runs/{run_id}/events` | SSE stream; replays buffered events first so late subscribers/reloads still see everything |
| GET | `/api/predictions?limit=&ticker=` | history list |
| GET | `/api/predictions/{id}` | stored Prediction (replay) |
| POST | `/api/duels` | `{ticker, mode, horizon, model_a, model_b}` → `{duel_id, run_ids: [a, b]}`; budget guard checks both together |
| GET | `/api/duels/{duel_id}` | both predictions + comparison summary |
| GET | `/api/scoreboard` | hit rate, band coverage, MAPE, agent leaderboard, **model leaderboard**, pending predictions with target_time |
| GET | `/api/budget` | spent (tracked + estimated actual), budget, remaining |
| POST | `/api/autoscalp/start` · `/stop` · GET `/status` | Auto-Scalp control (reuses `tick()`) |

**MockRunner** (`MOCK_LLM=true` or `?mock=1` on POST): picks a stored prediction (same ticker if available) and emits its events with realistic delays (tool calls 300–900 ms, agents 3–8 s, debate turns 2 s). No LLM calls, no cost. **All frontend development uses mock mode.**

---

## 3. Visual Design — "The Oracle Arena"

### 3.1 Concept
A late-night TV game show where five AI analysts compete on stage, a bull and a bear debate, and the Judge (the host) reveals the verdict. Original characters only, built from emoji + shapes (no third-party characters or logos).

### 3.2 Cast

| Role | Character name | Avatar | Accent |
|---|---|---|---|
| Business | **The CEO** | 🏢 | amber |
| Technical | **Chart Wizard** | 📈 | cyan |
| Valuation | **The Accountant** | 🧮 | lime |
| Macro | **The Economist** | 🌍 | sky |
| Psychology | **Mood Reader** | 🧠 | pink |
| Bull | **Bull** | 🐂 | green |
| Bear | **Bear** | 🐻 | red |
| Judge | **The Oracle** (host) | 🔮 | violet/gold |

Avatar = emoji inside a circular "podium light" in the accent colour, with state rings (idle dim, thinking pulse, tool call spin, done solid, absent grey + stamp).

### 3.3 Tokens

- **Background:** deep stage navy `#0B0B1E` → radial spotlight gradients (violet `#6D28D9`, magenta `#DB2777`) at 15–25 % opacity; subtle animated light sweep.
- **Surfaces:** `#15152E` cards, 1 px `#2A2A4A` borders, 16 px radius, soft glow on active elements.
- **Semantic:** bull `#22C55E`, bear `#EF4444`, neutral `#A1A1AA`, gold `#FACC15` (verdict, winners). Bull/bear always paired with 🐂/🐻 icons or ▲/▼ so colour isn't the only signal.
- **Type:** display = a bold rounded game-show font from Google Fonts (e.g. *Lilita One* or *Bungee*, OFL) for titles and big numbers' labels; body = *Inter*; numbers = *JetBrains Mono* (tabular).
- **Motion:** Framer Motion springs; nothing blocks interaction longer than 600 ms; `prefers-reduced-motion` turns animations into fades and disables confetti and sweeps.
- **Sound (optional, off by default):** toggle in the top bar; tiny synthesized sounds via Web Audio API (drumroll, ding, buzzer) — no audio files.

---

## 4. Screens

### 4.1 Top bar (all screens)
`🔮 FUNNY ORACLE` wordmark · nav (Arena · Duel · Hall of Fame) · **Budget meter** (`฿14 / ฿100`, turns amber > 70 %, red > 90 %) · sound toggle · "Not investment advice" pill.

### 4.2 Arena (home) — setup state

```
┌───────────────────────────────────────────────────────────────────────┐
│ 🔍 Search ticker…  [NVDA ▲1.2% ~~] [BTC ▼0.4% ~~] [GOLD ▲0.1% ~~] … │  preset chips w/ live sparkline
├───────────────────────────────────────────────────────────────────────┤
│ Mode:  ( 🐢 Swing )  ( ⚡ Scalp )      Horizon: [1W] [1M] [3M]         │
├───────────────────────────────────────────────────────────────────────┤
│ Choose your Oracle brain                                               │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                    │
│ │ 3.5 Flash-Lite│ │ 3.8 Flash    │ │ Claude …     │  (only configured) │
│ │ ⚡⚡⚡ fast     │ │ ⚡ slower     │ │              │                    │
│ │ ~฿0.5 / run  │ │ ~฿1.2–3 / run│ │              │                    │
│ │ 🔎 search ✓  │ │ 🔎 search ✓  │ │              │                    │
│ └──────────────┘ └──────────────┘ └──────────────┘                    │
│ Advanced ▸ separate Judge model                                       │
├───────────────────────────────────────────────────────────────────────┤
│        [ 🔔  START THE SHOW  ·  est. ฿0.6 ]                            │  disabled + reason if over budget
└───────────────────────────────────────────────────────────────────────┘
```

### 4.3 Arena — live show (war room)

```
┌ progress rail: News ● Analysts ● Tug-of-war ○ Debate ○ Verdict ○ Forecast ○ ┐
│                                                                              │
│   🏢 The CEO     📈 Chart Wizard   🧮 Accountant   🌍 Economist   🧠 Mood    │  5 podiums
│   (thinking…)    💬📡 get_indicators  ✓ +0.4       💬🔎 "fed rate…"  …       │  live bubbles
│                                                                              │
│   ──── 🐻 ━━━━━━━━━━━━━●━━━━━━━━ 🐂 ────   S_raw +0.18                        │  tug-of-war rope
│                                                                              │
│   Debate stage (chat):  🐂 "…"   🐻 "…↩ responding to: oversold bounce"  🐂 │  typewriter
│                                                                              │
│   ticker tape ▸ 22:41:03 Chart Wizard → get_indicators(BTC-USD,1h) 412 ms ✓ │  scrolling log
└──────────────────────────────────────────────────────────────────────────────┘
```
- Clicking a finished podium opens a drawer: score meter, confidence, key points, reasoning, sources, full tool trace.
- Debate text arrives as whole turns; the client reveals it with a typewriter effect (≈ 40 chars/s, click to skip).

### 4.4 Arena — verdict & forecast

1. **Drumroll** (1.2 s): screen dims, spotlight on 🔮 The Oracle.
2. **Verdict card** slams in: Judge `headline` (big display font), S_final meter, confidence, key risks.
3. Effect: bullish (S_final > 0.1) → green confetti; bearish (< −0.1) → gentle red "rain"; neutral → a shrug 🤷 bounce.
4. **Forecast panel:**
   - Candlestick chart (lightweight-charts) of recent history + projected fan band (P10–P90 shaded, P50 line) extending to the horizon.
   - Three prize cards: 🐻 Bear `P10` · 🎯 Base `P50` · 🐂 Bull `P90`, with % vs now; `P(up)` as a big number.
   - Scalp mode: countdown "Result checked in 14:32" until target_time, then the card flips to ✅ / ❌ with the real price.
   - Buttons: **Share card** · Replay · Run again · Duel this ticker.
   - Footer: cost of this run (tracked / estimated actual), model(s), duration.

### 4.5 Duel (Model vs Model)

```
┌─────────────── NVDA · Swing · 1M ───────────────┐
│  🔵 Model A: 3.5 Flash-Lite │ 🟣 Model B: 3.8 Flash │  pickers + combined est. cost
├─────────────────────────────┼──────────────────────┤
│ compact war room A          │ compact war room B   │  both stream in parallel
├─────────────────────────────┴──────────────────────┤
│  Head-to-head: P50 A vs B · P(up) · confidence ·   │
│  agent score diffs (bar pairs) · cost · time        │
│  Banner: "🤝 Both bullish"  or  "⚔️ They disagree!" │
└─────────────────────────────────────────────────────┘
```
- Duel predictions are stored with a shared `duel_id`; the Hall of Fame tracks **model win rates** once results are evaluated.

### 4.6 Hall of Fame (Scoreboard)

- **AI vs Coin Flip:** big animated coin (flips on load) next to the AI hit rate; caption like "The AI beats a coin by 10 points (n = 5, so… maybe luck)".
- Band coverage gauge with the 80 % "well-calibrated" marker; MAPE.
- **Agent podium:** gold/silver/bronze by directional accuracy, the rest listed below.
- **Model leaderboard:** hit rate, coverage, avg cost, avg time per model.
- **Pending:** predictions waiting for target_time with live countdowns.
- History table with filters (ticker, mode, model) and replay links.
- Auto-Scalp control card (start/stop, runs left under cap, next run countdown).

### 4.7 Share card

- 1080 × 1350 PNG rendered with `html-to-image` from a hidden React component.
- Content: wordmark, ticker + asset type, mode/horizon, Judge headline, Bear/Base/Bull + P(up), mini fan chart, five agent score chips, model name, timestamp, **"For fun — not investment advice"**.
- Actions: Download PNG · Copy image to clipboard.

### 4.8 Replay `/p/:id`
Same verdict & forecast layout from stored data; "▶ Replay the show" button re-runs the stored events through the live view (via MockRunner) for demos.

---

## 5. Folder Structure (new parts)

```
api/
├── main.py              # FastAPI app, CORS (dev), serves web/dist in production
├── routes/              # models.py tickers.py quotes.py predictions.py duels.py scoreboard.py budget.py autoscalp.py
├── runs.py              # RunManager: tasks, per-run event buffer + subscribers
├── mock_runner.py       # stored Prediction → timed events
├── events.py            # Event Pydantic models (2.2)
└── tests/               # pytest + httpx AsyncClient; mocked orchestrator
web/
├── index.html
├── package.json         # react, react-router, @tanstack/react-query, zustand, tailwindcss,
│                        # framer-motion, lightweight-charts, html-to-image, canvas-confetti
├── vite.config.ts       # proxy /api → http://localhost:8000
└── src/
    ├── lib/api.ts  lib/sse.ts  lib/format.ts
    ├── hooks/useRunStream.ts     # SSE → reducer → show state
    ├── state/show.ts             # zustand store for the live show
    ├── components/
    │   ├── TopBar, BudgetMeter, TickerSearch, PresetChips, ModelPicker, ModeHorizon
    │   ├── Stage, Podium, ToolBubble, TickerTape, ProgressRail, TugOfWar
    │   ├── DebateThread, VerdictReveal, ForecastChart, PrizeCards, Countdown
    │   ├── DuelView, HeadToHead, CoinFlip, AgentPodium, ModelLeaderboard
    │   └── ShareCard, AgentDrawer, Toasts
    └── pages/ Arena.tsx  Duel.tsx  HallOfFame.tsx  Replay.tsx
scripts/dev.ps1          # starts uvicorn (8000) + vite (5173) together
```

---

## 6. Run & Develop

```powershell
# one-time
cd web; npm install; cd ..
.venv\Scripts\pip install fastapi uvicorn[standard] sse-starlette

# development (two processes, hot reload)
scripts\dev.ps1                       # → http://localhost:5173  (MOCK_LLM=true by default in dev)

# production / demo (single process)
cd web; npm run build; cd ..
.venv\Scripts\python -m uvicorn api.main:app --port 8000   # → http://localhost:8000
```
- Requires Node.js (check `node -v`; LTS is fine).
- `.env` additions: `MOCK_LLM=false` (demo), `API_PORT=8000`.

---

## 7. Non-functional

| Concern | Approach |
|---|---|
| Budget | Mock mode for all UI work; budget guard on every POST (duel checks both runs); 402 shown as a friendly "The Oracle is out of coins" modal |
| Robustness | SSE auto-reconnect with `Last-Event-ID`; RunManager replays buffered events; a run continues server-side if the tab closes |
| One run at a time? | Allow up to 2 concurrent runs (duel); third request → 429 "Show already in progress" |
| Performance | Candles ≤ 500 points; animations GPU-friendly (transform/opacity); lazy-load Duel & Hall of Fame |
| Accessibility | Colour + icon for bull/bear; keyboard focus rings; reduced-motion support; aria-live for verdict |
| Licences | Fonts OFL; `lightweight-charts` keeps its required attribution; no third-party characters/logos |
| Fallback | Streamlit `app.py` untouched and still runnable |

---

## 8. Build Order & Checkpoints

| # | Task | Done when |
|---|---|---|
| R1 | Event hook + per-run model override in orchestrator/agents/llm | existing tests pass; new test asserts event order for a mocked run; CLI unchanged |
| R2 | FastAPI: RunManager, SSE, MockRunner, all REST routes | `pytest api/tests` passes; `curl -N /api/runs/<id>/events` streams a full mock show |
| R3 | Web scaffold: Vite + TS + Tailwind + tokens + TopBar + routing | `npm run build` passes; dev server shows the empty Arena with stage background |
| R4 | Arena setup: ticker search, presets w/ sparklines, mode/horizon, ModelPicker w/ est. cost, Start button + budget states | selecting everything and pressing Start (mock) begins a run |
| R5 | Live war room: podiums, bubbles, ticker tape, progress rail, tug-of-war, debate typewriter, agent drawer | a mock run plays end-to-end with every event visible |
| R6 | Verdict reveal + forecast chart + prize cards + countdown + Replay page | mock run ends in verdict + fan chart; `/p/10` renders; "Replay the show" works |
| R7 | Duel + Hall of Fame + Auto-Scalp card + Share card | mock duel shows head-to-head; Hall of Fame shows real DB metrics; PNG downloads |
| R8 | Real-run verification + polish | ≤ 5 real runs total (1 swing per asset type on flash-lite, 1 duel); reduced-motion checked; screenshots in `docs/`; README updated |

**Rules for Claude Code:** use mock mode until R8; keep `PROGRESS.md` updated; commit after each checkpoint; don't break `orchestrator.py` CLI or `app.py`; never commit `.env`.

---

## 9. Out of Scope (this redesign)
User accounts, deployment to the internet, mobile-native app, real audio assets, persistent multi-user state.
