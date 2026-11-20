# Funny Oracle Pro — implementation plan

Companion to `PRO.md` (the spec). This file says *how* each checkpoint is built: files, public interfaces,
tests and risks. Where PRO.md is ambiguous the choice made is listed under **Ambiguities** at the end, and the
same choices are logged under Decisions in `PROGRESS.md` as they are implemented.

Conventions used everywhere:

- **Time:** all timestamps stored as ISO-8601 UTC (`...+00:00`). The UI converts to local time.
- **Money:** the ledger (cash, fills, fees, realised P&L, equity snapshots) uses `decimal.Decimal` quantised to
  cents (`0.01`) for the account currency (USD); prices and quantities keep their own precision. Signals, risk
  maths and metrics use floats (they are estimates, not balances).
- **Units:** returns are fractions (0.01 = 1 %); costs are configured in **bps** and converted once in
  `trading/costs.py`; trade results are in **R** (1 R = the planned risk per unit × qty at entry).
- **Bars:** a bar is identified by its *open* timestamp. A decision made at the close of bar *t* can only use
  bars with timestamp ≤ *t* and is filled at the open of bar *t+1*.
- **Pro is additive:** new packages `trading/`, `evaluation/`, `api/routes/pro/`, `web/src/pro/`; shared code gets
  only new optional parameters/fields with defaults that reproduce today's behaviour.

---

## P1 — branch, migration, experiments, richer agent/judge fields, prompt versions, `MODEL_CUTOFFS`

**Files**
- `agents/prompts/*.md` → `agents/prompts/v1/*.md` (git mv, unchanged). `agents/prompts/v2/*.md`: copies of v1
  plus Pro instructions (scenario probabilities, key levels, invalidation; judge thesis/catalysts/no-trade reason).
- `agents/base.py`: `load_prompt(name, version="v1")`, `focus_for(name, asset_type, version="v1")`;
  `Context.prompt_version: str = "v1"`; agents pick `AgentOutputPro` when `prompt_version != "v1"`.
- `agents/judge.py`, `agents/debate.py`, `agents/news_researcher.py`: pass `ctx.prompt_version` to `load_prompt`.
- `core/schemas.py` (additive):
  ```python
  class ScenarioProbs(BaseModel): bull: float; base: float; bear: float        # normalised to sum 1 in code
  class KeyLevels(BaseModel): support: list[float] = []; resistance: list[float] = []
  class Catalyst(BaseModel): date: str; event: str; impact: Literal["high","medium","low"] = "medium"
  class AgentOutputPro(AgentOutput): scenario_probs: ScenarioProbs; key_levels: KeyLevels; invalidation: str
  class VerdictPro(Verdict): thesis: str; invalidation: str; catalysts: list[Catalyst]; no_trade_reason: str
  AgentReport += scenario_probs: ScenarioProbs|None = None, key_levels: KeyLevels|None = None, invalidation: str|None = None
  Verdict     += thesis/invalidation/no_trade_reason: str|None = None, catalysts: list[Catalyst] = []
  Prediction  += prompt_version: str = "v1", experiment_id: int|None = None, temperature: float|None = None,
                 degraded: list[str] = []          # data-quality flags (§4)
  ```
  plus strict variants (`AgentOutputProStrict`, `VerdictProStrict`) registered in `STRICT_SCHEMAS` for the
  local provider.
- `core/llm.py`: `LLM(temperature: float | None = None)`; Gemini (`GenerateContentConfig.temperature`), Claude
  (`temperature`) and local (`temperature`, today hard-coded 0.2 → `self.llm.temperature ?? 0.2`) honour it.
  `None` = today's behaviour.
- `orchestrator.run_prediction(..., prompt_version="v1", temperature=None, experiment_id=None,
  executor_factory=None, as_of=None)` — new keyword-only arguments, defaults = Arena behaviour. (`as_of` and the
  backtest executor arrive in P7; the parameter is added once, in P7.)
- `core/db.py`: additive migration — `predictions.prompt_version`, `predictions.experiment_id`; new tables from
  PRO.md §9 (`experiments`, `signals`, `orders`, `fills`, `positions`, `equity_snapshots`, `tool_snapshots`,
  `risk_events`, `backtests`, `reports`) plus `pro_settings` (key/value JSON) and `scheduler_runs` (job key,
  status — for no double runs). Pro tables carry `experiment_id` so one paper account = one experiment.
