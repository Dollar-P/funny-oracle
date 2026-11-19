# Progress

Status as of 2026-11-20. Default model: **gemini-3.5-flash-lite** for every role (pick others per run in the
Arena / Duel). Full suite: `pytest -q` → **176 passed** (`tests/` + `api/tests`, set in `pytest.ini`; network
tests included).

## Status

### Original build (ARCHITECTURE.md)

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| 1 | Project setup, venv, `.env` | ✅ | `python -c "import yfinance, google.genai, mcp"` |
| 2 | `mcp_server` + `data_sources` + `indicators` | ✅ | `pytest tests/test_mcp_tools.py` (live NVDA / BTC-USD / GC=F) |
| 3 | Register MCP in Claude Code | ✅ | `claude mcp add market-data` (✔ Connected) + `pytest tests/test_mcp_client.py` (stdio + in-process `get_indicators("BTC-USD","1h")`) |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | ✅ | `scripts/run_agents.py BTC-USD --agents technical` → valid AgentReport, cost logged; `tests/test_llm.py` (mocked) |
| 5 | Remaining agents + News Researcher | ✅ | `scripts/run_agents.py <T> --agents all` for NVDA, BTC-USD, GC=F → 15/15 valid reports |
| 6 | Debate + Judge + Monte Carlo + orchestrator | ✅ | `orchestrator.py NVDA swing 1M` and `BTC-USD scalp 15m`; `tests/test_montecarlo.py`, `tests/test_orchestrator.py` |
| 7 | Streamlit UI | ✅ | `scripts/ui_smoke.py GC=F swing 1M` (real Predict click, every section); `tests/test_app.py` (AppTest, mocked) |
| 8 | Scoreboard + Auto-Scalp | ✅ | Real: `scripts/auto_scalp.py --runs 4 --interval 300` → 4/4 evaluated (#4–#7); `tests/test_autoscalp.py` |
| 9 | README + screenshots + demo rehearsal | ✅ (live rehearsal is yours) | `README.md`, `docs/*.png` |

### Pre-redesign polish and quick fixes (2026-11-19)

| Change | Verified by |
|---|---|
| Sequential debate: Bull opens (≤150 words) → Bear rebuts the Bull's text (≤150) → Bull replies (≤80, clipped in code); the Judge sees the transcript. | `test_orchestrator.py` (order, who saw what, cap); real `ui_smoke.py NVDA swing 1M` |
| Deterministic signal labels in `get_indicators.signals` (`rsi_zone`, `price_vs_bollinger`, `macd_cross`, `trend_vs_sma*`, …); the Technical prompt quotes them as `label=value`. | `test_mcp_tools.py`; real Technical key points quote the labels |
| `AGENT_TIMEOUT_S` default 60 s. | `test_cost.py::test_agent_timeout_default_is_60` |
| Scalp σ from horizon-scale returns over a longer lookback (see Decisions → Monte Carlo). | `test_montecarlo.py`, `test_orchestrator.py` |
| Tracked vs estimated-actual cost (Gemini search free tier). | `tests/test_cost.py` |
| Tolerant JSON parsing before any repair call: whole text → fences → first `{…}` object → one level of un-escaping (`\"`, `\\`, or a JSON string holding the object). | `test_llm.py` incl. the backslash-escaped Bull opening from the R8 logs (parsed with **zero** repair calls) |

### Redesign — "The Oracle Arena" (REDESIGN.md)

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| R1 | Event hook + per-run model override | ✅ | `tests/test_events.py`: full swing event order, scalp stages, agent_failed (error/timeout), news_brief, search events, no-callback path, broken callback, budget refusal → fatal `error`, per-role override + storage, cross-provider rejected, per-model estimate |
| R2 | FastAPI: RunManager, SSE, MockRunner, REST | ✅ | `api/tests` (every route; mock show validated against `api/events.py`; SSE `id`/`event`; `Last-Event-ID` / `?after=`; 404/422/429/402; real run via mocked orchestrator; duels; replay; leaderboard; Auto-Scalp); live `curl -N /api/runs/<id>/events` streamed a full 54-event show |
| R3 | Web scaffold | ✅ | `npm run build` (tsc + vite); headless screenshot: stage background, TopBar with live budget, MOCK badge, disclaimer |
| R4 | Arena setup | ✅ | `web/e2e/setup.mjs`: search "eth" → ETH-USD, preset chips with sparklines, Scalp/1h, model card, Advanced judge, Start → run streams. Over-budget server (`BUDGET_THB=10`) → Start disabled with reason |
| R5 | Live war room | ✅ (committed with R6) | `web/e2e/show.mjs`: all events received; podium states, tool + search bubbles, provisional rope, 6/6 rail steps, 3 debate turns typed in order, ticker tape, agent drawer |
| R6 | Verdict reveal, forecast, Replay page | ✅ | `show.mjs` swing + scalp (countdown → ✅/❌); `web/e2e/replay.mjs 10`: `/p/10` renders; "▶ Replay the show" ends in the verdict |
| R7 | Duel, Hall of Fame, Auto-Scalp card, share card | ✅ | `web/e2e/r7.mjs`: Hall of Fame on the real DB, 1080×1350 share PNG, mock duel → head-to-head |
| R8 | Real-run verification + polish | ✅ | 5 real runs through the production build in headless Chrome (table below), every agent present; reduced motion (no drumroll / typewriter / confetti) vs normal (all three); 390 px with no horizontal scroll (`web/e2e/mobile.mjs`); `scripts\dev.ps1` brings up both servers; screenshots in `docs/`. CLI and Streamlit unchanged |

## Local LLM (branch `local-llm`, in progress — not merged)

Spec: `LOCAL_LLM.md`. Done and unit-tested (mocked server): step 2 `openai_compat` provider in `core/llm.py`
(lessons 1-8, 10); step 3 `web_search` / `fetch_url` MCP tools (lesson 9); step 4 pipeline wiring (News
Researcher `web` mode, LOCAL_MAX_TURNS / LOCAL_TIMEOUT_S, queue-aware agent timeouts, Quick/Deep preset,
scalp refusal). **Not done yet:** step 5 API + Arena/Duel model picker for local models; step 6 real runs
against LM Studio (1 swing Quick + 1 duel local vs gemini-3.5-flash-lite); step 7 final docs.

## GitHub readiness (2026-11-20, not pushed)

- **Secrets scan** of the working tree and all commits (every blob on every ref, commit messages, plus the
  *exact* key values from the local `.env`): no Gemini, Anthropic or FRED key anywhere; `.env`, `data/*.db`
  and `logs/` were never committed; no stashes or unreachable objects. Note: the Gemini key in `.env` uses the
  newer `AQ.`-prefixed format, which an `AIza…` pattern alone would miss.
- **Private LAN address** of the LM Studio host was hard-coded in `scripts/test_lmstudio.py` and
  `scripts/eval_local_llm.py`. Both now read `LOCAL_BASE_URL` (env or `.env`, default `http://localhost:1234/v1`).
  The address was also removed from the whole git history, and every commit's author/committer email was
  changed to the GitHub noreply address, in one rewrite (pre-rewrite state kept in the local branch
  `backup/pre-rewrite`, which must never be pushed).
- **Seeds**: `data/seeds.json` (#4, #9, #10–#17, 209 KB) + `core/seeds.py` / `scripts/seeds.py`; the API loads
  them on startup into an empty DB, so a fresh clone runs mock mode and `/p/<id>` with no key.
- `.gitignore` also covers `.env.*` (except the template), SQLite side files, e2e outputs, editor/OS files.
  `LICENSE` (MIT) added.

## How to run

See **README → How to run** (one section for the Arena dev / production servers, Streamlit, the CLI and
Auto-Scalp) and **README → Tests** (pytest + browser checks).

## Spend

**Total: ฿21.65 tracked / ฿11.49 estimated actual of ฿100** (2026-11-20 00:30 local). Live figures: top-bar
budget meter, Streamlit sidebar, `GET /api/budget`, `cost.spend_summary()`.

Observed per prediction (tracked): flash-lite swing ฿0.6–1.6 (more when it searches), flash-lite scalp ≈ ฿0.3,
gemini-3.8-flash swing ฿2–4. Most of the gap between tracked and estimated actual is Google Search, which is
free for the first 5,000 queries a month.

### Real LLM usage log

| Runs | What | Model | Cost (tracked) |
|---|---|---|---|
| CP4–CP5 | Technical alone; all 5 agents × NVDA / BTC-USD / GC=F | flash-lite | ≈ $0.03 (searches not yet counted then) |
| #1–#3 | CP6 CLI NVDA swing, BTC scalp; CP7 UI GC=F swing | flash-lite | $0.064 |
| #4–#7 | CP8 Auto-Scalp BTC 15m | flash-lite | ≈ $0.009 each |
| #8–#9 | Polish: UI NVDA swing, CLI BTC scalp (+ one Technical-only diagnosis) | flash-lite | ≈ $0.05 |
| #10–#12 | Demo seeds NVDA / BTC-USD / GC=F swing 1M | gemini-3.8-flash | $0.064 / $0.123 / $0.079 |
| #13–#15 | R8: Arena NVDA / BTC-USD / GC=F swing 1M | flash-lite | ฿0.64 / ฿1.42 / ฿1.57 (26 / 25 / 49 s) |
| #16–#17 | R8: Duel NVDA swing 1M, 3.8-flash vs flash-lite | both | ฿3.04 / ฿0.61 (36 / 37 s) — "🤝 Both bullish" |
| – | capability probes, one debug search call | – | ≈ $0.014 |

## Check manually

1. **A real browser with sound on.** I only drove headless Chrome, which has no audio. Also try
   copy-to-clipboard on the share card (headless can't verify the clipboard).
2. **A real scalp run in the Arena.** Only mock scalp shows were checked in the UI; real scalp ran via CLI and
   Auto-Scalp.
3. **Auto-Scalp start/stop from the Hall of Fame in real mode.** API-tested with a fake `tick` only.
4. **Real billing vs tracked spend.** Compare Google Cloud Billing with the estimated-actual figure, and set a
   Cloud Billing budget alert.
5. **Claude provider.** Implemented but only covered by mocks. To use it, set `LLM_PROVIDER=claude` and the
   `CLAUDE_*_MODEL` IDs, and add prices for any model not already in `config.PRICES`.

## Decisions

### Backend

- **mcp 2.x**: `mcp==2.3.0`; `FastMCP` → `mcp.server.mcpserver.MCPServer`; the client uses
  `mcp.Client(StdioServerParameters(...))`. Pinned `mcp>=2.3,<3`. The server logs to stderr only; mcp 2.x also
  diverts fd 1 while serving. No Windows event-loop policy workaround is needed on Python 3.14.
- **Data**: FRED API when `FRED_API_KEY` is set, otherwise the public `fredgraph.csv` endpoint. CPI YoY is
  computed by date because FRED has missing months. `get_sentiment(asset_type, ticker=None)` defaults to
  SPY / BTC-USD / GC=F. Cache in `core/cache.py`, all tables in `core/db.py` (`data/oracle.db`); errors are
  never cached.
- **Search + function calling (Gemini)** needs `tool_config.include_server_side_tool_invocations=True`;
  searches then arrive as `tool_call` parts (`GOOGLE_SEARCH_WEB`) and are counted from there. Capability is
  probed once per model (cached 7 days). Fallbacks: News Researcher with Search only, then the `get_news` tool
  (also when `ENABLE_SEARCH=false`).
- **Structured output**: the tool loop runs without JSON mode and the final text is parsed in code (tolerant
  parser, see the polish table). On failure the agent fixes its JSON in the same conversation (tools off, tool
  results still in context); a context-free JSON-mode conversion is the last resort, never runs on empty text,
  and reports confidence 0 rather than invent. Failing text is logged at WARNING. A third failure drops the agent.
- **Turn limit**: at `max_turns` the model is told to answer and function calling is set to `NONE`.
- **Agent timeout**: 60 s (`AGENT_TIMEOUT_S`). A timed-out or failed agent is marked absent and the weights
  are renormalized. If every analyst fails, S = 0 and the Judge is skipped.
- **Debate & Judge**: sequential Bull → Bear → Bull reply (3 calls); if a turn fails the debate stops there.
  The Judge sees each analyst's tool trace; its adjustment is clipped to ±0.2 in code.
- **Scalp mode**: Technical, Macro-lite (≤ 3 tool turns), Psychology; no search, no News Researcher, no
  debate; the Judge still runs (~4 LLM calls).
- **Monte Carlo**: log-normal increments N(μ_adj, σ) without the −σ²/2 term, so S = 0 ⇒ median = today's
  price and P(up) ≈ 50%. Steps: stocks/gold 5/21/63 trading days, crypto 7/30/91 days; scalp 15×1m, 12×5m,
  16×15m. **σ**: swing = per-bar σ of 1 year of daily returns. Scalp = σ of returns over the *whole horizon*
  (15m from 15m bars over 60 days; 1h from 1h bars over 6 months; 4h from 1h bars over 1 year;
  `config.SCALP_SIGMA`), counting only returns whose start bar is exactly one horizon earlier (so overnight /
  weekend gaps are skipped), then σ_step = σ_horizon / √steps. Falls back to per-bar σ of the scalp interval
  if fewer than 30 horizon returns exist or the fetch fails.
- **Cost**: prices in `config.PRICES` (flash-lite $0.30 / $2.50 per 1M tokens; gemini-3.8-flash $0.75 / $3.75
  until 2026-12-31, then $1.50 / $7.50; thinking tokens billed as output; unknown models get a pessimistic
  fallback). **Tracked** = every search at $0.014 (stored in `llm_usage.cost_usd`). **Estimated actual** =
  first `GEMINI_SEARCH_FREE_PER_MONTH` (5,000) Gemini searches per UTC month free, allocated in call order and
  shared across Gemini models; Claude searches are never free.
- **Budget guard**: refuses a prediction if `spent + estimate > BUDGET_THB` (spent on the `BUDGET_BASIS`,
  default tracked), and checks before every LLM call. Estimate = that analyst model's last-10 average × 1.25;
  without history, the mode default ($0.20 swing / $0.05 scalp) scaled up by the model's token price relative
  to gemini-3.8-flash. Auto-Scalp is also capped at `AUTO_SCALP_MAX_RUNS`.
- **Storage**: `predictions` has `run_id`, `cost_usd`, `cost_actual_usd`, `source` (manual | cli | auto_scalp |
  web), the full `prediction_json` for replay, and (since R1) `analyst_model`, `judge_model`, `duel_id`, added by
  an additive migration that also backfills the models of older rows from `llm_usage`. `llm_usage.run_id`
  links usage rows written during a run.
- **Scoring**: hit rate uses predicted direction = `prob_up > 0.5` (exactly 0.5 excluded); agent leaderboard
  uses sign(score); daily evaluation uses the close of the target date (waits ≥ 1 day, tolerates weekends);
  intraday uses the last bar at or before the target, falling back to 5m bars when 1m has aged out. Duel win
  = P50 closer to the actual price.
- **Claude provider**: `anthropic` SDK, manual loop, server `web_search`, `output_config.effort`; model IDs
  from `CLAUDE_*_MODEL`. Mocks only.
- **Git**: `.env`, `data/*.db`, `logs/`, `web/node_modules`, `web/dist` are git-ignored; `.env` has never been
  committed.

### Web (redesign)

- **Events** (`core/events.py`): an `EventSink` that is a no-op when `on_event=None`. Envelope
  `{type, run_id, ts (epoch ms), seq}`. The `LLM` object carries the sink, so agents, debate and backends emit
  without signature changes; tool events come from the per-agent executor wrapper. Extra event `news_brief`.
  `stage: news` is swing only. A failing callback is logged and ignored.
- **Per-run models**: `run_prediction(..., analyst_model=, judge_model=, run_id=, duel_id=)`; None → `.env`
  for that role. The API uses the analyst model for the judge too unless a separate judge is picked. Provider
  is inferred from the model ID; mixing providers in one run is rejected.
- **RunManager**: every run's events stay in memory (last 50 finished runs) and stream with `seq` as the SSE
  `id`, so reconnects resume exactly. Real runs execute in a worker thread with their own event loop (keeps
  blocking yfinance work off the server loop and gives the MCP stdio subprocess a Proactor loop on Windows);
  events are marshalled back with `run_coroutine_threadsafe`. Max 2 concurrent runs → 429.
- **MockRunner**: picks a stored prediction (same ticker+mode → same asset type+mode → same mode → any),
  relabels it for the requested ticker/horizon, rescales prices to today's price, shifts the clock to now and
  shows the models the user picked. Not saved, no budget check. Mock duels use two different stored shows.
  Extra endpoints: `POST /api/predictions/{id}/replay`, `GET /api/runs[/{id}]`, `GET /api/health`,
  `GET /api/predictions/{id}?evaluate=1`.
- **UI state**: one pure reducer (`web/src/state/show.ts`) turns events into show state and drops duplicate
  `seq`s, so EventSource reconnects and page reloads just replay; the stream is closed on `run_done` / fatal
  `error`. The rope shows a provisional S from finished agents until `aggregate`. The verdict waits until the
  debate has been typed out; the drumroll plays once per run. Duel lanes report their reduced state from an
  effect.
- **Fan chart** (lightweight-charts 5), fixed 2026-11-20 after a mock/replay of #4 (BTC scalp 15m) showed three
  inconsistencies (before/after screenshots in `docs/chart_consistency/`):
  - **Local time**: timestamps are shifted by the browser's UTC offset before they reach the chart (the
    library's recommended approach), so axis, crosshair and day boundaries read in e.g. Asia/Bangkok and match
    the tables and headers. The caption names the timezone.
  - **Anchored to the prediction's own time**: the chart fetches `GET /api/history/{t}?interval=&start=&end=`
    around `created_at` (120 scalp bars / ~130 trading days before, until the target + 25%), falling back to a
    coarser interval once yfinance no longer keeps the requested one. The fan starts at the original price;
    candles after `created_at` are faded and drawn *above* the fan (the P10 shading had been hiding reality
    whenever it fell below P10); the actual price at `target_time` is a white dot with a labelled dotted line.
  - **Mock shows**: Arena/Duel mocks move to "now" and are always rescaled to the live last close *at the
    chart's interval*. Before, Arena mocks were rescaled to the 15-min-cached daily close (fan ≈ ฿240 off the
    last 1m candle), and "▶ Replay the show" shifted the clock but didn't rescale at all (fan at the old price
    beside today's candles). "Replay the show" now keeps the original time, prices, models and result,
    identical to `/p/:id`. A shifted mock no longer shows the original ✅/❌ next to the live chart; a labelled
    🎭 card says which prediction it replays, what really happened, and opens a mini chart of the original window.
    `run_started.shifted` / `rescaled` tell the UI which case it is.
  - Daily stock/gold fans skip weekends.
- **Share card**: an offscreen React component (inline styles, SVG mini fan) captured with `html-to-image`.
  The Google Fonts `<link>` needs `crossorigin="anonymous"` so the fonts embed. Model line and disclaimer are
  pinned to the bottom and the headline is clamped to 3 lines.
- **Stack**: React 19, react-router 7, TanStack Query 5, zustand 5, framer-motion 14, Tailwind 4 (tokens in
  `@theme`), Vite 8, TypeScript 7; fonts Lilita One / Inter / JetBrains Mono (OFL). Dark-only by design.
  Drawers and modals render through portals (a `backdrop-filter` ancestor would clip `position: fixed`).
  `playwright-core` (dev only, installed Chrome) drives `web/e2e/*.mjs`. `scripts\dev.ps1` stops the uvicorn
  process tree on exit (killing only the reloader orphans its worker).

## Known issues

- **Local branch `backup/pre-rewrite`** still holds the pre-rewrite history (old email, LAN address). Push only
  `main` (`git push -u origin main`), never `--all` / `--mirror`; delete the branch once the push is verified.

- **flash-lite schema slips**: in the R8 runs, Business and Technical sometimes returned JSON missing required
  fields (`4 validation errors for AgentOutput`). The in-conversation fix-up recovered every case (all agents
  present), at the cost of one extra call each. The over-escaped JSON case seen at the same time is now
  handled by the parser.
- **flash-lite occasionally misreads indicator values** (e.g. said price was below the lower Bollinger band
  when it was above). The deterministic signal labels and the Judge's view of the trace mitigate it; read a
  few agent cards before presenting.
- **Scalp band coverage**: before the horizon-scale σ fix, only 2 of 5 evaluated BTC 15m predictions landed in
  P10–P90 (target ≈ 80%). Re-check in the Hall of Fame once newer scalp predictions are evaluated.
- **Mock shows** rescale prices to today's price, but the agents' key points and debate text still quote the
  original numbers.
- In grounded mode the analysts may pick `get_news` instead of Google Search. That's the model's choice (and
  cheaper).
- yfinance keeps 1m bars for ~7 days, so older 15m scalp predictions are evaluated from 5m bars or not at all.
- CP5 runs predate search counting, so their recorded cost excludes a handful of searches.
