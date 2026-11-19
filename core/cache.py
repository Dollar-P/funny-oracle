"""Tiny SQLite TTL cache (table `cache` in data/oracle.db)."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from core.db import connect


def get(key: str) -> Any | None:
    with connect() as conn:
        row = conn.execute("SELECT value_json, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
    if row is None or row[1] < time.time():
        return None
    return json.loads(row[0])


def set(key: str, value: Any, ttl: float) -> None:  # noqa: A001 - module-level API
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value_json, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), time.time() + ttl),
        )


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """Return cached value or compute, store, and return it. Errors are not cached."""
    hit = get(key)
    if hit is not None:
        return hit
    value = fn()
    if not (isinstance(value, dict) and value.get("error")):
        set(key, value, ttl)
    return value
