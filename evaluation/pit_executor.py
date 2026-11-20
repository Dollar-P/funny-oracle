"""Point-in-time tool executor for LLM backtests (PRO.md §6.2 rule 2).

Agents see only price-derived data that existed at `as_of`:
- get_price_history / get_indicators are computed from daily bars whose close was final at `as_of`
  (or served from `tool_snapshots` of the original run when replaying one);
- everything that is "as of now" — fundamentals, news, sentiment, macro, on-chain, web search, fetch_url — is not
  even listed, and calling it anyway returns an error.
Optional anonymisation (§6.2 rule 3): the ticker becomes "Asset A" and dates become relative day numbers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from core.mcp_client import ToolSpec
from core.schemas import ToolCall
from mcp_server import indicators as ind
from mcp_server.server import price_history_payload
from trading import calendar as cal

ALLOWED = ("get_price_history", "get_indicators")
REFUSED = ("get_fundamentals", "get_news", "get_sentiment", "get_macro", "get_onchain_lite", "web_search", "fetch_url")
REFUSAL = "not available in backtest mode (point-in-time data only: price history and indicators)"

SPECS = [
    ToolSpec("get_price_history", "Daily OHLCV bars + summary up to the decision date (point in time).",
             {"type": "object", "properties": {"ticker": {"type": "string"}, "interval": {"type": "string"},
                                               "period": {"type": "string"}}, "required": ["ticker"]}),
    ToolSpec("get_indicators", "Technical indicators at the decision date: RSI14, MACD, SMA20/50/200, Bollinger, ATR,"
                               " volume z-score, support/resistance and deterministic signal labels.",
             {"type": "object", "properties": {"ticker": {"type": "string"}, "interval": {"type": "string"}},
              "required": ["ticker"]}),
]

PERIOD_BARS = {"5d": 5, "1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}


class PointInTimeExecutor:
    def __init__(self, ticker: str, asset_type: str, as_of: datetime, history: pd.DataFrame | Callable[[], pd.DataFrame],
                 anonymize: bool = False):
        self.ticker, self.asset_type = ticker, asset_type
        self.as_of = as_of.astimezone(timezone.utc)
        self._history = history
        self._df: pd.DataFrame | None = None
        self.anonymize = anonymize
        self.calls: list[tuple[str, dict]] = []

    def bars(self) -> pd.DataFrame:
        """Daily bars whose close was final at as_of. The only data this executor ever reads."""
        if self._df is None:
            h = self._history() if callable(self._history) else self._history
            done = [cal.bar_close_time(self.asset_type, "1d", ts.to_pydatetime()) <= self.as_of for ts in h.index]
            self._df = h[done]
        return self._df

    async def list_tools(self):
        return list(SPECS)

    async def call(self, name: str, args: dict | None = None) -> tuple[dict, ToolCall]:
        args = dict(args or {})
        self.calls.append((name, args))
        as_of = self.as_of.isoformat(timespec="seconds")
        try:
            if name not in ALLOWED:
                raise PermissionError(f"{name} is {REFUSAL}")
            df = self.bars()
            if len(df) < 30:
                raise ValueError(f"only {len(df)} bars before {self.as_of.date()}")
            if name == "get_price_history":
                n = PERIOD_BARS.get(args.get("period") or "3mo", 63)
                data = price_history_payload(df.tail(n), self.ticker, "1d", args.get("period") or "3mo")
            else:
                data = {"ticker": self.ticker, "interval": "1d", "last_bar": df.index[-1].isoformat(),
                        **ind.compute_all(df.tail(400))}
            if self.anonymize:
                data = self._anon(data, df)
            out = {"as_of": as_of, "data": data}
        except Exception as e:
            out = {"as_of": as_of, "error": f"{type(e).__name__}: {e}"[:300], "data": None}
        text = json.dumps(out, default=str, separators=(",", ":"))
        return out, ToolCall(tool=name, args=args, latency_ms=0.0, result_bytes=len(text), error=out.get("error"))

    def _anon(self, data: dict, df: pd.DataFrame) -> dict:
        """Replace the ticker and absolute dates (day 0 = the decision bar)."""
        last = df.index[-1]
        text = json.dumps(data, default=str).replace(self.ticker, "Asset A")
        for ts in df.index[-400:]:
            day = (ts - last).days
            for fmt in ("%Y-%m-%d",):
                text = text.replace(ts.strftime(fmt), f"day {day}")
        return json.loads(text)