- `core/experiments.py`: `config_hash(cfg: dict) -> str` (sha256 of canonical JSON), `get_or_create(cfg, note="") -> int`,
  `get(id)`, `current() -> int` (from `pro_settings`).
- `config.py`: `MODEL_CUTOFFS: dict[str, date | None]` (unknown → `None` = every historical date contaminated),
  `PRO_TEMPERATURE = 0.2`, `PRO_PROMPT_VERSION = "v2"`.

**Tests** (`tests/pro/test_p1_*.py`): v1 prompts byte-identical to the old files (hash list); Arena run still
uses `AgentOutput`/`Verdict` schemas and v1 text; v2 run on the role-aware fake Gemini returns the new fields on
reports and verdict; same on a mocked local backend (scripted OpenAI-compatible fake from `test_local_llm.py`);
temperature reaches each backend's request; migration adds columns to an old-schema DB without touching rows;
`config_hash` stable under key order; same config → same experiment id.

**Risks:** strict JSON schema makes every property required — adding fields to `AgentOutput` itself would change
the Arena's schema, hence subclasses. Prompt move must not break `focus_for` section parsing.

## P2 — Signal Engine + cost model

**Files:** `trading/costs.py`, `trading/signals.py`, `trading/__init__.py`, `config.py` (`PRO_SIGNAL` defaults,
`PRO_COSTS_BPS`).

```python
# trading/costs.py
@dataclass(frozen=True) class CostModel: fee_bps: float; slippage_bps: float
def cost_model(asset_type: str, overrides: dict | None = None) -> CostModel
def round_trip_cost_per_unit(price: float, cm: CostModel) -> float      # 2 × (fee+slip) × price
def apply_slippage(price: float, side: Literal["buy","sell"], cm) -> float   # buy pays up, sell receives less
def fee(notional: Decimal, cm) -> Decimal                               # cents, ROUND_HALF_UP

# trading/signals.py
class EntrySpec(BaseModel): type: Literal["market","limit"]; ref_price: float; limit: float|None; expiry: str|None
class SizeSpec(BaseModel): qty: float; notional: float; risk_amount: float; risk_pct: float; capped_by: list[str]
class TradePlan(BaseModel): ... (PRO.md §3.2) + calibrated: bool, p_win: float, cost_r: float, inputs: dict
@dataclass class SignalParams: s_min=0.15, conf_min=0.5, rr_min=1.5, k_atr_swing=1.5, k_atr_scalp=1.0,
    catalyst_window=0.10, allow_crypto_short=False
def build_plan(pred: Prediction, *, atr: float, params: SignalParams, costs: CostModel,
               calibrator: Callable[[float], float] | None = None, now: datetime | None = None) -> TradePlan
```
Geometry: entry ref = `forecast.price_now`; ATR stop `entry ∓ k·ATR`; band stop = P10 (long) / P90 (short);
stop = the one farther from entry; T1 = P50, T2 = P90 (long) / P10 (short); `reward_risk = |T2−entry| / |entry−stop|`;
`p_win` = calibrated P(target before stop) or P(up) (long) / 1−P(up) (short), `calibrated=False`;
`EV_R = p·(0.5·R1 + 0.5·R2) − (1−p)·1 − cost_R`, `cost_R = round_trip_cost_per_unit / |entry−stop|`.
FLAT reasons are a fixed vocabulary (`weak_signal`, `low_confidence`, `low_rr`, `negative_ev`, `catalyst`,
`short_not_allowed`, `bad_geometry`, `degraded_data`, `judge_no_trade`).

**Tests:** each FLAT rule in isolation; long and short geometry on hand-computed numbers; stop is the wider of
ATR and band; T1 on the wrong side of entry → `bad_geometry`; EV after costs matches a hand calculation;
crypto short refused unless enabled; catalyst inside the first 10 % of the horizon → FLAT, at 11 % → not.

**Risk:** with the wider-stop rule most swing predictions are FLAT (RR ≥ 1.5 needs a strong signal). That is
by design (§2.1); the Lab reports the trade frequency.

## P3 — Risk Engine

**Files:** `trading/risk.py`, `config.py` (`PRO_RISK` defaults).

