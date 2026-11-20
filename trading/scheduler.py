"""Scheduler (PRO.md §3.5): which predictions run when, on which model, and whether they may run at all.

Timing (docs/pro-plan.md; a deliberate refinement of "crypto and gold at a fixed UTC time" so every decision
uses a closed daily bar and fills at the very next open):
    stocks  — `stock_after_close_min` after the NYSE close, valid until the next session opens;
    gold    — `gold_after_close_min` after the CME daily close (17:00 New York), valid until it reopens (18:00);
    crypto  — at `crypto_utc` (default 23:30 UTC, just before the 00:00 UTC daily bar), valid for 30 minutes.
    scalp   — optional, every `scalp_every_min` minutes for `scalp_tickers` (stocks only in market hours).

Routing per job:
    scalp → `models.scalp` (Gemini only).
    swing → `models.swing` (default local Quick). A local job runs only if LM Studio answers, the local window is
            open, and the queue ETA + this job's estimated duration ends inside the window. Otherwise it waits for
            the window (if it opens before the job expires) or falls back to `models.swing_fallback`.
    Any paid job must fit the daily money budget (`budgets.daily_thb`), else it is skipped with a logged reason.

Every job key is written to `scheduler_runs` *before* the run starts, so a restart never runs it twice.
Scheduled jobs yield to interactive Arena / Duel runs and never take the last RunManager slot.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import config
from core import cost
from core.db import connect
from core.schemas import Prediction
from core.worlds import PRO, PRO_ONLY
from trading import calendar as cal

log = logging.getLogger("scheduler")
UTC = timezone.utc
NY = ZoneInfo("America/New_York")

DEFAULT_EST_S = {"local:quick": 400.0, "local:deep": 1500.0, "gemini": 45.0, "claude": 60.0}


@dataclass
class Job:
    key: str
    ticker: str
    asset_type: str
    mode: str
    horizon: str
    due_at: datetime
    valid_until: datetime
    model: str | None = None
    think_preset: str | None = None
    est_s: float = 0.0
    est_cost_thb: float = 0.0
    route_reason: str = ""


@dataclass
class Skip:
    job: Job
    reason: str
    defer: bool = False      # True = try again later (not recorded as done)


# --- time helpers -------------------------------------------------------------------------------------

def _hm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def local_tz(s: dict):
    name = (s.get("local_tz") or config._env("PRO_TZ")).strip()
    return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo


def window_bounds(now: datetime, s: dict) -> tuple[datetime, datetime] | None:
    """(start, end) of the local window containing `now`, or None if outside. Handles windows across midnight."""
    w = s.get("local_window") or config._env("PRO_LOCAL_WINDOW", "23:00-07:00")
    a, b = (_hm(x) for x in w.split("-"))
    tz = local_tz(s)
    loc = now.astimezone(tz)
    for day in (loc.date() - timedelta(days=1), loc.date()):
        start = datetime.combine(day, a, tzinfo=tz)
        end = datetime.combine(day + (timedelta(days=1) if b <= a else timedelta(0)), b, tzinfo=tz)
        if start <= loc < end:
            return start.astimezone(UTC), end.astimezone(UTC)
    return None


def next_window_start(now: datetime, s: dict) -> datetime:
    w = s.get("local_window") or "23:00-07:00"
    a = _hm(w.split("-")[0])
    tz = local_tz(s)
    loc = now.astimezone(tz)
    start = datetime.combine(loc.date(), a, tzinfo=tz)
    if start <= loc:
        start += timedelta(days=1)
    return start.astimezone(UTC)


# --- due jobs ----------------------------------------------------------------------------------------------

def due_jobs(s: dict, now: datetime, done: set[str]) -> list[Job]:
    sch = s["schedule"]
    out: list[Job] = []
    for w in s["watchlist"]:
        t, a = w["ticker"], w["asset_type"]
        for h in w.get("horizons", ["1M"]):
            for job in _swing_slots(t, a, h, now, sch):
                if job.key not in done and job.due_at <= now < job.valid_until:
                    out.append(job)
    if sch.get("scalp_enabled"):
        every = int(sch.get("scalp_every_min", 60))
        slot = now.replace(second=0, microsecond=0) - timedelta(minutes=(now.minute % every) if every < 60 else now.minute)
        for t in sch.get("scalp_tickers", []):
            a = next((w["asset_type"] for w in s["watchlist"] if w["ticker"] == t), "crypto")
            if a != "crypto" and not cal.is_open(a, now):
                continue
            key = f"scalp:{t}:{sch.get('scalp_horizon', '1h')}:{slot.isoformat()}"
            if key not in done and now < slot + timedelta(minutes=5):
                out.append(Job(key, t, a, "scalp", sch.get("scalp_horizon", "1h"), slot, slot + timedelta(minutes=5)))
    return sorted(out, key=lambda j: (j.due_at, j.ticker))


def _swing_slots(t: str, a: str, h: str, now: datetime, sch: dict):
    if a == "stock":
        for i in range(4, -1, -1):
            d = (now.astimezone(NY) - timedelta(days=i)).date()
            sess = cal.nyse_session(d)
            if not sess:
                continue
            due = sess[1] + timedelta(minutes=int(sch.get("stock_after_close_min", 30)))
            yield Job(f"swing:{t}:{h}:{d}", t, a, "swing", h, due, cal.next_open("stock", sess[1] + timedelta(minutes=1)))
    elif a == "gold":
        for i in range(4, -1, -1):
            d = (now.astimezone(NY) - timedelta(days=i)).date()
            if d.weekday() >= 5:
                continue
            close = datetime.combine(d, time(17, 0), tzinfo=NY).astimezone(UTC)
            due = close + timedelta(minutes=int(sch.get("gold_after_close_min", 15)))
            reopen = cal.next_open("gold", close + timedelta(minutes=1))
            yield Job(f"swing:{t}:{h}:{d}", t, a, "swing", h, due, reopen)
    else:
        hm = _hm(sch.get("crypto_utc", "23:30"))
        for i in (1, 0):
            d = (now.astimezone(UTC) - timedelta(days=i)).date()
            due = datetime.combine(d, hm, tzinfo=UTC)
            nxt_bar = datetime.combine(d + timedelta(days=1), time(0), tzinfo=UTC)
            yield Job(f"swing:{t}:{h}:{d}", t, a, "swing", h, due, max(nxt_bar, due + timedelta(minutes=30)))


# --- estimates & budgets ---------------------------------------------------------------------------------------

def estimate_s(model: str, think_preset: str | None) -> float:
    """Recent average duration of runs on this model (last 10 predictions), else a default."""
    with connect() as conn:
        rows = conn.execute(f"SELECT prediction_json FROM predictions WHERE analyst_model = ? AND {PRO_ONLY}"
                            " ORDER BY id DESC LIMIT 25", (model,)).fetchall()
    vals = []
    for (pj,) in rows:
        try:
            p = Prediction.model_validate_json(pj)
        except Exception:
            continue
        if p.mode == "swing" and (not model.startswith("local:") or (p.think_preset or "quick") == (think_preset or "quick")):
            vals.append(p.elapsed_s)
        if len(vals) >= 10:
            break
    if vals:
        return sum(vals) / len(vals)
    if model.startswith("local:"):
        return DEFAULT_EST_S[f"local:{think_preset or 'quick'}"]
    return DEFAULT_EST_S.get(config.provider_for(model), 60.0)


def pro_spent_thb(since: datetime | None = None) -> float:
    """Pro (scheduled runs + reviewer) spend, tracked basis; since=None → all time. Arena spend is never included."""
    t0 = since.astimezone(UTC).isoformat(timespec="seconds") if since else ""
    with connect() as conn:
        v = conn.execute(f"SELECT COALESCE(SUM(cost_usd), 0) FROM predictions WHERE {PRO_ONLY} AND created_at >= ?",
                         (t0,)).fetchone()[0]
        r = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE agent = 'reviewer' AND created_at >= ?",
                         (t0,)).fetchone()[0]
    return cost.usd_to_thb(float(v) + float(r))


def spent_today_thb(now: datetime) -> float:
    """Pro (scheduled + reviewer) spend since 00:00 UTC, tracked basis."""
    return pro_spent_thb(datetime.combine(now.astimezone(UTC).date(), time(0), tzinfo=UTC))


def est_cost_thb(mode: str, model: str) -> float:
    if model.startswith(config.LOCAL_PREFIX):
        return 0.0
    from core.cost import BudgetGuard

    return cost.usd_to_thb(BudgetGuard().estimate_usd(mode, model, world=PRO))


def route(job: Job, s: dict, now: datetime, *, local_ok: bool, queue_eta_s: float, spent_thb: float) -> Job | Skip:
    m = s["models"]
    budget = float(s["budgets"]["daily_thb"])

    def paid(model: str, why: str) -> Job | Skip:
        c = est_cost_thb(job.mode, model)
        if spent_thb + c > budget:
            return Skip(job, f"{why}; money budget: ฿{spent_thb:.2f} spent today + ฿{c:.2f} est. > ฿{budget:.2f}")
        return replace(job, model=model, think_preset=None, est_s=estimate_s(model, None), est_cost_thb=c,
                       route_reason=why)

    if job.mode == "scalp":
        model = m["scalp"]
        if config.provider_for(model) == "local":
            return Skip(job, "scalp needs a Gemini model (local is too slow)")
        return paid(model, "scalp → Gemini")
    primary = m["swing"]
    if config.provider_for(primary) != "local":
        return paid(primary, "swing → configured model")
    preset = m.get("swing_think_preset", "quick")
    est = estimate_s(primary, preset)
    win = window_bounds(now, s)
    if not local_ok:
        why = "LM Studio unreachable"
    elif win is None:
        nws = next_window_start(now, s)
        if nws + timedelta(seconds=queue_eta_s + est) <= job.valid_until:
            return Skip(job, f"outside the local window; waiting until {nws.isoformat(timespec='minutes')}", defer=True)
        why = "outside the local window and the job expires before it opens"
    elif now + timedelta(seconds=queue_eta_s + est) > win[1]:
        why = (f"won't finish in the local window (queue {queue_eta_s / 60:.0f} min + est. {est / 60:.1f} min, "
               f"window ends {win[1].isoformat(timespec='minutes')})")
    else:
        return replace(job, model=primary, think_preset=preset, est_s=est, est_cost_thb=0.0,
                       route_reason=f"local {preset} (est. {est / 60:.1f} min)")
    return paid(m["swing_fallback"], f"fallback to {m['swing_fallback']}: {why}")


# --- persistence --------------------------------------------------------------------------------------------

def done_keys() -> set[str]:
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT job_key FROM scheduler_runs")}


def claim(job: Job, now: datetime, status: str, detail: str = "", experiment_id: int | None = None) -> bool:
    """Insert the job key; False if it already exists (another run / a restart already took it)."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO scheduler_runs (job_key, created_at, status, ticker, mode, horizon, model, think_preset,"
            " detail, experiment_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job.key, now.isoformat(timespec="seconds"), status, job.ticker, job.mode, job.horizon, job.model,
             job.think_preset, detail, experiment_id))
        return cur.rowcount == 1


