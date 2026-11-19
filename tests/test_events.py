"""R1: event hook (REDESIGN §2.2) + per-run model override, with the role-aware fake Gemini."""
from __future__ import annotations

import anyio
import pytest

import config
import orchestrator
from core import cost
from core.cost import BudgetExceeded, BudgetGuard
from core.events import EVENT_TYPES, STAGES, EventSink
from core.llm import LLM
from tests.fakes import FakeExecutor
from tests.test_orchestrator import RoleFake, synthetic_prices  # noqa: F401  (fixture)


def run_with_events(fake=None, ticker="NVDA", mode="swing", horizon="1M", llm=None, **kw):
    events: list[dict] = []

    async def on_event(ev):
        events.append(ev)

    llm = llm or LLM("gemini", client=fake or RoleFake())
    pred = anyio.run(lambda: orchestrator.run_prediction(ticker, mode, horizon, llm=llm, executor=FakeExecutor(),
                                                         seed=7, on_event=on_event, **kw))
    return pred, events


def types_of(events):
    return [e["type"] for e in events]


def test_swing_event_order(fresh_db, synthetic_prices):
    pred, ev = run_with_events()
    t = types_of(ev)
    assert set(t) <= set(EVENT_TYPES)
    # envelope: run_id / ts / seq on every event, seq strictly increasing from 1
    assert all(e["run_id"] == pred.run_id for e in ev)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))
    assert all(isinstance(e["ts"], int) for e in ev)
    # first / last
    assert t[0] == "run_started" and t[-1] == "run_done"
    assert ev[0]["agents"] == config.AGENTS and ev[0]["models"]["analyst"] == pred.models["analyst"]
    # stages in pipeline order
    assert [e["name"] for e in ev if e["type"] == "stage"] == list(STAGES)
    # per agent: started → tool_call → tool_result → done, all inside the analysts stage
    i_analysts = next(i for i, e in enumerate(ev) if e["type"] == "stage" and e["name"] == "analysts")
    i_aggregate = next(i for i, e in enumerate(ev) if e["type"] == "stage" and e["name"] == "aggregate")
    for a in config.AGENTS:
        mine = [(i, e["type"]) for i, e in enumerate(ev) if e.get("agent") == a]
        kinds = [k for _, k in mine]
        assert kinds[0] == "agent_started" and kinds[-1] == "agent_done", (a, kinds)
        assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("agent_done")
        assert all(i_analysts < i < i_aggregate for i, _ in mine)
    done = [e for e in ev if e["type"] == "agent_done"]
    assert {e["report"]["agent"] for e in done} == set(config.AGENTS)
    # aggregate → debate (3 turns, in order) → judge → quant → run_done
    order = [k for k in t if k in ("aggregate", "debate_turn", "judge", "quant", "run_done")]
    assert order == ["aggregate", "debate_turn", "debate_turn", "debate_turn", "judge", "quant", "run_done"]
    turns = [e for e in ev if e["type"] == "debate_turn"]
    assert [(e["side"], e["turn"]) for e in turns] == [("bull", 1), ("bear", 2), ("bull", 3)]
    assert turns[0]["responding_to"] is None and turns[1]["responding_to"]
    agg = next(e for e in ev if e["type"] == "aggregate")
    assert agg["s_raw"] == pytest.approx(pred.s_raw)
    j = next(e for e in ev if e["type"] == "judge")
    assert j["verdict"]["headline"] == "Bulls win by a whisker" and j["s_final"] == pytest.approx(pred.s_final)
    q = next(e for e in ev if e["type"] == "quant")
    assert q["p50"] == pytest.approx(pred.forecast.p50) and q["fan"]["p50"]
    final = ev[-1]
    assert final["prediction_id"] == pred.id and final["prediction"]["ticker"] == "NVDA"
    # cost updates: one per agent + after debate + final
    cu = [e for e in ev if e["type"] == "cost_update"]
    assert len(cu) == 5 + 2 and cu[-1]["run_cost_thb"] == pytest.approx(cost.usd_to_thb(pred.cost_usd))
    # search events from grounding metadata are not produced by RoleFake (no grounding) — fine


