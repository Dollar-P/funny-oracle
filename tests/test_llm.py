"""Agent loop + cost tracking + budget guard, with a scripted fake Gemini (no API calls)."""
from __future__ import annotations

import anyio
import pytest
from google.genai import errors

import config
from agents.base import Context
from agents.technical import TechnicalAgent
from core import cost
from core.cost import BudgetExceeded, BudgetGuard, Usage
from core.llm import LLM, extract_json, strict_schema
from core.schemas import AgentOutput
from tests.fakes import AGENT_JSON, FakeExecutor, FakeGemini, fn_call_response, text_response

MODEL = "gemini-3.5-flash-lite"


def ctx(asset="crypto"):
    return Context(ticker="BTC-USD", asset_type=asset, mode="swing", horizon="1M", interval="1d")


def run(coro):
    return anyio.run(lambda: coro)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('blah {"a": 3} blah') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_strict_schema_requires_all():
    s = strict_schema(AgentOutput)
    assert set(s["required"]) == set(s["properties"]) and s["additionalProperties"] is False


def test_agent_loop_calls_tools_and_logs_cost(fresh_db):
    fake = FakeGemini([
        fn_call_response(("get_indicators", {"ticker": "BTC-USD", "interval": "1d"}),
                         ("get_price_history", {"ticker": "BTC-USD"})),
        text_response(AGENT_JSON),
    ])
    ex = FakeExecutor()
    llm = LLM(provider="gemini", run_id="r1", client=fake)
    rep = run(TechnicalAgent(llm, MODEL, ex).run(ctx()))
    assert rep.ok and rep.score == 0.4 and rep.confidence == 0.7
    assert [c[0] for c in ex.calls] == ["get_indicators", "get_price_history"]
    assert [t.tool for t in rep.trace] == ["get_indicators", "get_price_history"]
    # second request must carry the function responses back
    last = fake.requests[1]["contents"]
    assert any(p.function_response for c in last for p in (c.parts or []))
    # cost: 2 calls logged; (300 in, 100 out, 40 thinking) at flash-lite prices
    expected = 300 * 0.30 / 1e6 + (100 + 40) * 2.50 / 1e6
    assert cost.run_cost("r1") == pytest.approx(expected)
    assert rep.cost_usd == pytest.approx(expected)


def test_tools_not_allowed_are_refused(fresh_db):
    fake = FakeGemini([fn_call_response(("get_fundamentals", {"ticker": "BTC-USD"})), text_response(AGENT_JSON)])
    ex = FakeExecutor()
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, ex).run(ctx()))
    assert ex.calls == []  # never reached MCP
    assert rep.trace[0].error == "not allowed"


def test_invalid_json_triggers_one_format_retry(fresh_db):
    fake = FakeGemini([text_response("I think it goes up."), text_response(AGENT_JSON)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert rep.ok
    assert fake.requests[1]["config"].response_mime_type == "application/json"


def test_invalid_json_twice_marks_agent_failed(fresh_db):
    fake = FakeGemini([text_response("nope"), text_response("still nope")])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert not rep.ok and "invalid JSON" in rep.error


def test_max_turns_forces_final_answer(fresh_db):
    loop = [fn_call_response(("get_indicators", {"ticker": "BTC-USD"})) for _ in range(3)]
    fake = FakeGemini(loop + [text_response(AGENT_JSON)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor(), max_turns=3).run(ctx()))
    assert rep.ok
    final_cfg = fake.requests[-1]["config"]
    assert final_cfg.tool_config.function_calling_config.mode == "NONE"
    assert len(fake.requests) == 4


def test_retry_on_429(fresh_db, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("core.llm.asyncio.sleep", no_sleep)
    err = errors.APIError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
    fake = FakeGemini([err, text_response(AGENT_JSON)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert rep.ok and len(fake.requests) == 2


def test_api_error_becomes_failed_report(fresh_db):
    err = errors.APIError(400, {"error": {"message": "bad", "status": "INVALID_ARGUMENT"}})
    rep = run(TechnicalAgent(LLM("gemini", client=FakeGemini([err])), MODEL, FakeExecutor()).run(ctx()))
    assert not rep.ok and "APIError" in rep.error


def test_grounding_sources_and_search_cost(fresh_db):
    fake = FakeGemini([text_response(AGENT_JSON, grounding=["https://a.example", "https://b.example"])])
    from agents.macro import MacroAgent

    rep = run(MacroAgent(LLM("gemini", run_id="g", client=fake), MODEL, FakeExecutor(), enable_search=True).run(ctx()))
    assert any("https://a.example" in s for s in rep.sources)
    assert rep.trace[0].tool == "google_search"
    assert cost.run_cost("g") == pytest.approx(200 * 0.3 / 1e6 + 110 * 2.5 / 1e6 + 2 * 0.014)
    tools = fake.requests[0]["config"].tools
    assert any(t.google_search is not None for t in tools)


def test_price_table():
    assert cost.price_for("gemini-3.5-flash-lite")["in"] == 0.30
    from datetime import date

    assert cost.price_for("gemini-3.8-flash", date(2026, 12, 1))["out"] == 3.75
    assert cost.price_for("gemini-3.8-flash", date(2027, 1, 2))["out"] == 7.50
    assert cost.price_for("totally-unknown-model") == config.FALLBACK_PRICE


def test_budget_guard_blocks_prediction_and_calls(fresh_db):
    guard = BudgetGuard(budget_thb=1.0)  # ≈ $0.03
    cost.record(Usage(MODEL, input_tokens=50_000, output_tokens=5_000), "x")  # ≈ $0.0275
    with pytest.raises(BudgetExceeded):
        guard.check_prediction("swing")
    cost.record(Usage(MODEL, input_tokens=50_000, output_tokens=5_000), "x")
    with pytest.raises(BudgetExceeded):
        guard.check_call()
    # and the LLM refuses before hitting the API
    fake = FakeGemini([text_response(AGENT_JSON)])
    with pytest.raises(BudgetExceeded):
        run(TechnicalAgent(LLM("gemini", client=fake, guard=guard), MODEL, FakeExecutor()).run(ctx()))
    assert fake.requests == []


def test_budget_estimate_uses_history(fresh_db):
    from core.db import connect

    with connect() as conn:
        for c in (0.1, 0.3):
            conn.execute("INSERT INTO predictions (created_at,ticker,asset_type,mode,horizon,target_time,cost_usd)"
                         " VALUES ('x','T','stock','swing','1M','y',?)", (c,))
    assert BudgetGuard().estimate_usd("swing") == pytest.approx(0.2 * 1.25)
    assert BudgetGuard().estimate_usd("scalp") == config.DEFAULT_ESTIMATE_USD["scalp"]


def test_prompt_rendering_has_no_placeholders():
    from agents import REGISTRY

    for name, cls in REGISTRY.items():
        for asset in ("stock", "crypto", "gold"):
            a = cls(LLM("gemini", client=FakeGemini([])), MODEL, FakeExecutor(), enable_search=True)
            sp = a.system_prompt(ctx(asset))
            assert "{" not in sp.split("JSON")[0], (name, asset)
            assert a.tool_names(ctx(asset)), (name, asset)


def test_search_plus_functions_sets_server_side_flag(fresh_db):
    from agents.macro import MacroAgent

    fake = FakeGemini([text_response(AGENT_JSON)])
    run(MacroAgent(LLM("gemini", client=fake), MODEL, FakeExecutor(), enable_search=True).run(ctx()))
    assert fake.requests[0]["config"].tool_config.include_server_side_tool_invocations is True
