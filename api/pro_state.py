"""Process-wide Pro state: the Desk event bus (SSE), the Desk and the Scheduler.

The bus is thread-safe (approve/reject routes run in FastAPI's thread pool) and keeps the last 2,000 events with
a monotonically increasing `seq`, so SSE clients resume exactly with Last-Event-ID — like the Arena's runs.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

from core.events import now_ms

KEEP = 2000
UTC = timezone.utc

PRO_EVENT_TYPES = ("signal_created", "signal_flat", "risk_rejected", "order_placed", "order_filled", "position_closed",
                   "breaker_tripped", "equity_tick", "entry_cancelled", "job_started", "job_done", "job_skipped",
                   "job_deferred", "job_failed", "scheduler_started", "scheduler_stopped", "settings_changed",
                   "account_reset", "report_ready", "backtest_progress", "backtest_done")


class EventBus:
    def __init__(self, keep: int = KEEP):
        self.keep = keep
        self.events: list[dict] = []
        self.seq = 0
        self._lock = threading.Lock()

    def publish(self, ev: dict) -> dict:
        with self._lock:
            self.seq += 1
            e = {**ev, "seq": self.seq, "ts": ev.get("ts_ms") or now_ms()}
            self.events.append(e)
            if len(self.events) > self.keep:
                del self.events[: len(self.events) - self.keep]
        return e

    def since(self, after: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > after]

    async def stream(self, after: int = 0, poll_s: float = 0.25, stop: "callable | None" = None,
                     max_s: float | None = None):
        t0 = time.monotonic()
        last = after
        while True:
            for e in self.since(last):
                last = e["seq"]
                yield e
            if (stop and await stop()) or (max_s is not None and time.monotonic() - t0 > max_s):
                return
            await asyncio.sleep(poll_s)

    def clear(self) -> None:
        with self._lock:
            self.events.clear()
            self.seq = 0


BUS = EventBus()
_STATE: dict = {}


def clock() -> datetime:
    return datetime.now(UTC)


def local_available() -> bool:
    from api.routes.models import local_server_models

    ids, _err = local_server_models()
    return bool(ids)


def desk():
    if "desk" not in _STATE:
        from agents.reviewer import cheapest_gemini  # noqa: F401  (import check)
        from evaluation.calibration import calibrator_for
        from trading.desk import Desk, default_reviewer
        from trading import scheduler as S
        from core import pro_settings

        def budget_ok() -> bool:
            s = pro_settings.load()
            return S.spent_today_thb(clock()) < float(s["budgets"]["daily_thb"])

        _STATE["desk"] = Desk(emit=BUS.publish, calibrator_for=calibrator_for(clock),
                              reviewer=default_reviewer(local_available, budget_ok=budget_ok))
    return _STATE["desk"]


def scheduler():
    if "scheduler" not in _STATE:
        from trading.scheduler import Scheduler, orchestrator_runner

        _STATE["scheduler"] = Scheduler(desk=desk(), runner=orchestrator_runner, clock=clock,
                                        local_available=local_available, emit=BUS.publish)
    return _STATE["scheduler"]


def set_for_tests(**kw) -> None:
    _STATE.clear()
    _STATE.update(kw)
