"""RunManager (REDESIGN §2): runs as background tasks, one event buffer + subscribers per run.

- Real runs execute `orchestrator.run_prediction` in a worker thread with its own event loop
  (isolates blocking yfinance work and works with any server loop policy on Windows); events
  are marshalled back to the server loop with `call_soon_threadsafe`.
- Mock runs (api/mock_runner.py) are plain asyncio tasks on the server loop.
- Every event is buffered, so late subscribers / reloads replay from any `seq`
  (SSE `Last-Event-ID`). A run keeps going if all subscribers disconnect.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

import config
from core.events import now_ms

log = logging.getLogger(__name__)

TERMINAL = {"run_done"}
KEEP_FINISHED = 50


class TooManyRuns(RuntimeError):
    pass


@dataclass
class Run:
    run_id: str
    kind: str                       # real | mock
    params: dict[str, Any]
    duel_id: str | None = None
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    done: bool = False
    task: asyncio.Future | None = None
    prediction: dict | None = None
    prediction_id: int | None = None
    error: str | None = None
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def push(self, ev: dict) -> None:
        ev = dict(ev)
        ev["run_id"] = self.run_id
        ev["seq"] = len(self.events) + 1
        ev.setdefault("ts", now_ms())
        if self.duel_id and "duel_id" not in ev:
            ev["duel_id"] = self.duel_id
        self.events.append(ev)
        if ev["type"] == "run_done":
            self.prediction = ev.get("prediction")
            self.prediction_id = ev.get("prediction_id")
        elif ev["type"] == "error" and ev.get("fatal"):
            self.error = ev.get("message")
        async with self._cond:
            self._cond.notify_all()

    async def finish(self) -> None:
        self.done = True
        async with self._cond:
            self._cond.notify_all()

    async def stream(self, after_seq: int = 0) -> AsyncIterator[dict]:
        i = max(0, after_seq)
        while True:
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.done:
                return
            async with self._cond:
                if i >= len(self.events) and not self.done:
                    await self._cond.wait()

    def summary(self) -> dict:
        return {"run_id": self.run_id, "kind": self.kind, "done": self.done, "events": len(self.events),
                "prediction_id": self.prediction_id, "error": self.error, "duel_id": self.duel_id,
                "params": self.params}


class RunManager:
    def __init__(self, max_concurrent: int | None = None):
        self.runs: dict[str, Run] = {}
        self.duels: dict[str, dict] = {}
        self.max_concurrent = config.MAX_CONCURRENT_RUNS if max_concurrent is None else max_concurrent

    # --- bookkeeping -------------------------------------------------------------
    def active(self) -> list[Run]:
        return [r for r in self.runs.values() if not r.done]

    def ensure_capacity(self, n: int = 1) -> None:
        if len(self.active()) + n > self.max_concurrent:
            raise TooManyRuns(f"Show already in progress ({len(self.active())} running, max {self.max_concurrent})")

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def _prune(self) -> None:
        finished = sorted((r for r in self.runs.values() if r.done), key=lambda r: r.created)
        for r in finished[:-KEEP_FINISHED]:
            self.runs.pop(r.run_id, None)

    def _new(self, kind: str, params: dict, duel_id: str | None, run_id: str | None = None) -> Run:
        self._prune()
        run = Run(run_id=run_id or uuid.uuid4().hex, kind=kind, params=params, duel_id=duel_id)
        self.runs[run.run_id] = run
        return run

    # --- starting runs ---------------------------------------------------------------
    def start_mock(self, params: dict, body: Callable[[Run], Awaitable[None]], duel_id: str | None = None) -> Run:
        run = self._new("mock", params, duel_id)

        async def main():
            try:
                await body(run)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never leave a subscriber hanging
                log.exception("mock run %s failed", run.run_id)
                await run.push({"type": "error", "message": f"{type(e).__name__}: {e}"[:300], "fatal": True})
            finally:
                await run.finish()

        run.task = asyncio.ensure_future(main())
        return run

    def start_real(self, params: dict, duel_id: str | None = None) -> Run:
        """params: ticker, mode, horizon, analyst_model, judge_model (passed to run_prediction)."""
        run = self._new("real", params, duel_id)
        loop = asyncio.get_running_loop()

        def on_event_threadsafe(ev: dict) -> None:
            fut = asyncio.run_coroutine_threadsafe(run.push(ev), loop)
            fut.result(timeout=10)

        def worker():
            import orchestrator

            async def on_event(ev):
                await asyncio.to_thread(on_event_threadsafe, ev)

            try:
                asyncio.run(orchestrator.run_prediction(
                    params["ticker"], params["mode"], params["horizon"], source=params.get("source", "web"),
                    analyst_model=params.get("analyst_model"), judge_model=params.get("judge_model"),
                    on_event=on_event, run_id=run.run_id, duel_id=duel_id,
                    think_preset=params.get("think_preset")))
            except Exception as e:  # orchestrator already emitted a fatal error event
                log.warning("run %s failed: %s", run.run_id, e)
                if run.error is None:
                    asyncio.run_coroutine_threadsafe(
                        run.push({"type": "error", "message": f"{type(e).__name__}: {e}"[:300], "fatal": True}),
                        loop).result(timeout=10)
            finally:
                asyncio.run_coroutine_threadsafe(run.finish(), loop).result(timeout=10)

        t = threading.Thread(target=worker, name=f"run-{run.run_id[:8]}", daemon=True)
        t.start()
        return run


MANAGER = RunManager()