def test_scalp_has_no_news_or_debate_stage(fresh_db, synthetic_prices):
    _, ev = run_with_events(ticker="BTC-USD", mode="scalp", horizon="15m")
    stages = [e["name"] for e in ev if e["type"] == "stage"]
    assert stages == ["analysts", "aggregate", "judge", "quant", "done"]
    assert "debate_turn" not in types_of(ev)


def test_failed_agent_emits_agent_failed(fresh_db, synthetic_prices):
    _, ev = run_with_events(RoleFake(fail_agents={"valuation"}))
    f = [e for e in ev if e["type"] == "agent_failed"]
    assert [e["agent"] for e in f] == ["valuation"] and f[0]["reason"] == "error"


def test_timeout_reason(fresh_db, synthetic_prices, monkeypatch):
    import asyncio

    from agents.technical import TechnicalAgent

    async def slow(self, ctx):
        await asyncio.sleep(5)

    monkeypatch.setattr(TechnicalAgent, "run", slow)
    monkeypatch.setattr(config, "AGENT_TIMEOUT_S", 0.05)
    _, ev = run_with_events()
    f = next(e for e in ev if e["type"] == "agent_failed")
    assert f["agent"] == "technical" and f["reason"] == "timeout"


def test_news_researcher_events(fresh_db, synthetic_prices):
    _, ev = run_with_events(RoleFake(search_ok=False))
    nb = [e for e in ev if e["type"] == "news_brief"]
    assert nb and nb[0]["bullets"] == ["News A"]
    i_news = next(i for i, e in enumerate(ev) if e.get("name") == "news")
    i_an = next(i for i, e in enumerate(ev) if e.get("name") == "analysts")
    assert i_news < ev.index(nb[0]) < i_an


def test_no_callback_means_no_change(fresh_db, synthetic_prices):
    llm = LLM("gemini", client=RoleFake())
    p = anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=llm, executor=FakeExecutor(), seed=7))
    assert p.id is not None and p.verdict is not None and len(p.debate) == 3


def test_broken_callback_never_breaks_the_run(fresh_db, synthetic_prices):
    async def boom(ev):
        raise RuntimeError("subscriber died")

    llm = LLM("gemini", client=RoleFake())
    p = anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=llm, executor=FakeExecutor(),
                                                      seed=7, on_event=boom))
    assert p.id is not None


def test_budget_refusal_emits_fatal_error(fresh_db, synthetic_prices):
    events = []

    async def on_event(ev):
        events.append(ev)

    llm = LLM("gemini", client=RoleFake(), guard=BudgetGuard(budget_thb=0.0))
    with pytest.raises(BudgetExceeded):
        anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=llm, executor=FakeExecutor(),
                                                      on_event=on_event, run_id="fixed"))
    assert types_of(events) == ["error"] and events[0]["fatal"] and events[0]["kind"] == "budget"
    assert events[0]["run_id"] == "fixed"


def test_model_override_used_and_stored(fresh_db, synthetic_prices):
    fake = RoleFake()
    p, ev = run_with_events(fake, analyst_model="gemini-3.5-flash-lite", judge_model="gemini-3.8-flash",
                            duel_id="d1", run_id="rid")
    assert p.models == {"analyst": "gemini-3.5-flash-lite", "judge": "gemini-3.8-flash"}
    assert p.run_id == "rid" and p.duel_id == "d1"
    used = {}
    for model, _contents, cfg in fake.requests:
        si = (cfg.system_instruction or "") if cfg else ""
        role = "judge" if ("JUDGE" in si or "BULL" in si or "BEAR" in si) else "analyst"
        if cfg is not None and si:
            used.setdefault(role, set()).add(model)
    assert used == {"analyst": {"gemini-3.5-flash-lite"}, "judge": {"gemini-3.8-flash"}}
    from core.db import connect

    with connect() as conn:
        row = conn.execute("SELECT analyst_model, judge_model, duel_id, cost_actual_usd FROM predictions WHERE id = ?",
                           (p.id,)).fetchone()
    assert tuple(row)[:3] == ("gemini-3.5-flash-lite", "gemini-3.8-flash", "d1") and row[3] is not None
    # usage rows are recorded under the overridden models
    with connect() as conn:
        models = {r[0] for r in conn.execute("SELECT DISTINCT model FROM llm_usage WHERE run_id = 'rid'")}
    assert models == {"gemini-3.5-flash-lite", "gemini-3.8-flash"}


