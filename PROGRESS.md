# Progress

## Current state (2026-11-20, `main`)

- **Arena** (REDESIGN.md) and the **original build** (ARCHITECTURE.md) are done and on `main`; **Local LLM** is merged.
- **Pro — "The Desk"** (PRO.md) is **merged into `main`** (merge commit `6a85896`, `--no-ff`, 2026-11-20): Signal Engine, Risk Engine, paper
  broker + Decimal ledger, scheduler with model routing and budgets, LLM backtests with contamination rules,
  calibration, Evaluation Lab + reports, `/api/pro/*` + SSE, and the Desk UI at `/pro` with an Arena ↔ Desk switch.
- **Arena and Pro are separate worlds** (merge prep, `core/worlds.py`): Arena views never show `source = 'pro'`
  predictions, Pro views read only them; Arena JSON is byte-identical to `main` on the real DB.
- **Tests:** `pytest -q` → **359 passed** (214 existing + 145 Pro); `web/e2e/pro.mjs` 35/35 on the demo dataset;
  Arena `mobile.mjs` 4/4, `show.mjs`, `replay.mjs`, `r7.mjs` OK on a copy of the real DB; `npm run build` OK.
- **Merged**: `pytest -q` 359 passed and `npm run build` OK on `main` after the merge. Safety tag `pre-pro-merge`
  = `df82d0c` (`main` before the merge). Run commands for demo and real mode: README "Pro mode".
- **Spend:** ฿27.39 tracked / ฿17.23 estimated actual of ฿100 (Pro P10 used **6 paid runs, ฿5.23 tracked**).
- **Still to do (yours):** the real overnight scheduler batch on the **local** model. LM Studio is now reachable
  (`GET /api/models` 2026-11-20: `local:prism-ml/bonsai-27b` available); the scheduler has not been started. See
  "P10 — what remains". Database backup before merge prep: `data/oracle.db.bak-20261120` (git-ignored).
