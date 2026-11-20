# Progress

## Current state (2026-11-20)

- **Arena** (REDESIGN.md, R1–R8) and the **original build** (ARCHITECTURE.md, CP1–CP9) are done and on `main`: FastAPI + React web app, Streamlit UI, CLI, Auto-Scalp, Hall of Fame, Duel, Replay, share card.
- **Local LLM** (LOCAL_LLM.md) is merged into `main`: OpenAI-compatible provider for LM Studio (`local:<id>`, host only from `LOCAL_BASE_URL`), `web_search` / `fetch_url` MCP tools, local models in the Arena/Duel pickers (Quick/Deep, no Scalp).
- **Default model:** `gemini-3.5-flash-lite` for every role; `gemini-3.8-flash` and `local:prism-ml/bonsai-27b` selectable per run. Claude provider implemented, mocks only.
- **Tests:** `pytest -q` → **214 passed** (`tests/` + `api/tests`, network tests included); browser checks in `web/e2e/`.
- **Spend so far:** ฿22.17 tracked / ฿12.00 estimated actual of the ฿100 budget (local runs are free).
- `main` is pushed to https://github.com/Dollar-P/funny-oracle. The local branch `backup/pre-rewrite` must never be pushed.
- **Next:** Funny Oracle Pro, "The Desk" (PRO.md), on branch `pro`. Paper trading only.

## Docs

- [docs/progress-arena.md](docs/progress-arena.md): full history of the original build, the Arena redesign and Local LLM (checkpoints, real runs, spend log, decisions, known issues)
- [ARCHITECTURE.md](ARCHITECTURE.md): original architecture
- [REDESIGN.md](REDESIGN.md): the Oracle Arena web UI
- [LOCAL_LLM.md](LOCAL_LLM.md): local LLM provider (LM Studio)
- [PRO.md](PRO.md): Pro spec, research and paper trading

## Pro checkpoints (PRO.md §12)

| # | Checkpoint | Status | Verified by |
|---|---|---|---|
| P1 | Branch, DB migration, experiments, richer agent fields, prompts v1/v2, `MODEL_CUTOFFS` | | |
| P2 | Signal Engine + cost model | | |
| P3 | Risk Engine | | |
| P4 | Paper Broker + Ledger + trading calendar | | |
| P5 | Baselines + quant backtester + metrics + stats | | |
| P6 | Scheduler + forward paper loop + model routing and budgets | | |
| P7 | LLM backtest mode with leakage rules + calibration | | |
| P8 | Pro API + SSE events | | |
| P9 | Desk UI | | |
| P10 | Real verification + docs | | |
