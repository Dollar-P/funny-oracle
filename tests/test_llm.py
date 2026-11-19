"""Agent loop + cost tracking + budget guard, with a scripted fake Gemini (no API calls)."""
from __future__ import annotations

import json

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


def test_invalid_json_is_fixed_in_same_conversation(fresh_db):
    fake = FakeGemini([fn_call_response(("get_indicators", {"ticker": "BTC-USD"})),
                       text_response("I think it goes up because rsi_zone=oversold."), text_response(AGENT_JSON)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert rep.ok and len(fake.requests) == 3
    fix = fake.requests[2]
    # same conversation (tool results still there), tools disabled, not a context-free JSON-mode call
    assert any(p.function_response for c in fix["contents"] for p in (c.parts or []))
    assert "could not be parsed" in fix["contents"][-1].parts[0].text
    assert fix["config"].tool_config.function_calling_config.mode == "NONE"
    assert fix["config"].response_mime_type is None


def test_json_mode_conversion_is_last_resort(fresh_db):
    long_text = "Technical view: rsi_zone=oversold (RSI 22), trend_vs_sma200=below, momentum fading."
    fake = FakeGemini([text_response(long_text), text_response("still not json " + long_text),
                       text_response(AGENT_JSON)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert rep.ok and len(fake.requests) == 3
    assert fake.requests[2]["config"].response_mime_type == "application/json"
    assert "rsi_zone=oversold" in fake.requests[2]["contents"][0].parts[0].text


def test_empty_output_is_not_invented(fresh_db):
    fake = FakeGemini([text_response(""), text_response("nope")])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert not rep.ok and "no usable output" in rep.error
    assert len(fake.requests) == 2  # no context-free conversion call on empty text


def test_invalid_json_three_times_marks_agent_failed(fresh_db):
    junk = "this is definitely not json but it is long enough to try converting it"
    fake = FakeGemini([text_response(junk), text_response(junk), text_response(junk)])
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


def test_news_researcher_search_mode(fresh_db):
    import json as _j

    from agents.news_researcher import NewsResearcher

    brief = _j.dumps({"bullets": ["Fed holds rates (2026-11-18)"], "links": []})
    fake = FakeGemini([text_response(brief, grounding=["https://news.example/a"])])
    nb, trace, c, err = run(NewsResearcher(LLM("gemini", client=fake), MODEL, None, "search").run(ctx()))
    assert err is None and nb.bullets and "https://news.example/a" in nb.links
    cfg = fake.requests[0]["config"]
    assert any(t.google_search for t in cfg.tools) and not any(t.function_declarations for t in cfg.tools)


def test_news_researcher_tool_mode(fresh_db):
    import json as _j

    from agents.news_researcher import NewsResearcher

    brief = _j.dumps({"bullets": ["Headline A"], "links": ["https://x"]})
    fake = FakeGemini([fn_call_response(("get_news", {"ticker": "BTC-USD"})), text_response(brief)])
    ex = FakeExecutor()
    nb, trace, c, err = run(NewsResearcher(LLM("gemini", client=fake), MODEL, ex, "tool").run(ctx()))
    assert nb.bullets == ["Headline A"] and ex.calls[0][0] == "get_news"
    assert not any(t.google_search for t in fake.requests[0]["config"].tools)


def test_server_side_search_parts_are_counted(fresh_db):
    from google.genai import types

    resp = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[
            types.Part(tool_call=types.ToolCall(id="s1", tool_type="GOOGLE_SEARCH_WEB",
                                                args={"queries": ["fed decision", "fomc 2026"]})),
            types.Part(tool_response=types.ToolResponse(id="s1", tool_type="GOOGLE_SEARCH_WEB", response={})),
            types.Part(text=AGENT_JSON),
        ]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=50),
    )
    from agents.macro import MacroAgent

    rep = run(MacroAgent(LLM("gemini", run_id="ss", client=FakeGemini([resp])), MODEL, FakeExecutor(),
                         enable_search=True).run(ctx()))
    assert rep.ok
    assert rep.trace[0].tool == "google_search" and rep.trace[0].args["queries"] == ["fed decision", "fomc 2026"]
    assert cost.run_cost("ss") == pytest.approx(100 * 0.3 / 1e6 + 50 * 2.5 / 1e6 + 2 * 0.014)


# --- tolerant JSON extraction (no repair call needed) -------------------------

OBJ = {"score": 0.4, "confidence": 0.7, "key_points": ["a {b}", "c"], "reasoning": "r", "sources": []}
OBJ_JSON = json.dumps(OBJ)


@pytest.mark.parametrize("text", [
    OBJ_JSON,                                                             # plain
    f"```json\n{OBJ_JSON}\n```",                                          # json fence
    f"```JSON\n{OBJ_JSON}\n```",                                          # upper-case fence
    f"```\n{OBJ_JSON}\n```",                                              # bare fence
    f"Here is my analysis:\n```json\n{OBJ_JSON}\n```\nHope this helps!",  # prose around a fence
    f"Here is the JSON: {OBJ_JSON}",                                      # leading prose
    f"{OBJ_JSON}\n\nNote: RSI {{14}} was used.",                          # trailing prose with braces
    f"Scores {{approx}}: {OBJ_JSON} -- end",                              # prose with a non-JSON brace first
    f"```json\n{OBJ_JSON}",                                               # unterminated fence (truncated)
    f"﻿  {OBJ_JSON}  ",                                              # BOM / whitespace
    f"```json\nSure! {OBJ_JSON} done\n```",                               # prose inside the fence
])
def test_extract_json_tolerant(text):
    assert extract_json(text) == OBJ


def test_extract_json_nested_and_first_object():
    nested = {"a": {"b": [1, {"c": "}"}]}, "d": "x"}
    assert extract_json("prefix " + json.dumps(nested) + " suffix") == nested
    assert extract_json('{"first": 1} and {"second": 2}') == {"first": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", '{"unterminated": ', "{not json}"])
def test_extract_json_rejects(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_tolerant_parse_avoids_any_repair_call(fresh_db):
    wrapped = "Analysis complete.\n```json\n" + AGENT_JSON + "\n```\nLet me know if you need more."
    fake = FakeGemini([text_response(wrapped)])
    rep = run(TechnicalAgent(LLM("gemini", client=fake), MODEL, FakeExecutor()).run(ctx()))
    assert rep.ok and len(fake.requests) == 1


# --- over-escaped JSON (seen in the R8 real runs: Bull opening on flash-lite) ---------------

R8_BULL = {
    "argument": "NVIDIA presents an exceptional buying opportunity over the next 1M, backed by stellar "
                "fundamentals: 103.2% YoY revenue growth, a 74.92% gross margin, and approximately $99.24 billion "
                "in free cash flow. Following a 31.44% monthly pullback, valuation has compressed to an attractive "
                "trailing P/E of 16.65, a forward P/E of 9.33, and a PEG ratio of just 0.18.",
    "strongest_opponent_point": "Founder CEO resignation creates a \"governance\" vacuum",
}


def _escape_once(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


R8_ESCAPED = _escape_once(json.dumps(R8_BULL))  # {\"argument\": \"NVIDIA … \\\"governance\\\" …\"}


def test_r8_escaped_bull_case_is_recovered():
    assert R8_ESCAPED.startswith('{\\"argument\\": \\"NVIDIA')  # same shape as the logged text
    assert extract_json(R8_ESCAPED) == R8_BULL


@pytest.mark.parametrize("text", [
    json.dumps(json.dumps(R8_BULL)),                              # object inside a JSON string
    "Here you go: " + json.dumps(json.dumps(R8_BULL)) + " thanks",  # …with prose around it
    f"```json\n{R8_ESCAPED}\n```",                                 # escaped object inside a fence
    _escape_once(_escape_once(json.dumps(R8_BULL))),               # escaped twice
    json.dumps(json.dumps(R8_BULL).replace('"argument"', '"argument"\n')),  # string with \n escapes
])
def test_extract_json_unescapes(text):
    assert extract_json(text) == R8_BULL


@pytest.mark.parametrize("text", ['"just a string"', '"{not json}"', '{\\"unterminated\\": ', json.dumps("[1, 2]")])
def test_extract_json_unescape_still_rejects(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_escaped_debate_turn_needs_no_repair_call(fresh_db):
    from core.schemas import DebateOutput

    fake = FakeGemini([text_response(R8_ESCAPED)])
    res = run(LLM("gemini", client=fake).run_agent(agent="bull_opening", system="s", user="u", schema=DebateOutput,
                                                   model=MODEL, max_turns=1))
    assert res.error is None and res.output.argument == R8_BULL["argument"]
    assert len(fake.requests) == 1  # parsed in code, no fix-up / JSON-mode call
