"""GET /api/pro/events — the Desk's SSE stream (Last-Event-ID / ?after= resume; ?once=1 returns what is buffered)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api import pro_state

router = APIRouter()


@router.get("/events")
async def events(request: Request, after: int = 0, once: bool = False, types: str | None = None,
                 max_s: float | None = None):
    last = request.headers.get("last-event-id")
    start = max(after, int(last) if last and last.isdigit() else 0)
    wanted = set(types.split(",")) if types else None
    if once:
        return {"events": [e for e in pro_state.BUS.since(start) if not wanted or e["type"] in wanted],
                "last_seq": pro_state.BUS.seq}

    async def gone():
        return await request.is_disconnected()

    async def gen():
        async for ev in pro_state.BUS.stream(start, stop=gone, max_s=max_s):
            if wanted is None or ev["type"] in wanted:
                yield ServerSentEvent(data=json.dumps(ev, default=str), event=ev["type"], id=str(ev["seq"]))

    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})
