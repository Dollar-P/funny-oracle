"""Experiments (PRO.md §6.4): a config hash of everything that can change results.

Every Pro prediction, signal, order, fill, position and equity snapshot references an experiment id. Changing
any parameter (prompt version, provider, models, think preset, weights, λ, risk, cost, signal params) gives a
new hash → a new experiment, so metrics are never mixed across configurations. One paper account per
experiment; an account reset forces a new experiment (the `reset` counter is part of the config).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from core.db import connect


def canonical(cfg: dict[str, Any]) -> str:
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)


def config_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(cfg).encode("utf-8")).hexdigest()[:16]


def get_or_create(cfg: dict[str, Any], note: str = "") -> int:
    h = config_hash(cfg)
    with connect() as conn:
        row = conn.execute("SELECT id FROM experiments WHERE config_hash = ? ORDER BY id LIMIT 1", (h,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute("INSERT INTO experiments (created_at, config_hash, config_json, note) VALUES (?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(timespec="seconds"), h, canonical(cfg), note))
        return int(cur.lastrowid)


def get(exp_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json"))
    return d


def list_all() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, created_at, config_hash, note FROM experiments ORDER BY id").fetchall()
    return [dict(r) for r in rows]
