"""Tool snapshots (PRO.md §4): every tool result used by a Pro decision, compressed, keyed by run + tool + args.

Enables audit, exact replay, and backtests without refetching. The Arena does not snapshot (unchanged).
"""
from __future__ import annotations

import hashlib
import json
import zlib
from datetime import datetime, timezone

from core.db import connect


def args_hash(tool: str, args: dict | None) -> str:
    return hashlib.sha256(json.dumps([tool, args or {}], sort_keys=True, default=str).encode()).hexdigest()[:16]


def save(run_id: str, tool: str, args: dict | None, data: dict) -> None:
    as_of = (data or {}).get("as_of") if isinstance(data, dict) else None
    blob = zlib.compress(json.dumps(data, default=str, separators=(",", ":")).encode("utf-8"), 6)
    with connect() as conn:
        conn.execute("INSERT INTO tool_snapshots (run_id, tool, args_hash, as_of, payload_zlib) VALUES (?,?,?,?,?)",
                     (run_id, tool, args_hash(tool, args), as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      blob))


def load(run_id: str, tool: str | None = None, args: dict | None = None) -> list[dict]:
    q, a = "SELECT id, tool, args_hash, as_of, payload_zlib FROM tool_snapshots WHERE run_id = ?", [run_id]
    if tool:
        q += " AND tool = ?"
        a.append(tool)
        if args is not None:
            q += " AND args_hash = ?"
            a.append(args_hash(tool, args))
    with connect() as conn:
        rows = conn.execute(q + " ORDER BY id", a).fetchall()
    return [{"id": r["id"], "tool": r["tool"], "args_hash": r["args_hash"], "as_of": r["as_of"],
             "data": json.loads(zlib.decompress(r["payload_zlib"]))} for r in rows]


class SnapshotExecutor:
    """Wraps a ToolExecutor and stores every successful result for the run."""

    def __init__(self, base, run_id: str):
        self.base, self.run_id = base, run_id

    async def list_tools(self):
        return await self.base.list_tools()

    async def call(self, name, args=None):
        data, tc = await self.base.call(name, args)
        if tc.error is None:
            try:
                save(self.run_id, name, args, data)
            except Exception:  # snapshotting must never break a run
                pass
        return data, tc
