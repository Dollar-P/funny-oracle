"""Data quality checks before a Pro run (PRO.md §4): stale data, missing bars, zero volume → "degraded".

A degraded prediction is still stored (for audit) but the Signal Engine forces FLAT. The Arena never calls this.
Checks are deliberately simple and conservative; they look only at bars that existed at decision time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

_INTERVAL_MIN = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90, "1h": 60, "1d": 1440,
                 "1wk": 7 * 1440}

# Max age of the last bar (its open time) before the data counts as stale.
# Daily: one session — a stock/gold Friday bar is still current on Monday morning, plus one holiday.
_DAILY_MAX_AGE = {"stock": timedelta(days=4), "gold": timedelta(days=4), "crypto": timedelta(days=2)}


def _us_session_now(now: datetime) -> bool:
    """Rough NYSE regular-hours test (13:30–21:00 UTC covers both DST states, weekdays)."""
    return now.weekday() < 5 and (13, 30) <= (now.hour, now.minute) < (21, 0)


def check(df: pd.DataFrame | None, mode: str, interval: str, asset_type: str,
          now: datetime | None = None) -> list[str]:
    """Return the list of data-quality flags (empty = OK)."""
    if df is None or len(df) == 0:
        return ["no_data"]
    now = now or datetime.now(timezone.utc)
    flags: list[str] = []
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    last = idx[-1].to_pydatetime()
    step = timedelta(minutes=_INTERVAL_MIN.get(interval, 1440))
    age = now - last

    if interval in ("1d", "1wk"):
        if age > _DAILY_MAX_AGE.get(asset_type, timedelta(days=4)) + (step if interval == "1wk" else timedelta(0)):
            flags.append("stale")
    else:  # intraday: one bar of tolerance on top of the bar itself (open time + 2 intervals)
        market_open = asset_type == "crypto" or _us_session_now(now)
        if market_open and age > 3 * step:
            flags.append("stale")

    tail = idx[-31:]
    if len(tail) >= 2:
        gaps = pd.Series(tail[1:] - tail[:-1])
        if asset_type == "crypto":
            missing = int(sum(max(0, round(g / step) - 1) for g in gaps))
            expected = len(tail) - 1 + missing
            if expected and missing / expected > 0.10:
                flags.append("missing_bars")
        elif interval in ("1d", "1wk"):
            limit = timedelta(days=5) if interval == "1d" else timedelta(days=15)
            if (gaps > limit).any():
                flags.append("missing_bars")

    if "Volume" in df.columns and asset_type in ("stock", "crypto"):
        vol = df["Volume"].tail(3)
        if len(vol) and (vol.fillna(0) <= 0).all():
            flags.append("zero_volume")
    return flags
