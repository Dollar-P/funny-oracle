"""Checkpoint 2: MCP tools fetch real data for NVDA / BTC-USD / GC=F.

These tests hit the network (yfinance, FRED, alternative.me, CoinGecko).
Run: pytest tests/test_mcp_tools.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TICKERS = ["NVDA", "BTC-USD", "GC=F"]


@pytest.fixture(scope="session", autouse=True)
def _isolated_db(tmp_path_factory):
    """Use a throwaway cache DB so tests don't touch data/oracle.db."""
    import config

    db = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["ORACLE_DB"] = str(db)
    config.DB_PATH = db
    yield


@pytest.fixture(scope="session")
def T():
    from mcp_server.server import TOOLS

    return TOOLS


def _ok(res: dict) -> dict:
    assert "as_of" in res
    assert res.get("error") is None, res.get("error")
    assert res["data"] is not None
    size = len(json.dumps(res, separators=(",", ":")))
    assert size <= 2048 + 256, f"response too large: {size} bytes"
    return res["data"]


# --- Live tool tests ---------------------------------------------------------

@pytest.mark.parametrize("ticker", TICKERS)
def test_price_history_daily(T, ticker):
    d = _ok(T["get_price_history"](ticker, "1d"))
    s = d["summary"]
    assert s["last"] > 0 and s["n_bars"] > 200
    assert 5 <= len(d["bars"]) <= 20
    assert len(d["bars"][0]) == len(d["bar_columns"])


@pytest.mark.parametrize("ticker", TICKERS)
def test_indicators_daily(T, ticker):
    d = _ok(T["get_indicators"](ticker, "1d"))
    assert 0 <= d["rsi14"] <= 100
    assert d["sma"]["sma200"] is not None
    assert d["atr14"] > 0
    assert d["levels"]["support"] and d["levels"]["resistance"]


def test_indicators_intraday_btc(T):
    d = _ok(T["get_indicators"]("BTC-USD", "1h"))
    assert 0 <= d["rsi14"] <= 100


def test_price_history_1m_btc(T):
    d = _ok(T["get_price_history"]("BTC-USD", "1m"))
    assert d["summary"]["n_bars"] > 100


def test_fundamentals_nvda(T):
    d = _ok(T["get_fundamentals"]("NVDA"))
    assert d["market_cap"] > 1e11
    assert "pe_trailing" in d or "pe_forward" in d


def test_macro(T):
    d = _ok(T["get_macro"]())
    for k in ("DGS10", "DFII10", "VIX", "DXY"):
        assert k in d and d[k]["value"] is not None, k
    assert d["CPI_YOY"]["value"] is not None


@pytest.mark.parametrize("ticker", TICKERS)
def test_news(T, ticker):
    d = _ok(T["get_news"](ticker, 5))
    assert d["count"] == len(d["items"])
    for it in d["items"]:
        assert it["headline"]


@pytest.mark.parametrize("asset_type,ticker", [("stock", "NVDA"), ("crypto", "BTC-USD"), ("gold", "GC=F")])
def test_sentiment(T, asset_type, ticker):
    d = _ok(T["get_sentiment"](asset_type, ticker))
    assert d["dist_from_52w_high_pct"] <= 0
    assert "vix" in d
    if asset_type == "crypto":
        assert 0 <= d["crypto_fear_greed"]["value"] <= 100


def test_onchain_lite_btc(T):
    d = _ok(T["get_onchain_lite"]("BTC-USD"))
    assert d["coin"] == "bitcoin"
    assert d["market_cap_usd"] > 1e11
    assert d["ath_distance_pct"] <= 0


# --- Error contract ----------------------------------------------------------

def test_bad_ticker_returns_error_not_raise(T):
    res = T["get_price_history"]("ZZZZNOTREAL123")
    assert res["data"] is None and res["error"] and res["as_of"]


def test_bad_interval_returns_error(T):
    res = T["get_indicators"]("NVDA", "7m")
    assert res["data"] is None and "interval" in res["error"]


def test_bad_asset_type_returns_error(T):
    res = T["get_sentiment"]("bonds")
    assert res["data"] is None and res["error"]


# --- Cache -------------------------------------------------------------------

def test_cache_roundtrip():
    from core import cache

    calls = []
    fn = lambda: calls.append(1) or {"x": 1}  # noqa: E731
    assert cache.cached("t:key", 60, fn) == {"x": 1}
    assert cache.cached("t:key", 60, fn) == {"x": 1}
    assert len(calls) == 1
    cache.cached("t:err", 60, lambda: {"error": "boom"})
    assert cache.get("t:err") is None  # errors are not cached


# --- Offline indicator sanity -----------------------------------------------

def _synthetic(n=300, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": rng.integers(1e5, 1e6, n)},
        index=idx,
    )


def test_rsi_extremes():
    from mcp_server import indicators as ind

    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100)
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0)


def test_compute_all_synthetic():
    from mcp_server import indicators as ind

    d = ind.compute_all(_synthetic())
    assert 0 <= d["rsi14"] <= 100
    assert d["bollinger"]["lower"] < d["bollinger"]["mid"] < d["bollinger"]["upper"]
    assert all(s < d["last"] for s in d["levels"]["support"])
    assert all(r > d["last"] for r in d["levels"]["resistance"])


def test_compute_all_too_short():
    from mcp_server import indicators as ind

    with pytest.raises(ValueError):
        ind.compute_all(_synthetic(10))


# --- End-to-end over MCP stdio ----------------------------------------------

def test_stdio_server_lists_and_calls_tools():
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp_server" / "server.py")],
            env={**os.environ},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"get_price_history", "get_indicators", "get_fundamentals", "get_macro",
                        "get_news", "get_sentiment", "get_onchain_lite"} <= tools
                res = await session.call_tool("get_indicators", {"ticker": "BTC-USD", "interval": "1h"})
                payload = res.structured_content or json.loads(res.content[0].text)
                payload = payload.get("result", payload)
                assert payload["data"]["rsi14"] is not None

    anyio.run(run)
