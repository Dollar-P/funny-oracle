"""Central configuration. Values that change often (models, prices) come from .env / this table."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if not v else v.lower() in {"1", "true", "yes", "on"}


# --- Storage -----------------------------------------------------------------
DB_PATH = Path(_env("ORACLE_DB", str(ROOT / "data" / "oracle.db")))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

# --- API keys ----------------------------------------------------------------
FRED_API_KEY = _env("FRED_API_KEY")
GEMINI_API_KEY = _env("GEMINI_API_KEY")

# --- LLM (never hard-code model IDs: they come from .env) ---------------------
LLM_PROVIDER = _env("LLM_PROVIDER", "gemini").lower()
ANALYST_MODEL = _env("ANALYST_MODEL")
JUDGE_MODEL = _env("JUDGE_MODEL") or ANALYST_MODEL
SCALP_MODEL = _env("SCALP_MODEL") or ANALYST_MODEL
CLAUDE_ANALYST_MODEL = _env("CLAUDE_ANALYST_MODEL")
CLAUDE_JUDGE_MODEL = _env("CLAUDE_JUDGE_MODEL") or CLAUDE_ANALYST_MODEL
CLAUDE_SCALP_MODEL = _env("CLAUDE_SCALP_MODEL") or CLAUDE_ANALYST_MODEL
CLAUDE_WEB_SEARCH_TOOL = _env("CLAUDE_WEB_SEARCH_TOOL", "web_search_20260209")
ENABLE_SEARCH = _bool("ENABLE_SEARCH", True)

ANALYST_THINKING = _env("ANALYST_THINKING", "low")   # Gemini thinking_level / Claude effort
JUDGE_THINKING = _env("JUDGE_THINKING", "medium")
MAX_CONCURRENCY = int(_env("LLM_MAX_CONCURRENCY", "5"))
AGENT_TIMEOUT_S = float(_env("AGENT_TIMEOUT_S", "45"))
MAX_AGENT_TURNS = 8


def models_for(mode: str, provider: str | None = None) -> dict[str, str]:
    """Model per role for a mode. Roles: analyst, judge."""
    p = provider or LLM_PROVIDER
    if p == "claude":
        if mode == "scalp":
            return {"analyst": CLAUDE_SCALP_MODEL, "judge": CLAUDE_SCALP_MODEL}
        return {"analyst": CLAUDE_ANALYST_MODEL, "judge": CLAUDE_JUDGE_MODEL}
    if mode == "scalp":
        return {"analyst": SCALP_MODEL, "judge": SCALP_MODEL}
    return {"analyst": ANALYST_MODEL, "judge": JUDGE_MODEL}


# --- Pricing (USD per 1M tokens; search USD per request) — §4.10 ---------------
# Update when providers change prices. Thinking tokens are billed as output.
# Gemini: https://ai.google.dev/gemini-api/docs/pricing (checked 2026-11-19).
# Search grounding: 5,000 free req/month shared across Gemini 3+, then $14/1k.
# We always charge $0.014/search so the budget guard stays conservative.
GEMINI_SEARCH_USD = 14 / 1000
CLAUDE_SEARCH_USD = 10 / 1000

PRICES: dict[str, list[dict]] = {
    # each entry: {"until": date|None, "in": $, "out": $, "search": $}; first matching wins
    "gemini-3.5-flash-lite": [{"until": None, "in": 0.30, "out": 2.50, "search": GEMINI_SEARCH_USD}],
    "gemini-3.6-flash": [
        {"until": date(2026, 12, 31), "in": 0.75, "out": 3.75, "search": GEMINI_SEARCH_USD},
        {"until": None, "in": 1.50, "out": 7.50, "search": GEMINI_SEARCH_USD},
    ],
    "gemini-3.8-flash": [
        {"until": date(2026, 12, 31), "in": 0.75, "out": 3.75, "search": GEMINI_SEARCH_USD},
        {"until": None, "in": 1.50, "out": 7.50, "search": GEMINI_SEARCH_USD},
    ],
    "claude-haiku-5-5": [{"until": None, "in": 0.10, "out": 0.50, "search": CLAUDE_SEARCH_USD}],
    "claude-sonnet-5-5": [{"until": None, "in": 2.00, "out": 10.00, "search": CLAUDE_SEARCH_USD}],
    "claude-opus-5-5": [{"until": None, "in": 4.00, "out": 20.00, "search": CLAUDE_SEARCH_USD}],
}
# Unknown model → charge like this (deliberately pessimistic) and log a warning.
FALLBACK_PRICE = {"in": 4.00, "out": 20.00, "search": GEMINI_SEARCH_USD}

# --- Budget -------------------------------------------------------------------
BUDGET_THB = float(_env("BUDGET_THB", "100"))
USD_THB = float(_env("USD_THB", "33"))
# Pre-prediction estimate when there is no history yet (USD). Replaced by the
# observed average × 1.25 once real predictions exist.
DEFAULT_ESTIMATE_USD = {"swing": 0.20, "scalp": 0.05}
AUTO_SCALP_MAX_RUNS = int(_env("AUTO_SCALP_MAX_RUNS", "40"))
AUTO_SCALP_INTERVAL_S = int(_env("AUTO_SCALP_INTERVAL_S", "900"))

# --- Cache TTLs (seconds) — §4.1 ----------------------------------------------
TTL_INTRADAY = 60
TTL_DAILY = 15 * 60
TTL_SLOW = 6 * 60 * 60  # fundamentals / macro

INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}

# Default lookback period per interval (respects yfinance intraday limits).
DEFAULT_PERIOD = {
    "1m": "5d",
    "2m": "1mo",
    "5m": "1mo",
    "15m": "1mo",
    "30m": "1mo",
    "60m": "3mo",
    "90m": "1mo",
    "1h": "3mo",
    "1d": "2y",   # enough bars for SMA200
    "1wk": "5y",  # 260 bars → 200-week MA available
    "1mo": "10y",
}

# Max bytes for a tool response sent to the LLM.
MAX_TOOL_BYTES = 2048

# --- Agents & weights — §4.6 ----------------------------------------------------
AGENTS = ["business", "technical", "valuation", "macro", "psychology"]

WEIGHTS = {
    "swing": {
        "stock": {"business": 0.25, "technical": 0.15, "valuation": 0.25, "macro": 0.15, "psychology": 0.20},
        "crypto": {"business": 0.15, "technical": 0.25, "valuation": 0.10, "macro": 0.20, "psychology": 0.30},
        "gold": {"business": 0.10, "technical": 0.20, "valuation": 0.15, "macro": 0.40, "psychology": 0.15},
    },
    "scalp": {
        a: {"business": 0.0, "technical": 0.55, "valuation": 0.0, "macro": 0.10, "psychology": 0.35}
        for a in ("stock", "crypto", "gold")
    },
}

# --- Modes & horizons — §4.7 ----------------------------------------------------
# steps: number of bars in the horizon. Daily bars for stocks/gold only exist on
# trading days, crypto trades 7 days a week.
HORIZONS = {
    "swing": {
        "1W": {"interval": "1d", "minutes": 7 * 24 * 60, "steps": {"stock": 5, "gold": 5, "crypto": 7}},
        "1M": {"interval": "1d", "minutes": 30 * 24 * 60, "steps": {"stock": 21, "gold": 21, "crypto": 30}},
        "3M": {"interval": "1d", "minutes": 91 * 24 * 60, "steps": {"stock": 63, "gold": 63, "crypto": 91}},
    },
    "scalp": {
        "15m": {"interval": "1m", "minutes": 15, "steps": 15},
        "1h": {"interval": "5m", "minutes": 60, "steps": 12},
        "4h": {"interval": "15m", "minutes": 240, "steps": 16},
    },
}
SIGMA_LOOKBACK = {"swing": "1y", "scalp": "5d"}

LAMBDA = 0.5          # |S|=1 shifts the median by 0.5σ over the horizon
MC_PATHS = 10_000
JUDGE_MAX_ADJ = 0.2
