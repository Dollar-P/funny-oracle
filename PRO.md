# Funny Oracle Pro — Research & Paper Trading Spec ("The Desk")

> Companion to `ARCHITECTURE.md` and `REDESIGN.md`. Turns predictions into **trade plans**, executes them in a **paper-trading** account, and measures — honestly, against baselines — whether the LLM agents add any value.
> **Paper money only. Not investment advice.** No live broker orders in this spec.

---

## 0. Branch & Merge Strategy

- Work on branch **`pro`** (from `main` after `local-llm` is merged and `main` is pushed to GitHub).
- The Arena stays untouched. Pro is **additive**: new backend packages (`trading/`, `evaluation/`), new API routes under `/api/pro/*`, new frontend routes under `/pro/*`.
- Shared core (agents, MCP, quant, LLM layer, DB) is extended only with backward-compatible changes, so `main` fixes keep merging cleanly.
- Goal: merge `pro` back into `main` as a second mode ("Arena" for fun, "Desk" for research). A top-level switch in the UI chooses the mode.
- Rebase/merge `main` into `pro` regularly; never let shared files diverge.

---

## 1. Goals & Non-goals

| Goals | Non-goals |
|---|---|
| Convert each prediction into a concrete **TradePlan** (direction, entry, stop, targets, size, invalidation, expiry) | Real-money execution, broker integration |
| Simulate execution realistically (fees, slippage, market hours, bracket orders) | High-frequency trading |
| Strict **risk management** with hard limits and circuit breakers | Guaranteed profitability claims |
| **Evidence**: baselines, ablations, calibration, confidence intervals, forward test | Optimising until the backtest looks good |
| Reproducible: every signal records data snapshot, prompt version, models, parameters | |
| Professional, dense, calm UI | Confetti, sounds, game elements (those stay in the Arena) |

Scope = all assets and modes as before: US stocks, crypto, gold; swing (1W/1M/3M) and scalp (15m/1h/4h).

---

## 2. Principles

1. **A prediction is not a trade.** Most predictions should result in **FLAT** (no trade). A trade requires positive expected value after costs and sufficient conviction.
2. **Evidence over narrative.** The LLM's reasoning is shown, but decisions are judged only by out-of-sample results versus baselines.
3. **Every LLM component must beat a non-LLM alternative** to justify its cost. If it doesn't, the report says so.
4. **No lookahead.** Data used for a decision must have existed at decision time (point-in-time). See §6.2 on LLM training-data leakage.
5. **Small samples are labelled as such.** Every metric shows n and a confidence interval; below thresholds the UI says "insufficient data".
6. **Risk first.** Hard limits are enforced in code before any order reaches the paper broker.

---

## 3. Architecture Additions

```
            ┌──────────────── existing pipeline ────────────────┐
 Scheduler →│ orchestrator → agents → debate → judge → quant    │→ Prediction
            └───────────────────────────────────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  Signal Engine      │  Prediction → TradePlan (or FLAT)
                        └──────────┬──────────┘
                        ┌──────────▼──────────┐
                        │  Risk Engine        │  sizing + limits + circuit breakers
                        └──────────┬──────────┘
              manual approve ◄─────┤─────► auto (paper)
                        ┌──────────▼──────────┐
                        │  Paper Broker       │  orders, fills, brackets, fees, slippage
                        └──────────┬──────────┘
                        ┌──────────▼──────────┐
                        │  Ledger             │  positions, equity curve, journal
                        └──────────┬──────────┘
                        ┌──────────▼──────────┐
                        │  Evaluation Lab     │  baselines, ablations, calibration, reports
                        └─────────────────────┘
```

### 3.1 Richer agent output (backward-compatible)

Extend `AgentReport` with optional fields (Arena ignores them):
```python
scenario_probs: {bull: float, base: float, bear: float}   # sums to 1
key_levels: {support: [float], resistance: [float]}         # Technical / Valuation
invalidation: str                                          # what would prove this view wrong
```
Judge adds: `thesis` (≤ 80 words), `invalidation`, `catalysts` (dated events within the horizon, e.g. earnings, CPI, FOMC), `no_trade_reason` (if it recommends FLAT).

