"""Demo seeds (data/seeds.json): a fresh clone can run mock mode and /p/<id> replays without any API call."""
from __future__ import annotations

import json

import pytest

import config
from core import scoreboard, seeds
from core.db import connect
from core.schemas import Prediction


def test_shipped_seed_file_is_valid():
    doc = json.loads(seeds.SEEDS_PATH.read_text(encoding="utf-8"))
    assert doc["format"] == seeds.FORMAT and "not investment advice" in doc["note"]
    ids = [p["id"] for p in doc["predictions"]]
    assert set(range(10, 18)) <= set(ids)                       # the demo predictions #10–#17
    for p in doc["predictions"]:
        Prediction.model_validate(p["prediction"])             # every stored Prediction still parses
    modes = {(p["ticker"], p["mode"]) for p in doc["predictions"]}
    assert ("BTC-USD", "scalp") in modes                       # so a mock scalp show has a scalp to replay
    assert any(p["actual_price"] is not None for p in doc["predictions"])  # at least one ✅/❌ example
    assert sum(1 for p in doc["predictions"] if p["duel_id"]) == 2         # the R8 duel pair


def test_load_into_fresh_db_keeps_ids_and_is_idempotent(fresh_db):
    r = seeds.load()
    assert r["skipped"] == [] and 10 in r["loaded"] and 17 in r["loaded"]
    p = scoreboard.load(10)
    assert p is not None and p.ticker == "NVDA" and p.verdict is not None
    with connect() as conn:
        n_agents = conn.execute("SELECT COUNT(*) FROM agent_outputs WHERE prediction_id = 10").fetchone()[0]
        duel = conn.execute("SELECT COUNT(*) FROM predictions WHERE duel_id IS NOT NULL").fetchone()[0]
    assert n_agents == 5 and duel == 2
    again = seeds.load()
    assert again["loaded"] == [] and sorted(again["skipped"]) == sorted(r["loaded"])


def test_load_never_overwrites_an_existing_id(fresh_db):
    with connect() as conn:
        conn.execute("INSERT INTO predictions (id, created_at, ticker, asset_type, mode, horizon, target_time)"
                     " VALUES (10, 'x', 'MINE', 'stock', 'swing', '1M', 'x')")
    r = seeds.load()
    assert 10 in r["skipped"] and 11 in r["loaded"]
    with connect() as conn:
        assert conn.execute("SELECT ticker FROM predictions WHERE id = 10").fetchone()[0] == "MINE"


def test_load_if_empty_only_touches_an_empty_db(fresh_db):
    assert seeds.load_if_empty() is not None                  # fresh clone → loaded
    assert seeds.load_if_empty() is None                      # second start → untouched


def test_export_roundtrip(fresh_db, tmp_path):
    seeds.load()
    out = tmp_path / "s.json"
    info = seeds.export([4, 10], out)
    assert info == {"predictions": 2, "agent_outputs": 3 + 5, "path": str(out)}
    with pytest.raises(ValueError, match="not found"):
        seeds.export([999], out)


def test_fresh_clone_api_serves_seeds_in_mock_mode(fresh_db, monkeypatch):
    """API startup on an empty DB loads the seeds; a mock scalp show and /p/<id> work with no API key."""
    from fastapi.testclient import TestClient

    from api import mock_runner
    from api.main import app

    monkeypatch.setattr(config, "MOCK_LLM", True)
    with TestClient(app) as client:                            # lifespan → seeds.load_if_empty()
        got = client.get("/api/predictions/10").json()
        assert got["prediction"]["ticker"] == "NVDA" and got["prediction"]["models"]["analyst"]
        assert client.get("/api/predictions/4").json()["actual_price"] is not None
        assert {r["id"] for r in client.get("/api/predictions?limit=50").json()["predictions"]} >= set(range(10, 18))
    assert mock_runner.load(mock_runner.pick("BTC-USD", "scalp")).mode == "scalp"
    assert mock_runner.load(mock_runner.pick("GC=F", "swing")).ticker == "GC=F"
