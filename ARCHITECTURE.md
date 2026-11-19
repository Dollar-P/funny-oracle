# Funny Oracle — System Architecture

> A multi-agent (Agentic AI) system that predicts asset prices (US stocks / Crypto / Gold) for education and fun.
> **Not investment advice.**
>
> *Implementation status and every departure from this document are logged in `PROGRESS.md`.*

---

## 1. Goals & Scope

| In scope (v1) | Out of scope (v1) |
|---|---|
| Predict a price range (Bear / Base / Bull) with confidence | Placing real orders |
| 5 independent analyst agents that also debate each other | Training custom ML models |
| 2 modes: **Swing** (1 week – 3 months) and **Scalp** (15 min – 4 hours) | Thai stocks / Forex |
| Scoreboard measuring real accuracy vs a coin flip | Production-grade backtesting |
| Tool-call trace showing what the AI decided to look up | User accounts / login |

**Core design principle:** the LLM does not "guess the price". The LLM produces a **directional score + reasoning**, and that score adjusts a **Monte Carlo simulation** driven by real historical volatility. The final numbers are grounded in a model, not invented.

**Extensibility principle:** prompts, methods, and knowledge sources live in files and registries, not hard-coded, so future extensions (Section 10) plug in without rewriting the core.

---

## 2. Architecture Overview (Layers)

```
┌─────────────────────────────────────────────────────────────────────┐
│  PRESENTATION      Streamlit app.py                                  │
│                    ticker / mode / horizon → price chart, fan chart, │
│                    agent cards, debate, tool trace, scoreboard       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ run_prediction(ticker, mode, horizon)
┌──────────────────────────────▼──────────────────────────────────────┐
│  ORCHESTRATION     orchestrator.py                                   │
│   1. Classify asset (stock / crypto / gold)                          │
│   2. Select agents + weights by mode × asset                         │
│   3. Run analysts in parallel (asyncio)                              │
│   4. Bull vs Bear debate (1 round)                                   │
│   5. Judge → final score                                             │
│   6. Quant engine → P10 / P50 / P90                                  │
│   7. Save to scoreboard                                              │
└───────┬──────────────────────┬──────────────────────┬───────────────┘
        │                      │                      │
┌───────▼────────┐   ┌─────────▼─────────┐   ┌────────▼────────┐
│  AGENT LAYER   │   │  QUANT LAYER      │   │  STORAGE        │
│  5 Analysts    │   │  montecarlo.py    │   │  SQLite         │
│  News Research │   │  indicators.py    │   │  predictions    │
│  Bull / Bear   │   │  (pandas, numpy)  │   │  agent_outputs  │
│  Judge         │   └─────────▲─────────┘   │  cache          │
└───┬────────┬───┘             │             └─────────────────┘
    │        │                 │
    │  ┌─────▼──────────┐      │
    │  │  LLM LAYER     │      │
    │  │  llm.py        │      │
    │  │  Gemini (def.) │      │
    │  │  / Claude      │      │
    │  │  + Google      │      │
    │  │    Search      │      │
    │  └────────────────┘      │
    │ MCP (stdio)              │
┌───▼──────────────────────────┴──────────────────────────────────────┐
│  TOOL LAYER        mcp_server/server.py  ("market-data-mcp")         │
│   get_price_history · get_indicators · get_fundamentals ·            │
│   get_macro · get_news · get_sentiment · get_onchain_lite            │
└───────┬──────────────┬──────────────┬──────────────┬────────────────┘
┌───────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐ ┌─────▼──────────────┐
│  yfinance    │ │  FRED API │ │ alternative │ │ CoinGecko (free)   │
│ price/fin/news│ │  Macro    │ │ .me F&G     │ │ crypto market data │
└──────────────┘ └───────────┘ └─────────────┘ └────────────────────┘
```

---

## 3. Data Flow (one prediction)

