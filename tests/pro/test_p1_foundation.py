"""P1: prompt versions, richer agent/judge output, temperature, additive migration, experiments, cutoffs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import anyio
import numpy as np
import pandas as pd
import pytest

import config
import orchestrator
from agents.base import PROMPTS, load_prompt, prompt_versions
from core import data_quality, experiments
from core.llm import LLM, strict_schema
from core.schemas import (AgentOutput, AgentOutputPro, DebateOutput, NewsBrief, STRICT_SCHEMAS, VerdictOutput,
                          VerdictPro)
from tests.fakes import FakeExecutor
from tests.pro.fakes_pro import LocalRoleFake, ProRoleFake
from tests.test_orchestrator import RoleFake, synthetic_prices  # noqa: F401  (fixture)

# sha256[:16] of agents/prompts/*.md on main before the move — v1 must stay byte-identical (the Arena uses it)
V1_HASHES = {
    "_analyst.md": "2f2de9d51ba7ad64", "bear.md": "2f99ab8bb3c81b00", "bull.md": "4d254b89d308725a",
    "bull_reply.md": "778a4d27210a973b", "business.md": "c7db9550668da968", "judge.md": "1c424bb921cc09f9",
    "macro.md": "2f6e3621597d510b", "news_researcher.md": "4386ce6973a1caff", "psychology.md": "583d9a588cda416a",
    "technical.md": "e7bcedd12ce16514", "valuation.md": "b30de8b987e7a32e",
}
# sha256[:16] of strict_schema(...) for the Arena's LLM-facing schemas on main
ARENA_SCHEMA_HASHES = {
    AgentOutput: "ca8e5d6caf33a46b", VerdictOutput: "e4a5519e67a567f3", DebateOutput: "35a7871d7a3c98a4",
    NewsBrief: "3ed5dc6af8640546", STRICT_SCHEMAS[AgentOutput]: "2388f8b3aefdce23",
    STRICT_SCHEMAS[VerdictOutput]: "399d7d78e405bb60",
}


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def test_v1_prompts_are_byte_identical_to_main():
    files = {p.name: _h(p.read_bytes()) for p in (PROMPTS / "v1").glob("*.md")}
    assert files == V1_HASHES
    assert {"v1", "v2"} <= set(prompt_versions())
    assert not list(PROMPTS.glob("*.md"))  # nothing left at the old location


def test_v2_prompts_extend_v1():
    for name in V1_HASHES:
        stem = name[:-3]
        assert load_prompt(stem, "v2").startswith(load_prompt(stem, "v1").rstrip("\n"))
    assert "scenario_probs" in load_prompt("_analyst", "v2") and "catalysts" in load_prompt("judge", "v2")
    with pytest.raises(ValueError):
        load_prompt("judge", "v9")


def test_arena_llm_schemas_unchanged():
    for schema, h in ARENA_SCHEMA_HASHES.items():
        assert _h(json.dumps(strict_schema(schema), sort_keys=True).encode()) == h, schema.__name__


def test_pro_schemas_have_new_fields_required():
    s = strict_schema(AgentOutputPro)
    assert {"scenario_probs", "key_levels", "invalidation"} <= set(s["required"])
    v = strict_schema(VerdictPro)
    assert {"thesis", "invalidation", "catalysts", "no_trade_reason"} <= set(v["required"])


def _sys_prompts(fake) -> list[str]:
    return [(c.system_instruction or "") for _m, _c, c in fake.requests if c is not None and not isinstance(_c, str)]


def test_arena_run_unchanged(synthetic_prices):  # noqa: F811
    fake = RoleFake()
    p = anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=LLM("gemini", client=fake),
                                                      executor=FakeExecutor(), seed=1, save=False))
    assert p.prompt_version == "v1" and p.temperature is None and p.degraded == []
    assert all(r.scenario_probs is None and r.invalidation is None for r in p.reports)
    assert p.verdict and p.verdict.thesis is None and p.verdict.catalysts == []
    prompts = _sys_prompts(fake)
    assert prompts and not any("Desk (research) rules" in s for s in prompts)
    assert all(getattr(c, "temperature", None) is None for _m, _c, c in fake.requests if c is not None)


def test_pro_run_on_gemini_has_new_fields(synthetic_prices):  # noqa: F811
    fake = ProRoleFake()
    exp = experiments.get_or_create({"test": "p1"})
    p = anyio.run(lambda: orchestrator.run_prediction(
        "NVDA", "swing", "1M", llm=LLM("gemini", client=fake), executor=FakeExecutor(), seed=1,
        prompt_version="v2", temperature=0.2, experiment_id=exp, source="pro"))
    assert p.prompt_version == "v2" and p.temperature == 0.2 and p.experiment_id == exp
    ok = [r for r in p.reports if r.ok]
    assert len(ok) == 5
    for r in ok:
        sp = r.scenario_probs
        assert sp is not None and abs(sp.bull + sp.base + sp.bear - 1) < 1e-9
        assert r.key_levels.support == [95.0, 90.0] and r.invalidation
    v = p.verdict
    assert v.thesis and v.invalidation and v.catalysts[0].event == "FOMC" and v.catalysts[0].impact == "high"
    assert v.no_trade_reason is None  # "" → None
    temps = {getattr(c, "temperature", None) for _m, _c, c in fake.requests if c is not None and not isinstance(_c, str)}
    assert temps == {0.2}
    from core.db import connect

    with connect() as conn:
        row = conn.execute("SELECT prompt_version, experiment_id FROM predictions WHERE id = ?", (p.id,)).fetchone()
    assert tuple(row) == ("v2", exp)
    from core import scoreboard

    assert scoreboard.load(p.id).reports[0].scenario_probs is not None  # survives the JSON round trip


def test_pro_run_on_mocked_local_backend(synthetic_prices, fresh_db):  # noqa: F811
    fake = LocalRoleFake()
    llm = LLM("local", client=fake, think_preset="quick")
    p = anyio.run(lambda: orchestrator.run_prediction(
        "NVDA", "swing", "1M", llm=llm, executor=FakeExecutor(), seed=1, save=False,
        analyst_model="local:prism-ml/bonsai-27b", prompt_version="v2", temperature=0.1))
    ok = [r for r in p.reports if r.ok]
    assert len(ok) == 5 and all(r.scenario_probs and r.key_levels and r.invalidation for r in ok)
    assert p.verdict.thesis and p.verdict.catalysts
    assert {req["temperature"] for req in fake.requests} == {0.1}
    finals = [req for req in fake.requests if "response_format" in req]
    assert any("thesis" in req["response_format"]["json_schema"]["schema"]["required"] for req in finals)


def test_local_arena_run_keeps_v1_and_default_temperature(synthetic_prices, fresh_db):  # noqa: F811
    fake = LocalRoleFake()
    p = anyio.run(lambda: orchestrator.run_prediction(
        "NVDA", "swing", "1M", llm=LLM("local", client=fake), executor=FakeExecutor(), seed=1, save=False,
        analyst_model="local:prism-ml/bonsai-27b"))
    assert p.prompt_version == "v1" and all(r.scenario_probs is None for r in p.reports)
    assert {req["temperature"] for req in fake.requests} == {0.2}


def test_unknown_prompt_version_fails_before_any_call(synthetic_prices):  # noqa: F811
    fake = RoleFake()
    with pytest.raises(ValueError):
        anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=LLM("gemini", client=fake),
                                                      executor=FakeExecutor(), save=False, prompt_version="v7"))
    assert fake.requests == []


def test_migration_is_additive(tmp_path, monkeypatch):
    from core import db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)  # the pre-migration layout
    conn.execute("INSERT INTO predictions (created_at, ticker, asset_type, mode, horizon, target_time, s_final)"
                 " VALUES ('2026-01-01', 'NVDA', 'stock', 'swing', '1M', '2026-02-01', 0.3)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(predictions)")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = c.execute("SELECT ticker, s_final, prompt_version, experiment_id FROM predictions").fetchone()
    assert {"prompt_version", "experiment_id", "analyst_model"} <= cols
    assert {"experiments", "signals", "orders", "fills", "positions", "equity_snapshots", "tool_snapshots",
            "risk_events", "backtests", "reports", "pro_settings", "scheduler_runs"} <= tables
    assert tuple(row) == ("NVDA", 0.3, None, None)


def test_experiments_hash_and_idempotence(fresh_db):
    a = {"prompt_version": "v2", "models": {"analyst": "x"}, "risk": {"r": 0.005}}
    b = {"risk": {"r": 0.005}, "models": {"analyst": "x"}, "prompt_version": "v2"}
    assert experiments.config_hash(a) == experiments.config_hash(b)
    i = experiments.get_or_create(a)
    assert experiments.get_or_create(b) == i
    assert experiments.get_or_create({**a, "risk": {"r": 0.004}}) != i
    assert experiments.get(i)["config"] == a


def test_model_cutoffs_env_override(monkeypatch):
    import importlib

    monkeypatch.setenv("MODEL_CUTOFFS", '{"gemini-3.5-flash-lite": "2026-01-31", "new-model": null}')
    cfg = importlib.reload(config)
    try:
        assert str(cfg.MODEL_CUTOFFS["gemini-3.5-flash-lite"]) == "2026-01-31"
        assert cfg.MODEL_CUTOFFS["new-model"] is None and cfg.MODEL_CUTOFFS["gemini-3.8-flash"] is None
    finally:
        monkeypatch.delenv("MODEL_CUTOFFS")
        importlib.reload(config)


def _bars(n=60, freq="D", end=None, vol=1000.0):
    end = end or datetime(2026, 11, 20, tzinfo=timezone.utc)
    idx = pd.date_range(end=end, periods=n, freq=freq)
    c = np.linspace(100, 110, n)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": vol}, index=idx)


def test_data_quality_flags():
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    assert data_quality.check(_bars(), "swing", "1d", "crypto", now) == []
    assert "stale" in data_quality.check(_bars(end=now - timedelta(days=3)), "swing", "1d", "crypto", now)
    assert "stale" not in data_quality.check(_bars(end=now - timedelta(days=3)), "swing", "1d", "stock", now)
    assert "zero_volume" in data_quality.check(_bars(vol=0.0), "swing", "1d", "stock", now)
    assert "zero_volume" not in data_quality.check(_bars(vol=0.0), "swing", "1d", "gold", now)
    gappy = _bars(60).drop(_bars(60).index[30:45])
    assert "missing_bars" in data_quality.check(gappy, "swing", "1d", "crypto", now)
    assert data_quality.check(None, "swing", "1d", "stock", now) == ["no_data"]
    m = _bars(200, "1min", end=now)
    assert data_quality.check(m, "scalp", "1m", "crypto", now) == []
    assert "stale" in data_quality.check(_bars(200, "1min", end=now - timedelta(minutes=10)), "scalp", "1m",
                                         "crypto", now)
