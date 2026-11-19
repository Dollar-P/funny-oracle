"""API test fixtures: throwaway DB seeded with stored predictions (made by the mocked pipeline), no network."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def synthetic_fetch(ticker, interval="1d", period=None):
    base = {"NVDA": 150.0, "BTC-USD": 60000.0, "GC=F": 3600.0}.get(ticker, 100.0)
    rng = np.random.default_rng(abs(hash((ticker, interval))) % 2**32)
    n = 260
    close = base * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    freq = "D" if interval in ("1d", "1wk") else "30min"
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq=freq)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1.0},
                        index=idx)


def fake_run_prediction_factory():
    """orchestrator.run_prediction with the role-aware fake Gemini + fake MCP executor injected."""
    import orchestrator
    from core.llm import LLM
    from tests.fakes import FakeExecutor
    from tests.test_orchestrator import RoleFake

    real = orchestrator.run_prediction

    async def fake(*a, **kw):
        kw.setdefault("llm", LLM("gemini", client=RoleFake()))
        kw.setdefault("executor", FakeExecutor())
        kw.setdefault("seed", 3)
        return await real(*a, **kw)

    return real, fake


@pytest.fixture(scope="session", autouse=True)
def api_db(tmp_path_factory):
    import config

    db = tmp_path_factory.mktemp("apidb") / "api.db"
    os.environ["ORACLE_DB"] = str(db)
    config.DB_PATH = db
    config.MOCK_SPEED = 0.0
    config.MOCK_LLM = True
    from mcp_server import data_sources as ds

    orig = ds.fetch_ohlcv
    ds.fetch_ohlcv = synthetic_fetch  # only while seeding; per-test patching is in no_network
    import anyio

    try:
        _real, fake = fake_run_prediction_factory()
        seeds = []
        for t, m, h in [("NVDA", "swing", "1M"), ("BTC-USD", "scalp", "15m"), ("GC=F", "swing", "1M"),
                        ("NVDA", "swing", "1W")]:
            p = anyio.run(lambda: fake(t, m, h, analyst_model="gemini-3.5-flash-lite"))
            seeds.append(p.id)
    finally:
        ds.fetch_ohlcv = orig
    yield {"ids": seeds}
    config.MOCK_LLM, config.MOCK_SPEED = False, 1.0


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from api.routes import market
    from mcp_server import data_sources as ds

    monkeypatch.setattr(ds, "fetch_ohlcv", synthetic_fetch)
    monkeypatch.setattr(market, "remote_search", lambda q, limit=8: [])
    monkeypatch.setattr("core.scoreboard.actual_price", lambda *a, **k: None)
    market._QUOTES.clear()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from api.main import app
    from api.runs import MANAGER

    MANAGER.runs.clear()
    MANAGER.duels.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def real_mode(monkeypatch):
    """MOCK_LLM=false, with the orchestrator's LLM + MCP replaced by fakes."""
    import config
    import orchestrator

    monkeypatch.setattr(config, "MOCK_LLM", False)
    _real, fake = fake_run_prediction_factory()
    monkeypatch.setattr(orchestrator, "run_prediction", fake)
