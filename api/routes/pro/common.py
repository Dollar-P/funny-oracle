"""Shared helpers for the /api/pro routes."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from api import pro_state
from core import pro_settings
from core.db import connect

UTC = timezone.utc
JSON_COLS = ("entry_json", "targets_json", "size_json")


def now() -> datetime:
    sch = pro_state._STATE.get("scheduler")
    return sch.clock() if sch is not None else pro_state.clock()


def experiment() -> int:
    return pro_settings.current_experiment()


def rows(sql: str, args=()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def one(sql: str, args=()) -> dict | None:
    with connect() as conn:
        r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def signal_row(r: dict) -> dict:
    d = dict(r)
    for c in JSON_COLS:
        if d.get(c):
            d[c.removesuffix("_json")] = json.loads(d.pop(c))
        else:
            d.pop(c, None)
            d[c.removesuffix("_json")] = None
    plan = json.loads(d.pop("plan_json") or "{}")
    inp = plan.get("inputs", {})
    d["p_win"] = plan.get("p_win")
    d["calibrated"] = plan.get("calibrated")
    d["cost_r"] = plan.get("cost_r")
    d["side"] = inp.get("side")
    d["flat_detail"] = (inp.get("detail") or {}).get(d.get("flat_reason") or "", None)
    d["fails"] = inp.get("fails", [])
    d["entry_deadline"] = inp.get("entry_deadline")
    d["model"] = inp.get("model")
    d["think_preset"] = inp.get("think_preset")
    d["prompt_version"] = inp.get("prompt_version")
    d["inputs"] = {k: inp.get(k) for k in ("atr", "k_atr", "atr_stop", "band_stop", "r1", "r2", "s_final", "confidence",
                                            "prob_up_raw", "prob_up_used", "sigma_annual", "calibration_n", "interval")}
    return d


def bad(status: int, msg: str):
    raise HTTPException(status, msg)
