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
| P2 | Signal Engine + cost model | | |
| P3 | Risk Engine | | |
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
- **Line endings**: the repo mixes LF and CRLF files; edits keep each file's existing endings.

## Pro known issues

- `MODEL_CUTOFFS` are unknown for every model, so LLM backtests can never produce a verdict until the cutoffs
  are filled in (by design — see Pro decisions).
