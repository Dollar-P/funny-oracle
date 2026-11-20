# Progress

## Current state (2026-11-20)

- **Arena** (REDESIGN.md, R1–R8) and the **original build** (ARCHITECTURE.md, CP1–CP9) are done and on `main`: FastAPI + React web app, Streamlit UI, CLI, Auto-Scalp, Hall of Fame, Duel, Replay, share card.
- **Local LLM** (LOCAL_LLM.md) is merged into `main`: OpenAI-compatible provider for LM Studio (`local:<id>`, host only from `LOCAL_BASE_URL`), `web_search` / `fetch_url` MCP tools, local models in the Arena/Duel pickers (Quick/Deep, no Scalp).
- **Default model:** `gemini-3.5-flash-lite` for every role; `gemini-3.8-flash` and `local:prism-ml/bonsai-27b` selectable per run. Claude provider implemented, mocks only.
- **Tests:** `pytest -q` → **214 passed** (`tests/` + `api/tests`, network tests included); browser checks in `web/e2e/`.
- **Spend so far:** ฿22.17 tracked / ฿12.00 estimated actual of the ฿100 budget (local runs are free).
- `main` is pushed to https://github.com/Dollar-P/funny-oracle. The local branch `backup/pre-rewrite` must never be pushed.
- **In progress:** Funny Oracle Pro, "The Desk" (PRO.md), on branch `pro` (plan: [docs/pro-plan.md](docs/pro-plan.md)). Paper trading only.

## Docs

- [docs/progress-arena.md](docs/progress-arena.md): full history of the original build, the Arena redesign and Local LLM (checkpoints, real runs, spend log, decisions, known issues)
- [ARCHITECTURE.md](ARCHITECTURE.md): original architecture
- [REDESIGN.md](REDESIGN.md): the Oracle Arena web UI
- [LOCAL_LLM.md](LOCAL_LLM.md): local LLM provider (LM Studio)
- [PRO.md](PRO.md): Pro spec, research and paper trading

