"""Test doubles: fake MCP executor and a scripted fake Gemini client (real genai types)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from google.genai import types

from core.mcp_client import ToolSpec
from core.schemas import ToolCall

TOOL_NAMES = ["get_price_history", "get_indicators", "get_fundamentals", "get_macro", "get_news",
              "get_sentiment", "get_onchain_lite"]


class FakeExecutor:
    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return [ToolSpec(n, f"{n} tool", {"type": "object", "properties": {"ticker": {"type": "string"}}})
                for n in TOOL_NAMES]

    async def call(self, name, args=None):
        self.calls.append((name, args or {}))
        data = self.responses.get(name, {"as_of": "2026-11-19T00:00:00+00:00", "data": {"rsi14": 55.0}})
        return data, ToolCall(tool=name, args=args or {}, latency_ms=1.0, result_bytes=len(json.dumps(data)))


def fn_call_response(*calls: tuple[str, dict], prompt=100, out=20, thoughts=10):
    parts = [types.Part(function_call=types.FunctionCall(id=f"c{i}", name=n, args=a)) for i, (n, a) in enumerate(calls)]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts),
    )


def text_response(text: str, prompt=200, out=80, thoughts=30, grounding: list[str] | None = None):
    gm = None
    if grounding:
        gm = types.GroundingMetadata(
            web_search_queries=["q"] * len(grounding),
            grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri=u, title="t")) for u in grounding],
        )
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]),
                                    grounding_metadata=gm)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts),
    )


class FakeGemini:
    """Mimics genai.Client: .aio.models.generate_content returns scripted responses in order."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

    async def _gen(self, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(self.requests[-1]) if callable(item) else item


AGENT_JSON = json.dumps({
    "score": 0.4, "confidence": 0.7,
    "key_points": ["RSI 55 neutral", "price above SMA50", "MACD hist positive"],
    "reasoning": "Uptrend intact.", "sources": ["get_indicators"],
})
