"""MCP client for the market-data server (mcp 2.x `mcp.Client` API).

Usage:
    async with MarketDataClient() as mc:          # spawns mcp_server/server.py over stdio
        tools = await mc.list_tools()             # list[ToolSpec]
        result, call = await mc.call("get_indicators", {"ticker": "BTC-USD", "interval": "1h"})

`MarketDataClient(in_process=True)` connects to the MCPServer object directly (no subprocess),
which is handy for tests.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from core.schemas import ToolCall

ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = ROOT / "mcp_server" / "server.py"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


def _parse_result(res: Any) -> dict:
    """CallToolResult → dict. Prefers structured content, falls back to text JSON."""
    sc = getattr(res, "structured_content", None)
    if isinstance(sc, dict):
        # MCPServer wraps non-object return types as {"result": ...}
        if set(sc) == {"result"} and isinstance(sc["result"], dict):
            return sc["result"]
        return sc
    for block in getattr(res, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"error": text[:300], "data": None}
    if getattr(res, "is_error", False):
        return {"error": "tool call failed", "data": None}
    return {"error": "empty tool result", "data": None}


class MarketDataClient:
    def __init__(self, in_process: bool = False, read_timeout: float = 60.0):
        if in_process:
            from mcp_server.server import mcp as server_obj

            target: Any = server_obj
        else:
            target = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)], cwd=str(ROOT))
        self._client = Client(target, read_timeout_seconds=read_timeout)
        self._tools: list[ToolSpec] | None = None

    async def __aenter__(self) -> "MarketDataClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    async def list_tools(self) -> list[ToolSpec]:
        if self._tools is None:
            res = await self._client.list_tools()
            self._tools = [
                ToolSpec(t.name, t.description or "", dict(t.input_schema or {"type": "object", "properties": {}}))
                for t in res.tools
            ]
        return self._tools

    async def call(self, name: str, args: dict | None = None) -> tuple[dict, ToolCall]:
        """Call a tool; never raises. Returns (result_dict, ToolCall trace entry)."""
        args = args or {}
        t0 = time.perf_counter()
        try:
            res = await self._client.call_tool(name, args)
            data = _parse_result(res)
        except Exception as e:  # transport / protocol failure → error envelope
            data = {"error": f"{type(e).__name__}: {e}"[:300], "data": None}
        latency = (time.perf_counter() - t0) * 1000
        text = json.dumps(data, separators=(",", ":"), default=str)
        call = ToolCall(
            tool=name,
            args=args,
            latency_ms=round(latency, 1),
            result_bytes=len(text),
            error=data.get("error") if isinstance(data, dict) else None,
        )
        return data, call
