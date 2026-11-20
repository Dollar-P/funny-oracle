"""POST /api/pro/scheduler/start · /stop · /tick · GET /scheduler/status"""
from __future__ import annotations

from fastapi import APIRouter

from api import pro_state

router = APIRouter()


@router.get("/scheduler/status")
def status():
    return pro_state.scheduler().status()


@router.post("/scheduler/start")
async def start(tick_s: float | None = None):
    sch = pro_state.scheduler()
    sch.start(tick_s)
    pro_state.BUS.publish({"type": "scheduler_started"})
    return sch.status()


@router.post("/scheduler/stop")
async def stop():
    sch = pro_state.scheduler()
    await sch.stop()
    pro_state.BUS.publish({"type": "scheduler_stopped"})
    return sch.status()


@router.post("/scheduler/tick")
async def tick():
    """Run one scheduler pass now (market step + due jobs). Useful for verification and tests."""
    events = await pro_state.scheduler().tick()
    return {"events": events, "status": pro_state.scheduler().status()}
