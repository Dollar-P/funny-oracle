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


def test_compute_all_has_signal_labels():
    from mcp_server import indicators as ind

    d = ind.compute_all(_synthetic())
    sig = d["signals"]
    for k in ("price_vs_bollinger", "rsi_zone", "macd_cross", "macd_momentum", "trend_vs_sma50",
              "trend_vs_sma200", "sma_regime", "volume_state", "volatility_state"):
        assert k in sig, k
    # labels must agree with the raw numbers they summarise
    assert sig["trend_vs_sma50"] == ("above" if d["last"] > d["sma"]["sma50"] else "below")
    assert sig["rsi_zone"] == ind.label_rsi(d["rsi14"])
    assert sig["price_vs_bollinger"] == ind.label_bollinger(d["bollinger"]["pct_b"])


@pytest.mark.parametrize("pct_b,label", [(1.2, "above_upper_band"), (0.9, "near_upper_band"), (0.5, "mid_band"),
                                         (0.1, "near_lower_band"), (-0.3, "below_lower_band"), (None, "n/a")])
def test_label_bollinger(pct_b, label):
    from mcp_server.indicators import label_bollinger

    assert label_bollinger(pct_b) == label


@pytest.mark.parametrize("v,label", [(80, "overbought"), (70, "overbought"), (60, "bullish"), (50, "neutral"),
                                     (40, "bearish"), (30, "oversold"), (12, "oversold")])
def test_label_rsi(v, label):
    from mcp_server.indicators import label_rsi

    assert label_rsi(v) == label


def test_label_macd_cross_and_momentum():
    from mcp_server.indicators import label_macd

    assert label_macd(pd.Series([-2, -1, 0.5, 1.0])) == {
        "macd_cross": "bullish_cross", "macd_cross_bars_ago": 1, "macd_momentum": "bullish_strengthening"}
    assert label_macd(pd.Series([2, 1, -0.5]))["macd_cross"] == "bearish_cross"
    old = label_macd(pd.Series([-1, 1, 2, 3, 2.5]))  # cross 3 bars ago is outside the default lookback of 3
    assert old["macd_cross"] == "none" and old["macd_momentum"] == "bullish_fading"
    assert label_macd(pd.Series([-1.0, -2.0]))["macd_momentum"] == "bearish_strengthening"


def test_label_volume_vs_volatility():
    from mcp_server.indicators import label_volatility, label_volume, label_vs

    assert [label_volume(z) for z in (2.5, 1.2, 0, -1.5, None)] == ["very_high", "high", "normal", "low", "n/a"]
    assert label_vs(10, 9) == "above" and label_vs(8, 9) == "below" and label_vs(8, None) == "n/a"
    flat = pd.Series([2.0] * 50)
    assert label_volatility(flat) == "normal"
    assert label_volatility(pd.concat([flat, pd.Series([4.0])], ignore_index=True)) == "elevated"
    assert label_volatility(pd.concat([flat, pd.Series([1.0])], ignore_index=True)) == "compressed"