def finish(key: str, now: datetime, status: str, detail: str = "", prediction_id: int | None = None,
           signal_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute("UPDATE scheduler_runs SET status = ?, detail = ?, prediction_id = ?, signal_id = ?, finished_at = ?"
                     " WHERE job_key = ?", (status, detail, prediction_id, signal_id, now.isoformat(timespec="seconds"), key))


def recent_runs(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM scheduler_runs ORDER BY created_at DESC, job_key LIMIT ?",
                                              (limit,))]


# --- the loop -------------------------------------------------------------------------------------------------

Runner = Callable[[Job, int, dict], Awaitable[Prediction]]


def interactive_busy() -> tuple[bool, str]:
    """True if an Arena / Duel run is active, or if starting a job would take the last RunManager slot."""
    try:
        from api.runs import MANAGER
    except Exception:
        return False, ""
    active = MANAGER.active()
    if any(r.params.get("source") != "pro" for r in active):
        return True, "an interactive Arena/Duel run is in progress"
    if len(active) + 1 >= MANAGER.max_concurrent:
        return True, "leaving the last run slot free for the Arena"
    return False, ""


@dataclass
class Scheduler:
    desk: "object"
    runner: Runner
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    local_available: Callable[[], bool] = lambda: False
    busy: Callable[[], tuple[bool, str]] = interactive_busy
    emit: Callable[[dict], None] = lambda ev: None
    running: bool = False
    last_tick: datetime | None = None
    last_error: str | None = None
    queue: list[Job] = field(default_factory=list)
    current: Job | None = None
    _task: asyncio.Task | None = None

    def status(self) -> dict:
        from core import pro_settings

        s = pro_settings.load()
        now = self.clock()
        win = window_bounds(now, s)
        return {"running": self.running, "last_tick": self.last_tick.isoformat() if self.last_tick else None,
                "current": self.current.__dict__ if self.current else None,
                "queue": [j.key for j in self.queue], "local_available": self.local_available(),
                "local_window": s["local_window"], "in_local_window": win is not None,
                "window_end": win[1].isoformat() if win else None, "approval": s["approval"],
                "spent_today_thb": round(spent_today_thb(now), 2), "daily_budget_thb": s["budgets"]["daily_thb"],
                "halted": s.get("halted", False), "last_error": self.last_error, "recent": recent_runs(20)}

    def _ev(self, type_: str, **kw) -> None:
        try:
            self.emit({"type": type_, **kw})
        except Exception:
            log.exception("emit failed")

    async def tick(self) -> list[dict]:
        """One pass: market step, then run due jobs one at a time (local jobs serially)."""
        from core import pro_settings

        now = self.clock()
        self.last_tick = now
        events = list(await self.desk.market_step(now))
        s = pro_settings.load()
        exp = pro_settings.current_experiment(s)
        jobs = due_jobs(s, now, done_keys())
        local_ok = bool(self.local_available()) if any(
            j.mode == "swing" and config.provider_for(s["models"]["swing"]) == "local" for j in jobs) else False
        eta = 0.0
        self.queue = jobs
        for job in jobs:
            busy, why = self.busy()
            if busy:
                self._ev("job_deferred", key=job.key, reason=why)
                events.append({"type": "job_deferred", "key": job.key, "reason": why})
                break  # yield to the Arena; try again on the next tick
            r = route(job, s, self.clock(), local_ok=local_ok, queue_eta_s=eta, spent_thb=spent_today_thb(self.clock()))
            if isinstance(r, Skip):
                if r.defer:
                    events.append({"type": "job_deferred", "key": job.key, "reason": r.reason})
                    continue
                if claim(job, self.clock(), "skipped", r.reason, exp):
                    self._ev("job_skipped", key=job.key, ticker=job.ticker, reason=r.reason)
                    events.append({"type": "job_skipped", "key": job.key, "reason": r.reason})
                continue
            if not claim(r, self.clock(), "running", r.route_reason, exp):
                continue  # already taken (restart / concurrent tick)
            if r.model and r.model.startswith("local:"):
                eta += r.est_s
            self.current = r
            self._ev("job_started", key=r.key, ticker=r.ticker, model=r.model, think_preset=r.think_preset,
                     reason=r.route_reason)
            try:
                pred = await self.runner(r, exp, s)
                sig = self.desk.process_prediction(pred, self.clock(), job_key=r.key)
                finish(r.key, self.clock(), "done", r.route_reason, pred.id, sig["id"])
                self._ev("job_done", key=r.key, prediction_id=pred.id, signal_id=sig["id"], status=sig["status"])
                events.append({"type": "job_done", "key": r.key, "signal": sig["status"], "model": r.model})
            except Exception as e:  # a failed run is recorded and never retried automatically
                msg = f"{type(e).__name__}: {e}"[:300]
                log.warning("job %s failed: %s", r.key, msg)
                self.last_error = msg
                finish(r.key, self.clock(), "failed", msg)
                self._ev("job_failed", key=r.key, error=msg)
                events.append({"type": "job_failed", "key": r.key, "error": msg})
            finally:
                self.current = None
        self.queue = []
        return events

    async def _loop(self, tick_s: float) -> None:
        while self.running:
            try:
                await self.tick()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                log.exception("scheduler tick failed")
            await asyncio.sleep(tick_s)

    def start(self, tick_s: float | None = None) -> None:
        if self.running:
            return
        from core import pro_settings

        self.running = True
        self._task = asyncio.ensure_future(self._loop(tick_s or pro_settings.load()["schedule"].get("tick_s", 60)))

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


async def orchestrator_runner(job: Job, experiment_id: int, s: dict) -> Prediction:
    """Run a scheduled prediction through the RunManager (so it shares the Arena's run limit)."""
    from api.runs import MANAGER
    from core import scoreboard

    run = MANAGER.start_real({"ticker": job.ticker, "mode": job.mode, "horizon": job.horizon, "source": "pro",
                              "analyst_model": job.model, "think_preset": job.think_preset,
                              "prompt_version": s.get("prompt_version", config.PRO_PROMPT_VERSION),
                              "temperature": s.get("temperature", config.PRO_TEMPERATURE),
                              "experiment_id": experiment_id})
    async for _ev in run.stream():
        pass
    if run.error or run.prediction_id is None:
        raise RuntimeError(run.error or "run finished without a prediction")
    pred = scoreboard.load(run.prediction_id, PRO)
    if pred is None:
        raise RuntimeError(f"prediction {run.prediction_id} not found")
    return pred
