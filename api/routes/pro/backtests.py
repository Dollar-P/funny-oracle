"""POST /api/pro/backtests · GET /backtests · /backtests/{id} · /backtests/{id}/events (SSE progress)

kind: "baselines" (buy & hold, cash, momentum, SMA, quant-only + random distribution) or "llm" (PRO.md §6.2 rules:
contaminated dates refused, point-in-time data only, paid models need confirm_cost after seeing the estimate).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Body, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api import pro_state
from api.routes.pro.common import bad, one, rows
from core import pro_settings
from core.db import connect

router = APIRouter()
log = logging.getLogger("pro.backtests")
UTC = timezone.utc


def _update(bid: int, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    with connect() as conn:
        conn.execute(f"UPDATE backtests SET {sets} WHERE id = ?", (*cols.values(), bid))


def _serialise(obj):
    import pandas as pd

    if isinstance(obj, pd.Series):
        return [[ts.isoformat(), float(v)] for ts, v in obj.iloc[::max(1, len(obj) // 300)].items()]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _work(bid: int, kind: str, cfg: dict) -> None:
    from api.routes.pro.evaluation import _history

    hist = _history()
    s = pro_settings.load()
    tickers = cfg.get("tickers") or {w["ticker"]: w["asset_type"] for w in s["watchlist"]}

    def progress(p: dict):
        frac = p["done"] / max(1, p["total"])
        _update(bid, progress=frac)
        pro_state.BUS.publish({"type": "backtest_progress", "backtest_id": bid, **p, "progress": frac})

    try:
        if kind == "baselines":
            from evaluation.report import run_baseline_backtests

            data = {t: (a, hist(t, a)) for t, a in tickers.items()}
            progress({"done": 0, "total": 1})
            res = run_baseline_backtests(data, cfg.get("start", "2023-01-01"), n_random=int(cfg.get("n_random", 1000)),
                                         n_boot=int(cfg.get("n_boot", 1000)))
            progress({"done": 1, "total": 1})
        else:
            from evaluation.llm_backtest import LLMBacktestConfig, run_llm_backtest

            lc = LLMBacktestConfig(tickers=tickers, start=cfg["start"], end=cfg["end"], model=cfg["model"],
                                   horizon=cfg.get("horizon", "1M"), think_preset=cfg.get("think_preset", "quick"),
                                   allow_contaminated=bool(cfg.get("allow_contaminated")),
                                   confirm_cost=bool(cfg.get("confirm_cost")), max_runs=cfg.get("max_runs"))
            history = {t: hist(t, a) for t, a in tickers.items()}
            runner = pro_state._STATE.get("llm_runner")
            res = anyio.run(lambda: run_llm_backtest(lc, history, runner=runner, progress=progress))
        _update(bid, status="done", progress=1.0, result_json=json.dumps(res, default=_serialise))
        pro_state.BUS.publish({"type": "backtest_done", "backtest_id": bid, "status": "done"})
    except Exception as e:
        from evaluation.llm_backtest import CostConfirmationRequired

        msg = f"{type(e).__name__}: {e}"[:400]
        extra = {"estimate_thb": e.estimate_thb, "runs": e.runs} if isinstance(e, CostConfirmationRequired) else {}
        log.warning("backtest %s failed: %s", bid, msg)
        _update(bid, status="needs_confirmation" if extra else "failed", error=msg,
                result_json=json.dumps(extra) if extra else None)
        pro_state.BUS.publish({"type": "backtest_done", "backtest_id": bid, "status": "failed", "error": msg, **extra})


@router.post("/backtests")
def create(body: dict = Body(...)):
    kind = body.get("kind", "baselines")
    if kind not in ("baselines", "llm"):
        bad(422, "kind must be baselines or llm")
    if kind == "llm" and not all(body.get(k) for k in ("model", "start", "end")):
        bad(422, "llm backtests need model, start and end")
    with connect() as conn:
        cur = conn.execute("INSERT INTO backtests (created_at, kind, config_json, period, status, progress) VALUES"
                           " (?,?,?,?,?,0)", (datetime.now(UTC).isoformat(timespec="seconds"), kind, json.dumps(body),
                                              f"{body.get('start', '')}..{body.get('end', '')}", "running"))
        bid = int(cur.lastrowid)
    if body.get("wait"):
        _work(bid, kind, body)
    else:
        threading.Thread(target=_work, args=(bid, kind, body), name=f"backtest-{bid}", daemon=True).start()
    return get(bid)


@router.get("/backtests")
def list_backtests():
    return {"backtests": rows("SELECT id, created_at, kind, period, status, progress, error FROM backtests"
                              " ORDER BY id DESC LIMIT 100")}


@router.get("/backtests/{bid}")
def get(bid: int):
    r = one("SELECT * FROM backtests WHERE id = ?", (bid,))
    if not r:
        bad(404, "unknown backtest")
    r["config"] = json.loads(r.pop("config_json") or "{}")
    r["result"] = json.loads(r.pop("result_json") or "null")
    return r


@router.get("/backtests/{bid}/events")
async def events(bid: int, request: Request, after: int = 0):
    if not one("SELECT id FROM backtests WHERE id = ?", (bid,)):
        bad(404, "unknown backtest")

    async def gone():
        return await request.is_disconnected()

    async def gen():
        async for ev in pro_state.BUS.stream(after, stop=gone):
            if ev.get("backtest_id") == bid:
                yield ServerSentEvent(data=json.dumps(ev, default=str), event=ev["type"], id=str(ev["seq"]))
                if ev["type"] == "backtest_done":
                    return  # the stream ends with the final event, never before it

    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})
