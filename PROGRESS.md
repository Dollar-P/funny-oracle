# Progress

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| 1 | Project setup, venv, `.env` | ✅ done | `python -c "import yfinance, google.genai, mcp"` |
| 2 | `mcp_server` + `data_sources` + `indicators` | ✅ done | `pytest tests/test_mcp_tools.py` (25 passed, live data) |
| 3 | Register MCP in Claude Code | ✅ done | `claude mcp add market-data` (status ✔ Connected) + `pytest tests/test_mcp_client.py` (stdio + in-process) |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | ⏳ | |
| 5 | Remaining agents + News Researcher | ⏳ | |
| 6 | Debate + Judge + Monte Carlo + orchestrator | ⏳ | |
| 7 | Streamlit UI | ⏳ | |
| 8 | Scoreboard + Auto-Scalp | ⏳ | |
| 9 | README + demo rehearsal | ⏳ | |

## Decisions

- **mcp 2.x**: installed SDK is `mcp==2.3.0`; `FastMCP` → `mcp.server.mcpserver.MCPServer`. Pinned `mcp>=2.3,<3`.
- **FRED**: uses the API when `FRED_API_KEY` is set, otherwise the public `fredgraph.csv` endpoint (no key).
- **`get_sentiment(asset_type, ticker=None)`**: optional ticker; defaults to proxy SPY / BTC-USD / GC=F.
- **No Windows event-loop policy workaround**: stdio works on Windows/Python 3.14 without it (and the policy API is deprecated in 3.14).
- **CPI YoY** is computed by date (12 months back, monthly-interpolated) because FRED has missing months (Oct 2025).
- **Cache** lives in `core/cache.py` (table `cache` in `data/oracle.db`); errors are never cached.

## Known issues

- (none yet)
- **Checkpoint 3 verification**: instead of waiting for Claude Code to reconnect, `tests/test_mcp_client.py` spawns the same command Claude Code uses and calls `get_indicators("BTC-USD","1h")` via `core/mcp_client.py` (mcp 2.x `mcp.Client` with `StdioServerParameters`). Registration is in local scope (`~/.claude.json`, project-private).
- **stdout hygiene**: server logs to stderr only; mcp 2.x also diverts fd 1 while serving.
