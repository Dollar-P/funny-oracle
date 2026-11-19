# 🔮 Funny Oracle

Five AI analysts argue about whether a stock, a coin or gold goes up or down — then a Monte Carlo
simulation turns their verdict into a price range, and a scoreboard checks them against a coin flip.

> **For education and fun. Not investment advice.**

- **Agentic:** each analyst (Business, Technical, Valuation, Macro, Psychology) decides which market-data
  tools to call through an MCP server (`market-data-mcp`), up to 8 tool turns.
- **Debate:** a Bull and a Bear argue from the reports; a Judge may adjust the team score by at most ±0.2.
- **Grounded numbers:** the LLM only produces a direction score in [-1, +1]. The P10 / P50 / P90 range comes
  from 10,000 simulated paths using real historical volatility — a fully confident team can move the median
  by at most ½σ over the horizon.
- **Honest:** the Scoreboard tracks direction hit rate vs 50%, P10–P90 band coverage, MAPE and an agent
  leaderboard.

See `ARCHITECTURE.md` for the design and `PROGRESS.md` for build status, decisions and known issues.

## Setup (Windows / PowerShell)

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # then fill in GEMINI_API_KEY (FRED_API_KEY optional)
```

Models are configured only in `.env` (`ANALYST_MODEL`, `JUDGE_MODEL`, `SCALP_MODEL`). Check the IDs in
Google AI Studio and add new models to the price table in `config.py` (`PRICES`). Unknown models are
charged a deliberately pessimistic price.

## Run

```powershell
.venv\Scripts\streamlit run app.py                        # the app
.venv\Scripts\python orchestrator.py NVDA swing 1M        # one prediction in the terminal
.venv\Scripts\python orchestrator.py BTC-USD scalp 15m
.venv\Scripts\python scripts\run_agents.py GC=F --agents all   # analysts only, no debate/judge
.venv\Scripts\python scripts\auto_scalp.py --runs 4 --interval 900   # headless Auto-Scalp
```

- **Swing:** horizons 1W / 1M / 3M on daily data. 5 analysts + debate + judge (~9 LLM calls, ~10–15 s, about ฿0.5–1.5 with flash-lite).
- **Scalp:** horizons 15m / 1h / 4h on 1m / 5m / 15m bars. Technical + Macro-lite + Psychology + Judge (~4 calls).
  US stocks only move during market hours, so use BTC-USD for a scalp demo.
- **Replay:** the sidebar can reload any stored prediction (no API cost), or open `http://localhost:8501/?replay=<id>`.

### Budget guard

`BUDGET_THB` (default ฿100) is a hard cap. Before each prediction the guard compares *spent + estimate*
with the budget and refuses if it would go over. The estimate is the average of recent predictions × 1.25.
It also checks before every single LLM call. Spend lives in the `llm_usage` table and is shown in the
sidebar. Auto-Scalp is also capped at `AUTO_SCALP_MAX_RUNS` (40) per session. As a second safety net, set a
budget alert in Google Cloud Billing too.

## Use the MCP server from Claude Code

```powershell
claude mcp add market-data -- D:\funny-price-prediction-project\.venv\Scripts\python.exe D:\funny-price-prediction-project\mcp_server\server.py
```

Then ask Claude Code e.g. *"call get_indicators for BTC-USD on 1h"*.

Tools: `get_price_history`, `get_indicators`, `get_fundamentals`, `get_macro`, `get_news`, `get_sentiment`,
`get_onchain_lite`. Each returns compact JSON (≤ ~2 KB) with an `as_of` timestamp. Errors come back as
`{"error": ..., "data": null}` and never raise.

## Tests

```powershell
.venv\Scripts\python -m pytest -q                         # everything (network tests hit yfinance/FRED/CoinGecko)
.venv\Scripts\python -m pytest -q --ignore tests\test_mcp_tools.py --ignore tests\test_mcp_client.py   # offline
.venv\Scripts\python scripts\ui_smoke.py GC=F swing 1M    # real end-to-end Predict click (costs one prediction)
```

The LLM, orchestrator, UI and Auto-Scalp tests use a scripted fake Gemini client built from real `google.genai`
response types. They never call the API.

## Demo script (3–5 min)

1. *"Five AIs argue about whether Bitcoin goes up or down."*
2. Predict **NVDA · Swing · 1M**: walk through the agent cards and their weights.
3. Open **🔍 Tool-call trace**: the AI chose by itself to fetch RSI, fundamentals, macro or news.
4. Open **🐂 vs 🐻 Debate** and read the Judge's headline.
5. Switch to **GOLD (GC=F)**: Macro now has 40% weight (real yields and the dollar).
6. **Scoreboard**: *"The AI is right X% of the time… a coin flip gets 50%."*
7. Recap: MCP + agentic loop + debate + quant anchoring.

Before presenting, set `ANALYST_MODEL` / `JUDGE_MODEL` to `gemini-3.8-flash` in `.env`. Toggle Auto-Scalp
(or run `scripts\auto_scalp.py`) at least an hour beforehand so the Scoreboard has evaluated predictions.
Keep a few stored predictions ready to replay in case the network or budget fails.
