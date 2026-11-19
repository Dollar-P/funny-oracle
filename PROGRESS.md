# Progress

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| 1 | Project setup, venv, `.env` | ✅ done | `python -c "import yfinance, google.genai, mcp"` |
| 2 | `mcp_server` + `data_sources` + `indicators` | ✅ done | `pytest tests/test_mcp_tools.py` (25 passed, live data) |
| 3 | Register MCP in Claude Code | ✅ done | `claude mcp add market-data` (✔ Connected) + `pytest tests/test_mcp_client.py` (stdio + in-process `get_indicators("BTC-USD","1h")`) |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | ✅ done | `python scripts/run_agents.py BTC-USD --agents technical` → valid AgentReport, $0.0029 logged in `llm_usage`; `pytest tests/test_llm.py` (mocked) |
| 5 | Remaining agents + News Researcher | ✅ done | `python scripts/run_agents.py <T> --agents all` for NVDA, BTC-USD, GC=F → 15/15 valid reports; News Researcher (search + get_news modes) covered by mocks |
| 6 | Debate + Judge + Monte Carlo + orchestrator | ✅ done | `python orchestrator.py NVDA swing 1M` → P10 134.85 / P50 155.51 / P90 178.89 (11 s, $0.0137); also `BTC-USD scalp 15m`; `pytest tests/test_montecarlo.py tests/test_orchestrator.py` |
| 7 | Streamlit UI | ✅ done | `python scripts/ui_smoke.py GC=F swing 1M` (real: Predict → every section, 2 charts, no errors); `pytest tests/test_app.py` (AppTest, mocked); `streamlit run app.py` health OK; light/dark/scalp screenshots reviewed (`docs/`) |
| 8 | Scoreboard + Auto-Scalp | 🟡 running — verify later (see below) | Scoreboard evaluation/metrics/leaderboard + Auto-Scalp tick: `pytest tests/test_orchestrator.py tests/test_autoscalp.py` (mocked). Real: `scripts/auto_scalp.py --runs 4 --interval 300` started 2026-11-19 22:29 local; first run = prediction #4 |
| 9 | README + screenshots + demo rehearsal | ✅ done (live rehearsal is yours) | `README.md`, `docs/*.png`; demo steps 2–5 exercised via CLI / AppTest (NVDA swing, GC=F swing, debate, trace) |

Full suite: `pytest -q` → **85 passed** (network tests included).

## Pre-demo polish (2026-11-19, after checkpoint 9)

| Change | Verified by |
|---|---|
| **Sequential debate**: Bull opens (≤150 words) → Bear reads the Bull's text and rebuts (≤150) → Bull short reply (≤80, hard-capped in code). Judge gets the full transcript. UI shows a chat-style thread (🐂 / 🐻 avatars, "↩ responding to: …"). | mocked: order, who-saw-what, 80-word cap, Judge input (`test_orchestrator.py`), 3 chat messages (`test_app.py`); real: `ui_smoke.py NVDA swing 1M` → thread Bull → Bear → Bull, Bear explicitly rebutted the Bull's oversold-bounce point, reply 61 words |
| **Deterministic signal labels** in `get_indicators.signals`: `price_vs_bollinger`, `rsi_zone`, `macd_cross` (+`macd_cross_bars_ago`, last 3 bars), `macd_momentum`, `trend_vs_sma20/50/200`, `sma_regime`, `volume_state`, `volatility_state`; plus `pct_vs_sma` and `pct_to_nearest` support/resistance. Technical prompt now must rely on and quote them as `label=value`. | unit tests per label + consistency with raw numbers (`test_mcp_tools.py`); live payloads ≈ 950 B; real: Technical key points quote labels verbatim, e.g. `rsi_zone=oversold (RSI 22.2)`, `price_vs_bollinger=near_lower_band (%B 0.14)` |
| **Replay syncs the sidebar** (ticker, mode, horizon) from the Load button and from `?replay=<id>`. | `test_app.py` (Load button + query param + switching mode afterwards) |
| **Safer JSON recovery** (found during verification, see Known issues): on a parse failure the agent first fixes its JSON *in the same conversation* (tools disabled, all tool results still in context); the context-free JSON-mode conversion is now a last resort, never runs on empty text, and is told to report confidence 0 rather than invent. Failing text is logged at WARNING. | `test_llm.py` (4 new cases) |