## Pro checkpoints (PRO.md §12)

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| P1 | Branch, DB migration, experiments, richer agent fields, prompts v1/v2, `MODEL_CUTOFFS` | ✅ | `tests/pro/test_p1_foundation.py` (13): v1 prompts byte-identical to `main` (sha256 per file); Arena LLM-facing JSON schemas identical to `main` (hash per schema); Arena run (Gemini fake) has v1 text, no Pro fields, no temperature; v2 run on the Gemini fake and on a mocked local backend returns scenario probs (normalised), key levels, invalidation, thesis, catalysts, no-trade reason; temperature reaches both backends; `prompt_version` / `experiment_id` stored; old-schema DB migrated additively; experiment hash stable under key order; `MODEL_CUTOFFS` env override; data-quality flags. Full suite 227 passed; `npm run build` OK |
| P2 | Signal Engine + cost model | ✅ | `tests/pro/test_p2_signals.py` (26): bps defaults and overrides, slippage direction, fees to the cent (half-up), round-trip cost; long and short geometry + EV checked by hand (e.g. long 100 / P10 90 / P50 104 / P90 118 / ATR 4 → stop 90, R:R 1.8, cost 0.004 R, EV +0.298 R); ATR vs band stop; band edge on the wrong side ignored; scalp k=1; every FLAT rule in isolation; inclusive thresholds; EV = 0 → FLAT; costs flip EV (crypto); crypto short gate; catalyst window boundary; calibrator use; ATR uses no future bar |
| P3 | Risk Engine | ✅ | `tests/pro/test_p3_risk.py` (14): hypothesis property test (1,000 random portfolios × plans × parameter sets; ~35 % approved, every cap seen binding) — an approved plan never exceeds risk per trade, max position, vol cap, gross, asset-class cap, open-position count, breakers or the Kelly cap; every rejection carries a known rule and a detail; a halted account never approves (100 cases); a Kelly property test (300 cases). Hand-checked sizing (0.5 % / 0.25 % risk, lot rounding down, each cap binding), breakers, duplicate ticker, correlation guard (ρ ≈ 0.99 same side → rejected only when combined risk > 1 %), Kelly only at calibration n ≥ 100, rejections written to `risk_events` |
| P4 | Paper Broker + Ledger + trading calendar | ✅ | `tests/pro/test_p4_broker.py` (23), synthetic bars with hand-computed fills: next-open entry (never the signal bar; a decision after Monday's close fills at Tuesday's 14:30 UTC open), entry gap past stop/T1 cancels, limit entry, expiry, gap through stop fills at the open (−751.85 / −1.50 R), T1 → breakeven → stop (+99.00), T2 cancels the stop (OCO), gap through targets, intrabar ambiguity = stop and 1m resolver (failure → stop), time exit, crypto fees/slippage to the cent (+384.60, costs 15.40), short mirror, MAE/MFE, 1-share T1, state round trip; ledger: equity = cash + Σ qty × mark every bar and `reconcile()` = 0.00 exactly, cash − start = Σ realised P&L; persistent account: restart never re-feeds a bar, order statuses (OCO cancelled), equity snapshot 100,204.00 reconciles with the fills; calendar: Thanksgiving, early close, DST, weekends, gold Sunday open, bar open/close times |
| P5 | Baselines + quant backtester + metrics + stats | ✅ | `tests/pro/test_p5_metrics.py` (13): Sharpe √75/30, Sortino √300/30, vol, CAGR, max DD, Calmar, expectancy, profit factor, beta, IR, Brier, log loss, pinball, band coverage, reliability bins, Wilson 8/10 = (0.490, 0.943), block-bootstrap coverage ≥ 88 % on iid data, paired bootstrap, non-overlapping selection, percentile rank, Metric labels. `tests/pro/test_p5_backtest.py` (12): buy & hold by hand (119,936.04), cash, engine reconciles to 0.00 and final equity = start + Σ P&L, momentum/SMA/random (seeded), quant-only through the Signal Engine, analytic forecast = Monte Carlo (0.5 %), max-DD halt for good; **lookahead**: a next-bar oracle is ≈ 50 % on the real view and > 95 % on a deliberately leaky view; **speed**: buy & hold + momentum + 1,000 random runs over 3 years × 3 assets in ≈ 27 s (< 60 s) |
| P6 | Scheduler + forward paper loop + model routing and budgets | ✅ | `tests/pro/test_p6_scheduler.py` (13): **mocked 3-day simulation** (Tue–Fri, 30-min simulated ticks, synthetic bars released only once complete, fake runner + reviewer): 9 jobs (incl. Monday's stock job caught up before Tuesday's open), signals → fills (entry/T1/T2) → closed trades with positive R and reviewer post-mortems, duplicate signals rejected by risk, stock entries at 14:30 UTC, ≥ 150 equity snapshots, cash reconciles with fills; **restart** with fresh objects and overlapping ticks → no job runs twice, no fill duplicated; **LM Studio down** → Gemini fallback logged; **won't finish in the window** → fallback, or skip when the money budget is 0; outside the window → wait if the job is still valid when it opens, else fall back; **Arena active** → job deferred, nothing claimed, runs on the next idle tick; manual approve → fill; approval after the entry bar opened → expired; scalp is Gemini-only; window across midnight in Asia/Bangkok; due times for stock/gold/crypto and weekends |
| P7 | LLM backtest mode with leakage rules + calibration | | |
| P8 | Pro API + SSE events | | |
| P9 | Desk UI | | |
| P10 | Real verification + docs | | |

## Pro decisions

- **Prompt versions** (P1): `agents/prompts/v1/` = the old prompts moved unchanged (a test pins every file's
  hash); `v2/` = v1 + a "Desk (research) rules" block in `_analyst.md` and `judge.md`. `run_prediction` takes
  `prompt_version` (default `"v1"`), `temperature` (default `None` = provider default) and `experiment_id`.
- **Richer output without touching the Arena schema** (P1): the new fields are optional on the stored
  `AgentReport` / `Verdict`; the LLM sees them only through `AgentOutputPro` / `VerdictPro` (v2). The judge's v1
  schema is now `VerdictOutput` (same JSON schema as `main`'s `Verdict`, pinned by hash — a class docstring
  would have changed it, so it has a comment instead). Scenario probabilities are normalised to sum 1 in code.
- **Temperature** (P1): Pro runs use `PRO_TEMPERATURE` = 0.2 on every provider (Gemini config, Claude
  `temperature`, local payload). The Arena keeps provider defaults (local stays at 0.2).
- **Data quality** (P1, `core/data_quality.py`, Pro runs only): `stale` (daily: last bar older than 4 days for
  stocks/gold, 2 for crypto; intraday: older than 3 bars while the market is open), `missing_bars` (crypto > 10 %
  of the last 30 bars missing; stocks/gold a gap > 5 days), `zero_volume` (last 3 bars, stocks/crypto only).
  Flags are stored on the prediction (`degraded`); the Signal Engine forces FLAT.