```python
@dataclass class RiskParams: risk_pct_swing=0.005, risk_pct_scalp=0.0025, vol_target=0.15, max_position=0.20,
    max_gross=1.0, max_class={"stock":0.6,"crypto":0.3,"gold":0.3}, max_open=8, corr_max=0.8,
    corr_combined_risk=0.01, daily_loss=-0.02, max_dd=-0.10, kelly_fraction=0.25, kelly_min_n=100, lot={...}
@dataclass class OpenPos: ticker, asset_type, side, qty, entry, stop, notional
@dataclass class Portfolio: equity, peak_equity, day_start_equity, positions: list[OpenPos],
    halted: bool, returns: dict[str, pd.Series] | None
class RiskDecision(BaseModel): approved: bool; size: SizeSpec | None; rejections: list[RiskEvent]; notes
def size_plan(plan, pf, params, sigma_annual: float, calibration_n: int = 0) -> RiskDecision
def breaker_state(pf, params) -> list[str]           # "daily_loss", "max_drawdown"
```
Order: breakers → max open → base size (`risk_amount/|entry−stop|`) → caps (vol, position, gross, class, Kelly)
take the minimum → lot rounding **down** → correlation guard → re-check every limit on the final qty (defence in
depth). Every rejection is a `RiskEvent(rule, detail)` written to `risk_events`.

**Tests:** hypothesis property test generating random portfolios/plans/params: the approved plan never breaks
risk-per-trade, position, gross, class, open-count, vol cap or Kelly cap (with float tolerance 1e-9); rejection
always carries a rule; a halted account never approves. Unit tests for each rule with hand numbers; correlation
guard with synthetic correlated series.

## P4 — Paper Broker + Ledger + trading calendar

**Files:** `trading/calendar.py`, `trading/broker_paper.py`, `trading/ledger.py`.

```python
# calendar.py
def is_open(asset_type, ts) -> bool; def next_session_open(asset_type, ts) -> datetime
def sessions(asset_type, start, end) -> pd.DatetimeIndex     # NYSE via pandas_market_calendars; crypto 24/7;
                                                               gold (GC=F) CME Globex Sun 22:00–Fri 21:00 UTC
# broker_paper.py
@dataclass class Bar: ts, open, high, low, close
@dataclass class Fill: ts, price, qty, fee: Decimal, slippage: Decimal, reason
class Bracket: (side, qty, stop, t1, t2, time_exit, cost model, state)
    def on_bar(bar, resolver=None) -> list[Fill]   # entry at the first bar after the signal bar; OCO legs
def resolve_intrabar(...)                         # 1m bars when available, else "stop"
class PaperBroker: submit(plan, signal_id) / process_bar(ticker, bar) / cancel(...)  (persists orders/fills)
# ledger.py
class Ledger: cash: Decimal; positions; apply_fill(fill); mark(ts, prices) -> EquitySnapshot;
    close_position(...) -> JournalEntry (R multiple, MAE/MFE, holding time, costs); reconcile() -> Decimal diff
```
Rules: market entry at next bar open ± slippage; limit entry fills if low ≤ limit (buy) at min(open, limit);
stop gapped through fills at the open; both stop and target in one bar → stop unless 1m bars resolve it; T1
closes 50 % (rounded down to lot) and moves the stop to breakeven (entry fill price); T2 closes the rest; time
exit at the open of the first bar ≥ `time_exit`; if the entry bar's open is already beyond the stop or T1, the
entry is **cancelled** (`gap_invalidated`). Fees per side on fill notional.

**Tests** (synthetic bars, hand-computed): next-open fill, never the signal bar; gap through stop; OCO (target
cancels stop); T1 → breakeven → stop at entry; ambiguity → stop, and 1m resolver picking the target; fees +
slippage to the cent; time exit; equity snapshots = cash + Σ qty × mark and `reconcile()` == 0.00 exactly;
calendar: NYSE holiday and weekend skipped, crypto 24/7.

## P5 — baselines, quant backtester, metrics, stats

**Files:** `evaluation/metrics.py`, `evaluation/stats.py`, `evaluation/baselines.py`, `evaluation/backtest.py`.

