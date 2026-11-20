"""POST /api/pro/evaluation/run · GET /evaluation/reports · /evaluation/reports/{id} · /evaluation/latest"""
from __future__ import annotations

import json
import logging
import threading

from fastapi import APIRouter, Body

from api import pro_state
from api.routes.pro.common import bad, one, rows

router = APIRouter()
log = logging.getLogger("pro.evaluation")
_RUNNING: dict = {"busy": False, "error": None}


def _history():
    hist = pro_state._STATE.get("history")
    if hist is not None:
        return hist
    from evaluation.report import live_history

    return live_history


def run_report(n_random: int = 1000, include_backtests: bool = True, n_boot: int = 1000) -> int:
    from evaluation.report import build_report, save_report

    rep = build_report(history=_history(), n_random=n_random, include_backtests=include_backtests, n_boot=n_boot,
                       now=pro_state._STATE["scheduler"].clock() if "scheduler" in pro_state._STATE else None)
    rid = save_report(rep)
    pro_state.BUS.publish({"type": "report_ready", "report_id": rid})
    return rid


@router.post("/evaluation/run")
def run(body: dict | None = Body(None)):
    b = body or {}
    if _RUNNING["busy"]:
        bad(409, "an evaluation is already running")
    kw = {"n_random": int(b.get("n_random", 1000)), "include_backtests": bool(b.get("include_backtests", True)),
          "n_boot": int(b.get("n_boot", 1000))}
    if b.get("wait"):
        return {"report_id": run_report(**kw)}

    def work():
        _RUNNING.update(busy=True, error=None)
        try:
            run_report(**kw)
        except Exception as e:
            log.exception("evaluation failed")
            _RUNNING["error"] = f"{type(e).__name__}: {e}"[:300]
        finally:
            _RUNNING["busy"] = False

    threading.Thread(target=work, name="pro-evaluation", daemon=True).start()
    return {"status": "started"}


@router.get("/evaluation/status")
def status():
    return dict(_RUNNING)


@router.get("/evaluation/reports")
def reports():
    return {"reports": rows("SELECT id, created_at, experiment_id, period FROM reports ORDER BY id DESC LIMIT 100")}


@router.get("/evaluation/reports/{rid}")
def report(rid: int):
    r = one("SELECT * FROM reports WHERE id = ?", (rid,))
    if not r:
        bad(404, "unknown report")
    return {"id": r["id"], "created_at": r["created_at"], "report": json.loads(r["result_json"]),
            "markdown": r["markdown"]}


@router.get("/evaluation/latest")
def latest():
    r = one("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
    if not r:
        return {"report": None}
    return report(r["id"])
