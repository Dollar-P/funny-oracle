"""market-data-mcp — MCP server exposing market data tools over stdio.

Run:      python mcp_server/server.py
Register: claude mcp add market-data -- <venv python> mcp_server/server.py

Every tool returns compact JSON (<= ~2 KB) of the form
    {"as_of": <utc iso>, "data": {...}}            on success
    {"as_of": <utc iso>, "error": "...", "data": null}  on failure (never raises)
"""
from __future__ import annotations

import functools
import json
import logging
import sys
from pathlib import Path

# stdio transport: stdout carries JSON-RPC only. All logging goes to stderr.
# (mcp 2.x additionally diverts fd 1 to stderr while serving, so stray print()s
#  from libraries like yfinance cannot corrupt the protocol stream.)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402  (mcp 2.x; formerly FastMCP)

import config  # noqa: E402
from core.asset import classify, normalize  # noqa: E402
from mcp_server import data_sources as ds  # noqa: E402
from mcp_server import indicators as ind  # noqa: E402
from mcp_server import web_tools as web  # noqa: E402

mcp = MCPServer(
    "market-data-mcp",
    instructions="Market data for stocks, crypto and gold. All responses are compact JSON with an as_of timestamp.",
)

VALID_INTERVALS = set(config.DEFAULT_PERIOD)


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str))