Pro mode settings: temperature ≤ 0.3; prompts are versioned files (`agents/prompts/v{N}/`) and the version is stored with each prediction. Move today's prompts to `v1/` unchanged (the Arena keeps using v1); Pro prompts live in `v2/`.

### 3.1a Providers available (current state of `main`)

| Provider | Model IDs | Cost | Speed (swing run) | Modes |
|---|---|---|---|---|
| Gemini | `gemini-3.5-flash-lite` (**default for every role**), `gemini-3.8-flash` | ฿0.6–1.6 / ฿2–4 tracked | 12–50 s | swing + scalp |
| Local (LM Studio, `openai_compat`) | `local:<LM Studio id>`, e.g. `local:prism-ml/bonsai-27b` | ฿0 | Quick ≈ 6–7 min, Deep ≈ 20–30 min (estimate) | **swing only** (scalp refused) |
| Claude | `CLAUDE_*_MODEL` | per price table | – | implemented, mocks only |

Pro must support all of them through the existing per-run `analyst_model` / `judge_model` / `think_preset` parameters — no new provider code.

### 3.2 Signal Engine — `trading/signals.py`

```python
class TradePlan(BaseModel):
    prediction_id: int
    ticker: str; asset_type: str; mode: str; horizon: str
    direction: Literal["long", "short", "flat"]
    conviction: float            # 0..1, from |S_final| × confidence (calibrated, §6.5)
    entry: EntrySpec             # market at next bar open | limit (price, expiry)
    stop: float                  # max(ATR-based, P10/P90-based) — wider of the two
    targets: list[float]         # T1 = P50 side, T2 = P90/P10
    time_exit: datetime          # = prediction target_time
    reward_risk: float
    expected_value_r: float      # EV in R units after estimated costs
    size: SizeSpec | None        # filled by Risk Engine
    thesis: str; invalidation: str
    flat_reason: str | None
```
**Rules (configurable):**
- `flat` if `|S_final| < S_MIN` (default 0.15) or `confidence < CONF_MIN` (0.5) or `reward_risk < RR_MIN` (1.5) or `expected_value_r ≤ 0` after costs, or a high-impact catalyst falls inside the first 10% of the horizon (configurable).
- Direction = sign(S_final); short allowed for stocks/gold (paper), and for crypto only if `ALLOW_CRYPTO_SHORT=true`.
- Stop = `entry ∓ k_atr × ATR` (k_atr default 1.5 swing / 1.0 scalp), never inside the P10/P90 band edge on the adverse side.
- EV uses calibrated P(target before stop) when available (§6.5); otherwise P(up) as a rough proxy, flagged "uncalibrated".

### 3.3 Risk Engine — `trading/risk.py`

| Control | Default |
|---|---|
| Risk per trade | 0.5 % of equity (swing), 0.25 % (scalp) |
| Position size | `risk_amount / |entry − stop|`, rounded to lot/precision |
| Volatility cap | position notional ≤ `VOL_TARGET × equity / σ_annual` |
| Max position | 20 % of equity notional |
| Max gross exposure | 100 % of equity |
| Max per asset class | stocks 60 %, crypto 30 %, gold 30 % |
| Max open positions | 8 |
| Correlation guard | reject if the new position's 60-day correlation with an existing same-direction position > 0.8 and combined risk > 1 % |
| Daily loss limit | −2 % equity → no new trades until next day |
| Max drawdown breaker | −10 % from peak → halt new trades, require manual reset |
| Optional Kelly cap | size ≤ 0.25 × Kelly using calibrated probabilities (off until calibration n ≥ 100) |

Every rejection is logged with the rule that triggered it.

### 3.4 Paper Broker & Ledger — `trading/broker_paper.py`, `trading/ledger.py`

