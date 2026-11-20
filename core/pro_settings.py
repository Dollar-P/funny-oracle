"""Pro settings (PRO.md §7 Settings, §3.5): watchlist, schedule, model routing, budgets, risk/cost/signal params.

Stored as one JSON document in `pro_settings` (key "settings"). Anything that can change results is part of the
experiment config (§6.4): changing it — or resetting the paper account — starts a new experiment. Operational
settings (watchlist, schedule, approval mode, budgets, local window) do not.

Routing policy note: the experiment covers the routing *policy* (primary swing model + fallback), so a night
when LM Studio is down and the job falls back to Gemini stays in the same paper account. Every prediction stores
the provider, model, think preset and prompt version it actually used, and the Lab splits metrics by actual model.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import config
from core import experiments
from core.db import connect

STOCKS = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "XOM"]

DEFAULTS: dict = {
    "watchlist": ([{"ticker": t, "asset_type": "stock", "horizons": ["1M"]} for t in STOCKS]
                  + [{"ticker": t, "asset_type": "crypto", "horizons": ["1M"]} for t in ("BTC-USD", "ETH-USD", "SOL-USD")]
                  + [{"ticker": "GC=F", "asset_type": "gold", "horizons": ["1M"]}]),
    "schedule": {
        "stock_after_close_min": 30,          # stocks: this many minutes after the NYSE close
        "crypto_utc": "23:30",                # crypto: fixed UTC time, just before the 00:00 UTC daily bar
        "gold_after_close_min": 15,           # gold: after the CME daily close (17:00 New York)
        "scalp_enabled": False,               # §11: off by default — costs grow fast
        "scalp_tickers": ["BTC-USD", "ETH-USD"],
        "scalp_horizon": "1h",
        "scalp_every_min": 60,
        "tick_s": 60,
    },
    "models": {
        "swing": config.LOCAL_PREFIX + config.LOCAL_MODEL,   # local Quick when LM Studio is reachable
        "swing_think_preset": "quick",
        "swing_fallback": "gemini-3.5-flash-lite",
        "scalp": "gemini-3.5-flash-lite",                     # Gemini only
        "reviewer": "auto",                                   # local if reachable, else the cheapest Gemini
    },
    "local_window": "23:00-07:00",
    "local_tz": "",                           # "" = the machine's local time zone; e.g. "Asia/Bangkok"
    "budgets": {"daily_thb": 10.0},
    "approval": "manual",                     # manual (default) | auto
    "starting_equity": config.PRO_STARTING_EQUITY,
    "account_reset": 0,                       # bump → new experiment (fresh paper account)
    "halted": False,                          # max-drawdown breaker; cleared only by a manual reset
    "prompt_version": config.PRO_PROMPT_VERSION,
    "temperature": config.PRO_TEMPERATURE,
    "risk": copy.deepcopy(config.PRO_RISK),
    "costs": copy.deepcopy(config.PRO_COSTS_BPS),
    "signal": copy.deepcopy(config.PRO_SIGNAL),
}

EXPERIMENT_KEYS = ("prompt_version", "temperature", "risk", "costs", "signal", "starting_equity", "account_reset")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT value_json FROM pro_settings WHERE key = 'settings'").fetchone()
    return _merge(DEFAULTS, json.loads(row[0]) if row else {})


def save(s: dict) -> dict:
    with connect() as conn:
        conn.execute("INSERT INTO pro_settings (key, value_json, updated_at) VALUES ('settings', ?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                     (json.dumps(s), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return s


def update(patch: dict) -> dict:
    return save(_merge(load(), patch))


def experiment_config(s: dict) -> dict:
    cfg = {k: s[k] for k in EXPERIMENT_KEYS}
    cfg["routing"] = {"swing": s["models"]["swing"], "swing_think_preset": s["models"]["swing_think_preset"],
                      "swing_fallback": s["models"]["swing_fallback"], "scalp": s["models"]["scalp"]}
    cfg["weights"] = config.WEIGHTS
    cfg["lambda"] = config.LAMBDA
    cfg["judge_max_adj"] = config.JUDGE_MAX_ADJ
    return cfg


def current_experiment(s: dict | None = None) -> int:
    s = s or load()
    return experiments.get_or_create(experiment_config(s), note="pro")


def reset_account() -> dict:
    """New paper account = new experiment. Also clears the max-drawdown halt."""
    s = load()
    s["account_reset"] = int(s.get("account_reset", 0)) + 1
    s["halted"] = False
    return save(s)