```
User: BTC-USD, mode=swing, horizon=1M
 │
 ├─▶ [1] classify("BTC-USD") → crypto
 ├─▶ [2] weights = WEIGHTS["swing"]["crypto"]
 ├─▶ [3] parallel:
 │        Business ─┐   (each runs an agent loop: think → call MCP tool → read result → think ≤ 8 turns)
 │        Technical ─┤
 │        Valuation ─┼─▶ AgentReport × 5  {score, confidence, key_points, reasoning, sources, trace}
 │        Macro ─────┤
 │        Psychology ┘
 ├─▶ [4] S_raw = Σ(wᵢ·scoreᵢ·confᵢ) / Σ(wᵢ·confᵢ)
 ├─▶ [5] Bull agent reads all reports → bullish case
 │        Bear agent reads all reports → bearish case
 ├─▶ [6] Judge: reports + debate → adjustment ∈ [-0.2, +0.2], final_confidence, verdict
 │        S_final = clip(S_raw + adjustment, -1, 1)
 ├─▶ [7] Monte Carlo(price, σ_hist, horizon, S_final) → P10 / P50 / P90, P(up)
 ├─▶ [8] save prediction (target_time = now + horizon)
 └─▶ [9] render UI
```

---

## 4. Components

### 4.1 MCP Server — `mcp_server/server.py`