- Starting equity configurable (default 100,000 USD).
- **Fills:** market orders fill at the **next bar's open** (never the signal bar) + slippage; limit orders fill if the bar trades through the limit.
- **Costs:** stocks commission 0 + slippage 2 bps; gold (via GC=F/GLD proxy) 3 bps; crypto fee 10 bps per side + slippage 5 bps (configurable, all in `config.py`).
- **Market hours:** stocks only during NYSE sessions (use a trading calendar, e.g. `pandas_market_calendars`); crypto 24/7.
- **Brackets:** entry + stop + targets as OCO. T1 closes 50 %, then stop moves to breakeven; T2 closes the rest; time exit at `time_exit`.
- **Intrabar ambiguity:** if stop and target are both touched in the same bar, assume the **stop** filled (conservative). Use 1m bars where available to resolve, otherwise assume stop.
- **Gaps:** a stop gapped through fills at the open (worse price).
- **Mark-to-market:** equity snapshot each bar for open positions (daily for swing, per interval for scalp).
- **Journal:** each closed trade stores entry/exit, R multiple, MAE/MFE, holding time, costs, and a short post-mortem by a **Reviewer agent** (what the thesis said vs what happened). Reviewer runs on the cheapest model, max 1 call per closed trade.

### 3.5 Scheduler — `trading/scheduler.py`

- **Watchlist** (configurable) e.g. 10 US stocks, BTC, ETH, SOL, GC=F.
- Swing: once per day per asset — stocks after the US close, crypto and gold at a fixed UTC time. Horizons 1W/1M/3M, configurable per asset.
- Scalp: optional loop for crypto only by default (stocks only during market hours), at most one open scalp position per asset.
- **Model routing per job** (configurable in Settings):
  - Swing → `PRO_SWING_MODEL` (default `local:<LOCAL_MODEL>` with the Quick preset when LM Studio is reachable, else `gemini-3.5-flash-lite`).
  - Scalp → `PRO_SCALP_MODEL` (Gemini only; default `gemini-3.5-flash-lite`).
  - Reviewer post-mortems → cheapest available (local if reachable).
- **Two budgets:**
  - Money: `PRO_DAILY_BUDGET_THB` (default ฿10) via the existing budget guard; free local runs never count.
  - **Time:** local runs are serial (`LOCAL_CONCURRENCY=1`) and slow, so the scheduler builds a queue and estimates duration from each model's recent average. `PRO_LOCAL_WINDOW` (default 23:00–07:00 local) is when local jobs may run; jobs that won't finish inside the window fall back to Gemini (if money budget allows) or are skipped with a logged reason.
- **Local availability:** check `/api/models` before each batch; if LM Studio is down, apply the fallback rule above and show it in the UI.
- **Concurrency:** the scheduler shares the existing `RunManager` limit (2 concurrent runs) with the Arena; scheduled jobs yield to interactive Arena/Duel runs.
- Runs inside the API process (APScheduler or an asyncio loop), with a persisted "last run" table so restarts don't double-run.
- Every job stores provider, model, think_preset and prompt version, so results are never mixed across models (§6.4).

---

## 4. Data Integrity

- **Snapshot every tool result** used for a decision (`tool_snapshots` table, compressed JSON, keyed by run_id + tool + args + as_of). Enables audit, exact replay, and backtests without refetching.
- **Adjusted prices** for stocks (splits/dividends) in all P&L and indicator calculations.
- **Data quality checks** before a run: stale data (as_of older than one bar for scalp, one session for swing), missing bars, zero volume → mark run "degraded" and force FLAT.
- **Point-in-time caveat:** yfinance fundamentals and news are "as of now", not historical. They're valid for forward runs, but backtests must not use them (§6.2).

---

## 5. Evaluation Lab — `evaluation/`

### 5.1 Strategies compared (same assets, same period, same costs and risk engine)

| Strategy | Description |
|---|---|
| Buy & hold | per asset and an equal-weight basket |
| Cash / always flat | 0 % return, sanity floor |
| Random | random direction at the same trade frequency and sizing (seeded, 1,000 runs → distribution) |
| Momentum | time-series momentum (sign of the 3-month return) / SMA 50–200 crossover |
| Quant-only | the existing Monte Carlo + technical signal labels with fixed rules, **no LLM** |
| Single-agent | each analyst alone (5 variants) |
| Team, no debate | weighted S_raw only |
| **Full system** | analysts + debate + judge (production) |
| Per model | each LLM model (and local Quick vs Deep) as a separate strategy — e.g. "free local" vs "paid Flash-Lite": does paying add value? |

### 5.2 Metrics

