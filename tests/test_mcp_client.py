"""Checkpoint 3: the registered stdio MCP server answers get_indicators("BTC-USD","1h")
through the project's mcp 2.x client (same path the agents use)."""
from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mcp_client import MarketDataClient  # noqa: E402

EXPECTED = {"get_price_history", "get_indicators", "get_fundamentals", "get_macro",
            "get_news", "get_sentiment", "get_onchain_lite"}


def _run(in_process: bool):
    async def go():
        async with MarketDataClient(in_process=in_process) as mc:
            tools = await mc.list_tools()
            assert EXPECTED <= {t.name for t in tools}
            spec = next(t for t in tools if t.name == "get_indicators")
            assert "ticker" in spec.input_schema["properties"]
            data, call = await mc.call("get_indicators", {"ticker": "BTC-USD", "interval": "1h"})
            assert data.get("error") is None, data
            assert 0 <= data["data"]["rsi14"] <= 100
            assert call.tool == "get_indicators" and call.result_bytes > 0 and call.error is None
            bad, bad_call = await mc.call("get_indicators", {"ticker": "BTC-USD", "interval": "7m"})
            assert bad["data"] is None and bad_call.error

    anyio.run(go)


def test_stdio_get_indicators_btc_1h():
    _run(in_process=False)


def test_in_process_get_indicators_btc_1h():
    _run(in_process=True)