- `main` and the tag `pre-pro-merge` are on GitHub; `pro` stays local for the forward test (never pushed). The
  local branch `backup/pre-rewrite` must never be pushed.

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
| P7 | LLM backtest mode with leakage rules + calibration | ✅ | `tests/pro/test_p7_backtest_llm.py` (16): contamination labels (on the cutoff day = contaminated; unknown cutoff = contaminated); backtest **never runs** a pre-cutoff date by default and replays it as FLAT `contaminated`; with `allow_contaminated` the dates run, are labelled and `verdict_eligible = false`; unknown cutoff → nothing runs; paid model → `CostConfirmationRequired` with an estimate; decision dates match the engine's schedule; point-in-time executor: last bar = the last bar closed at `as_of` (EDT/EST aware), price history and indicators computed from truncated bars, fundamentals/news/sentiment/macro/web refused and not even listed; anonymised payloads; orchestrator `as_of` mode: search off, no News Researcher, created_at/target_time from `as_of`, prompts show the historical time, only price tools offered, scalp refused; ablation variants (team-no-debate, single-agent, leave-one-out); PAV textbook case; isotonic monotone and fixes an overconfident model; identity below n = 50; Platt fallback; walk-forward never uses an outcome unknown at prediction time (label-flip test); Desk bucket calibrator |
| P8 | Pro API + SSE events | ✅ | `api/tests/test_pro_api.py` (8): settings GET/PUT (deep merge, validation, operational vs experiment keys), **a mocked scheduler day through the API** (simulated clock, `/scheduler/tick`): signal → detail (agents, forecast, deadline, replay link) → approve (409 on a second approve/reject, 404 unknown) → orders → entry + T1 with the stop at breakeven → T2 → journal with review, tags, CSV; the **event stream** is ordered by `seq`, typed (`job_started`, `job_done`, `signal_created`, `order_placed`, `order_filled` ×3, `position_closed`, `equity_tick`), resumable with `after`/`Last-Event-ID`, filterable, and real SSE framing; overview KPIs (Sharpe labelled insufficient data), equity + buy & hold and cash benchmarks, risk limits + correlation matrix of open positions + rejection log, account reset = new experiment (old signals expire), scheduler start/stop/status, evaluation report run (wait) and fetch, backtests (baselines; LLM backtest on a paid model → `needs_confirmation` with an estimate) with progress events and an SSE stream that ends on `backtest_done`; Arena routes untouched. Full suite 351 passed; `npm run build` OK |
| P9 | Desk UI | ✅ | `web/e2e/pro.mjs` against the demo dataset (`scripts/pro_demo.py`, served from a copy of its DB with the production build): **35/35 checks, 0 console errors** — all 8 pages render at 1440 px and 390 px, no horizontal scroll at 390 px, Approve and Reject work from the blotter (the approved signal shows as a pending entry), the Lab shows confidence intervals, n, insufficient-data badges and contamination labels, the Arena ↔ Desk switch round-trips. `web/e2e/mobile.mjs` (Arena) still 4/4 at 390 px. Screenshots in `docs/pro/` were reviewed; fixes made from them: backtest warm-up excluded from equity (see Pro decisions), straight P10/P50/P90 lines when a prediction has no fan path, 120-bar trade chart, thousands separators on price axes, calibration-plot label overlap, Lab tables in scroll containers, the Arena top bar's switch collapsing to an icon on phones, 100-row blotter. `npm run build` OK |
| P10 | Real verification + docs | ⚠️ partial (overnight batch is yours) | **Paid:** 6 real runs (≤ 10) through the real API server and `/api/pro/scheduler/tick` on the real DB — table below. **Report:** `docs/pro/reports/2026-11-20-report-1.{md,json}`. **Docs:** README "Pro mode", this file, merge plan below. **Not done:** the overnight batch on local Quick (LM Studio unreachable) — instructions below |
| Merge prep | Arena/Pro separation, main merged, full verification, run docs | ✅ | `api/tests/test_worlds.py` (7): mixed DB → every Arena endpoint/helper identical before and after adding Pro rows and free of them, Pro ids 404 in the Arena; Pro views identical before and after adding Arena rows and never render an Arena prediction; filtered = unfiltered queries without Pro rows (NULL source = Arena); Arena JSON has no Pro keys and both worlds round-trip; guard over every `predictions` query. `main` worktree vs this branch on copies of the real DB: identical Arena JSON (md5). pytest 359 passed; `npm run build` OK; Arena e2e mobile 4/4, show, replay, r7 OK (0 errors); Desk `pro.mjs` 35/35; demo and real-mode commands run in PowerShell |

## P10 — real verification (2026-11-20, 10:00–10:05 Bangkok)

LM Studio was unreachable (no `LOCAL_BASE_URL` in `.env`; the default `localhost:1234` refused), so the primary
swing model (`local:prism-ml/bonsai-27b`, Quick) could not run and every job took the documented fallback. The
real database `data/oracle.db` was backed up first. Settings for the batch: 6 stocks (NVDA, AAPL, MSFT, AMZN,
GOOGL, META), 1M, manual approval, ฿10 daily budget. At 02:58 UTC Thursday's stock jobs were due (valid until
Friday's 14:30 UTC open), so one `POST /api/pro/scheduler/tick` ran them:

| Job | Routed to (logged reason) | Time | Cost | S_final · conf · P(up) | Result |
|---|---|---|---|---|---|
| AAPL 2026-11-19 | flash-lite — "fallback …: LM Studio unreachable" | 29 s | $0.0256 | −0.01 · 0.60 · 51 % | FLAT `judge_no_trade` (also weak signal, bad geometry, R:R 0.90, EV −0.29 R) |
| AMZN | same | 34 s | $0.0274 | +0.10 · 0.62 · 52 % | FLAT `judge_no_trade` (R:R 1.25, EV −0.16 R) |
| GOOGL | same | 32 s | $0.0262 | +0.00 · 0.48 · 50 % | FLAT `judge_no_trade` (low confidence, EV −0.21 R) |
| META | same | 31 s | $0.0251 | −0.04 · 0.65 · 48 % | FLAT `judge_no_trade` (R:R 0.90, EV −0.25 R) |
| MSFT | same | 32 s | $0.0283 | +0.04 · 0.50 · 51 % | FLAT `judge_no_trade` (R:R 1.15, EV −0.20 R) |
| NVDA | same | 30 s | $0.0258 | −0.06 · 0.55 · 48 % | FLAT `judge_no_trade` (R:R 0.89, EV −0.25 R) |

