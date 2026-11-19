"""Shared fixtures: every test session uses a throwaway SQLite DB."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def _isolated_db(tmp_path_factory):
    import config

    db = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["ORACLE_DB"] = str(db)  # inherited by the stdio MCP subprocess
    config.DB_PATH = db
    yield


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Per-test empty DB (for spend/budget assertions)."""
    import config

    db = tmp_path / "fresh.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    yield db