def test_override_falls_back_to_env_per_role(fresh_db, synthetic_prices, monkeypatch):
    monkeypatch.setattr(config, "JUDGE_MODEL", "gemini-3.8-flash")
    p, _ = run_with_events(analyst_model="gemini-3.5-flash-lite")
    assert p.models == {"analyst": "gemini-3.5-flash-lite", "judge": "gemini-3.8-flash"}


def test_cross_provider_override_rejected(fresh_db, synthetic_prices):
    with pytest.raises(ValueError, match="one provider per run"):
        run_with_events(analyst_model="gemini-3.5-flash-lite", judge_model="claude-haiku-5-5")


def test_provider_for():
    assert config.provider_for("claude-opus-5-5") == "claude"
    assert config.provider_for("gemini-3.8-flash") == "gemini"
    assert config.provider_for(None, "claude") == "claude"


def test_budget_estimate_per_model(fresh_db):
    g = BudgetGuard()
    ref = config.DEFAULT_ESTIMATE_USD["swing"]
    assert g.estimate_usd("swing", "gemini-3.5-flash-lite") == pytest.approx(ref)   # cheaper → never below 1×
    p, r = cost.price_for("claude-opus-5-5"), cost.price_for(config.REFERENCE_PRICE_MODEL)
    assert g.estimate_usd("swing", "claude-opus-5-5") == pytest.approx(ref * (p["in"] + p["out"]) / (r["in"] + r["out"]))
    from core.db import connect

    with connect() as conn:
        for m, c in [("gemini-3.5-flash-lite", 0.02), ("gemini-3.8-flash", 0.08)]:
            conn.execute("INSERT INTO predictions (created_at,ticker,asset_type,mode,horizon,target_time,cost_usd,"
                         "analyst_model) VALUES ('x','NVDA','stock','swing','1M','x',?,?)", (c, m))
    assert g.estimate_usd("swing", "gemini-3.5-flash-lite") == pytest.approx(0.02 * 1.25)
    assert g.estimate_usd("swing", "gemini-3.8-flash") == pytest.approx(0.08 * 1.25)
    assert g.estimate_usd("swing") == pytest.approx(0.05 * 1.25)


def test_event_sink_rejects_unknown_type():
    got = []

    async def cb(e):
        got.append(e)

    with pytest.raises(ValueError):
        anyio.run(lambda: EventSink(cb, "r").emit("nope"))
    anyio.run(lambda: EventSink(None, "r").emit("nope"))  # null sink: no-op, no validation cost


def test_grounded_search_emits_search_events(fresh_db):
    from agents.base import Context
    from agents.macro import MacroAgent
    from tests.fakes import AGENT_JSON, FakeGemini, text_response

    got = []

    async def cb(e):
        got.append(e)

    llm = LLM("gemini", client=FakeGemini([text_response(AGENT_JSON, grounding=["https://a", "https://b"])]))
    llm.events = EventSink(cb, "r")
    ctx = Context(ticker="NVDA", asset_type="stock", mode="swing", horizon="1M", interval="1d")
    anyio.run(lambda: MacroAgent(llm, "gemini-3.5-flash-lite", FakeExecutor(), enable_search=True).run(ctx))
    assert [(e["type"], e["agent"]) for e in got] == [("search", "macro"), ("search", "macro")]
