"""openai_compat provider (LOCAL_LLM.md lessons 1-10) against a scripted fake /chat/completions — no server."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import anyio
import pytest

import config
from core import cost
from core.cost import BudgetGuard
from core.llm import LLM, split_reasoning
from core.schemas import AgentOutput, DebateOutput, Verdict
from tests.fakes import FakeExecutor

MODEL = "local:prism-ml/bonsai-27b"
GOOD = {"score": 0.35, "confidence": 0.7, "key_points": ["RSI 55", "above SMA50 87,400", "MACD hist +32"],
        "reasoning": "Uptrend.", "sources": ["get_indicators"]}


class Resp:
    def __init__(self, status: int, data: dict | None = None, text: str = ""):
        self.status_code, self._data, self.text = status, data or {}, text or json.dumps(data or {})

    def json(self):
        return self._data


def msg(content: str = "", tool_calls=None, reasoning: str | None = None, finish="stop", usage=None) -> Resp:
    m: dict = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    if reasoning is not None:
        m["reasoning_content"] = reasoning
    return Resp(200, {"choices": [{"message": m, "finish_reason": finish}],
                      "usage": usage or {"prompt_tokens": 120, "completion_tokens": 40}})


def call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLocal:
    """Mimics httpx.AsyncClient.post for /chat/completions; replies from a script, records every payload."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.urls: list[str] = []

    async def post(self, url, json=None, timeout=None):
        self.urls.append(url)
        self.requests.append(json)
        item = self.script.pop(0)
        out = item(json) if callable(item) else item
        return await out if asyncio.iscoroutine(out) else out


def run_agent(fake, schema=AgentOutput, tools=("get_indicators",), preset="quick", run_id="loc", **kw):
    llm = LLM("local", run_id=run_id, client=fake, think_preset=preset)
    return anyio.run(lambda: llm.run_agent(agent=kw.pop("agent", "technical"), system="You are an analyst.",
                                           user="Analyze BTC-USD.", schema=schema, model=MODEL,
                                           tools=list(tools), executor=FakeExecutor(), max_turns=4, **kw))


