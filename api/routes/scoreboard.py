"""GET /api/scoreboard · /api/budget · Auto-Scalp control."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import config
from core import cost, scoreboard
from core.autoscalp import HORIZON, MODE, TICKER, AutoScalpState, tick
from core.cost import BudgetGuard
from core.db import connect
from core.worlds import ARENA_ONLY

log = logging.getLogger(__name__)
router = APIRouter()


def _analyst_model_by_usage() -> dict[int, str]:
    """Most-used model per prediction from llm_usage (for rows saved before analyst_model existed)."""
    with connect() as conn:
        rows = conn.execute("SELECT prediction_id, model, COUNT(*) n FROM llm_usage WHERE prediction_id IS NOT NULL"
                            " GROUP BY prediction_id, model").fetchall()
    best: dict[int, tuple[str, int]] = {}
    for r in rows:
        if r["prediction_id"] not in best or r["n"] > best[r["prediction_id"]][1]:
            best[r["prediction_id"]] = (r["model"], r["n"])
    return {k: v[0] for k, v in best.items()}


def model_leaderboard() -> list[dict]:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, analyst_model, duel_id, mode, price_now, p10, p50, p90, prob_up, cost_usd, cost_actual_usd,"
            f" actual_price, prediction_json FROM predictions WHERE {ARENA_ONLY}").fetchall()]
    fallback = _analyst_model_by_usage()
    by_model: dict[str, dict] = {}
    duels: dict[str, list[dict]] = {}
    for r in rows:
        m = r["analyst_model"] or fallback.get(r["id"]) or "unknown"
        r["model"] = m
        try:
            r["elapsed_s"] = float(json.loads(r["prediction_json"] or "{}").get("elapsed_s") or 0)
        except ValueError:
            r["elapsed_s"] = 0.0
        d = by_model.setdefault(m, {"model": m, "n": 0, "n_evaluated": 0, "hits": 0, "n_directional": 0,
                                    "in_band": 0, "cost": 0.0, "time": 0.0, "duels": 0, "duel_wins": 0})
        d["n"] += 1
        d["cost"] += r["cost_usd"] or 0
        d["time"] += r["elapsed_s"]
        if r["actual_price"] is not None:
            d["n_evaluated"] += 1
            d["in_band"] += int(r["p10"] <= r["actual_price"] <= r["p90"])
            if abs(r["prob_up"] - 0.5) > 1e-9:
                d["n_directional"] += 1
                d["hits"] += int((r["prob_up"] > 0.5) == (r["actual_price"] > r["price_now"]))
        if r["duel_id"]:
            duels.setdefault(r["duel_id"], []).append(r)
    for pair in duels.values():
        if len(pair) != 2 or any(p["actual_price"] is None for p in pair):
            continue
        err = [abs(p["p50"] - p["actual_price"]) for p in pair]
        for p in pair:
            by_model[p["model"]]["duels"] += 1
        if err[0] != err[1]:
            by_model[pair[0 if err[0] < err[1] else 1]["model"]]["duel_wins"] += 1
    out = []
    for d in by_model.values():
        out.append({
            "model": d["model"], "n": d["n"], "n_evaluated": d["n_evaluated"],
            "hit_rate": d["hits"] / d["n_directional"] if d["n_directional"] else None,
            "band_coverage": d["in_band"] / d["n_evaluated"] if d["n_evaluated"] else None,
            "avg_cost_thb": cost.usd_to_thb(d["cost"] / d["n"]) if d["n"] else 0.0,
            "avg_time_s": d["time"] / d["n"] if d["n"] else 0.0,
            "duels": d["duels"], "duel_wins": d["duel_wins"],
            "duel_win_rate": d["duel_wins"] / d["duels"] if d["duels"] else None,
        })
    out.sort(key=lambda x: (-(x["hit_rate"] or -1), -x["n"]))
    return out


def pending() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, ticker, mode, horizon, created_at, target_time, price_now, p50, prob_up,"
                            f" analyst_model FROM predictions WHERE actual_price IS NULL AND {ARENA_ONLY}"
                            " ORDER BY target_time"
                            " LIMIT 50").fetchall()
    return [dict(r) for r in rows]


@router.get("/scoreboard")
async def get_scoreboard(evaluate: bool = True, mode: str | None = None):
    evaluated = 0
    if evaluate:
        try:
            evaluated = await asyncio.wait_for(asyncio.to_thread(scoreboard.evaluate_due), 30)
        except Exception as e:  # network trouble must not break the page
            log.warning("evaluate_due failed: %s", e)
    m = await asyncio.to_thread(scoreboard.metrics, mode)
    return {"metrics": m, "models": model_leaderboard(), "pending": pending(), "evaluated_now": evaluated,
            "now": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def budget_snapshot() -> dict:
    tracked = cost.total_spent_usd()
    actual = cost.total_actual_usd()
    g = BudgetGuard()
    month = cost.searches_this_month()
    return {
        "budget_thb": g.budget_thb,
        "usd_thb": config.USD_THB,
        "spent_thb": round(cost.usd_to_thb(tracked), 2),
        "estimated_actual_thb": round(cost.usd_to_thb(actual), 2),
        "remaining_thb": round(cost.usd_to_thb(g.remaining_usd()), 2),
        "basis": config.BUDGET_BASIS,
        "gemini_searches_this_month": month,
        "gemini_free_searches_left": max(0, config.GEMINI_SEARCH_FREE_PER_MONTH - month),
        "mock": config.MOCK_LLM,
    }


@router.get("/budget")
async def budget():
    return budget_snapshot()


# --- Auto-Scalp ------------------------------------------------------------------

class AutoScalpController:
    """Background thread calling core.autoscalp.tick() every few seconds."""

    def __init__(self, poll_s: float = 20):
        self.state: AutoScalpState | None = None
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.poll_s = poll_s
        self.started_at: float | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, interval_s: int | None = None, max_runs: int | None = None) -> None:
        if self.running:
            return
        self.state = AutoScalpState(max_runs=max_runs or config.AUTO_SCALP_MAX_RUNS,
                                    interval_s=interval_s or config.AUTO_SCALP_INTERVAL_S)
        self.stop_flag.clear()
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._loop, name="auto-scalp", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop_flag.is_set():
            st = self.state
            try:
                line = tick(st)
                if line and (not st.log or st.log[-1] != line):
                    st.log.append(line)
            except Exception as e:
                st.log.append(f"tick failed: {e}"[:200])
            if st.stopped_reason:
                break
            self.stop_flag.wait(self.poll_s)

    def stop(self) -> None:
        self.stop_flag.set()

    def status(self) -> dict:
        st = self.state
        return {
            "running": self.running, "ticker": TICKER, "mode": MODE, "horizon": HORIZON,
            "runs": st.runs if st else 0,
            "max_runs": st.max_runs if st else config.AUTO_SCALP_MAX_RUNS,
            "runs_left": (st.max_runs - st.runs) if st else config.AUTO_SCALP_MAX_RUNS,
            "interval_s": st.interval_s if st else config.AUTO_SCALP_INTERVAL_S,
            "seconds_to_next": st.seconds_to_next() if st and self.running else None,
            "stopped_reason": st.stopped_reason if st else None,
            "log": (st.log[-10:] if st else []),
            "mock_blocked": config.MOCK_LLM,
        }


AUTOSCALP = AutoScalpController()


@router.post("/autoscalp/start")
async def autoscalp_start():
    if config.MOCK_LLM:
        raise HTTPException(409, "Auto-Scalp makes real (paid) predictions and is disabled while MOCK_LLM=true")
    AUTOSCALP.start()
    return AUTOSCALP.status()


@router.post("/autoscalp/stop")
async def autoscalp_stop():
    AUTOSCALP.stop()
    return AUTOSCALP.status()


@router.get("/autoscalp/status")
async def autoscalp_status():
    return AUTOSCALP.status()
