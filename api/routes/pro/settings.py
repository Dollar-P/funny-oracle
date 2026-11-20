"""GET/PUT /api/pro/settings · POST /api/pro/account/reset"""
from __future__ import annotations

from fastapi import APIRouter, Body

import config
from api import pro_state
from api.routes.pro.common import bad
from core import experiments, pro_settings

router = APIRouter()

TOP_KEYS = set(pro_settings.DEFAULTS)


def _payload(s: dict) -> dict:
    exp = pro_settings.current_experiment(s)
    e = experiments.get(exp)
    return {"settings": s, "experiment_id": exp, "experiment_hash": e["config_hash"] if e else None,
            "experiment_keys": list(pro_settings.EXPERIMENT_KEYS) + ["routing"]}


@router.get("/settings")
def get_settings():
    return _payload(pro_settings.load())


def _validate(patch: dict) -> None:
    unknown = set(patch) - TOP_KEYS
    if unknown:
        bad(422, f"unknown settings: {sorted(unknown)}")
    if "approval" in patch and patch["approval"] not in ("manual", "auto"):
        bad(422, "approval must be manual or auto")
    m = patch.get("models") or {}
    if m.get("scalp") and config.provider_for(m["scalp"]) == "local":
        bad(422, "scalp needs a Gemini model (local models are too slow for scalp horizons)")
    if m.get("swing_think_preset") and m["swing_think_preset"] not in config.THINK_PRESETS:
        bad(422, f"think preset must be one of {config.THINK_PRESETS}")
    if "local_window" in patch:
        try:
            a, b = patch["local_window"].split("-")
            for x in (a, b):
                h, mm = x.split(":")
                assert 0 <= int(h) < 24 and 0 <= int(mm) < 60
        except Exception:
            bad(422, "local_window must look like 23:00-07:00")
    for w in patch.get("watchlist", []) or []:
        if w.get("asset_type") not in ("stock", "crypto", "gold") or not w.get("ticker"):
            bad(422, f"bad watchlist entry {w}")
    if "halted" in patch:
        bad(422, "the drawdown halt is cleared only by an account reset")


@router.put("/settings")
def put_settings(patch: dict = Body(...)):
    _validate(patch)
    before = pro_settings.current_experiment()
    s = pro_settings.update(patch)
    out = _payload(s)
    out["new_experiment"] = out["experiment_id"] != before
    pro_state.BUS.publish({"type": "settings_changed", "experiment_id": out["experiment_id"],
                           "new_experiment": out["new_experiment"]})
    return out


@router.post("/account/reset")
def reset_account():
    before = pro_settings.current_experiment()
    s = pro_settings.reset_account()
    out = _payload(s)
    pro_state.BUS.publish({"type": "account_reset", "old_experiment_id": before, "experiment_id": out["experiment_id"]})
    return out
