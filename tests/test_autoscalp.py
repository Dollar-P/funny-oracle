from __future__ import annotations

import pytest

import orchestrator
from core import autoscalp
from core.autoscalp import AutoScalpState, tick
from core.cost import BudgetExceeded
from tests.test_orchestrator import RoleFake, synthetic_prices  # noqa: F401


@pytest.fixture()
def fake_run(monkeypatch, synthetic_prices):  # noqa: F811
    from core.llm import LLM
    from tests.fakes import FakeExecutor

    real = orchestrator.run_prediction
    calls = []

    async def fake(ticker, mode, horizon, **kw):
        calls.append((ticker, mode, horizon, kw.get("source")))
        return await real(ticker, mode, horizon, llm=LLM("gemini", client=RoleFake()), executor=FakeExecutor(),
                          source=kw.get("source", "x"))

    monkeypatch.setattr(orchestrator, "run_prediction", fake)
    return calls


def test_tick_runs_when_due_and_respects_interval_and_cap(fresh_db, fake_run):
    st = AutoScalpState(max_runs=2, interval_s=900)
    assert tick(st).startswith("#")
    assert fake_run == [("BTC-USD", "scalp", "15m", "auto_scalp")]
    assert tick(st) is None and len(fake_run) == 1     # not due yet
    st.last_run -= 901
    tick(st)
    assert st.runs == 2 and "cap" in st.stopped_reason
    st.last_run -= 901
    tick(st)
    assert len(fake_run) == 2                             # capped


def test_tick_stops_on_budget(fresh_db, monkeypatch):
    async def broke(*a, **k):
        raise BudgetExceeded("no money")

    monkeypatch.setattr(orchestrator, "run_prediction", broke)
    st = AutoScalpState(max_runs=5, interval_s=1)
    tick(st)
    assert st.stopped_reason == "no money" and st.runs == 0
    assert not st.due()
