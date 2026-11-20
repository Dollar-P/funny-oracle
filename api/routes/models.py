"""GET /api/models — configured models with price, speed tier, search support and per-mode cost estimate."""
from __future__ import annotations

import json

from fastapi import APIRouter

import config
from core import cache, cost
from core.cost import BudgetGuard
from core.db import connect

router = APIRouter()

SCALP_REASON = ("Local models are too slow for Scalp mode (a 15-minute horizon would be over before the show "
                "ends). Use Swing, or pick a Gemini model for Scalp.")

LABELS = {
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5-5": "Claude Opus 5.5",
}
# Guess when nothing has been observed yet: 3 = fast, 2 = medium, 1 = slow
SPEED_GUESS = {"flash-lite": 3, "haiku": 3, "flash": 2, "sonnet": 2, "opus": 1}


def _label(mid: str) -> str:
    return LABELS.get(mid) or mid.replace("-", " ").title()


def _speed_guess(mid: str) -> int:
    for k, v in SPEED_GUESS.items():  # ordered: flash-lite before flash
        if k in mid:
            return v
    return 2


def observed_latency() -> dict[str, dict]:
    """avg elapsed_s per (analyst_model, mode) from stored predictions (older rows: model from llm_usage)."""
    with connect() as conn:
        rows = conn.execute("SELECT p.id, p.mode, p.analyst_model, p.prediction_json FROM predictions p"
                            " WHERE p.prediction_json IS NOT NULL").fetchall()
        usage = conn.execute("SELECT prediction_id, model, COUNT(*) n FROM llm_usage WHERE prediction_id IS NOT NULL"
                             " GROUP BY prediction_id, model").fetchall()
    top: dict[int, tuple[str, int]] = {}
    for u in usage:
        if u["prediction_id"] not in top or u["n"] > top[u["prediction_id"]][1]:
            top[u["prediction_id"]] = (u["model"], u["n"])
    out: dict[str, dict] = {}
    for r in rows:
        model = r["analyst_model"] or (top.get(r["id"]) or (None,))[0]
        if not model:
            continue
        try:
            el = float(json.loads(r["prediction_json"]).get("elapsed_s") or 0)
        except (ValueError, TypeError):
            continue
        if el <= 0:
            continue
        d = out.setdefault(model, {}).setdefault(r["mode"], [])
        d.append(el)
    return {m: {mode: round(sum(v) / len(v), 1) for mode, v in modes.items()} for m, modes in out.items()}


def _speed_tier(mid: str, lat: dict) -> int:
    swing = lat.get("swing")
    if swing is None:
        return _speed_guess(mid)
    return 3 if swing < 20 else 2 if swing < 45 else 1


def _supports_search(provider: str, mid: str) -> bool | None:
    if provider == "claude":
        return True
    for feat in ("search+functions", "search"):
        hit = cache.get(f"cap:gemini:{mid}:{feat}")
        if hit is not None and hit.get("ok"):
            return True
    hits = [cache.get(f"cap:gemini:{mid}:{f}") for f in ("search+functions", "search")]
    if all(h is not None for h in hits):
        return False
    return None  # not probed yet


def configured_providers() -> set[str]:
    p = set()
    if config.GEMINI_API_KEY or config.MOCK_LLM:
        p.add("gemini")
    if config.ANTHROPIC_API_KEY or config.CLAUDE_ANALYST_MODEL and config.MOCK_LLM:
        p.add("claude")
    return p


_LOCAL_CACHE: dict = {"at": 0.0, "ids": None, "error": None}
LOCAL_CACHE_S = 60


def local_server_models() -> tuple[list[str] | None, str | None]:
    """Chat models the LM Studio server at LOCAL_BASE_URL reports (cached 60 s). (ids, error)."""
    import time

    import httpx

    if time.time() - _LOCAL_CACHE["at"] < LOCAL_CACHE_S:
        return _LOCAL_CACHE["ids"], _LOCAL_CACHE["error"]
    ids, err = None, None
    try:
        r = httpx.get(f"{config.LOCAL_BASE_URL}/models", timeout=3.0)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", []) if "embed" not in m.get("id", "").lower()]
    except Exception as e:
        err = f"LM Studio not reachable at LOCAL_BASE_URL ({type(e).__name__})"
    _LOCAL_CACHE.update(at=time.time(), ids=ids, error=err)
    return ids, err


def local_models(lat: dict) -> list[dict]:
    """Local models for the pickers: free, slow, no Scalp (LOCAL_LLM.md "Where local fits")."""
    import os

    ids, err = local_server_models()
    if ids is None:
        # only advertise an unreachable server when the user configured one
        if not (os.getenv("LOCAL_BASE_URL") or config.LLM_PROVIDER == "local"):
            return []
        ids, available = [config.LOCAL_MODEL], False
    else:
        available = True
    ordered = sorted(ids, key=lambda m: (m != config.LOCAL_MODEL, m))
    out = []
    for mid in ordered:
        full = config.LOCAL_PREFIX + mid
        obs = lat.get(full, {})
        out.append({
            "id": full, "provider": "local", "label": f"{mid.split('/')[-1]} (local)",
            "price_in": 0.0, "price_out": 0.0, "price_search": 0.0,
            "speed_tier": 1, "avg_elapsed_s": obs,
            "supports_search": False,  # uses the web_search / fetch_url MCP tools instead of grounding
            "est_cost_thb": {m: 0.0 for m in config.HORIZONS},
            "is_default": config.LLM_PROVIDER == "local" and mid == config.LOCAL_MODEL,
            "scalp_ok": False, "scalp_reason": SCALP_REASON,
            "available": available, "unavailable_reason": None if available else err,
            "think_presets": list(config.THINK_PRESETS), "default_think_preset": config.LOCAL_THINK_PRESET,
        })
    return out


def list_models() -> list[dict]:
    providers = configured_providers()
    lat = observed_latency()
    guard = BudgetGuard()
    default = config.models_for("swing")["analyst"]
    out = []
    for mid in config.PRICES:
        prov = config.provider_for(mid)
        if prov not in providers or prov == "local":
            continue
        price = cost.price_for(mid)
        out.append({
            "id": mid, "provider": prov, "label": _label(mid),
            "price_in": price["in"], "price_out": price["out"], "price_search": price["search"],
            "speed_tier": _speed_tier(mid, lat.get(mid, {})),
            "avg_elapsed_s": lat.get(mid, {}),
            "supports_search": _supports_search(prov, mid),
            "est_cost_thb": {m: round(cost.usd_to_thb(guard.estimate_usd(m, mid)), 2) for m in config.HORIZONS},
            "is_default": mid == default,
            "scalp_ok": True, "available": True,
        })
    out.sort(key=lambda m: (m["provider"] != config.LLM_PROVIDER, m["price_in"] + m["price_out"]))
    return out + local_models(lat)


@router.get("/models")
def models():
    return {"models": list_models(), "default": config.models_for("swing"),
            "default_scalp": config.models_for("scalp"), "mock": config.MOCK_LLM}
