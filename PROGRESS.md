# Progress

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| 1 | Project setup, venv, `.env` | ✅ done | `python -c "import yfinance, google.genai, mcp"` |
| 2 | `mcp_server` + `data_sources` + `indicators` | ✅ done | `pytest tests/test_mcp_tools.py` (25 passed, live data) |
| 3 | Register MCP in Claude Code | ✅ done | `claude mcp add market-data` (✔ Connected) + `pytest tests/test_mcp_client.py` |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | ✅ done | `python scripts/run_agents.py BTC-USD --agents technical` → valid AgentReport, $0.0029 logged in `llm_usage`; `pytest tests/test_llm.py` (mocked) |
| 5 | Remaining agents + News Researcher | ✅ done | `python scripts/run_agents.py <T> --agents all` for NVDA, BTC-USD, GC=F → 15/15 valid reports; News Researcher (search + get_news modes) covered by mocks |
| 6 | Debate + Judge + Monte Carlo + orchestrator | ✅ done | `python orchestrator.py NVDA swing 1M` → P10 134.85 / P50 155.51 / P90 178.89 (11 s, $0.0137); also `BTC-USD scalp 15m`; `pytest tests/test_montecarlo.py tests/test_orchestrator.py` |
| 7 | Streamlit UI | ⏳ | |
| 8 | Scoreboard + Auto-Scalp | ⏳ | |
| 9 | README + demo rehearsal | ⏳ | |

## Real Gemini usage log (cap: ~20 real predictions)

| # | What | Model | Cost |
|---|---|---|---|
| 1 | CP4: Technical agent alone, BTC-USD swing 1M | gemini-3.5-flash-lite | $0.0029 |
| – | capability probes (search+functions) | gemini-3.5-flash-lite | <$0.0001 |
| 2–4 | CP5: all 5 agents × NVDA / BTC-USD / GC=F (swing 1M) | gemini-3.5-flash-lite | $0.0089 / $0.0096 / $0.0083 (+ uncounted searches, see Known issues) |
| – | one debug search call to inspect server-side tool parts | gemini-3.5-flash-lite | $0.0141 |
| 5 | CP6: `orchestrator.py NVDA swing 1M` (prediction #1) | gemini-3.5-flash-lite | $0.0137 |
| 6 | CP6: `orchestrator.py BTC-USD scalp 15m` (prediction #2) | gemini-3.5-flash-lite | $0.0075 |

## Decisions

- **mcp 2.x**: installed SDK is `mcp==2.3.0`; `FastMCP` → `mcp.server.mcpserver.MCPServer`; client uses `mcp.Client(StdioServerParameters(...))`. Pinned `mcp>=2.3,<3`.
- **FRED**: uses the API when `FRED_API_KEY` is set, otherwise the public `fredgraph.csv` endpoint (no key).
- **`get_sentiment(asset_type, ticker=None)`**: optional ticker; defaults to proxy SPY / BTC-USD / GC=F.
- **No Windows event-loop policy workaround**: stdio works on Windows/Python 3.14 without it (and the policy API is deprecated in 3.14).
- **CPI YoY** is computed by date (12 months back, monthly-interpolated) because FRED has missing months (Oct 2025).
- **Cache** lives in `core/cache.py`, all tables in `core/db.py` (`data/oracle.db`); errors are never cached.
- **Checkpoint 3 verification**: `tests/test_mcp_client.py` spawns the same command Claude Code uses and calls `get_indicators("BTC-USD","1h")` through `core/mcp_client.py`. Registration is local scope (`~/.claude.json`, project-private).
- **stdout hygiene**: server logs to stderr only; mcp 2.x also diverts fd 1 while serving.
- **Search + function calling (Gemini)**: requires `tool_config.include_server_side_tool_invocations=True`. Capability is probed once per model with a tiny request and cached 7 days (`cache` key `cap:gemini:<model>:<feature>`). If unsupported → News Researcher (search-only) path; if search itself is unsupported or `ENABLE_SEARCH=false` → News Researcher builds the brief from the `get_news` MCP tool.
- **Structured output**: the tool loop runs without JSON mode; the final text is parsed as JSON. If parsing fails, one extra "format" call uses `response_mime_type=application/json` + `response_json_schema` (Claude: `output_config.format`). Second failure → agent dropped.
- **Turn limit**: at max_turns the model is told to answer and function calling is set to `NONE`.
- **Cost**: price table in `config.PRICES` (gemini-3.5-flash-lite $0.30/$2.50; gemini-3.8-flash $0.75/$3.75 until 2026-12-31, then $1.50/$7.50; search $0.014/request — the free 5,000/month tier is ignored so the guard stays conservative). Unknown models are charged a pessimistic fallback price. Thinking tokens billed as output.
- **`llm_usage.run_id`** (extra column vs §4.8): usage rows are written during a run, before the prediction row exists; `run_id` links them, and `prediction_id` is filled in on save.
- **Budget guard**: before a prediction, `spent + estimate > BUDGET_THB` → refuse. Estimate = avg of last 10 predictions of the mode × 1.25 (default $0.20 swing / $0.05 scalp). Also checked before every single LLM call.
- **Claude provider** (`LLM_PROVIDER=claude`): implemented with the `anthropic` SDK (manual loop, server `web_search`, `output_config.effort`), model IDs from `CLAUDE_*_MODEL` in `.env`. Only covered by mocks — never called for real.

- **Debate**: Bull and Bear run in parallel on the same reports (one round), so each rebuts the opponent's *strongest likely* point rather than the opponent's actual text. Cheaper and faster; same number of LLM calls.
- **Scalp mode**: analysts = Technical, Macro ("macro-lite" = same agent with max 3 tool turns), Psychology; no web search, no News Researcher, no debate; Judge still runs (~4 LLM calls).
- **Judge** gets each analyst's tool trace (tool names + errors) so it can discount unbacked claims; adjustment clipped to ±0.2 in code regardless of model output.
- **Agent timeout** 45 s (`AGENT_TIMEOUT_S`) → agent marked absent, weights renormalized. If every analyst fails, S=0 and the judge is skipped.
- **Monte Carlo**: log-normal increments N(μ_adj, σ) without the −σ²/2 term, so S=0 ⇒ median = today's price and P(up) ≈ 50%. Steps: stocks/gold 5/21/63 trading days, crypto 7/30/91 days; scalp 15×1m, 12×5m, 16×15m. σ from 1y daily (swing) or 5d of the scalp interval.
- **Predicted direction** for the hit rate = `prob_up > 0.5`; predictions with exactly 0.5 are excluded from the hit rate. Agent leaderboard uses sign(score), skipping 0 scores.
- **Prediction storage**: the full `Prediction` JSON is stored in `predictions.prediction_json` so the UI can replay past runs without API calls (demo "replay mode").

## Known issues

- CP5 runs happened before server-side search `tool_call` parts were counted, so their recorded cost excludes search requests (at most ~10 queries ≈ $0.14 at list price, likely $0 under the 5,000/month free tier). Fixed since: `GeminiBackend._search_queries`.

- flash-lite occasionally misreads indicator values (e.g. said price was below the lower Bollinger band when it was above). The Judge sees the trace and is told to be skeptical; a stronger model (gemini-3.8-flash) should help.