- **Experiments** (P1): `core/experiments.py` hashes the canonical JSON of the config (sha256, 16 hex). Same
  config → same id.
- **Model cutoffs** (P1): every entry in `config.MODEL_CUTOFFS` is `None` (unknown) for now, so every
  historical date is contaminated until a cutoff is filled in (from the model card, or `MODEL_CUTOFFS` JSON in
  `.env`). Deliberately conservative.
- **Signal Engine** (P2, `trading/signals.py`): R:R = distance to **T2** / stop distance; EV uses the bracket
  payoff 0.5·R1 + 0.5·R2 on a win and −1 R on a loss, minus round-trip costs in R. Without a calibrator,
  p = P(up) (long) or 1 − P(up) (short), flagged `calibrated=false` — a rough proxy for P(target before stop).
  |EV| ≤ 1e-9 counts as zero (FLAT). The band stop (P10 for longs, P90 for shorts) only counts when it is on the
  adverse side of entry. FLAT reasons are a fixed vocabulary checked in a fixed order; the plan keeps its
  geometry so the UI can explain a FLAT. A Judge `no_trade_reason` forces FLAT (`judge_no_trade`). Catalyst
  dates are days: a high-impact event dated between the decision day and the end of the first 10 % of the
  horizon forces FLAT; unparseable dates are ignored.
- **Costs** (P2, `trading/costs.py`): bps per side from `config.PRO_COSTS_BPS`; slippage worsens the fill price,
  fee = bps × fill notional rounded half-up to the cent.
- **Risk Engine** (P3, `trading/risk.py`): pure `size_plan(plan, portfolio, params)`. Exposure at the current
  mark; open risk = qty × distance from mark to the current stop. The quantity is the minimum of the risk-based
  size and every cap, then rounded **down** to the lot (stocks 1 share, gold 0.01, crypto 0.0001); every limit is
  re-checked on the final quantity. A cap with no room rejects with that cap's rule. One open position per
  ticker (`duplicate_position`), which also gives "at most one open scalp position per asset". Correlation:
  Pearson ρ of the last 60 daily returns (≥ 20 overlapping), same direction only. Kelly cap: f* = p − (1−p)/b with
  b = 0.5·R1 + 0.5·R2, active only for calibrated plans once calibration n ≥ 100; f* ≤ 0 rejects. A drawdown
  ≤ −10 % rejects with `halt=true`; the caller persists the halt until a manual reset.
- **Paper broker** (P4, `trading/broker_paper.py`): one `Bracket` implementation shared by the forward loop
  and the backtester. Orders fill only on a bar whose *open time* (`trading/calendar.py`: NYSE open for stock
  daily bars, 18:00 New York the evening before for gold futures, the bar stamp otherwise) is ≥ the submit
  time. Target (limit) fills get exactly the limit price; stops, market entries and time exits pay slippage.
  After T1 fills *inside* a bar, nothing else happens in that bar; the breakeven stop and T2 apply from the
  next bar (changed in P6: since every bar's low ≤ its open ≤ the entry fill, a same-bar breakeven stop
  stopped out every trade that reached T1 on its entry bar — a bias, not conservatism). After a gap through T1
  at the open, the breakeven stop and T2 are checked intrabar. An entry
  whose fill price is already past the stop or T1 is cancelled (`gap_invalidated`). Intrabar fills are stamped
  at the bar's close time. If half the quantity rounds to zero lots, T1 closes everything.
- **Ledger** (P4): Decimal cents. Each fill's cash flow = notional rounded to the cent ± fee; equity marks each
  open position at qty × mark rounded to the cent. The persistent `PaperAccount` (`trading/paper_account.py`)
  rebuilds cash from the fills table and stores each bracket's state plus its last processed bar.
  Callers must only feed *completed* bars (close time ≤ now): a partial bar would be skipped later.