**Prediction quality** (per mode/horizon/asset class):
- Directional hit rate with **Wilson 95 % CI**
- **Brier score** and log loss for P(up); **reliability diagram** (calibration curve)
- Quantile loss (pinball) for P10/P50/P90 and **band coverage** (target 80 %)

**Trading performance:**
- Total return, CAGR, volatility, **Sharpe**, Sortino, **max drawdown**, Calmar
- Trades, win rate, average R, **expectancy (R)**, profit factor, exposure %, turnover, total costs
- Benchmark-relative: excess return vs buy & hold, beta, information ratio

**Statistics:**
- **Bootstrap CIs** (block bootstrap for time series) for Sharpe and for the Sharpe difference vs each baseline
- Random-strategy percentile: where the full system ranks within the 1,000 random runs
- **Non-overlapping samples** for significance tests (overlapping horizons are correlated; effective n is shown)
- Minimum-sample rules: below n = 30 trades or 30 non-overlapping predictions per bucket → "insufficient data" badge, no verdict
- Multiple-comparisons warning when many strategy variants are compared

### 5.3 Ablation & attribution
- Remove one agent at a time → change in Brier/Sharpe.
- Contribution of debate + judge (full vs team-no-debate).
- Per-agent directional accuracy and calibration over time (rolling window).

### 5.4 Reports
- `evaluation/report.py` produces a dated report (Markdown + JSON) and the UI's Evaluation Lab page reads the JSON.
- Every report lists: period, assets, n, cost assumptions, model versions, prompt version, and contamination labels (§6.2).

---

## 6. Testing Methodology

### 6.1 Forward test (primary evidence)
Paper trading going forward from the date Pro mode starts. This is the only fully clean test for the LLM system. Expect weeks to months to reach meaningful n for swing horizons; the UI shows a progress bar towards the minimum sample per bucket.

### 6.2 Backtests and the LLM leakage problem
- **Quant-only and baselines** can be backtested freely over years of history.
- **LLM strategies** are contaminated on any date before the model's training cutoff: the model may "remember" what happened. Rules:
  1. LLM backtests run only on dates **after the model's knowledge cutoff** (configured per model in `config.py`); results before that are labelled **"contaminated — not evidence"** and excluded from verdicts.
  2. Backtest mode **disables live web search and current fundamentals/news**; agents receive only point-in-time price-derived data from `tool_snapshots` or historical bars truncated at the decision date.
  3. Optional experiment: **anonymised backtest** (ticker replaced by "Asset A", dates shifted to relative days) to reduce memorisation; reported separately.
- Each model's knowledge cutoff is set in `config.py` (`MODEL_CUTOFFS`). For local models the cutoff is that of the **base model** (e.g. Bonsai-27B → its Qwen base); if unknown, treat every historical date as contaminated.
- Backtests are cost-capped (budget guard). **Use the local model for LLM backtests by default** (฿0, run overnight inside `PRO_LOCAL_WINDOW`); Gemini backtests require an explicit cost estimate confirmation.

### 6.3 Walk-forward
Any tunable parameter (S_MIN, k_atr, weights, calibration) is fit on a training window and evaluated on the next window only; parameters are never chosen using the evaluation window.

### 6.4 Reproducibility
`experiments` table: config hash of (prompt version, provider, models, think_preset, weights, λ, risk params, cost params). Every prediction, plan, and trade references an experiment id. Changing any parameter starts a new experiment; results are never mixed across experiments in one metric.

### 6.5 Calibration layer — `evaluation/calibration.py`
Once a bucket has n ≥ 50 evaluated predictions, fit isotonic regression (or Platt scaling) mapping raw P(up) → calibrated P(up), walk-forward. The Signal Engine uses calibrated values; the UI shows both.

---

## 7. Pro UI — "The Desk"

Calm, dense, professional. Dark neutral theme (no stage gradients), one accent colour, green/red only for P&L and direction, always paired with ▲/▼ or text. Tabular numbers (JetBrains Mono), Inter for text. No confetti, sounds, or typewriter effects; live updates appear as subtle row highlights. Mode switch "Arena ↔ Desk" in the top bar.

