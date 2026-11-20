"""Synthetic daily bars with realistic calendars (NYSE sessions, crypto 24/7, gold weekdays)."""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from trading import calendar as cal

NY = ZoneInfo("America/New_York")


def daily_index(asset_type: str, start: str, end: str) -> pd.DatetimeIndex:
    days = cal.sessions(asset_type, start, end)
    if asset_type == "crypto":
        return pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=timezone.utc) for d in days])
    return pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=NY).astimezone(timezone.utc) for d in days])


def synthetic_daily(asset_type: str = "stock", start: str = "2022-01-03", end: str = "2024-12-31",
                    price: float = 100.0, drift: float = 0.0, vol: float = 0.02, seed: int = 0,
                    closes: np.ndarray | None = None) -> pd.DataFrame:
    idx = daily_index(asset_type, start, end)
    rng = np.random.default_rng(seed)
    n = len(idx)
    if closes is None:
        closes = price * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    closes = np.asarray(closes, dtype=float)[:n]
    n = len(closes)
    idx = idx[:n]
    prev = np.concatenate([[closes[0]], closes[:-1]])
    opens = prev * np.exp(rng.normal(0, vol * 0.3, n))
    hi = np.maximum(opens, closes) * np.exp(np.abs(rng.normal(0, vol * 0.5, n)))
    lo = np.minimum(opens, closes) * np.exp(-np.abs(rng.normal(0, vol * 0.5, n)))
    return pd.DataFrame({"Open": opens, "High": hi, "Low": lo, "Close": closes, "Volume": 1e6}, index=idx)
