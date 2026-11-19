"""Streamlit UI smoke tests with AppTest (mocked pipeline — no API calls)."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

import config
from tests.test_orchestrator import RoleFake, synthetic_prices  # noqa: F401  (fixture)

APP = str(config.ROOT / "app.py")


@pytest.fixture()
def fake_pipeline(monkeypatch, synthetic_prices):  # noqa: F811
    import orchestrator
    from core.llm import LLM
    from tests.fakes import FakeExecutor

    real = orchestrator.run_prediction

    async def fake_run(ticker, mode="swing", horizon="1M", **kw):
        kw.pop("llm", None)
        return await real(ticker, mode, horizon, llm=LLM("gemini", client=RoleFake()), executor=FakeExecutor(),
                          seed=1, **kw)

    monkeypatch.setattr(orchestrator, "run_prediction", fake_run)


def _texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [e.label for e in at.expander]
    parts += [m.label for m in at.metric]
    return "\n".join(str(p) for p in parts)


def test_app_initial_render(fresh_db):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Predict" in b.label for b in at.button)
    assert "Not investment advice" in "\n".join(w.value for w in at.warning)


def test_predict_renders_every_section(fresh_db, fake_pipeline):
    at = AppTest.from_file(APP, default_timeout=120).run()
    next(b for b in at.button if "Predict" in b.label).click().run()
    assert not at.exception, at.exception
    t = _texts(at)
    for needle in ["NVDA · Swing · 1M", "Bulls win by a whisker", "Bear (P10)", "Base (P50)", "Bull (P90)",
                   "P(up)", "Confidence", "Agent scores", "Debate", "Tool-call trace", "Sources"]:
        assert needle in t, needle
    assert len(at.get("plotly_chart")) >= 2
    chat = at.get("chat_message")
    assert [c.proto.name for c in chat] == ["Bull", "Bear", "Bull"]
    for agent in config.AGENTS:
        assert f"**{agent.title()}**" in t


def test_scoreboard_tab_and_replay(fresh_db, fake_pipeline):
    import anyio

    import orchestrator

    anyio.run(lambda: orchestrator.run_prediction("BTC-USD", "scalp", "15m"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    sel = next(s for s in at.selectbox if "Replay" in s.label)
    sel.select(sel.options[1]).run()
    next(b for b in at.button if b.label == "Load").click().run()
    assert not at.exception
    assert "BTC-USD · Scalp · 15m" in _texts(at)
    # sidebar follows the replayed prediction
    assert at.text_input(key="ticker").value == "BTC-USD"
    assert at.radio(key="mode").value == "scalp"
    assert at.selectbox(key="horizon").value == "15m"
    assert "Evaluated / pending" in _texts(at)


def test_replay_query_param_syncs_sidebar(fresh_db, fake_pipeline):
    import anyio

    import orchestrator

    p = anyio.run(lambda: orchestrator.run_prediction("GC=F", "swing", "3M"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["replay"] = str(p.id)
    at.run()
    assert not at.exception
    assert at.text_input(key="ticker").value == "GC=F"
    assert at.radio(key="mode").value == "swing" and at.selectbox(key="horizon").value == "3M"
    # switching mode afterwards still works (horizon falls back to a valid option)
    at.radio(key="mode").set_value("scalp").run()
    assert not at.exception and at.selectbox(key="horizon").value == "15m"
