"""GET /api/pro/signals · /signals/{id} · POST /signals/{id}/approve · /reject"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Query

from api import pro_state
from api.routes.pro.common import bad, experiment, now, one, rows, signal_row
from core import cost, scoreboard, snapshots
from core.worlds import PRO

router = APIRouter()
STATUSES = ("flat", "rejected", "new", "approved", "filled", "closed", "cancelled", "expired")


@router.get("/signals")
def list_signals(status: str | None = None, ticker: str | None = None, mode: str | None = None,
                 limit: int = Query(200, le=1000), experiment_id: int | None = None):
    q = "SELECT * FROM signals WHERE experiment_id = ?"
    a: list = [experiment_id or experiment()]
    for col, v in (("status", status), ("ticker", ticker), ("mode", mode)):
        if v:
            vals = v.split(",")
            q += f" AND {col} IN ({','.join('?' * len(vals))})"
            a += vals
    out = [signal_row(r) for r in rows(q + " ORDER BY created_at DESC, id DESC LIMIT ?", (*a, limit))]
    counts = {r["status"]: r["n"] for r in rows("SELECT status, COUNT(*) n FROM signals WHERE experiment_id = ?"
                                                " GROUP BY status", (a[0],))}
    return {"signals": out, "counts": counts, "statuses": STATUSES}


@router.get("/signals/{sid}")
def signal_detail(sid: int):
    r = one("SELECT * FROM signals WHERE id = ?", (sid,))
    if not r:
        bad(404, "unknown signal")
    sig = signal_row(r)
    pred = scoreboard.load(r["prediction_id"], PRO) if r["prediction_id"] else None
    pos = one("SELECT * FROM positions WHERE signal_id = ? ORDER BY id DESC LIMIT 1", (sid,))
    fills = rows("SELECT * FROM fills WHERE position_id = ? ORDER BY id", (pos["id"],)) if pos else []
    if pos:
        pos.pop("state_json", None)
        pos["review"] = json.loads(pos["review"]) if pos.get("review") else None
    detail = {
        "signal": sig,
        "risk_events": rows("SELECT ts, rule, detail FROM risk_events WHERE signal_id = ? ORDER BY id", (sid,)),
        "position": pos, "fills": fills,
    }
    if pred:
        v = pred.verdict
        detail["prediction"] = {
            "id": pred.id, "created_at": pred.created_at, "target_time": pred.target_time, "s_raw": pred.s_raw,
            "s_final": pred.s_final, "confidence": pred.confidence, "forecast": pred.forecast.model_dump(),
            "models": pred.models, "think_preset": pred.think_preset, "prompt_version": pred.prompt_version,
            "elapsed_s": pred.elapsed_s, "degraded": pred.degraded, "search_mode": pred.search_mode,
            "cost_thb": cost.usd_to_thb(pred.cost_usd or 0.0),
            "cost_actual_thb": cost.usd_to_thb(pred.cost_actual_usd or 0.0),
            "thesis": (v.thesis or v.summary) if v else None, "invalidation": v.invalidation if v else None,
            "catalysts": [c.model_dump() for c in v.catalysts] if v else [], "headline": v.headline if v else None,
            "key_risks": v.key_risks if v else [], "no_trade_reason": v.no_trade_reason if v else None,
            "agents": [{"agent": a.agent, "score": a.score, "confidence": a.confidence, "ok": a.ok, "error": a.error,
                        "view": (a.key_points[0] if a.key_points else a.reasoning)[:160],
                        "invalidation": a.invalidation, "weight": pred.weights.get(a.agent, 0.0),
                        "scenario_probs": a.scenario_probs.model_dump() if a.scenario_probs else None}
                       for a in pred.reports],
        }
        detail["snapshots"] = [{"id": s["id"], "tool": s["tool"], "as_of": s["as_of"]}
                               for s in snapshots.load(pred.run_id or "")] if pred.run_id else []
    return detail


@router.get("/snapshots/{snap_id}")
def snapshot(snap_id: int):
    r = one("SELECT run_id FROM tool_snapshots WHERE id = ?", (snap_id,))
    if not r:
        bad(404, "unknown snapshot")
    return next(s for s in snapshots.load(r["run_id"]) if s["id"] == snap_id)


@router.post("/signals/{sid}/approve")
def approve(sid: int):
    try:
        return {"signal": signal_row(_raw(pro_state.desk().approve(sid, now())))}
    except KeyError:
        bad(404, "unknown signal")
    except ValueError as e:
        bad(409, str(e))


@router.post("/signals/{sid}/reject")
def reject(sid: int, body: dict | None = Body(None)):
    try:
        reason = ((body or {}).get("reason") or "manual").strip()[:200]
        return {"signal": signal_row(_raw(pro_state.desk().reject(sid, now(), reason)))}
    except KeyError:
        bad(404, "unknown signal")
    except ValueError as e:
        bad(409, str(e))


def _raw(sig: dict) -> dict:
    return one("SELECT * FROM signals WHERE id = ?", (sig["id"],))