def safe_tool(fn):
    """Wrap a tool body: add as_of, convert exceptions into an error envelope."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        as_of = ds.now_iso()
        try:
            return {"as_of": as_of, "data": fn(*args, **kwargs)}
        except Exception as e:  # never kill the agent
            return {"as_of": as_of, "error": f"{type(e).__name__}: {e}"[:300], "data": None}

    return wrapper


def _check_interval(interval: str) -> None:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(VALID_INTERVALS)}")


# --- Tools -------------------------------------------------------------------

@mcp.tool()
@safe_tool
def get_price_history(ticker: str, interval: str = "1d", period: str | None = None) -> dict:
    """Recent OHLCV bars + summary for a ticker.

    ticker: yfinance symbol, e.g. NVDA, BTC-USD, GC=F
    interval: 1m 5m 15m 30m 1h 1d 1wk ... (1m only covers ~7 days)
    period: yfinance period like 5d, 1mo, 1y (default depends on interval)
    Returns summary (last, % change over period, high/low, n_bars) and the last <=20 bars.
    """
    _check_interval(interval)
    df = ds.fetch_ohlcv(ticker, interval, period)
    return price_history_payload(df, ticker, interval, period)


def price_history_payload(df, ticker: str, interval: str, period: str | None) -> dict:
    """get_price_history's response for a given DataFrame (shared with the point-in-time backtest executor)."""
    c = df["Close"]
    logret = np.log(c).diff().dropna()
    summary = {
        "ticker": normalize(ticker),
        "asset_type": classify(ticker),
        "interval": interval,
        "period": period or config.DEFAULT_PERIOD.get(interval),
        "first_bar": df.index[0].isoformat(),
        "last_bar": df.index[-1].isoformat(),
        "n_bars": len(df),
        "last": ds._num(c.iloc[-1]),
        "chg_pct_period": ds._num((c.iloc[-1] / c.iloc[0] - 1) * 100, 2),
        "chg_pct_last_bar": ds._num((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2) if len(c) > 1 else None,
        "high": ds._num(df["High"].max()),
        "low": ds._num(df["Low"].min()),
        "stdev_logret_per_bar": ds._num(logret.std(), 5),
    }
    intraday = interval in config.INTRADAY_INTERVALS
    fmt = "%m-%d %H:%M" if intraday else "%Y-%m-%d"

    def bars(n):
        tail = df.tail(n)
        return [
            [ts.strftime(fmt), ds._num(r.Open), ds._num(r.High), ds._num(r.Low), ds._num(r.Close), int(r.Volume)]
            for ts, r in zip(tail.index, tail.itertuples())
        ]

    out = {"summary": summary, "bar_columns": ["t", "o", "h", "l", "c", "v"], "bars": bars(20)}
    n = 20
    while _size(out) > config.MAX_TOOL_BYTES - 60 and n > 5:  # leave room for envelope
        n -= 2
        out["bars"] = bars(n)
    return out


@mcp.tool()
@safe_tool
def get_indicators(ticker: str, interval: str = "1d") -> dict:
    """Technical indicators at the latest bar: RSI14, MACD, SMA20/50/200, EMA12/26,
    Bollinger(20,2), ATR14, volume z-score, nearest support/resistance levels."""
    _check_interval(interval)
    df = ds.fetch_ohlcv(ticker, interval)
    out = ind.compute_all(df)
    out = {"ticker": normalize(ticker), "interval": interval, "last_bar": df.index[-1].isoformat(), **out}
    return out


@mcp.tool()
@safe_tool
def get_fundamentals(ticker: str) -> dict:
    """Valuation & quality metrics: P/E, forward P/E, PEG, EV/EBITDA, margins, revenue growth,
    FCF, debt/equity, analyst targets. Mostly meaningful for stocks/ETFs."""
    return ds.fetch_fundamentals(ticker)


@mcp.tool()
@safe_tool
def get_macro() -> dict:
    """US macro snapshot: FEDFUNDS, CPI YoY, DGS10, DFII10 (10Y real yield), T10Y2Y,
    DXY dollar index, VIX — each with date and ~1-month change."""
    return ds.fetch_macro()


@mcp.tool()
@safe_tool
def get_news(ticker: str, limit: int = 6) -> dict:
    """Latest news headlines for a ticker: headline, publisher, date, link."""
    limit = max(1, min(int(limit), 10))
    items = ds.fetch_news(ticker, limit)
    out = {"ticker": normalize(ticker), "items": items}
    while _size(out) > config.MAX_TOOL_BYTES - 60 and len(out["items"]) > 1:
        out["items"] = out["items"][:-1]
    out["count"] = len(out["items"])
    return out


SENTIMENT_PROXY = {"stock": "SPY", "crypto": "BTC-USD", "gold": "GC=F"}


@mcp.tool()
@safe_tool
def get_sentiment(asset_type: str, ticker: str | None = None) -> dict:
    """Market sentiment: VIX, Crypto Fear & Greed (crypto), distance from 52-week high,
    and momentum proxies (1M / 3M returns, vs SMA50).

    asset_type: stock | crypto | gold
    ticker: optional; defaults to a proxy (SPY / BTC-USD / GC=F)
    """
    asset_type = asset_type.lower().strip()
    if asset_type not in SENTIMENT_PROXY:
        raise ValueError("asset_type must be stock, crypto, or gold")
    sym = normalize(ticker) if ticker else SENTIMENT_PROXY[asset_type]
    out: dict = {"asset_type": asset_type, "ticker": sym}

    df = ds.fetch_ohlcv(sym, "1d", "1y")
    c = df["Close"]
    hi52 = df["High"].max()
    out["dist_from_52w_high_pct"] = ds._num((c.iloc[-1] / hi52 - 1) * 100, 2)
    out["dist_from_52w_low_pct"] = ds._num((c.iloc[-1] / df["Low"].min() - 1) * 100, 2)
    out["ret_1m_pct"] = ds._num((c.iloc[-1] / c.iloc[max(0, len(c) - 22)] - 1) * 100, 2)
    out["ret_3m_pct"] = ds._num((c.iloc[-1] / c.iloc[max(0, len(c) - 64)] - 1) * 100, 2)
    sma50 = c.rolling(50).mean().iloc[-1]
    out["pct_vs_sma50"] = ds._num((c.iloc[-1] / sma50 - 1) * 100, 2)
    out["rsi14"] = ds._num(ind.rsi(c).iloc[-1], 1)

    try:
        v = ds.fetch_ohlcv("^VIX", "1d", "3mo")["Close"]
        out["vix"] = ds._num(v.iloc[-1], 2)
        out["vix_3m_avg"] = ds._num(v.mean(), 2)
    except Exception as e:
        out["vix_error"] = str(e)[:100]

    if asset_type == "crypto":
        try:
            out["crypto_fear_greed"] = ds.fetch_fear_greed()
        except Exception as e:
            out["fear_greed_error"] = str(e)[:100]
    return out


@mcp.tool()
@safe_tool
def get_onchain_lite(coin: str) -> dict:
    """Crypto market structure from CoinGecko: market cap, rank, volume/mcap, dominance,
    distance from ATH, 7d/30d change, supply. coin: BTC-USD, BTC, or a CoinGecko id."""
    return ds.fetch_onchain_lite(coin)


@mcp.tool()
@safe_tool
def web_search(query: str, limit: int = 6) -> dict:
    """Search the web/news (for models without built-in search). Returns title, url, source, date, snippet.
    Prefer specific queries, e.g. "NVIDIA earnings guidance November 2026". Follow up with fetch_url on the
    1-2 most relevant urls."""
    return web.web_search(query, limit)


@mcp.tool()
@safe_tool
def fetch_url(url: str, max_chars: int = 1500) -> dict:
    """Read one public web page (article text, max 1,500 chars) from a url returned by web_search."""
    return web.fetch_url(url, max_chars)


TOOLS = {
    f.__name__: f
    for f in (get_price_history, get_indicators, get_fundamentals, get_macro, get_news, get_sentiment, get_onchain_lite,
              web_search, fetch_url)
}


if __name__ == "__main__":
    mcp.run("stdio")
