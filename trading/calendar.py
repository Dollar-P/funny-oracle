"""Trading calendar (PRO.md §3.4): when each asset class trades, and when a bar really opens.

- stock:  NYSE sessions (pandas_market_calendars "XNYS": holidays, early closes, DST).
- crypto: 24/7.
- gold:   GC=F on CME Globex: Sunday 18:00 → Friday 17:00 New York time with a daily 17:00–18:00 break
          (exchange holidays are not modelled; yfinance bars decide which bars exist).

`bar_open_time` maps a bar timestamp to the moment its first trade happened. yfinance stamps daily bars at
midnight New York (stocks, gold) or midnight UTC (crypto); the real open is the NYSE open for stocks and
18:00 New York the evening before for gold futures. The broker only fills an order on a bar whose open time is
at or after the order's submit time — so a decision made after the close can never be filled at a price that
was already in the past.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DAILY = {"1d", "1wk", "1mo"}


@lru_cache(maxsize=1)
def _nyse():
    import pandas_market_calendars as mcal

    return mcal.get_calendar("XNYS")


@lru_cache(maxsize=64)
def _schedule(year: int) -> pd.DataFrame:
    return _nyse().schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")


def _utc(ts) -> datetime:
    t = pd.Timestamp(ts)
    t = t.tz_localize(UTC) if t.tz is None else t.tz_convert(UTC)
    return t.to_pydatetime()


def nyse_session(day) -> tuple[datetime, datetime] | None:
    """(open, close) in UTC for a NYSE trading day, or None if the exchange is closed that day."""
    d = pd.Timestamp(day).date() if not isinstance(day, datetime) else day.date()
    s = _schedule(d.year)
    key = pd.Timestamp(d)
    if key not in s.index:
        return None
    row = s.loc[key]
    return _utc(row["market_open"]), _utc(row["market_close"])


def is_trading_day(asset_type: str, day) -> bool:
    d = pd.Timestamp(day).date()
    if asset_type == "stock":
        return nyse_session(d) is not None
    if asset_type == "gold":
        return d.weekday() < 5
    return True


def is_open(asset_type: str, ts) -> bool:
    t = _utc(ts)
    if asset_type == "crypto":
        return True
    if asset_type == "stock":
        s = nyse_session(t.astimezone(NY))
        return s is not None and s[0] <= t < s[1]
    ny = t.astimezone(NY)  # gold: Sun 18:00 → Fri 17:00 New York, daily break 17:00–18:00
    wd, hm = ny.weekday(), (ny.hour, ny.minute)
    if wd == 5:
        return False
    if wd == 6:
        return hm >= (18, 0)
    if wd == 4:
        return hm < (17, 0)
    return not ((17, 0) <= hm < (18, 0))


def next_open(asset_type: str, ts) -> datetime:
    """First moment ≥ ts at which the market is open."""
    t = _utc(ts)
    if asset_type == "crypto" or is_open(asset_type, t):
        return t
    if asset_type == "stock":
        d = t.astimezone(NY).date()
        for i in range(0, 15):
            s = nyse_session(d + timedelta(days=i))
            if s and s[0] >= t:
                return s[0]
            if s and s[0] < t < s[1]:
                return t
        raise RuntimeError("no NYSE session within 15 days")
    ny = t.astimezone(NY)  # gold: daily break (Mon–Thu 17:00–18:00) or the weekend (Fri 17:00 → Sun 18:00)
    if ny.weekday() < 4 or (ny.weekday() == 6 and ny.hour >= 17):
        return datetime.combine(ny.date(), time(18, 0), tzinfo=NY).astimezone(UTC)
    days_to_sun = (6 - ny.weekday()) % 7
    sunday = datetime.combine(ny.date() + timedelta(days=days_to_sun), time(18, 0), tzinfo=NY)
    return sunday.astimezone(UTC)


def bar_open_time(asset_type: str, interval: str, ts) -> datetime:
    t = _utc(ts)
    if interval not in DAILY or asset_type == "crypto":
        return t
    ny_day = t.astimezone(NY).date()
    if asset_type == "stock":
        s = nyse_session(ny_day)
        return s[0] if s else t
    # gold futures: the session for trade date D opens at 18:00 New York the previous evening (Monday → Sunday)
    prev = datetime.combine(ny_day - timedelta(days=1), time(18, 0), tzinfo=NY)
    return prev.astimezone(UTC)


def bar_close_time(asset_type: str, interval: str, ts, next_ts=None) -> datetime:
    """When a bar's close is final (a decision on that bar can be made from this moment)."""
    t = _utc(ts)
    if asset_type == "stock" and interval in DAILY:
        s = nyse_session(t.astimezone(NY).date())
        if s:
            return s[1]
    if asset_type == "gold" and interval in DAILY:
        ny_day = t.astimezone(NY).date()
        return datetime.combine(ny_day, time(17, 0), tzinfo=NY).astimezone(UTC)
    if next_ts is not None:
        return _utc(next_ts)
    step = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "60m": 60, "1d": 1440, "1wk": 7 * 1440}.get(interval, 1440)
    return t + timedelta(minutes=step)


def sessions(asset_type: str, start, end) -> list:
    """Trading days (dates) between start and end, inclusive."""
    days = pd.date_range(pd.Timestamp(start).date(), pd.Timestamp(end).date(), freq="D")
    return [d.date() for d in days if is_trading_day(asset_type, d)]
