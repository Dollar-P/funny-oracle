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
| P4 | Paper Broker + Ledger + trading calendar | | |
| P5 | Baselines + quant backtester + metrics + stats | | |
| P6 | Scheduler + forward paper loop + model routing and budgets | | |
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
- **Line endings**: the repo mixes LF and CRLF files; edits keep each file's existing endings.

## Pro known issues

- `MODEL_CUTOFFS` are unknown for every model, so LLM backtests can never produce a verdict until the cutoffs
  are filled in (by design — see Pro decisions).