| Page | Contents |
|---|---|
| **Overview** | Equity curve vs buy & hold basket and cash; KPIs (equity, day P&L, open risk %, drawdown, Sharpe with CI and n); circuit-breaker status; today's LLM spend vs daily budget; scheduler status |
| **Signals** | Blotter: time, asset, mode/horizon, direction or FLAT (with reason), conviction, R:R, EV (R), status (new / approved / rejected-by-risk / filled / expired); filter by asset/mode/status; **Approve / Reject** in manual mode |
| **Signal detail** | Chart with entry, stop, targets, time exit and the P10–P90 fan; thesis, invalidation, catalysts; compact agent table (score, confidence, one-line view); risk calculation breakdown; data snapshot links; cost; link to the full Arena-style replay |
| **Positions & Orders** | Open positions (unrealised P&L, R, distance to stop/target, time left), working orders, fills |
| **Journal** | Closed trades with R, MAE/MFE, costs, Reviewer post-mortem; tags; export CSV |
| **Evaluation Lab** | Strategy comparison table with CIs and "insufficient data" badges; equity curves overlay; calibration plot; Brier/coverage by bucket; ablation bars; per-agent and per-model attribution; random-strategy distribution with the system's percentile; contamination labels |
| **Risk** | Exposure by asset class and direction, limit usage bars, correlation matrix of open positions, rejected-signal log |
| **Settings** | Watchlist, schedule, models per role, risk and cost parameters (changes create a new experiment), approval mode (**manual by default** / auto-paper), budgets, paper account reset |

---

## 8. API Additions (`/api/pro/*`)

| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/settings` | risk, cost, watchlist, schedule, approval mode |
| GET | `/signals?status=&ticker=` · `/signals/{id}` | blotter, detail |
| POST | `/signals/{id}/approve` · `/reject` | manual mode |
| GET | `/positions` · `/orders` · `/fills` · `/journal` | ledger |
| GET | `/equity?from=&to=` | equity curve + benchmarks |
| GET | `/risk` | exposure, limits, correlations, rejections |
| POST | `/scheduler/start` · `/stop` · GET `/scheduler/status` | scheduler |
| POST | `/evaluation/run` · GET `/evaluation/reports` · `/evaluation/reports/{id}` | Evaluation Lab |
| POST | `/backtests` · GET `/backtests/{id}` (SSE progress) | quant/baseline/LLM backtests |
| POST | `/account/reset` | reset paper account (new experiment) |

SSE event types added: `signal_created`, `signal_flat`, `risk_rejected`, `order_placed`, `order_filled`, `position_closed`, `breaker_tripped`, `equity_tick`.

---

## 9. Database (additive)

```sql
experiments(id PK, created_at, config_hash, config_json, note)
signals(id PK, prediction_id FK, experiment_id FK, created_at, ticker, asset_type, mode, horizon,
        direction, conviction, entry_json, stop, targets_json, time_exit, reward_risk, ev_r,
        size_json, thesis, invalidation, flat_reason, status, status_reason)
orders(id PK, signal_id FK, created_at, type, side, qty, limit_price, stop_price, status, oco_group)
fills(id PK, order_id FK, ts, price, qty, fee, slippage)
positions(id PK, signal_id FK, opened_at, closed_at, side, qty, avg_entry, avg_exit,
          realized_pnl, r_multiple, mae, mfe, costs, exit_reason, review)
equity_snapshots(ts, equity, cash, gross_exposure, open_risk, drawdown)
tool_snapshots(id PK, run_id, tool, args_hash, as_of, payload_zlib)
risk_events(id PK, ts, signal_id NULL, rule, detail)
backtests(id PK, created_at, kind, config_json, period, status, result_json)
reports(id PK, created_at, experiment_id, period, result_json, markdown)
```

---

## 10. Folder Structure (new)

```
trading/      signals.py  risk.py  broker_paper.py  ledger.py  scheduler.py  calendar.py  costs.py
evaluation/   baselines.py  backtest.py  metrics.py  stats.py  calibration.py  ablation.py  report.py
agents/prompts/v2/   pro prompts (versioned)
api/routes/pro/      settings.py signals.py ledger.py risk.py scheduler.py evaluation.py backtests.py
web/src/pro/         pages/ Overview Signals SignalDetail Positions Journal Lab Risk Settings
                     components/ EquityChart Blotter TradeChart RiskBars CalibrationPlot StrategyTable …