```python
# metrics.py (pure functions on numpy/pandas)
total_return, cagr(equity, days), volatility(r, ppy), sharpe(r, ppy), sortino(r, ppy), max_drawdown(equity),
calmar, expectancy_r(rs), profit_factor(pnls), win_rate, exposure(...), turnover(...),
hit_rate, brier(p, y), log_loss(p, y), pinball(q, y, tau), band_coverage(lo, hi, y), beta, information_ratio
# stats.py
wilson_ci(k, n, z=1.96); block_bootstrap(series, stat, n_boot=2000, block=None, seed) -> (est, lo, hi);
bootstrap_diff(a, b, stat, ...); non_overlapping(rows) -> rows; effective_n(...); percentile_rank(x, dist);
MIN_N = 30; Metric(value, n, lo, hi, sufficient: bool)       # every user-facing number is a Metric
# baselines.py — strategies emit Decision(ticker, ts, direction, stop, targets, time_exit) from data ≤ ts
buy_and_hold, cash, random_strategy(seed, freq), momentum_3m, sma_cross, quant_only (labels + analytic MC → Prediction → Signal Engine)
# backtest.py
@dataclass class BacktestConfig: tickers, start, end, strategy, horizon, risk, costs, seed
def run_backtest(cfg, bars: dict[str, pd.DataFrame]) -> BacktestResult  # same Risk Engine, Bracket fills, Ledger
```
The engine walks the union calendar; at bar *t* the strategy sees `df.loc[:t]` only (a `PointInTimeView`
that raises on future access); orders fill at *t+1*.

**Tests:** metrics vs hand-computed fixtures (small equity curves, R lists); Wilson CI vs textbook values;
bootstrap CI covers the true mean on iid normal data in ≥ 90 % of seeded repeats; a **leak test**: a strategy that
peeks at the next bar would score ~100 % hit rate — the view raises/returns truncated data so it can't; speed:
buy & hold + momentum + random (1,000 seeds) over 3 years × 3 assets < 60 s.

## P6 — scheduler + forward paper loop + routing + budgets

**Files:** `trading/scheduler.py`, `trading/desk.py` (the forward loop: prediction → plan → risk → approval →
broker → ledger → reviewer), `agents/reviewer.py`, `agents/prompts/v2/reviewer.md`, `core/pro_settings.py`.

```python
class Clock(Protocol): def now() -> datetime            # real or simulated
class Job(BaseModel): key, ticker, mode, horizon, model, think_preset, est_s, route_reason
def due_jobs(settings, clock, last_runs) -> list[Job]
def route(job, settings, local_ok: bool, clock, queue_eta_s) -> Job | Skip     # local window, money budget, fallback
class Scheduler: start() / stop() / status() / tick()   # asyncio loop in the API process; yields to RunManager
async def process_prediction(pred, settings, ...) -> Signal                # Desk forward loop
```
Persistence: `scheduler_runs(job_key UNIQUE)` → a restart sees the job as done/running and skips it.
Arena priority: before starting a job the scheduler checks `RunManager.active()`; if any interactive run is
active (or capacity is full) the job waits.

**Tests:** mocked 3-day simulation (simulated clock, synthetic bars, fake `run_prediction`) → signals, fills,
closed trades, journal entries with reviewer notes; restart mid-day → no duplicate predictions; LM Studio down →
Gemini fallback logged; not finishing in the local window → fallback or skip with reason; money budget
exhausted → skip; Arena run active → job deferred.

## P7 — LLM backtest mode + calibration

**Files:** `evaluation/llm_backtest.py`, `evaluation/pit_executor.py` (point-in-time tool executor),
`evaluation/calibration.py`, `orchestrator.py` (`as_of`, `backtest` flags).

- Point-in-time executor serves `get_price_history` / `get_indicators` from bars truncated at `as_of` (and from
  `tool_snapshots` when present); `get_fundamentals`, `get_news`, `get_sentiment`, `get_macro`, `web_search`,
  `fetch_url`, `get_onchain_lite` return an error "not available in backtest mode". Search off, News Researcher off.
- `contamination(model, decision_date) -> "clean" | "contaminated"` from `MODEL_CUTOFFS`; contaminated rows are
  stored but excluded from verdicts and labelled in reports.
- Calibration: pool-adjacent-violators isotonic fit (own implementation, no new heavy dependency) with Platt
  fallback; `walk_forward_calibrate(rows) -> list[float]` uses only rows evaluated before each prediction;
  activates at n ≥ 50 per bucket.

