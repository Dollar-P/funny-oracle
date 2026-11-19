"""Central configuration. Values that change often (models, prices) come from .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --- Storage -----------------------------------------------------------------
DB_PATH = Path(os.getenv("ORACLE_DB", ROOT / "data" / "oracle.db"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

# --- API keys ----------------------------------------------------------------
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()

# --- Cache TTLs (seconds) — ARCHITECTURE 4.1 ---------------------------------
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
    "1wk": "5y",
    "1mo": "10y",
}

# Max bytes for a tool response sent to the LLM.
MAX_TOOL_BYTES = 2048