def test_quick_tool_turns_then_json_final_turn(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_BASE_URL", "http://lm-studio.test:1234/v1")
    fake = FakeLocal([msg(tool_calls=[call("get_indicators", {"ticker": "BTC-USD", "interval": "1h"})]),
                      msg("I have what I need."),              # free text after tools → not accepted as answer
                      msg(json.dumps(GOOD))])
    res = run_agent(fake)
    assert res.error is None and res.output.score == 0.35
    assert [t.tool for t in res.trace] == ["get_indicators"]
    tool_turn, _, final = fake.requests
    # base URL comes from LOCAL_BASE_URL; model id without the local: prefix
    assert fake.urls[0] == "http://lm-studio.test:1234/v1/chat/completions" and tool_turn["model"] == "prism-ml/bonsai-27b"
    # tool-selection turns: tools, thinking off, short max_tokens (lessons 4-5)
    assert tool_turn["tools"][0]["function"]["name"] == "get_indicators"
    assert tool_turn["reasoning_effort"] == "none" and tool_turn["max_tokens"] == config.LOCAL_MAX_TOKENS_FAST
    # final turn: no tools, json_schema with the strict schema + decimal descriptions (lessons 2, 6)
    assert "tools" not in final and final["response_format"]["type"] == "json_schema"
    props = final["response_format"]["json_schema"]["schema"]["properties"]
    assert "NOT a percentage" in props["score"]["description"]
    assert final["reasoning_effort"] == "none"                                       # quick = all off
    # tool result was sent back as a tool message, and the system prompt carries the number rules (lesson 3)
    assert any(m["role"] == "tool" for m in final["messages"])
    assert "NOT percentages" in final["messages"][0]["content"] and "Compare numbers carefully" in final["messages"][0]["content"]


def test_quick_reuses_a_valid_direct_answer(fresh_db):
    fake = FakeLocal([msg(tool_calls=[call("get_indicators", {"ticker": "BTC-USD"})]), msg(json.dumps(GOOD))])
    res = run_agent(fake)
    assert res.output.confidence == 0.7 and len(fake.requests) == 2  # no extra final call needed


def test_deep_preset_thinks_only_on_the_final_turn(fresh_db):
    fake = FakeLocal([msg(tool_calls=[call("get_indicators", {"ticker": "BTC-USD"})]),
                      msg(json.dumps(GOOD)),                    # deep: still does a thinking final turn
                      msg(json.dumps(GOOD), reasoning="RSI 55 is neutral; price 91,250 is above SMA200 84,100…")])
    res = run_agent(fake, preset="deep")
    tool_turn, direct, final = fake.requests
    assert tool_turn["reasoning_effort"] == "none" and direct["reasoning_effort"] == "none"
    assert "reasoning_effort" not in final and final["max_tokens"] == config.LOCAL_MAX_TOKENS_THINK
    assert "above SMA200" in res.reasoning and res.output.score == 0.35


def test_debate_and_judge_turns_think_in_deep_but_not_quick(fresh_db):
    deb = {"argument": "Bull case", "strongest_opponent_point": "bear point"}
    for preset, thinks in (("deep", True), ("quick", False)):
        fake = FakeLocal([msg(json.dumps(deb))])
        res = run_agent(fake, schema=DebateOutput, tools=(), preset=preset, agent="bull_opening")
        assert res.output.argument == "Bull case"
        assert ("reasoning_effort" not in fake.requests[0]) is thinks
        assert len(fake.requests) == 1 and "tools" not in fake.requests[0]


def test_range_validation_retries_with_feedback(fresh_db):
    """Lesson 1: LM Studio ignores minimum/maximum, so 72.5 must be caught and sent back."""
    bad = {**GOOD, "score": 72.5, "confidence": 70}
    fake = FakeLocal([msg(json.dumps(bad)), msg(json.dumps(GOOD))])
    res = run_agent(fake, tools=())
    assert res.error is None and res.output.score == 0.35
    feedback = fake.requests[1]["messages"][-1]["content"]
    assert '"score": 72.5' in feedback                                            # the invalid JSON is sent back
    assert "score: Input should be less than or equal to 1" in feedback and "confidence" in feedback
    assert fake.requests[1]["messages"][-2] == {"role": "assistant", "content": json.dumps(bad)}


def test_gives_up_after_retries(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_VALIDATION_RETRIES", 1)
    bad = json.dumps({**GOOD, "score": 72})
    res = run_agent(FakeLocal([msg(bad), msg(bad)]), tools=())
    assert res.output is None and "invalid output after 2 attempts" in res.error and "score" in res.error


def test_truncated_thinking_is_retried(fresh_db):
    """Lesson 5: a thinking answer that hits max_tokens comes back empty → retried, not parsed as garbage."""
    fake = FakeLocal([msg("", reasoning="long thoughts…", finish="length"), msg(json.dumps(GOOD))])
    res = run_agent(fake, tools=(), preset="deep")
    assert res.output.score == 0.35 and "truncated" in fake.requests[1]["messages"][-1]["content"]


def test_reasoning_content_and_leaked_think_tags():
    assert split_reasoning({"content": '{"a":1}', "reasoning_content": "hmm"}) == ('{"a":1}', "hmm")
    assert split_reasoning({"content": "<think>let me see</think>\n{\"a\": 1}"}) == ('{"a": 1}', "let me see")
    assert split_reasoning({"content": "<think>never closed"}) == ("", "never closed")


def test_verdict_uses_strict_schema(fresh_db):
    v = {"adjustment": 15, "final_confidence": 0.6, "headline": "h", "summary": "s", "key_risks": ["r"]}
    fake = FakeLocal([msg(json.dumps(v)), msg(json.dumps({**v, "adjustment": 0.1}))])
    res = run_agent(fake, schema=Verdict, tools=(), agent="judge")
    assert res.output.adjustment == 0.1 and "adjustment" in fake.requests[1]["messages"][-1]["content"]


def test_price_zero_tokens_logged_and_budget_guard_passes(fresh_db):
    """Lesson 10: free, usage still recorded; even an exhausted budget doesn't block a local run."""
    fake = FakeLocal([msg(json.dumps(GOOD), usage={"prompt_tokens": 900, "completion_tokens": 300,
                                                    "completion_tokens_details": {"reasoning_tokens": 200}})])
    llm = LLM("local", run_id="free", client=fake, guard=BudgetGuard(budget_thb=0.0))
    assert llm.guard.check_prediction("swing", MODEL) == 0.0                     # no refusal, estimate 0
    res = anyio.run(lambda: llm.run_agent(agent="judge", system="s", user="u", schema=AgentOutput, model=MODEL))
    assert res.output is not None and cost.run_cost("free") == 0.0
    from core.db import connect

    with connect() as conn:
        row = conn.execute("SELECT input_tokens, output_tokens, thinking_tokens, cost_usd, model FROM llm_usage"
                           " WHERE run_id = 'free'").fetchone()
    assert tuple(row) == (900, 100, 200, 0.0, MODEL)
    assert BudgetGuard().estimate_usd("swing", MODEL) == 0.0


def test_http_error_fails_the_agent_gracefully(fresh_db):
    res = run_agent(FakeLocal([Resp(400, text='{"error":"reasoning_effort off is invalid"}')]))
    assert res.output is None and "HTTP 400" in res.error


def test_concurrency_limit_is_process_wide(fresh_db, monkeypatch):
    """Lesson 7: LOCAL_CONCURRENCY=1 serialises requests even across event loops (duel runs use threads)."""
    monkeypatch.setattr(config, "LOCAL_CONCURRENCY", 1)
    active, peak = [0], [0]
    lock = threading.Lock()

    async def slow(payload):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.05)
        with lock:
            active[0] -= 1
        return msg(json.dumps(GOOD))

    def one():
        run_agent(FakeLocal([slow]), tools=(), run_id=f"c{threading.get_ident()}")

    ts = [threading.Thread(target=one) for _ in range(3)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1 and time.perf_counter() - t0 >= 0.15


def test_provider_and_presets():
    assert config.provider_for(MODEL) == "local"
    assert config.provider_for("openai/gpt-oss-20b") == "gemini"  # unprefixed ids are never treated as local
    with pytest.raises(ValueError):
        LLM("local", think_preset="medium")


def test_scalp_refuses_local_models_before_any_call(fresh_db):
    import orchestrator

    fake = FakeLocal([])
    llm = LLM("local", client=fake)
    with pytest.raises(ValueError, match="too slow for Scalp"):
        anyio.run(lambda: orchestrator.run_prediction("BTC-USD", "scalp", "15m", llm=llm, executor=FakeExecutor(),
                                                      analyst_model=MODEL))
    assert fake.requests == []


def test_local_swing_uses_news_web_mode():
    import orchestrator

    assert anyio.run(lambda: orchestrator._search_mode(LLM("local", client=FakeLocal([])), MODEL, "swing")) == "news_web"


def test_repeated_identical_tool_calls_end_the_tool_phase(fresh_db):
    """Seen live: bonsai repeated get_price_history + get_indicators 4x until the turn limit."""
    same = [call("get_price_history", {"ticker": "NVDA"}, "a"), call("get_indicators", {"ticker": "NVDA"}, "b")]
    fake = FakeLocal([msg(tool_calls=same), msg(tool_calls=same), msg(json.dumps(GOOD))])
    res = run_agent(fake, tools=("get_price_history", "get_indicators"))
    assert [t.tool for t in res.trace] == ["get_price_history", "get_indicators"]   # executed once
    assert len(fake.requests) == 3 and "tools" not in fake.requests[2]             # 2nd turn → straight to final
    final_msgs = fake.requests[2]["messages"]
    # the repeated assistant tool_calls turn was dropped (no dangling tool_calls without results)
    assert sum(1 for m in final_msgs if m.get("tool_calls")) == 1 and res.output.score == 0.35
