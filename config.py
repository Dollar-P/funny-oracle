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
AGENT_TIMEOUT_S = float(_env("AGENT_TIMEOUT_S", "60"))
MAX_AGENT_TURNS = 8


# --- Local LLM: OpenAI-compatible server such as LM Studio (LOCAL_LLM.md) --------
# The server address comes only from LOCAL_BASE_URL (env / .env) — never hard-code a host.
LOCAL_BASE_URL = _env("LOCAL_BASE_URL", "http://localhost:1234/v1").rstrip("/")
LOCAL_MODEL = _env("LOCAL_MODEL", "prism-ml/bonsai-27b")       # LM Studio model id (without prefix)
LOCAL_PREFIX = "local:"                                         # our model ids: local:<LM Studio id>
LOCAL_CONCURRENCY = int(_env("LOCAL_CONCURRENCY", "1"))         # lesson 7: ~1.3× speed-up at best
LOCAL_TIMEOUT_S = float(_env("LOCAL_TIMEOUT_S", "240"))         # lesson 8: per agent (and per request)
LOCAL_MAX_TURNS = int(_env("LOCAL_MAX_TURNS", "4"))             # analyst tool turns
LOCAL_THINK_PRESET = _env("LOCAL_THINK_PRESET", "quick").lower()  # quick = all off · deep = LOCAL_LLM.md policy
LOCAL_MAX_TOKENS_THINK = int(_env("LOCAL_MAX_TOKENS_THINK", "4000"))  # lesson 5
LOCAL_MAX_TOKENS_FAST = int(_env("LOCAL_MAX_TOKENS_FAST", "1000"))
LOCAL_VALIDATION_RETRIES = int(_env("LOCAL_VALIDATION_RETRIES", "2"))  # lesson 2: retry with feedback
THINK_PRESETS = ("quick", "deep")
SCALP_PROVIDERS = ("gemini", "claude")                          # local is too slow for a 15 min horizon


def local_model_id(model: str) -> str:
    """'local:prism-ml/bonsai-27b' → 'prism-ml/bonsai-27b' (the id LM Studio expects)."""
    return model[len(LOCAL_PREFIX):] if model.startswith(LOCAL_PREFIX) else model


def models_for(mode: str, provider: str | None = None) -> dict[str, str]:
    """Model per role for a mode. Roles: analyst, judge."""
    p = provider or LLM_PROVIDER
    if p == "local":
        m = LOCAL_PREFIX + LOCAL_MODEL
        return {"analyst": m, "judge": m}
    if p == "claude":
        if mode == "scalp":
            return {"analyst": CLAUDE_SCALP_MODEL, "judge": CLAUDE_SCALP_MODEL}
        return {"analyst": CLAUDE_ANALYST_MODEL, "judge": CLAUDE_JUDGE_MODEL}
    if mode == "scalp":
        return {"analyst": SCALP_MODEL, "judge": SCALP_MODEL}
    return {"analyst": ANALYST_MODEL, "judge": JUDGE_MODEL}


def provider_for(model: str | None, default: str | None = None) -> str:
    """Provider a model ID belongs to (claude-* → claude, else gemini)."""
    if not model:
        return default or LLM_PROVIDER
    if model.startswith(LOCAL_PREFIX):
        return "local"
    return "claude" if model.removeprefix("models/").startswith("claude") else "gemini"


# --- Pricing (USD per 1M tokens; search USD per request) — §4.10 ---------------
# Update when providers change prices. Thinking tokens are billed as output.
# Gemini: https://ai.google.dev/gemini-api/docs/pricing (checked 2026-11-19).
# Search grounding: 5,000 free req/month shared across Gemini 3+, then $14/1k.
# We always charge $0.014/search so the budget guard stays conservative.
GEMINI_SEARCH_USD = 14 / 1000
# Estimated-actual cost treats the first N Gemini search queries of each calendar month (UTC)
# as free. The budget guard uses the tracked (list-price) figure unless BUDGET_BASIS=actual.
GEMINI_SEARCH_FREE_PER_MONTH = int(_env("GEMINI_SEARCH_FREE_PER_MONTH", "5000"))
BUDGET_BASIS = _env("BUDGET_BASIS", "tracked").lower()   # tracked | actual
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
    # lesson 10: local models are free; usage is still logged from the server's `usage` block
    "local:": [{"until": None, "in": 0.0, "out": 0.0, "search": 0.0}],
}
# Unknown model → charge like this (deliberately pessimistic) and log a warning.
FALLBACK_PRICE = {"in": 4.00, "out": 20.00, "search": GEMINI_SEARCH_USD}

