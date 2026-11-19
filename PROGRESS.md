# Progress

## Redesign — "The Oracle Arena" (REDESIGN.md)

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| R7 | Duel + Hall of Fame + Auto-Scalp card + Share card | ✅ done | `node web/e2e/r7.mjs` (headless Chrome, mock API, real DB): **Hall of Fame** shows real metrics (AI 67% vs coin 50%, n = 6 → "maybe luck"), band coverage gauge with 80% marker + MAPE, agent podium, model leaderboard (2 models), 6 pending with live countdowns, 12-row history with ticker/mode/model filters (scalp → 6) and replay links, Auto-Scalp card (disabled in mock mode with reason); **share card** from `/p/10` and `/p/11` downloads a 1080×1350 PNG (~400 KB) with wordmark, ticker, headline, Bear/Base/Bull + P(up), mini fan, 5 agent chips, model, timestamp and the disclaimer; **mock duel** GOLD swing 1M (3.5 Flash-Lite vs 3.8 Flash) streams both lanes in parallel and ends in the head-to-head ("🤝 Both bearish", P50/P(up)/conf/S_final/cost/time rows, agent bar pairs). 0 console errors |
| R6 | Verdict reveal + forecast chart + prize cards + countdown + Replay page | ✅ done (committed together with R5) | `node web/e2e/show.mjs … NVDA swing 1M` → drumroll → verdict card (headline, S_final meter, confidence, key risks) → fan chart (candles + P10–P90 band + P50, TradingView attribution) → Bear/Base/Bull + P(up) cards → countdown, Replay/Run again/Duel buttons, cost/model footer; `… BTC-USD scalp 15m` → no news/debate stages, countdown card flips to ✅/❌ (mock shows the original prediction's result, labelled); `node web/e2e/replay.mjs … 10` → `/p/10` renders verdict, 5 podiums, 3 debate turns, chart + prize cards; "▶ Replay the show" streams it live and ends in the verdict. 0 console errors. API: `GET /api/predictions/{id}?evaluate=1` (+ test) |
| R5 | Live war room: podiums, bubbles, ticker tape, progress rail, tug-of-war, debate typewriter, agent drawer | ✅ done | `node web/e2e/show.mjs` (mock NVDA swing, replay of #10): all 54 events received (13 types), podium states thinking / "on the phone" (spinning ring) / done with score chip, tool bubbles ✓, search bubbles, live provisional S on the rope, 6/6 rail steps, 3 debate turns typed out in order (click to skip), 31 tape lines (= 54 − 7 stage − 5 cost − 11 results merged into their call lines), drawer opens with score meter, key points, reasoning, tool trace, sources |
| R4 | Arena setup: ticker search, presets w/ sparklines, mode/horizon, ModelPicker w/ est. cost, Start + budget states | ✅ done | `node web/e2e/setup.mjs` (headless Chrome, mock API): type "eth" → ETH-USD option → Enter; preset GOLD then BTC (live quote + sparkline chips); Scalp + 1h; 2nd model card; Advanced judge selector; Start shows "mock replay ฿0" → run begins and streams (`status: live`, heading BTC-USD · scalp · 1h), 0 console errors. Over-budget: API with `MOCK_LLM=false BUDGET_THB=10` → Start disabled with "Over budget: ~฿3.65 needed, ฿0.00 left" (no LLM call). 402 → "The Oracle is out of coins" modal, 429 → toast |
| R3 | Web scaffold: Vite + TS + Tailwind + tokens + TopBar + routing | ✅ done | `cd web; npm run build` (tsc + vite, 0 errors); `scripts\dev.ps1` equivalents running (uvicorn :8000 mock + vite :5173), headless Chrome screenshot of `/` shows stage background, TopBar (wordmark, nav, live budget ฿14/฿100 + MOCK badge, sound toggle, disclaimer pill), no console errors |
| R2 | FastAPI: RunManager, SSE, MockRunner, all REST routes | ✅ done | `pytest api/tests` (22 passed: every route, full mock show validated against `api/events.py`, SSE `id`/`event` lines, `Last-Event-ID` + `?after=` replay, 404/422/429/402, real run through a mocked orchestrator, mock + real duels, replay endpoint, model leaderboard incl. duel wins, Auto-Scalp start/stop/mock block); live: `MOCK_LLM=true uvicorn api.main:app` → `curl -N /api/runs/<id>/events` streamed a complete 54-event show (replay of #10 NVDA) |
| R1 | Event hook + per-run model override | ✅ done | `pytest tests/test_events.py` (15: full swing event order, scalp stages, agent_failed error/timeout, news_brief, search events, no-callback path, broken callback, budget refusal → fatal `error`, model override used per role + stored, per-role .env fallback, cross-provider rejected, per-model estimate); full suite 132 passed; `orchestrator.py` CLI unchanged |

### Redesign decisions

- **R7**: share card = offscreen React component (inline styles, SVG mini fan instead of a canvas chart) captured with `html-to-image`; the Google Fonts `<link>` needs `crossorigin="anonymous"` or html-to-image can't read the cross-origin rules to embed the fonts. Model name + disclaimer are pinned to the bottom so long headlines (clamped to 3 lines) can never push them off the card (found by looking at the PNG). Copy-to-clipboard uses `ClipboardItem` (needs a secure context — fine on localhost). Duel lanes report their *reduced* state upward from an effect (the stream callback sees the pre-event state — found when the head-to-head never appeared). Duel win (leaderboard) = P50 closer to the actual price.

- **R5/R6 UI**: one pure reducer (`web/src/state/show.ts`) turns events into show state; it drops duplicate `seq`s, so EventSource auto-reconnect (`Last-Event-ID`) and page reloads (run id kept in sessionStorage) just replay. The stream is closed on `run_done`/fatal `error` so EventSource doesn't reconnect forever. The rope shows a *provisional* S from finished agents (same formula as the server) until `aggregate` arrives. The verdict waits until the debate has been typed out, so the reveal doesn't spoil it. The drumroll plays once per run id. Drawer and modals render through a portal (an ancestor with `backdrop-filter` clipped `position: fixed`; found by screenshot). Fan chart: fan starts at the prediction's `created_at`; candles after that are drawn faded (on replays you see what really happened); daily stock/gold fans skip weekends. Mock shows rescale prices to the live price so the fan starts at the last candle (debate text still quotes the original numbers — it's a replay).

- **R3 stack** (resolved by npm on 2026-11-19): React 19.3, react-router 7, TanStack Query 5, zustand 5, framer-motion 14, lightweight-charts 5.3, Tailwind 4.3 (`@tailwindcss/vite`, tokens in `@theme` in `src/index.css`), Vite 8, TypeScript 7. Fonts: Lilita One (display), Inter (body), JetBrains Mono (numbers) from Google Fonts (all OFL). The UI is dark-only by design (stage at night). `playwright-core` (dev only, uses the installed Chrome, no browser download) drives `web/e2e/*.mjs` for screenshots and click-through checks. Duel / Hall of Fame / Replay are lazy routes.

- **R2 API** (`api/`): `RunManager` keeps every run's events in memory (last 50 finished runs) and streams them with `seq` as the SSE `id`, so `Last-Event-ID` / `?after=N` resume exactly. **Real runs execute in a worker thread with their own event loop** (`asyncio.run`), events are marshalled back with `run_coroutine_threadsafe`; this keeps blocking yfinance work off the server loop and gives the MCP stdio subprocess a Proactor loop on Windows regardless of how uvicorn was started. Mock runs are tasks on the server loop. Max 2 concurrent runs (mock included) → 429.
- **MockRunner** picks a stored prediction: same ticker+mode → same asset type+mode → same mode → anything. If the ticker differs it is relabelled and its prices rescaled to the live price (free yfinance call); the clock is shifted to now (so scalp countdowns work) and `models` is set to the models the user picked. Mock runs skip the budget guard and are not saved. Mock duels use two *different* stored shows. `run_started.mock`/`replay_of` tell the UI it's a replay. `MOCK_SPEED` scales the delays (0 = instant, used by tests).
- **Extra endpoints** beyond §2.3: `POST /api/predictions/{id}/replay` (▶ Replay the show), `GET /api/runs/{id}` (status), `GET /api/runs` (active), `GET /api/health`. `POST /api/predictions` with only `analyst_model` uses it for the judge too ("one Oracle brain").
- **Model leaderboard** (`/api/scoreboard`): per analyst model hit rate, band coverage, avg cost, avg time, and duel wins (closer P50 to the actual price). Rows saved before R1 get `analyst_model`/`judge_model` **backfilled** from `llm_usage` (most-used model per prediction) by the additive migration — a one-time UPDATE on `data/oracle.db`, nothing deleted.
- **Auto-Scalp** via API runs `core.autoscalp.tick()` in a background thread (poll 20 s); refused with 409 while `MOCK_LLM=true` because it makes paid calls.

- **R1 events** live in `core/events.py` (`EventSink`, a no-op when `on_event=None`). Envelope `{type, run_id, ts (epoch ms), seq}`. The `LLM` object carries the sink (`llm.events`) so agents/debate/backends emit without signature changes; tool events come from the per-agent executor wrapper. Payload notes: `agent_done.report` = AgentReport JSON; `agent_failed.reason` ∈ timeout/error + `detail`; `debate_turn.turn` 1–3 plus `turn_name`; `judge` carries `verdict` (or null), `s_raw`, `s_final`, `confidence`; `quant.fan` is the dict `{step,p10,p50,p90}`; `cost_update` has tracked and estimated-actual figures. **Extra event** `news_brief` (bullets, links) when the News Researcher runs. `stage: news` is emitted in swing only (scalp has no news step). A failing callback is logged and ignored.
- **Model override**: `run_prediction(..., analyst_model=, judge_model=, run_id=, duel_id=)`. None → `.env` per role. Provider is inferred from the model ID (`config.provider_for`); mixing providers in one run raises `ValueError`. `Prediction` gains `models`, `run_id`, `duel_id`; `predictions` gains `analyst_model`, `judge_model`, `duel_id`, `cost_actual_usd` (additive `ALTER TABLE` migration in `core/db.py`).
- **Per-model estimate**: `BudgetGuard.estimate_usd(mode, model)` = that model's last-10 average × 1.25; without history the mode default scaled by token price vs `gemini-3.8-flash` (never below 1×).


| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| 1 | Project setup, venv, `.env` | ✅ done | `python -c "import yfinance, google.genai, mcp"` |
| 2 | `mcp_server` + `data_sources` + `indicators` | ✅ done | `pytest tests/test_mcp_tools.py` (25 passed, live data) |
| 3 | Register MCP in Claude Code | ✅ done | `claude mcp add market-data` (✔ Connected) + `pytest tests/test_mcp_client.py` (stdio + in-process `get_indicators("BTC-USD","1h")`) |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | ✅ done | `python scripts/run_agents.py BTC-USD --agents technical` → valid AgentReport, $0.0029 logged in `llm_usage`; `pytest tests/test_llm.py` (mocked) |
| 5 | Remaining agents + News Researcher | ✅ done | `python scripts/run_agents.py <T> --agents all` for NVDA, BTC-USD, GC=F → 15/15 valid reports; News Researcher (search + get_news modes) covered by mocks |
| 6 | Debate + Judge + Monte Carlo + orchestrator | ✅ done | `python orchestrator.py NVDA swing 1M` → P10 134.85 / P50 155.51 / P90 178.89 (11 s, $0.0137); also `BTC-USD scalp 15m`; `pytest tests/test_montecarlo.py tests/test_orchestrator.py` |
| 7 | Streamlit UI | ✅ done | `python scripts/ui_smoke.py GC=F swing 1M` (real: Predict → every section, 2 charts, no errors); `pytest tests/test_app.py` (AppTest, mocked); `streamlit run app.py` health OK; light/dark/scalp screenshots reviewed (`docs/`) |
| 8 | Scoreboard + Auto-Scalp | ✅ done | Real: `scripts/auto_scalp.py --runs 4 --interval 300` (22:29–23:02 local) → **4/4 auto_scalp predictions evaluated** (#4–#7); final metrics over all 5 evaluated scalp predictions: hit rate 60% vs 50% coin flip, P10–P90 coverage 40%, MAPE 0.45%; leaderboard Macro 60%, Psychology 60%, Technical 40% (n=5). Mocked: `test_orchestrator.py`, `test_autoscalp.py` |
| 9 | README + screenshots + demo rehearsal | ✅ done (live rehearsal is yours) | `README.md`, `docs/*.png`; demo steps 2–5 exercised via CLI / AppTest (NVDA swing, GC=F swing, debate, trace) |

Full suite: `pytest -q` → **117 passed** (network tests included, after the quick fixes below).

## Quick fixes (2026-11-19, before the redesign)

| Change | Verified by |
|---|---|
| `AGENT_TIMEOUT_S` default **45 → 60 s** (config + `.env.example`). | `tests/test_cost.py::test_agent_timeout_default_is_60` |
| **Scalp σ from horizon-scale returns** over a longer lookback: 15m horizon → 15m returns from 15m bars over 60 days; 1h → 1h returns from 1h bars over 6 months; 4h → 4h returns from 1h bars over 1 year (`config.SCALP_SIGMA`). Returns are overlapping but only counted when the bar exactly one horizon earlier exists, so overnight/weekend gaps don't inflate σ. σ per sim step = σ_horizon / √steps, so the fan chart still has per-minute/per-bar steps. Falls back to the old per-bar σ if fewer than 30 horizon returns exist or the fetch fails. Swing is unchanged. | `test_montecarlo.py` (exact-span, gap skipping, random-walk recovery, momentum case where per-bar σ underestimates, insufficient data) and `test_orchestrator.py` (scalp uses `15m/60d` data; fallback) |
| **Search free tier in the cost tracker**: two figures everywhere. *Tracked* = every search at list price ($0.014), what `llm_usage.cost_usd` stores. *Estimated actual* = first `GEMINI_SEARCH_FREE_PER_MONTH` (5,000) Gemini search queries per UTC month are free, allocated chronologically and shared across Gemini models; Claude searches are never free. `Prediction.cost_actual_usd` (new, optional), CLI output, Streamlit sidebar caption, and `cost.spend_summary()` (`tracked_*`, `estimated_actual_*`, searches this month, free left). The budget guard stays on **tracked** (conservative) unless `BUDGET_BASIS=actual`. | `tests/test_cost.py` (within tier, per-month chronological allocation, Claude, summary, budget basis); all mocked |


## gemini-3.8-flash demo seeds (2026-11-19 ~23:10 local)

Three real swing 1M predictions, stored for replay: **#10 NVDA, #11 BTC-USD, #12 GC=F** (`?replay=10` etc.). The models were set with environment variables for these runs only (see Known issues: `.env` still says flash-lite).

| # | Ticker | Total cost | Search queries (tracked at $0.014 each) | Token cost | Time | Result |
|---|---|---|---|---|---|---|
| 10 | NVDA | **$0.064 (฿2.11)** | 2 → $0.028 | $0.036 | 39 s | P50 154.93, P(up) 54%, conf 0.62 |
| 11 | BTC-USD | **$0.123 (฿4.05)** | 6 → $0.084 | $0.039 | 38 s | P50 62,658, P(up) 48%, conf 0.55 |
| 12 | GC=F | **$0.079 (฿2.62)** | 3 → $0.042 | $0.037 | 44 s | P50 3,646, P(up) 46%, conf 0.72 |

- **Cost per swing prediction on 3.8-flash: ≈ $0.06–0.12 (฿2–4), average ฿2.9.** Tokens are a steady ≈ $0.037 (≈ ฿1.2); the rest is Google Search, which the tracker charges at list price even though the first 5,000 queries a month are free. Actual billing is probably about ฿1.2 per prediction. At the tracked rate, ฿100 covers about 30 swing predictions.
- Only the Business and Macro agents searched (2–6 queries per prediction). Each analyst made exactly 2 LLM calls; the debate made 3 and the Judge 1 (≈ 750 thinking tokens at `JUDGE_THINKING=medium`).
- **No `output not valid JSON` warnings** in any of the 3 runs (`logs/seed_*.log`): zero fix-up or repair calls, and every agent was present.
- Latency rose from ~11 s (flash-lite) to ~40 s. No agent hit the 45 s per-agent timeout, but there's little headroom. If one shows "absent (timeout)" during the demo, raise `AGENT_TIMEOUT_S` to 60.


## Pre-demo polish (2026-11-19, after checkpoint 9)

| Change | Verified by |
|---|---|
| **Sequential debate**: Bull opens (≤150 words) → Bear reads the Bull's text and rebuts (≤150) → Bull short reply (≤80, hard-capped in code). Judge gets the full transcript. UI shows a chat-style thread (🐂 / 🐻 avatars, "↩ responding to: …"). | mocked: order, who-saw-what, 80-word cap, Judge input (`test_orchestrator.py`), 3 chat messages (`test_app.py`); real: `ui_smoke.py NVDA swing 1M` → thread Bull → Bear → Bull, Bear explicitly rebutted the Bull's oversold-bounce point, reply 61 words |
| **Deterministic signal labels** in `get_indicators.signals`: `price_vs_bollinger`, `rsi_zone`, `macd_cross` (+`macd_cross_bars_ago`, last 3 bars), `macd_momentum`, `trend_vs_sma20/50/200`, `sma_regime`, `volume_state`, `volatility_state`; plus `pct_vs_sma` and `pct_to_nearest` support/resistance. Technical prompt now must rely on and quote them as `label=value`. | unit tests per label + consistency with raw numbers (`test_mcp_tools.py`); live payloads ≈ 950 B; real: Technical key points quote labels verbatim, e.g. `rsi_zone=oversold (RSI 22.2)`, `price_vs_bollinger=near_lower_band (%B 0.14)` |
| **Replay syncs the sidebar** (ticker, mode, horizon) from the Load button and from `?replay=<id>`. | `test_app.py` (Load button + query param + switching mode afterwards) |
| **Tolerant JSON parser** (in code, before any repair call): tries the whole text, then the contents of ```` ```json ```` / ```` ``` ```` fences (any case), then the first complete `{...}` object found by `json.JSONDecoder.raw_decode` from each `{`. So leading/trailing prose (even prose containing braces), nested objects, unterminated fences and BOMs are all handled; non-object JSON (e.g. arrays) is rejected. | `test_llm.py`: 11 accepted shapes, 5 rejected, nested/first-object case, and an end-to-end check that fenced output with prose needs **zero** repair calls |
| **Safer JSON recovery** (found during verification, see Known issues): on a parse failure the agent first fixes its JSON *in the same conversation* (tools disabled, all tool results still in context); the context-free JSON-mode conversion is now a last resort, never runs on empty text, and is told to report confidence 0 rather than invent. Failing text is logged at WARNING. | `test_llm.py` (4 new cases) |


## How to run

```powershell
.venv\Scripts\streamlit run app.py                    # app → http://localhost:8501
.venv\Scripts\python orchestrator.py NVDA swing 1M    # one prediction in the terminal
.venv\Scripts\python scripts\auto_scalp.py --runs 4 --interval 900   # headless Auto-Scalp
.venv\Scripts\python -m pytest -q                     # tests
```

Before the demo, make sure `.env` really has `ANALYST_MODEL=gemini-3.8-flash` and `JUDGE_MODEL=gemini-3.8-flash` (and optionally `SCALP_MODEL`). As of 23:10 it still said flash-lite. Its price is already in `config.PRICES`, and its capability probe is already cached.

## Checkpoint 8: how it was verified (re-check anytime)

The headless Auto-Scalp started at 22:29 local (15:29 UTC), with 4 BTC-USD 15m predictions 5 min apart (#4 and the next three). It then keeps evaluating until about 23:02 local and prints final metrics. Done = **≥ 3 evaluated `auto_scalp` predictions**.

```powershell
Get-Content logs\auto_scalp.log | Select-String -NotMatch "HTTP Request"     # FINAL metrics line at the end
.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); from core import scoreboard as s; s.evaluate_due(); import sqlite3; c=sqlite3.connect('data/oracle.db'); print(c.execute(\"select count(*) from predictions where source='auto_scalp' and actual_price is not null\").fetchone())"
```

Or open the app's **Scoreboard** tab, which evaluates due predictions on open. If fewer than 3 are evaluated, toggle **Auto-Scalp** in that tab (every 15 min, cap 40 per session) and check back after an hour.

## Current spend (at 2026-11-19 ~23:12 local, after the demo seeds)

**$0.435 ≈ ฿14.37 of ฿100**: $0.170 on gemini-3.5-flash-lite (everything up to the polish pass, including Auto-Scalp) + $0.266 on gemini-3.8-flash (3 demo seeds + 1 capability probe). The sidebar always shows the live figure, and `cost.spend_summary()` has the details.

Observed cost per prediction (flash-lite): swing ≈ $0.014–0.043 (≈ ฿0.45–1.40, higher when the model searches), scalp ≈ $0.008 (≈ ฿0.27). gemini-3.8-flash is about 2.5× the per-token price, so a swing prediction should cost roughly ฿1–4.

### Real Gemini usage log (cap ≈ 20 real predictions; used 11 + 3 polish + 3 demo seeds)

| # | What | Cost |
|---|---|---|
| 1 | CP4: Technical agent alone, BTC-USD swing 1M | $0.0029 |
| 2–4 | CP5: all 5 agents × NVDA / BTC-USD / GC=F (swing 1M) | $0.0089 / $0.0096 / $0.0083 (+ uncounted searches, see Known issues) |
| 5 | CP6: `orchestrator.py NVDA swing 1M` (prediction #1) | $0.0137 |
| 6 | CP6: `orchestrator.py BTC-USD scalp 15m` (prediction #2) | $0.0075 |
| 7 | CP7: UI Predict button, GC=F swing 1M (prediction #3) | $0.0427 (incl. search) |
| 8–11 | CP8: Auto-Scalp BTC-USD 15m (predictions #4–#7) | ≈ $0.009 each |
| – | capability probes + one debug search call | ≈ $0.014 |
| P1 | Polish: UI Predict NVDA swing 1M (prediction #8, 3-turn debate + labels) | ≈ $0.04 |
| P2 | Polish: `orchestrator.py BTC-USD scalp 15m` (prediction #9) | $0.0086 |
| P3 | Polish: Technical agent alone, BTC-USD scalp 15m (diagnosing #9) | $0.0019 |
| S1–S3 | Demo seeds on **gemini-3.8-flash**: NVDA / BTC-USD / GC=F swing 1M (#10–#12) | $0.0638 / $0.1226 / $0.0794 |

## What you should check manually

1. **Checkpoint 8 result.** See above.
2. **UI in a real browser.** I only reviewed headless screenshots (`docs/`). Click through the presets, the scalp horizons, the Replay selector, the Scoreboard tab and the Auto-Scalp toggle.
3. **Real billing vs tracked spend.** Compare Google Cloud Billing with the sidebar figure, and set a Cloud Billing budget alert (§4.10 second safety net). The tracker charges $0.014 per search query even inside Google's 5,000 free per month, so it should read high, not low.
4. **gemini-3.8-flash.** After switching `.env`, run one `orchestrator.py NVDA swing 1M` and confirm it works. The capability probe re-runs automatically for the new model ID. Its price doubles on 2027-01-01, and that change is already in the table.
5. **Claude Code MCP.** In a new Claude Code session in this folder, ask it to call `get_indicators` for BTC-USD 1h. The server is registered in local scope (`~/.claude.json`).
6. **Claude provider.** It is implemented but only covered by mocks and never called for real. To use it, set `LLM_PROVIDER=claude` and the `CLAUDE_*_MODEL` IDs (see `.env.example`), and add prices for any model not already in `config.PRICES`.
7. **Prompt quality.** flash-lite sometimes misreads indicator values (see Known issues). Read a few agent cards before presenting.

## Decisions

- **mcp 2.x**: installed SDK is `mcp==2.3.0`; `FastMCP` → `mcp.server.mcpserver.MCPServer`; client uses `mcp.Client(StdioServerParameters(...))`. Pinned `mcp>=2.3,<3`.
- **FRED**: uses the API when `FRED_API_KEY` is set, otherwise the public `fredgraph.csv` endpoint (no key).
- **`get_sentiment(asset_type, ticker=None)`**: optional ticker; defaults to proxy SPY / BTC-USD / GC=F.
- **No Windows event-loop policy workaround**: stdio works on Windows/Python 3.14 without it (and the policy API is deprecated in 3.14).
- **CPI YoY** is computed by date (12 months back, monthly-interpolated) because FRED has missing months (Oct 2025).
- **Cache** lives in `core/cache.py`, all tables in `core/db.py` (`data/oracle.db`); errors are never cached.
- **Checkpoint 3 verification**: `tests/test_mcp_client.py` spawns the same command Claude Code uses and calls `get_indicators("BTC-USD","1h")` through `core/mcp_client.py`. Registration is local scope.
- **stdout hygiene**: server logs to stderr only; mcp 2.x also diverts fd 1 while serving.
- **Search + function calling (Gemini)**: requires `tool_config.include_server_side_tool_invocations=True`; search calls then come back as `tool_call` parts (`GOOGLE_SEARCH_WEB`) and are counted from there. Capability is probed once per model (tiny request, cached 7 days, key `cap:gemini:<model>:<feature>`). If unsupported, it uses the News Researcher with Search only. If Search itself is unsupported or `ENABLE_SEARCH=false`, the News Researcher builds the brief from the `get_news` MCP tool.
- **Structured output**: the tool loop runs without JSON mode and the final text is parsed as JSON. If parsing fails, one extra "format" call uses JSON mode + `response_json_schema` (Claude: `output_config.format`). A second failure drops the agent.
- **Turn limit**: at max_turns the model is told to answer and function calling is set to `NONE`.
- **Cost**: price table in `config.PRICES`. gemini-3.5-flash-lite is $0.30 in / $2.50 out per 1M tokens. gemini-3.8-flash is $0.75 / $3.75 until 2026-12-31, then $1.50 / $7.50. Search is $0.014 per query, ignoring the free tier. Unknown models are charged a pessimistic fallback price. Thinking tokens are billed as output.
- **`llm_usage.run_id`** (extra column vs §4.8) links usage rows written during a run; `prediction_id` is filled in on save. `predictions` also has `run_id`, `cost_usd`, `source`, `prediction_json`.
- **Budget guard**: before a prediction, refuse if `spent + estimate > BUDGET_THB`. Estimate = avg of the last 10 predictions of the mode × 1.25 (default $0.20 swing / $0.05 scalp). Also checked before every single LLM call. Auto-Scalp is additionally capped at `AUTO_SCALP_MAX_RUNS`.
- **Claude provider**: `anthropic` SDK, manual loop, server `web_search`, `output_config.effort`; model IDs from `CLAUDE_*_MODEL`. Mocks only.
- **Debate** (updated in the polish pass): sequential Bull opening → Bear rebuttal → Bull reply (3 LLM calls, was 2 in parallel). If a turn fails the debate stops there. The reply is clipped to 80 words in code. Old stored predictions (no `turn` field) still render, bull first.
- **Scalp mode**: Technical, Macro ("macro-lite" = max 3 tool turns), Psychology. No web search, no News Researcher, no debate; the Judge still runs (~4 LLM calls).
- **Judge** sees each analyst's tool trace; adjustment is clipped to ±0.2 in code.
- **Agent timeout** is 45 s (`AGENT_TIMEOUT_S`). A timed-out agent is marked absent and the weights are renormalized. If every analyst fails, S=0 and the Judge is skipped.
- **Monte Carlo**: log-normal increments N(μ_adj, σ) without the −σ²/2 term, so S=0 ⇒ median = today's price, P(up) ≈ 50%. Steps: stocks/gold 5/21/63 trading days, crypto 7/30/91 days; scalp 15×1m, 12×5m, 16×15m. σ from 1y daily (swing) or 5d of the scalp interval.
- **Hit rate** uses predicted direction = `prob_up > 0.5` (exact 0.5 excluded). Agent leaderboard uses sign(score), skipping 0. Daily-bar evaluation uses the close of the target date (waits ≥ 1 day; tolerates weekends). Intraday uses the last bar at or before the target, with a 5m fallback when 1m data has aged out.
- **Prediction storage**: full `Prediction` JSON in `predictions.prediction_json` for replay without API calls (sidebar selector or `?replay=<id>`).
- **UI**: Plotly with the dataviz reference palette (blue series; blue/red diverging scores; gray zero line), one y-axis, hover everywhere, dark-mode steps from `st.context.theme`. The "score gauge" is a diverging bar row (agents + team S_final) instead of a dial. The LLM selector lists `claude` only when `CLAUDE_ANALYST_MODEL` is set.
- **Auto-Scalp**: one `tick()` shared by the Streamlit fragment (checks every 20 s, runs every `AUTO_SCALP_INTERVAL_S` = 900 s) and `scripts/auto_scalp.py`. The verification run used a 300 s interval to get evaluated results sooner.
- **Git**: repo-local identity set to the account email; `.env`, `data/*.db` and `logs/` are git-ignored. `.env` has never been committed.

## Known issues

- **`.env` was not switched to gemini-3.8-flash**: all three model lines still read `gemini-3.5-flash-lite` (3.8-flash appears only in a comment), so the app and CLI still run flash-lite. The demo seeds were run with `ANALYST_MODEL`/`JUDGE_MODEL`/`SCALP_MODEL` set as environment variables (python-dotenv doesn't override existing variables). Save `.env` before the demo.

- **Scalp bands may be too narrow** (addressed by the horizon-scale σ quick fix; re-check coverage once new scalp predictions are evaluated): only 2 of 5 evaluated BTC 15m predictions landed inside P10–P90 (#4 and #5 closed below P10 during a sell-off). A well-calibrated band covers ~80%. n=5 is far too small to conclude anything, but if it persists, σ from the last 5 days of 1m bars underestimates 15-minute moves (volatility clustering / fat tails). Options: a longer lookback, or σ from 15m returns scaled to the horizon. Watch the Scoreboard as more data comes in.

- **Intermittent invalid JSON from flash-lite** (seen once in the polish run, prediction #9; none in the 3 gemini-3.8-flash seeds): the Technical and Psychology agents' final text failed to parse, and the old context-free repair call produced generic filler ("Positive momentum observed"). Recovery now fixes the JSON inside the conversation first (see the polish table). The tolerant parser now accepts fenced or prose-wrapped JSON without any repair call. The failing text wasn't captured, so the root cause is unknown. It's logged now, so watch the console for `output not valid JSON` warnings. Prediction #9 stays in the DB as it was.

- CP5 runs happened before server-side search `tool_call` parts were counted, so their recorded cost excludes search requests (at most ~10 queries ≈ $0.14 at list price, likely $0 under the free tier). Fixed since then in `GeminiBackend._search_queries`.
- flash-lite occasionally misreads indicator values (e.g. said price was below the lower Bollinger band when it was above). The Judge sees the trace and is told to be skeptical; gemini-3.8-flash should help.
- In grounded mode the analysts may choose `get_news` instead of Google Search (prediction #1 made no searches). That's the model's choice and it's cheaper. The News Researcher is used only when search can't be combined with tools.
- yfinance keeps 1m bars for ~7 days, so scalp predictions older than that are evaluated from 5m bars (15m horizon) or not at all.