Total ฿5.23 tracked. Verified for real: v2 prompts at temperature 0.2 returned every Pro field (5/5 agents with
scenario probabilities, key levels and invalidation; the judge's thesis, invalidation and a no-trade reason);
11–12 tool snapshots stored per run; routing reason logged in `scheduler_runs`; budget accounting; the signal
detail page on real data (`docs/pro/real_signal_detail_1440.png`). **No trade was opened** — all six were FLAT,
which is what PRO.md §2.1 expects for low-conviction days, but it means real fills have not happened yet.

**First evaluation report** (`docs/pro/reports/2026-11-20-report-1.md`): forward section = insufficient data
(6 predictions, none evaluated yet; 0 trades). Backtests, 2023-11-21 → 2026-11-19, the 6 stocks, same costs and
Risk Engine, 751 trading days each:

| Strategy | Sharpe [95 % CI] | ΔSharpe vs B&H [95 % CI] | Total return | Max DD | Trades |
|---|---|---|---|---|---|
| Buy & hold (equal weight) | 1.00 [−0.08, 2.08] | — | +96.2 % | −26.8 % | – |
| Momentum (3-month) | −0.04 [−1.18, 1.08] | −1.04 [−2.38, 0.27] | −0.7 % | −11.0 % | 107 |
| SMA 50/200 | 0.14 [−0.94, 1.23] | −0.86 [−1.87, 0.01] | +1.6 % | −8.9 % | 214 |
| Quant-only (no LLM) | 0.09 [−0.91, 1.00] | −0.91 [−2.07, 0.06] | +0.7 % | −4.9 % | 135 |

Quant-only directional hit rate 54.8 % [48.0 %, 61.3 %] (n = 210 non-overlapping), Brier 0.259 (coin flip 0.250);
it ranks at the 57th percentile of 1,000 random strategies (Sharpe 5/50/95th pct −1.14 / 0.01 / 0.87). Verdict:
**no evidence of a difference vs buy & hold** (every CI touches 0); with 0.5 % risk per trade the systematic
strategies are barely invested next to a +96 % buy & hold in mega-cap tech. 5 variants compared → Bonferroni α/k
= 0.01.

### P10 — what remains and how to run it

Exact PowerShell commands: README → "Pro mode" → "Real mode".

1. `LOCAL_BASE_URL` is set in `.env` and LM Studio answered on 2026-11-20 (`local:prism-ml/bonsai-27b`
   available). Check `GET /api/models` again before starting.
2. Start the API in the evening: `.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000`. In Settings, set the 14-asset default
   watchlist (or keep the 6 stocks), approval mode, and `local_tz` (`Asia/Bangkok`).
3. Start the scheduler before 23:00 (Overview → Start, or `POST /api/pro/scheduler/start`). Inside the window
   (23:00–07:00) stock jobs (after the 04:00 Bangkok close + 30 min), gold and crypto jobs run on local Quick,
   serially (≈ 6–7 min each). To verify outside the window, set a temporary `local_window` in Settings and record
   it here.
4. Next morning: check Overview → Recent scheduler jobs (routing reasons), approve/reject signals, then Lab → Run
   evaluation. Record the batch here (jobs, times, how many local vs fallback, signals).

## Check manually (Pro)

1. **The local overnight batch** above (not run during development).
2. **A real trade lifecycle**: approve a real signal and follow it to a fill, T1/T2/stop and the Reviewer
   post-mortem over the following days (only simulated so far).
3. **Real 1m intrabar resolution**: `minute_resolver` is unit-tested with fake bars; the forward loop does not
   pass a resolver yet, so ambiguous bars are always resolved as "stop".
4. **The Desk in a real browser**: live row highlights via SSE and the CSV download (headless can't check the file).
5. **Real data in `data/oracle.db`**: P10 added Pro tables, settings, 6 Pro predictions (source `pro`) and 6 FLAT
   signals. They are additive and, since merge prep, hidden from every Arena view (Hall of Fame shows #1–20).
6. **Arena on a phone**: the coin meter now shows only the amount spent below 640 px (the cap is in its tooltip),
   and the nav icons are 4 px tighter, so the bar fits one line with the Desk icon (it wrapped to 3 lines).

## Merge plan (`pro` → `main`) — ✅ merged (`6a85896`)

Done 2026-11-20: merged with `git merge --no-ff pro` as `6a85896` ("Merge pro: Funny Oracle Pro (The Desk)"),
tests and build green on `main`, pushed `main` + tag `pre-pro-merge`. Roll back:
`git checkout main && git reset --hard pre-pro-merge` (and `git push --force-with-lease origin main` only if the
merge must be removed from GitHub too). **Still open: the overnight batch on the local model** (P10, below).

1. Rebase/merge `main` into `pro` first. Done: `git merge main` → already up to date (`main` = `origin/main` =
   `df82d0c`, the branch point). Repeat right before merging if `main` moves.
2. Shared-code changes are additive and default to the Arena's behaviour: `agents/prompts/` moved to `v1/`
   (byte-identical, pinned by test), optional fields on `AgentReport`/`Verdict`/`Prediction`, `VerdictOutput` (same
   JSON schema as before, pinned), `run_prediction(prompt_version, temperature, experiment_id, as_of)` with
   Arena defaults, `LLM(temperature)`, additive DB migration + new tables, `RunManager.start_real` passing Pro
   params only when present, `mcp_server.server.price_history_payload` (extracted, same output), the Arena top
   bar's mode switch, and the new `/pro` routes. Merge prep added `core/worlds.py` (Arena/Pro filters on every
   `predictions` query), a serializer that leaves Pro fields at their defaults out of JSON (Arena JSON =
   `main`), `BudgetGuard.estimate_usd(world=…)`, and Pro runs hidden from `GET /api/runs`.
3. Before merging: run `pytest -q`, `npm run build`, `web/e2e/mobile.mjs`, `show.mjs`, `replay.mjs`, `r7.mjs`
   (Arena) and `pro.mjs` (Desk demo). All green on 2026-11-20 (see Current state). Optional: fill in
   `MODEL_CUTOFFS` if LLM backtests are wanted.
4. Merge with `--no-ff`, push `main` only, and keep `pro` for the forward test that continues to accumulate data.

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
- **LLM backtests** (P7, `evaluation/llm_backtest.py`): decision dates on or before `MODEL_CUTOFFS[model]` (or
  any date if the cutoff is unknown) are "contaminated — not evidence" and are **not run** unless
  `allow_contaminated=True`, which also makes the result ineligible for a verdict. Runs go through
  `run_prediction(as_of=…)` with `evaluation/pit_executor.py`: only `get_price_history` / `get_indicators`
  (computed from bars closed at `as_of`) are offered; everything "as of now" is not listed and refused if
  called; search and the News Researcher are off; σ and the price come from bars ≤ `as_of`. Predictions are not
  saved to the `predictions` table (they would show up in the Arena's Hall of Fame). The trades go through the
  normal backtest engine via a `PrecomputedStrategy`. Paid models raise `CostConfirmationRequired` until
  confirmed; local is the default.
- **Ablation** (P7, `evaluation/ablation.py`): team-no-debate, single-agent and leave-one-out variants are derived
  from the stored full-system predictions (no extra LLM calls); every variant, including `full`, uses the
  closed-form forecast so only the score differs.
- **Calibration** (P7, `evaluation/calibration.py`): own PAV isotonic on ≤ 20 quantile bins (≥ 5 per bin, ties
  kept together) — fitting raw 0/1 outcomes pinned the curve to 0.99 at the top edge; Platt fallback (fewer than 3
  levels) with an L2 prior toward the identity map; active at n ≥ 50 per (mode, asset class) bucket of evaluated
  Pro predictions; walk-forward uses only outcomes known before each prediction.
- **Tool snapshots** (P7, `core/snapshots.py`): Pro live runs store every successful tool result (zlib JSON) in
  `tool_snapshots`; the Arena does not.
- **API** (P8, `api/routes/pro/`, `api/pro_state.py`): all routes from PRO.md §8 plus `/overview`, `/scheduler/tick`
  (one pass on demand), `/evaluation/status|latest`, `/snapshots/{id}`, `/journal/{id}/tags` and `/journal?format=csv`.
  One process-wide Desk and Scheduler. The Desk event bus is thread-safe (approve/reject run in the thread pool),
  keeps the last 2,000 events with a global `seq`, and streams by polling every 0.25 s; `?once=1` returns the
  buffered events as JSON and `max_s` bounds an SSE stream. Reports and backtests run in background threads
  (`wait=true` runs them inline). The scheduler is stopped on API shutdown.
- **Report** (P8, `evaluation/report.py`): forward section (prediction quality per bucket on non-overlapping
  samples, Wilson / block-bootstrap CIs, pinball in return units, reliability, trading metrics from daily equity,
  per model, ablation, per agent, progress towards n = 30), backtests of quant-only and baselines over 3 years of
  the watchlist with Sharpe CIs and paired Sharpe-difference CIs vs buy & hold, the random-strategy distribution
  with quant-only's percentile, stored LLM backtests with contamination labels, verdicts only where n allows,
  and a multiple-comparisons note. Markdown + JSON in `reports`.
- **Backtest warm-up** (P9, found in the Lab screenshot): the engine recorded equity during the 210-bar warm-up,
  so momentum / SMA / quant-only carried ~540 flat days that diluted Sharpe, vol and CAGR (n = 1,633 vs 1,095
  for buy & hold). Equity now starts at `start`; a test pins it to the benchmark's period. Momentum's demo Sharpe
  moved from 0.86 to 1.06.
- **Desk UI** (P9, `web/src/pro/`): its own layout under `/pro/*` (lazy-loaded, so the Arena bundle is unchanged),
  dark neutral tokens (`--color-desk-*`), one accent, green/red only for P&L and direction and always with ▲/▼ or
  text, JetBrains Mono tabular numbers, no confetti/sound/typewriter; live updates via `/api/pro/events`
  invalidate queries and briefly highlight the affected blotter row (off with reduced motion). Every metric is
  shown as value [lo, hi] n=… with an "insufficient data" badge below n = 30. Times are local (the time zone is
  shown in the top bar).
- **Demo dataset** (P9, `trading/demo.py`, `scripts/pro_demo.py`): 6 synthetic assets, 75 days of the real
  scheduler + Desk loop on a simulated clock with a rule-based `DemoRunner` (model id `demo:rule-based`, seeded
  Monte Carlo so predictions carry a fan), evaluation from the synthetic bars, one report and one baseline
  backtest. It ends Friday 23:50 UTC with two signals awaiting approval (topped up deterministically). The API
  runs in demo mode with `PRO_DEMO_DIR` (synthetic bars, frozen clock, no network, no LLM). Output in
  `data/pro_demo/` (git-ignored).
- **Line endings**: the repo mixes LF and CRLF files; edits keep each file's existing endings.
- **Arena / Pro separation** (merge prep, `core/worlds.py`): one table, two worlds by `source`. `ARENA_ONLY` =
  `COALESCE(source, '') != 'pro'` (NULL and legacy sources are Arena), `PRO_ONLY` = `source = 'pro'`. Every
  query on `predictions` carries one of them or a `# any-world: <reason>` marker (writers, by-id lookups behind
  an explicit check, the migration, seed id collisions); `api/tests/test_worlds.py` fails otherwise. Audit:

  | Reader | World | Changed? |
  |---|---|---|
  | `GET /api/scoreboard` (Hall of Fame: coin flip, band coverage, agent podium, model leaderboard, pending) — `scoreboard.metrics`, `model_leaderboard`, `pending` | Arena | yes |
  | `GET /api/predictions` (history list, filters) | Arena | yes |
  | `GET /api/predictions/{id}` and `POST …/replay` (Replay page `/p/:id`) | Arena; Pro ids → 404 | yes |
  | Mock shows (`api/mock_runner.stored_ids` / `pick` / `load`), used by mock runs and mock duels | Arena | yes |
  | `GET /api/duels/{id}` (stored duel fallback) | Arena | yes (Pro never has a `duel_id`) |
  | `GET /api/models` latency (`observed_latency`) and cost estimates (`BudgetGuard.estimate_usd`) | Arena by default; Pro passes `world="pro"` | yes |
  | `GET /api/runs` (active runs) | Arena; Pro scheduler runs still share the slots | yes |
  | `cost.spend_summary()["predictions"]` (Streamlit "spend" panel) | Arena | yes |
  | Streamlit `app.py` (recent, metrics, replay by id) and CLI `scripts/auto_scalp.py`, `scripts/ui_smoke.py` (via `scoreboard`) | Arena | yes (through `scoreboard`) |
  | `core/seeds.export` | Arena | yes |
  | `core/seeds.load` / `load_if_empty` (id collisions, "DB in use") | any | no (marked) |
  | `scoreboard.evaluate_due` (fills actual prices) | any; both worlds need outcomes | no (marked) |
  | `/api/budget`, `cost.total_spent_usd`, BudgetGuard cap (from `llm_usage`) | combined, by design | no |
  | MCP tools (`mcp_server/`) | — | no (never read `predictions`) |
  | Pro: signal detail, scheduler duration estimate and spend, report forward section, calibration buckets, demo evaluation | Pro | yes (`PRO_ONLY` / `load(id, "pro")`) |

  The Desk's "Arena replay →" link was removed: Pro predictions are not Arena shows. Money: one combined
  figure for the real budget cap (the Arena's coin meter and BudgetGuard). The Desk Overview shows Pro spend
  today, Pro total and the shared cap separately (`pro_spent_total_thb`, `combined_spent_thb`). Arena stats
  (average cost per model, estimates) never include Pro.
- **Arena JSON identical to `main`** (merge prep): the Pro fields added in P1 (`prompt_version`, `temperature`,
  `experiment_id`, `degraded`, `as_of`; verdict `thesis`/`invalidation`/`catalysts`/`no_trade_reason`; report
  `scenario_probs`/`key_levels`/`invalidation`) are left out of dumps while at their defaults, so Arena
  responses, stored `prediction_json` and run events are byte-identical to `main`, and loading restores the
  defaults. Verified by running `main` (a git worktree) and this branch on copies of the real DB: `main`
  without the Pro rows and this branch with them produce the same JSON (scoreboard, list, every detail,
  budget, Streamlit helpers; md5 equal).

## Pro known issues

- **No real fill yet**: the 6 real P10 predictions were all FLAT; trading behaviour is verified on simulations
  and the demo only.
- **Intrabar resolution** with 1m bars is implemented but not wired into the forward loop (ambiguous bars → stop).
- **Demo dataset build is slow** (~5 min for 75 simulated days) because every tick re-reads bars; fine for a
  one-off build.
- **Backtest equity is daily**; the forward equity curve is marked on every scheduler tick (resampled to daily in
  the Lab).

- **Anonymised backtests are partial**: `PointInTimeExecutor(anonymize=True)` hides the ticker and dates in tool
  payloads, but the prompts still name the ticker and the decision time, so it is not a full anonymised
  experiment yet.
- `MODEL_CUTOFFS` are unknown for every model, so LLM backtests can never produce a verdict until the cutoffs
  are filled in (by design — see Pro decisions).