**Tests:** refusal/labels around the cutoff; executor never returns a bar after `as_of`; fundamentals/news
refused; isotonic fit monotone and exact on PAV textbook example; walk-forward never uses a future label.

## P8 — Pro API + SSE

**Files:** `api/routes/pro/{settings,signals,ledger,risk,scheduler,evaluation,backtests,events}.py`,
`api/pro_bus.py` (event buffer + subscribers like `Run`), `api/main.py` (include router under `/api/pro`).
Routes exactly as PRO.md §8. SSE `/api/pro/events` with the event types `signal_created`, `signal_flat`,
`risk_rejected`, `order_placed`, `order_filled`, `position_closed`, `breaker_tripped`, `equity_tick` (+ `job_*`
scheduler status events). `/api/pro/backtests/{id}/events` streams progress.

**Tests:** every route (TestClient), approve/reject state machine, settings PUT creates a new experiment,
account reset, event stream for a mocked scheduler day (ordered, typed, `seq` ids).

## P9 — Desk UI

**Files:** `web/src/pro/` (pages Overview, Signals, SignalDetail, Positions, Journal, Lab, Risk, Settings;
components EquityChart, Blotter, TradeChart, RiskBars, CalibrationPlot, StrategyTable, MetricCell, Badge),
`web/src/pro/desk.css` (Desk tokens), `App.tsx` (routes `/pro/*`, mode switch), `TopBar.tsx` (Arena ↔ Desk).
Desk renders its own calm top bar (no stage background). `scripts/pro_demo.py` builds a deterministic demo DB
(3-day simulated scheduler run + a report + backtests) for e2e. `web/e2e/pro.mjs` drives every page at 1440 px
and 390 px, approve/reject, saves `docs/pro/*.png`, fails on console errors.

## P10 — real verification + docs

≤ 10 paid runs (Gemini flash-lite); one overnight scheduler batch on the watchlist with local Quick (inside
`PRO_LOCAL_WINDOW`, or a temporary window recorded in PROGRESS.md); generate the first evaluation report;
README "Pro mode"; merge plan in PROGRESS.md.

---

## Ambiguities and choices

| # | PRO.md says | Choice |
|---|---|---|
| A1 | "Extend AgentReport with optional fields" | New fields are optional on `AgentReport`/`Verdict`; the *LLM-facing* schema is a subclass used only with v2 prompts, so the Arena's JSON schema is byte-identical. |
| A2 | Where Pro settings come from | `run_prediction(prompt_version="v2", temperature=0.2)` — new optional args; the Arena passes nothing. |
| A3 | `reward_risk` with two targets | R:R = distance to **T2** / stop distance (the full-target convention); EV uses the bracket payoff 0.5·R1 + 0.5·R2 on a win. |
| A4 | EV probability | `p_win` = calibrated P(target before stop) when a calibrator for the bucket exists; otherwise P(up)/1−P(up), flagged `calibrated=false`. |
| A5 | Entry when the next open gaps past stop or T1 | Entry cancelled (`gap_invalidated`), no trade. |
| A6 | "Day" for the daily loss limit | UTC calendar day. |
| A7 | Gold market hours | CME Globex approximation (Sun 22:00 – Fri 21:00 UTC, daily break ignored); GC=F bars from yfinance decide what exists. |
| A8 | Baseline trade geometry | Momentum/random/buy & hold: ATR stop, no targets, time exit at the horizon, same Risk Engine and costs. Quant-only goes through the full Signal Engine. |
| A9 | Annualisation | Equity snapshots once per UTC day including weekends → 365 periods/year for Sharpe/Sortino/vol. |
| A10 | Block length for the bootstrap | ⌈n^(1/3)⌉ (moving-block bootstrap), 2,000 resamples, seeded. |
| A11 | Calibration method | Own PAV isotonic regression (no scikit-learn); Platt not needed unless isotonic has < 3 distinct levels. |
| A12 | Extra tables | `pro_settings`, `scheduler_runs`, and an `experiment_id` column on all Pro tables (one paper account per experiment; reset = new experiment). |
| A13 | Approval mode | Manual by default: signals stay `new` until approved; signals not approved before their entry bar expire (`expired`). |
| A14 | Reviewer model | Local model when LM Studio is reachable, else the cheapest Gemini model by input price; at most one call per closed trade (stored even if it fails, as "review unavailable"). |