- **Gold hours** (P4, refined in P6): Sun 18:00 → Fri 17:00 New York with the 17:00–18:00 daily break; CME holidays are not modelled.
- **Backtester** (P5, `evaluation/backtest.py`): one engine for every strategy — same Risk Engine, `Bracket`
  fills, costs and Decimal ledger as the forward loop. Per union date: bars → fills, mark at the last known
  closes (one equity point per date, stamped 00:00 UTC of that date, valued at that date's closes), then
  decisions at the close through a `PointInTimeView` (bars ≤ the decision bar only). Decisions per ticker every
  `horizon` bars (non-overlapping) and only when the ticker is flat. Annualisation: 365 periods/year if the
  portfolio holds crypto (daily points every day), else 252. A max-drawdown halt lasts to the end of the
  backtest (no manual reset). σ = 0 → `no_volatility` rejection.
- **Baselines** (P5, `evaluation/baselines.py`): momentum (sign of the 63-bar return), SMA 50/200, random
  (seeded; 1,000 runs in parallel processes) use the Signal Engine's stop rule for a zero-drift forecast (wider
  of k·ATR and the P10/P90 band edge), no targets, time exit at the horizon. Quant-only = deterministic label
  score (SMA regime 0.30, trend vs SMA200 0.20, vs SMA50 0.20, MACD momentum 0.20, RSI zone 0.10) with
  confidence 0.6 and the **closed-form** log-normal forecast (equal to the Monte Carlo in expectation, tested to
  0.5 %) → full Signal Engine. Buy & hold = equal-weight basket at the first open, not risk-sized (a benchmark).
- **Statistics** (P5, `evaluation/stats.py`): Wilson 95 % CI; circular moving-block bootstrap, block ⌈n^(1/3)⌉,
  2,000 resamples, seeded; paired bootstrap for differences; greedy non-overlapping selection per asset;
  `MIN_N` = 30 → "insufficient data". Ratios on a series with σ < 1e-12 per period are reported as 0.
- **numpy 2**: `repr(np.float64(x))` is `"np.float64(x)"`, so every Decimal conversion goes through
  `repr(float(x))`.
- **Scheduler timing** (P6, `trading/scheduler.py`): stocks 30 min after the NYSE close (valid until the next
  open); gold 15 min after the CME 17:00 New York close (valid until the 18:00 reopen); crypto at 23:30 UTC
  (valid 30 min, so it fills at the 00:00 UTC open). This refines PRO.md's "crypto and gold at a fixed UTC
  time": a single fixed time would either use an unfinished bar or miss the next open by a day. Jobs missed
  while the API was down are caught up while still valid (e.g. Monday's stock job until Tuesday's open).
- **Routing** (P6): local runs need LM Studio reachable, the local window open and queue ETA + estimate inside
  the window; outside the window a local job waits if it is still valid when the window opens, else it falls
  back. Estimates = average `elapsed_s` of the last 10 swing predictions on that model/preset (defaults: local
  Quick 400 s, Deep 1,500 s, Gemini 45 s). Money budget = Pro predictions + reviewer calls since 00:00 UTC,
  tracked basis.
- **No double runs** (P6): a job key is inserted into `scheduler_runs` before its run starts (INSERT OR IGNORE);
  failed runs are recorded and never retried automatically. Deferred jobs (Arena busy, waiting for the window)
  are not recorded, so they run later.
- **Arena priority** (P6): a job starts only if no interactive run is active *and* it would not take the last
  RunManager slot. Scheduled runs go through `RunManager.start_real` (which now passes `prompt_version`,
  `temperature`, `experiment_id` when present).
- **Experiments vs routing** (P6, `core/pro_settings.py`): the experiment covers the routing *policy* (primary
  swing model + fallback); each prediction records the model actually used and the Lab splits by it. Changing
  risk / cost / signal params, prompt version, temperature, starting equity or resetting the account starts a
  new experiment (= a fresh paper account); watchlist, schedule, approval mode and budgets do not.
- **Signals** (P6, `trading/desk.py`): statuses flat · rejected · new · approved · filled · closed · cancelled ·
  expired. Risk is re-checked at approval time. A manual signal expires when its entry bar opens (the plan's
  prices were computed for that bar). Daily-loss breaker: day start = last equity snapshot before 00:00 UTC.
- **Reviewer** (P6, `agents/reviewer.py`): one call per closed trade (`positions.review`), local when reachable,
  else the cheapest Gemini model if the money budget allows; a failure is stored as "review unavailable".
- **Line endings**: the repo mixes LF and CRLF files; edits keep each file's existing endings.

## Pro known issues

- `MODEL_CUTOFFS` are unknown for every model, so LLM backtests can never produce a verdict until the cutoffs
  are filled in (by design — see Pro decisions).
