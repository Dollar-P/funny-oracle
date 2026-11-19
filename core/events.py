"""Run events (REDESIGN §2.2): the orchestrator, agents and LLM layer report each step here.

    async def on_event(ev: dict) -> None: ...
    pred = await run_prediction("NVDA", "swing", "1M", on_event=on_event)

Every event is a plain dict: {"type", "run_id", "ts" (epoch ms), "seq", ...payload}.
With on_event=None a NullSink is used and nothing changes for the CLI / Streamlit.
A failing callback is logged and never breaks the run.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

OnEvent = Callable[[dict], Awaitable[None]]

EVENT_TYPES = (
    "run_started", "stage", "agent_started", "tool_call", "tool_result", "search", "news_brief",
    "agent_done", "agent_failed", "aggregate", "debate_turn", "judge", "quant", "cost_update",
    "run_done", "error",
)
STAGES = ("news", "analysts", "aggregate", "debate", "judge", "quant", "done")


def now_ms() -> int:
    return int(time.time() * 1000)


class EventSink:
    def __init__(self, on_event: OnEvent | None = None, run_id: str | None = None):
        self.on_event = on_event
        self.run_id = run_id
        self.seq = 0

    @property
    def enabled(self) -> bool:
        return self.on_event is not None

    async def emit(self, type_: str, **payload: Any) -> None:
        if self.on_event is None:
            return
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        self.seq += 1
        ev = {"type": type_, "run_id": self.run_id, "ts": now_ms(), "seq": self.seq, **payload}
        try:
            await self.on_event(ev)
        except Exception:  # a broken subscriber must never kill the prediction
            log.exception("on_event callback failed for %s", type_)


NULL_SINK = EventSink(None)