## How to run

```powershell
.venv\Scripts\streamlit run app.py                    # app → http://localhost:8501
.venv\Scripts\python orchestrator.py NVDA swing 1M    # one prediction in the terminal
.venv\Scripts\python scripts\auto_scalp.py --runs 4 --interval 900   # headless Auto-Scalp
.venv\Scripts\python -m pytest -q                     # tests
```

Before the demo, switch `ANALYST_MODEL` / `JUDGE_MODEL` (and optionally `SCALP_MODEL`) in `.env` to `gemini-3.8-flash`. Its price is already in `config.PRICES`.

## Checkpoint 8: how to verify later

The headless Auto-Scalp started at 22:29 local (15:29 UTC), with 4 BTC-USD 15m predictions 5 min apart (#4 and the next three). It then keeps evaluating until about 23:02 local and prints final metrics. Done = **≥ 3 evaluated `auto_scalp` predictions**.

```powershell
Get-Content logs\auto_scalp.log | Select-String -NotMatch "HTTP Request"     # FINAL metrics line at the end
.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); from core import scoreboard as s; s.evaluate_due(); import sqlite3; c=sqlite3.connect('data/oracle.db'); print(c.execute(\"select count(*) from predictions where source='auto_scalp' and actual_price is not null\").fetchone())"
```

Or open the app's **Scoreboard** tab, which evaluates due predictions on open. If fewer than 3 are evaluated, toggle **Auto-Scalp** in that tab (every 15 min, cap 40 per session) and check back after an hour.

## Current spend (at 2026-11-19 22:51 local, after the polish pass)

**$0.170 ≈ ฿5.59 of ฿100**, all gemini-3.5-flash-lite (includes all 4 Auto-Scalp runs). The sidebar always shows the live figure, and `cost.spend_summary()` has the details.

Observed cost per prediction (flash-lite): swing ≈ $0.014–0.043 (≈ ฿0.45–1.40, higher when the model searches), scalp ≈ $0.008 (≈ ฿0.27). gemini-3.8-flash is about 2.5× the per-token price, so a swing prediction should cost roughly ฿1–4.

### Real Gemini usage log (cap ≈ 20 real predictions; used 11 + 3 for the polish pass)

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

- **Intermittent invalid JSON from flash-lite** (seen once in the polish run, prediction #9): the Technical and Psychology agents' final text failed to parse, and the old context-free repair call produced generic filler ("Positive momentum observed"). Recovery now fixes the JSON inside the conversation first (see the polish table). The failing text wasn't captured, so the root cause is unknown. It's logged now, so watch the console for `output not valid JSON` warnings. Prediction #9 stays in the DB as it was.

- CP5 runs happened before server-side search `tool_call` parts were counted, so their recorded cost excludes search requests (at most ~10 queries ≈ $0.14 at list price, likely $0 under the free tier). Fixed since then in `GeminiBackend._search_queries`.
- flash-lite occasionally misreads indicator values (e.g. said price was below the lower Bollinger band when it was above). The Judge sees the trace and is told to be skeptical; gemini-3.8-flash should help.
- In grounded mode the analysts may choose `get_news` instead of Google Search (prediction #1 made no searches). That's the model's choice and it's cheaper. The News Researcher is used only when search can't be combined with tools.
- yfinance keeps 1m bars for ~7 days, so scalp predictions older than that are evaluated from 5m bars (15m horizon) or not at all.