tests/pro/           unit + property tests (risk limits, broker fills, metrics vs known values)
```

---

## 11. Cost Planning

| Setup | Money / day | Time / day |
|---|---|---|
| 14 assets × 1 swing run on **local Quick** (default) | ฿0 | ≈ 14 × 6.6 min ≈ **1.5 h** serial — fits the overnight window |
| 14 assets on **local Deep** | ฿0 | ≈ 14 × 25 min ≈ **6 h** — only with a long window or a smaller watchlist |
| 14 assets on **flash-lite** (fallback) | ≈ ฿8–10 tracked (≈ ฿7 estimated actual) | ≈ 10 min |
| Scalp loop (crypto, Gemini only) | grows fast — **off by default** | – |

- Reviewer post-mortems ≈ 1 cheap call per closed trade (local when reachable).
- LLM backtests: local by default (§6.2); Gemini backtests always show a cost estimate first.

---

## 12. Build Order & Checkpoints

| # | Task | Done when |
|---|---|---|
| P1 | Branch `pro`; additive DB migration; `experiments`; richer agent/judge fields; prompts moved to `v1/` + new `v2/`; `MODEL_CUTOFFS` | all existing tests pass (214+); Arena, Duel, CLI, Streamlit and local provider unchanged; new fields appear in a mocked run on Gemini and on a mocked local backend |
| P2 | Signal Engine + cost model | unit tests: FLAT rules, stop/target geometry, EV after costs, short rules |
| P3 | Risk Engine | property tests: no plan ever exceeds any limit; every rejection logged with its rule |
| P4 | Paper Broker + Ledger + trading calendar | tests on synthetic bars: next-open fills, gaps, OCO, T1→breakeven, intrabar-ambiguity = stop, fees; equity snapshots reconcile with fills to the cent |
| P5 | Baselines + quant backtester + metrics + stats | metrics match hand-computed values on fixtures; buy & hold / momentum / random backtests over ≥ 3 years run in < 1 min |
| P6 | Scheduler + forward paper loop (manual approval default) + model routing, money and time budgets, local window, LM Studio fallback | mocked 3-day simulation produces signals, fills, closed trades, journal entries; restart doesn't double-run; "LM Studio down" and "won't finish in window" fall back or skip with a logged reason; Arena runs take priority over scheduled jobs |
| P7 | LLM backtest mode with leakage rules + calibration layer | backtest refuses pre-cutoff dates for verdicts (labelled contaminated); search/fundamentals disabled; calibration fitted walk-forward on fixtures |
| P8 | Pro API + SSE events | `pytest api/tests` incl. pro routes; event stream for a mocked scheduler day |
| P9 | Desk UI (all pages in §7) | headless e2e on mock data: every page renders, approve/reject works, Lab shows CIs and insufficient-data badges; 0 console errors |
| P10 | Real verification + docs | ≤ 10 **paid** runs (local runs are free and not capped, but stay inside the local window); one real overnight scheduler batch on the watchlist using local Quick; report generated; README "Pro mode" section; merge plan for `main` documented |

**Rules for Claude Code:** mocks, fixtures, `data/seeds.json` and synthetic bars for development; paid LLM runs only in P10 (≤ 10); never place real orders; never hard-code the LM Studio host (only `LOCAL_BASE_URL`); keep `PROGRESS.md` updated; commit per checkpoint; keep the Arena working.

---

## 13. Honest Expectations

- The most likely result is that the LLM system **does not beat simple baselines after costs**. That is a valid, publishable finding — the Evaluation Lab exists to show it clearly either way.
- Swing evidence accumulates slowly (1M horizon → about 12 non-overlapping samples per asset per year). Pooling across assets helps but correlations reduce effective n.
- Scalp trading is dominated by costs and noise; expect it to look worst.
- Paper results overstate real results (no queue position, partial fills, psychology). Treat any positive result as "worth more testing", never as proof.

**Disclaimer:** educational research tool. Not financial advice. Paper trading only.