Built with the MCP Python SDK **2.x** (`mcp.server.mcpserver.MCPServer` — the 2.x name for v1's `FastMCP`), stdio transport. Shared by the in-app agents (via `core/mcp_client.py`, which uses the 2.x `mcp.Client` + `StdioServerParameters`) and by Claude Code during development (`claude mcp add market-data -- <venv python> mcp_server/server.py`). stdout carries JSON-RPC only: the server logs to stderr, and mcp 2.x additionally diverts fd 1 while serving so stray library `print()`s cannot corrupt the stream.

| Tool | Input | Output (JSON) | Source |
|---|---|---|---|
| `get_price_history` | ticker, interval (`1m`…`1d`), period | last N bars + summary (last, %chg, high/low) | yfinance |
| `get_indicators` | ticker, interval | `signals` (deterministic labels: price_vs_bollinger, rsi_zone, macd_cross, macd_momentum, trend_vs_sma20/50/200, sma_regime, volume_state, volatility_state) + RSI14, MACD, SMA20/50/200, EMA, Bollinger, ATR, volume z-score, support/resistance and % distances | computed from OHLCV |
| `get_fundamentals` | ticker | P/E, Fwd P/E, PEG, EV/EBITDA, margins, revenue growth, FCF, debt/equity, analyst target | yfinance `.info` |
| `get_macro` | – | FEDFUNDS, CPI YoY, DGS10, DFII10 (real yield), T10Y2Y, DXY, ^VIX | FRED API if `FRED_API_KEY` is set, otherwise FRED's public `fredgraph.csv` endpoint (no key) + yfinance |
| `get_news` | ticker, limit | headline, publisher, date, link | yfinance `.news` |
| `get_sentiment` | asset_type, ticker (optional; defaults to SPY / BTC-USD / GC=F) | VIX, Crypto Fear & Greed, distance from 52w high/low, 1M/3M momentum, vs SMA50, RSI | yfinance + alternative.me |
| `get_onchain_lite` | coin | market cap, volume/mcap, dominance, ATH distance | CoinGecko free API |

**Rules:**
- Every tool returns compact JSON (~2 KB max). Never send full raw OHLCV to the LLM — send a summary + last 20 bars.
- SQLite cache TTLs: intraday price 60 s, daily 15 min, fundamentals/macro 6 h.
- On error return `{"error": "...", "data": null}` — never raise and kill the agent.
- Every response includes an `as_of` timestamp.

### 4.2 LLM Layer — `core/llm.py`

```python
class LLM:
    def __init__(self, provider="gemini", model=...): ...
    async def run_agent(self, system, user, tools, enable_search=False, max_turns=8) -> AgentResult
```
- **Default: Gemini** via the `google-genai` SDK, **paid Tier 1** (so Google Search grounding is available).
- Models are configured in `.env`, never hard-coded (Gemini releases new versions often — verify IDs in Google AI Studio):
  ```env
  LLM_PROVIDER=gemini
  ANALYST_MODEL=gemini-3.8-flash      # 5 analysts + News Researcher
  JUDGE_MODEL=gemini-3.8-flash        # Bull / Bear / Judge
  SCALP_MODEL=<latest flash-lite>     # scalp mode, low latency
  ENABLE_SEARCH=true                  # Google Search grounding (paid tier)
  BUDGET_THB=100                      # hard spending cap, see 4.10
  USD_THB=33                          # for cost display
  ```
- Keep thinking/reasoning effort low for analysts (cheaper, faster); allow a higher setting for the Judge only.
- Convert MCP tool schemas → Gemini `FunctionDeclaration`s and run the loop manually: model requests a function call → call MCP → return result → repeat until the model emits the final JSON.
- `enable_search=True` → enable Google Search grounding; collect `grounding_metadata` as sources.
- Gemini only accepts Search + function calling together with `tool_config.include_server_side_tool_invocations=True`; search calls then arrive as `tool_call` parts (`GOOGLE_SEARCH_WEB`), which are counted for cost.
- **Capability probe:** each model is probed once (tiny request, cached 7 days) for `search+functions` and `search`. Fallback chain: grounded analysts → `NewsResearcher` with Search only (4.4) → `NewsResearcher` over the `get_news` MCP tool (also used when `ENABLE_SEARCH=false`).
- JSON output: the tool loop asks for JSON in the prompt and parses the final text; on parse failure one extra call uses JSON mode + `response_json_schema` (retry once). Second failure → agent absent.
- At `max_turns` the model is told to answer and function calling is set to `NONE`.
- Record every tool call in `trace` (tool name, args, latency, result size) for the UI.
- Switch provider via `.env` (`LLM_PROVIDER=gemini|claude`). The Claude backend uses the `anthropic` SDK (manual loop, server `web_search` tool, `output_config.effort`), with model IDs from `CLAUDE_*_MODEL`.

### 4.3 Analyst Agents — `agents/`

All agents inherit `BaseAgent` and share one output schema:

```python
class AgentReport(BaseModel):
    agent: str
    score: float          # -1 (strong down) .. +1 (strong up)
    confidence: float     # 0..1
    key_points: list[str] # 3–5 items
    reasoning: str        # ≤ 120 words
    sources: list[str]    # URLs / tool names
    trace: list[ToolCall]
```

| Agent | Tools | Search | Stock | Crypto | Gold |
|---|---|---|---|---|---|
| **Business** | fundamentals, news | ✅ | growth, moat, guidance, earnings news | adoption, ETF flows, upgrades, regulation | central-bank demand, jewelry/ETF demand |
| **Technical** | price_history, indicators | ❌ | trend, momentum, S/R, volume, patterns | same + volatility regime | same |
| **Valuation** | fundamentals, price_history, onchain_lite | ❌ | P/E vs history/sector, simple DCF, analyst target | MVRV-proxy (price vs 200W MA), NVT-proxy | price vs real yield, gold/DXY |
| **Macro** | macro, news | ✅ | rates, yield curve, risk-on/off | liquidity, DXY, correlation with Nasdaq | **real yield, DXY (most important)** |
| **Psychology** | sentiment, news, price_history | ✅ | VIX, distance from ATH, FOMO/panic in news | Fear & Greed, euphoria | safe-haven demand |

**System prompt template (abridged) — stored in `agents/prompts/*.md`:**
```
You are a senior {role} analyst at a hedge fund.
Asset: {ticker} ({asset_type})  Mode: {mode}  Horizon: {horizon}
Current time: {now}
{optional: methods / knowledge context — see Section 10}
- Use tools to gather real data before concluding. Never invent numbers.
- Judge ONLY from the {role} perspective.
- score: -1..+1 for the next {horizon}; lower confidence if data is conflicting or insufficient.
- Reply with JSON matching the schema only.
```

### 4.4 News Researcher (fallback / helper)

A Search-only agent that runs before the analysts and returns a `NewsBrief` (5–8 bullet points + links), passed as context to Business, Macro, and Psychology. Used when Search + function calling can't be combined; it also saves search calls by searching once and sharing results.

### 4.5 Debate & Judge — `agents/debate.py`, `agents/judge.py`

- Sequential, no tool calls: **Bull** opens from the 5 AgentReports (≤ 150 words) → **Bear** reads the Bull's text and rebuts its strongest point (≤ 150) → **Bull** gives a short reply (≤ 80, clipped in code). The UI shows the three turns as a chat thread.
- **Judge** receives reports + debate + `S_raw` and returns:
  ```python
  class Verdict(BaseModel):
      adjustment: float      # -0.2..+0.2
      final_confidence: float
      headline: str          # one witty line, e.g. "Bears win on points, but the bulls refuse to leave the ring"
      summary: str
      key_risks: list[str]
  ```
- `adjustment` is capped at ±0.2 so the Judge can't overrule the whole team.

### 4.6 Quant Engine — `quant/montecarlo.py`

**Agent weights by mode × asset**

| | Business | Technical | Valuation | Macro | Psychology |
|---|---|---|---|---|---|
| Swing · Stock | 0.25 | 0.15 | 0.25 | 0.15 | 0.20 |
| Swing · Crypto | 0.15 | 0.25 | 0.10 | 0.20 | 0.30 |
| Swing · Gold | 0.10 | 0.20 | 0.15 | 0.40 | 0.15 |
| Scalp · all | 0 (skipped) | 0.55 | 0 (skipped) | 0.10 | 0.35 |

**Aggregate score**
```
S_raw   = Σ(wᵢ · scoreᵢ · confᵢ) / Σ(wᵢ · confᵢ)
S_final = clip(S_raw + judge.adjustment, -1, 1)
```

**Monte Carlo (GBM)**
```
σ      = stdev(log returns) over lookback  (swing: 1y daily; scalp: 5d of the chosen interval)
T      = horizon in the same units as σ
μ_base = 0                                  (no historical drift assumed, avoids bias)
μ_adj  = S_final · λ · σ / √T per step      (λ = 0.5 → |S|=1 shifts the median ~0.5σ over the horizon)
paths  = 10,000 → price at T
Output = P10 (Bear), P50 (Base), P90 (Bull), P(price > now), fan chart P10–P90 per step
```
Why λ = 0.5: even a fully confident team can only move the median by half a sigma — far more realistic than letting the AI claim +40%.

### 4.7 Modes

| | Swing | Scalp |
|---|---|---|
| Horizons | 1W, 1M, 3M | 15m, 1h, 4h |
| Data interval | 1d | 1m (15m), 5m (1h), 15m (4h) |
| yfinance limits | – | 1m history ~7 days; 5m/15m ~60 days |
| Agents | 5 + News + Debate + Judge (~9 LLM calls) | Technical + Psychology + Macro-lite + Judge (~4 calls) |
| Est. latency | 30–60 s | 10–20 s |
| Notes | – | US stocks: market hours only · BTC: 24/7 |

### 4.8 Scoreboard — `core/scoreboard.py`

**SQLite schema**
```sql
predictions(
  id INTEGER PK, run_id UNIQUE, created_at, ticker, asset_type, mode, horizon, target_time,
  price_now, p10, p50, p90, prob_up, s_raw, s_final, confidence, headline,
  cost_usd, source,               -- manual | cli | auto_scalp
  prediction_json,                -- full Prediction, for replay in the UI (?replay=<id>)
  actual_price NULL, evaluated_at NULL
)
agent_outputs(prediction_id FK, agent, score, confidence, reasoning, trace_json, error)
cache(key PK, value_json, expires_at)
llm_usage(id PK, prediction_id FK NULL, run_id, created_at, agent, model,
          input_tokens, output_tokens, thinking_tokens, search_calls, cost_usd)
-- run_id links usage rows written during a run; prediction_id is filled on save
```

**Evaluation** (runs whenever the Scoreboard tab opens: find predictions past `target_time`, fetch actual prices)
- **Direction hit rate** vs 50% baseline
- **Band coverage**: % of actual prices inside P10–P90 (well-calibrated ≈ 80%)
- **MAPE** of P50
- **Agent leaderboard**: which agent's direction was right most often (great demo joke: "Psychology beats Valuation")

**Auto-Scalp Demo button:** predicts BTC-USD 15m every 15 minutes while the app is open, so real scoreboard data exists before presenting.

### 4.9 UI — `app.py` (Streamlit)

```
┌ Sidebar ─────────────┐ ┌ Main ─────────────────────────────────────────┐
│ Ticker [NVDA ▾]      │ │ 🔮 NVDA · Swing · 1M          Judge headline   │
│ Presets: NVDA AAPL   │ │ ┌───────────────────────────────────────────┐ │
│   BTC ETH GOLD       │ │ │ Price chart + fan chart (P10–P90) plotly  │ │
│ Mode (•)Swing ( )Scalp│ │ └───────────────────────────────────────────┘ │
│ Horizon [1M ▾]       │ │ Bear $X | Base $Y | Bull $Z | P(up) 58% | Conf │
│ LLM [Gemini ▾]       │ │ Score gauge  ◀━━━━━━●━━━━▶                    │
│ [🔮 Predict]         │ │ ┌Business┐┌Technical┐┌Valuation┐┌Macro┐┌Psych┐ │
│ ─────                │ │ │ +0.4   ││ -0.2    ││ +0.1    ││-0.3 ││+0.6 │ │
│ ⚠ Not investment     │ │ └────────┘└─────────┘└─────────┘└─────┘└─────┘ │
│   advice             │ │ ▸ 🐂 vs 🐻 Debate                              │
└──────────────────────┘ │ ▸ 🔍 Tool-call trace (what each agent called)  │
                         │ ▸ 📰 Sources                                  │
                         │ Tabs: [Predict] [Scoreboard] [About]           │
                         └───────────────────────────────────────────────┘
```
- Use `st.status` for live progress: "Technical Agent calling get_indicators…"
- Show cost per prediction and a "Budget used: ฿X / ฿100" bar in the sidebar.

### 4.10 Cost Tracker & Budget Guard — `core/cost.py`

- Read `usage_metadata` (input, output, thinking tokens) from every Gemini response; count Search grounding calls.
- Price table per model lives in `config.py` (update when Google changes prices).
- Store per-call cost in a `llm_usage` table; aggregate per prediction and in total.
- **Budget guard:** before each prediction, estimate cost (swing ≈ 9 calls, scalp ≈ 4). If `spent + estimate > BUDGET_THB`, refuse with a friendly message instead of calling the API.
- Auto-Scalp has its own cap (default: max 40 runs per session).
- Rough expectation (verify against real usage after the first runs): a swing prediction costs a few baht, a scalp prediction under one baht → ฿100 ≈ dozens of predictions.
- Also set a budget alert in Google Cloud Billing as a second safety net.

---

## 5. Folder Structure

```
funny-oracle/
├── app.py                     # Streamlit entry
├── orchestrator.py            # main pipeline
├── config.py                  # WEIGHTS, HORIZONS, λ, model names
├── core/
│   ├── llm.py                 # Gemini/Claude wrapper + agent loop
│   ├── mcp_client.py          # MCP stdio client, schema conversion
│   ├── schemas.py             # Pydantic: AgentReport, Verdict, Prediction
│   ├── scoreboard.py          # persistence + evaluation + metrics
│   ├── db.py                  # SQLite connection + schema
│   ├── cache.py               # TTL cache (table `cache`)
│   ├── cost.py                # price lookup, llm_usage, BudgetGuard
│   ├── autoscalp.py           # Auto-Scalp tick (UI fragment + headless script)
│   └── asset.py               # classify ticker → stock/crypto/gold
├── agents/
│   ├── base.py
│   ├── business.py  technical.py  valuation.py  macro.py  psychology.py
│   ├── news_researcher.py
│   ├── debate.py              # Bull & Bear
│   ├── judge.py
│   └── prompts/               # system prompts as .md files
├── methods/                   # (extension point, empty in v1) analysis method cards
├── knowledge/                 # (extension point, empty in v1) books / notes for RAG
├── theses/                    # (extension point, empty in v1) user theses
├── mcp_server/
│   ├── server.py              # MCPServer tools (mcp 2.x)
│   ├── data_sources.py        # yfinance, FRED, alternative.me, CoinGecko
│   └── indicators.py
├── quant/
│   └── montecarlo.py
├── scripts/
│   ├── run_agents.py          # run analysts only (checkpoints 4–5)
│   ├── ui_smoke.py            # real Predict click via Streamlit AppTest
│   └── auto_scalp.py          # headless Auto-Scalp loop
├── tests/
│   ├── test_mcp_tools.py      # fetch NVDA, BTC-USD, GC=F for real
│   ├── test_mcp_client.py     # stdio server via mcp 2.x client
│   ├── test_llm.py            # agent loop / cost / budget (mocked Gemini)
│   ├── test_orchestrator.py   # full pipeline + scoreboard (mocked)
│   ├── test_app.py            # Streamlit AppTest (mocked)
│   ├── test_autoscalp.py
│   └── test_montecarlo.py
├── data/oracle.db
├── .env.example               # GEMINI_API_KEY, FRED_API_KEY, LLM_PROVIDER, ANTHROPIC_API_KEY (optional)
├── requirements.txt           # streamlit yfinance pandas numpy plotly mcp>=2.3,<3 google-genai pydantic httpx python-dotenv pytest (+ anthropic, optional)
├── ARCHITECTURE.md            # this file
└── README.md
```

---

## 6. Non-functional

| Concern | Approach |
|---|---|
| **Rate limits (Gemini Tier 1)** | Semaphore limits concurrency (default 5) + exponential backoff on 429 |
| **Cost** | Budget guard (4.10) blocks calls past `BUDGET_THB`; cache tool results to avoid repeat LLM work |
| **Agent failure** | Error/timeout (45 s) → drop from aggregate, renormalize weights; UI shows "⚠ absent" |
| **Hallucination** | Prompts require citing numbers from tools only; Judge also sees the trace |
| **Stale data** | Every tool returns `as_of`; agents must flag data older than the horizon |
| **Secrets** | API keys in `.env` (git-ignored) |
| **Windows** | No event-loop workaround needed: mcp 2.x stdio works on Windows / Python 3.14 out of the box (the loop-policy API is deprecated in 3.14 anyway) |

---

## 7. Build Order & Checkpoints

| # | Task | Done when |
|---|---|---|
| 1 | Project setup, venv, `.env` | `python -c "import yfinance, google.genai, mcp"` passes |
| 2 | `mcp_server` + `data_sources` + `indicators` | `pytest tests/test_mcp_tools.py` fetches NVDA / BTC-USD / GC=F |
| 3 | Register MCP in Claude Code and call a tool | Claude Code can call `get_indicators("BTC-USD","1h")` |
| 4 | `llm.py` + `mcp_client.py` + agent loop + `cost.py` | Technical Agent alone returns a valid AgentReport and its cost is logged |
| 5 | Remaining agents + News Researcher | All 5 run on all 3 asset types |
| 6 | Debate + Judge + Monte Carlo + orchestrator | `python orchestrator.py NVDA swing 1M` prints P10/P50/P90 |
| 7 | Streamlit UI | Predict button renders every section |
| 8 | Scoreboard + Auto-Scalp | Auto mode produces ≥ 3 evaluated predictions |
| 9 | README + screenshots + demo rehearsal | – |

---

## 8. Demo Script (3–5 minutes)

1. **Hook:** "Five AIs argue about whether Bitcoin goes up or down."
2. Predict **NVDA Swing 1M** → explain agent cards and weights.
3. Open the **Tool-call trace** → show that the AI chose by itself to fetch RSI, news, or FRED data.
4. Open the **Debate** → read the Judge's witty headline.
5. Switch to **GC=F** → show Macro gaining weight for gold.
6. **Scoreboard** → "The AI is right X% of the time… a coin flip gets 50%." (Funniest moment — and shows we understand real market limits.)
7. Recap the architecture: MCP + agentic loop + debate + quant anchoring.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| yfinance rate-limited / broken | SQLite cache + CSV snapshots for offline demo |
| Gemini can't combine Search + function calling | Separate News Researcher (4.4) |
| Budget runs out mid-demo | Budget guard + pre-run predictions stored in DB ("replay mode") + switch provider via `.env` |
| US market closed during scalp demo | Use BTC-USD for scalp |
| Results look more credible than they are | Disclaimer + Scoreboard for honesty |

---

## 10. Future Extensions (not in v1)

v1 already reserves the extension points (`methods/`, `knowledge/`, `theses/`, prompt files, agent registry), so each item below plugs in without rewriting the core.

### 10.1 User Thesis Mode
- The user writes their own thesis (e.g. "NVDA will keep rising because AI capex isn't slowing") into `theses/*.md` or a UI text box.
- New **Thesis Challenger** agent: stress-tests the thesis against agent reports — lists supporting evidence, contradicting evidence, and "what would prove you wrong" triggers.
- Theses are tracked in the scoreboard: was the user right, or the AI?

### 10.2 Book & Knowledge Base (RAG)
- Drop the user's own books/notes (PDF, Markdown) into `knowledge/`.
- Ingest pipeline: chunk → embed (e.g. Gemini embeddings) → local vector store (e.g. Chroma).
- New MCP tool `search_knowledge(query, agent_role)` so agents can retrieve relevant principles, e.g. Valuation pulls margin-of-safety ideas, Psychology pulls crowd-behavior concepts.
- Cite book + page in `sources`.

### 10.3 Pluggable Analysis Methods
- Each method is a "method card" in `methods/*.md` with front-matter: name, applicable agents, asset types, required tools, step-by-step procedure, output rules.
- Examples: Wyckoff, Elliott Wave, Ichimoku, CAN SLIM, Graham Number, Reverse DCF, Debt-cycle macro, Contrarian sentiment.
- UI multiselect: choose which methods each agent must apply; the card is injected into the agent's system prompt.

### 10.4 Investing-Style Personas
- Persona agents with distinct philosophies (deep value, growth, momentum trader, contrarian, macro tourist) that vote alongside the analysts — fun "panel show" demo.

### 10.5 Learning from the Scoreboard
- Auto-tune agent weights from historical accuracy (e.g. weight ∝ hit rate, Bayesian update).
- **Reflection memory:** after each evaluated prediction, a Reviewer agent writes a short lesson ("Technical over-trusted RSI in a strong trend"), retrieved in future runs for the same asset.

### 10.6 More
- Multi-round debate, or a debate where analysts can call tools to rebut.
- More assets: Thai SET, forex, commodities, ETFs.
- Alerts via LINE / Telegram / Discord when a scheduled prediction flips direction.
- Scheduled daily briefing and a portfolio-level view.
- Proper walk-forward backtest harness.