# --- Budget -------------------------------------------------------------------
BUDGET_THB = float(_env("BUDGET_THB", "100"))
USD_THB = float(_env("USD_THB", "33"))
# Pre-prediction estimate when there is no history yet (USD). Replaced by the
# observed average × 1.25 once real predictions exist.
DEFAULT_ESTIMATE_USD = {"swing": 0.20, "scalp": 0.05}
# Defaults above are sized for this model; pricier models scale the default up by token price.
REFERENCE_PRICE_MODEL = "gemini-3.8-flash"
AUTO_SCALP_MAX_RUNS = int(_env("AUTO_SCALP_MAX_RUNS", "40"))
AUTO_SCALP_INTERVAL_S = int(_env("AUTO_SCALP_INTERVAL_S", "900"))

# --- Web API (REDESIGN) ----------------------------------------------------------
MOCK_LLM = _bool("MOCK_LLM", False)          # replay stored predictions as fake live shows (฿0)
MOCK_SPEED = float(_env("MOCK_SPEED", "1"))  # delay multiplier for mock shows (0 = instant)
API_PORT = int(_env("API_PORT", "8000"))
MAX_CONCURRENT_RUNS = int(_env("MAX_CONCURRENT_RUNS", "2"))
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")

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
# Scalp σ: horizon-scale log returns (one return = the whole horizon) over a longer lookback,
# then σ_step = σ_horizon / √steps. Bar data must exist that far back in yfinance
# (15m ≤ 60 days, 1h ≤ 730 days). Falls back to per-bar σ of the scalp interval when too few
# horizon returns are available.
SCALP_SIGMA = {
    "15m": {"interval": "15m", "period": "60d"},
    "1h": {"interval": "1h", "period": "6mo"},
    "4h": {"interval": "1h", "period": "1y"},
}
MIN_HORIZON_RETURNS = 30

LAMBDA = 0.5          # |S|=1 shifts the median by 0.5σ over the horizon
MC_PATHS = 10_000
JUDGE_MAX_ADJ = 0.2

# --- Pro mode: "The Desk" (PRO.md) -------------------------------------------------
# Everything here is additive; the Arena never reads it.
import json as _json  # noqa: E402

PRO_PROMPT_VERSION = _env("PRO_PROMPT_VERSION", "v2")
PRO_TEMPERATURE = float(_env("PRO_TEMPERATURE", "0.2"))      # §3.1: ≤ 0.3


def _cutoffs() -> dict[str, date | None]:
    """Knowledge cutoff per model (§6.2). None = unknown → every historical date counts as contaminated.

    Fill these in from the model cards; override without editing code with
    MODEL_CUTOFFS='{"gemini-3.5-flash-lite": "2026-01-31"}' in .env. Local models use the cutoff of their
    *base* model (Bonsai-27B → its Qwen base)."""
    base: dict[str, date | None] = {
        "gemini-3.5-flash-lite": None,
        "gemini-3.6-flash": None,
        "gemini-3.8-flash": None,
        "local:prism-ml/bonsai-27b": None,
        "claude-haiku-5-5": None,
        "claude-sonnet-5-5": None,
        "claude-opus-5-5": None,
    }
    raw = _env("MODEL_CUTOFFS")
    if raw:
        for k, v in _json.loads(raw).items():
            base[k] = date.fromisoformat(v) if v else None
    return base


MODEL_CUTOFFS = _cutoffs()
