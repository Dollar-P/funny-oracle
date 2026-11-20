"""Arena vs Pro ("The Desk"): the one place that decides which stored predictions each world may read.

Both worlds share the `predictions` table; `predictions.source` tells them apart. Pro rows have source 'pro';
everything else, including NULL and legacy values ('web', 'cli', 'manual', 'auto_scalp', seeds), is Arena.

Every query that reads `predictions` uses one of these fragments, or is marked `# any-world: <reason>` on the
line of its FROM/JOIN (writers, per-id lookups behind an explicit check, migrations). tests/test_worlds.py
fails when a new query reads `predictions` without either.
"""
from __future__ import annotations

PRO = "pro"
ARENA = "arena"
WORLDS = (ARENA, PRO)


def arena_only(alias: str = "") -> str:
    """SQL condition: Arena rows only (NULL/legacy sources count as Arena)."""
    col = f"{alias}.source" if alias else "source"
    return f"COALESCE({col}, '') != '{PRO}'"


def pro_only(alias: str = "") -> str:
    """SQL condition: Pro rows only."""
    col = f"{alias}.source" if alias else "source"
    return f"{col} = '{PRO}'"


def only(world: str, alias: str = "") -> str:
    if world == ARENA:
        return arena_only(alias)
    if world == PRO:
        return pro_only(alias)
    raise ValueError(f"unknown world {world!r}")


def world_of(source: str | None) -> str:
    return PRO if source == PRO else ARENA


ARENA_ONLY = arena_only()
PRO_ONLY = pro_only()
