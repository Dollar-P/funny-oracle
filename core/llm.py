"""LLM layer (§4.2): provider wrapper + manual agent loop over MCP tools.

    llm = LLM(run_id="...")                       # provider from LLM_PROVIDER
    res = await llm.run_agent(agent="technical", system=..., user=..., schema=AgentOutput,
                              model=..., tools=["get_indicators"], executor=mcp_client,
                              enable_search=False)

Loop: model requests function call(s) → executor.call (MCP) → function response → repeat,
until the model returns text. The text is parsed into `schema`; on failure one extra
"format" call is made with JSON mode + response schema (retry once).

Every API call: BudgetGuard.check_call() first, then usage is recorded to llm_usage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

import config
from core import cache, cost
from core.cost import BudgetGuard, Usage
from core.events import NULL_SINK, EventSink
from core.schemas import STRICT_SCHEMAS, ToolCall

log = logging.getLogger(__name__)

_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    """One semaphore per event loop (Streamlit/pytest may create several loops)."""
    loop = asyncio.get_running_loop()
    sem = _SEMAPHORES.get(id(loop))
    if sem is None:
        sem = _SEMAPHORES[id(loop)] = asyncio.Semaphore(config.MAX_CONCURRENCY)
    return sem


class ToolExecutor(Protocol):
    async def list_tools(self) -> list: ...
    async def call(self, name: str, args: dict | None = None) -> tuple[dict, ToolCall]: ...


@dataclass
class AgentResult:
    output: BaseModel | None
    text: str = ""
    trace: list[ToolCall] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    usage: Usage | None = None
    cost_usd: float = 0.0
    error: str | None = None
    searched: bool = False
    elapsed_s: float = 0.0
    reasoning: str = ""  # local reasoning models: the thinking text, kept apart from the answer


# --- JSON helpers ----------------------------------------------------------------

_FENCE = re.compile(r"```[ \t]*(?:json)?[ \t]*\n?(.*?)```", re.S | re.I)


_DECODER = json.JSONDecoder()


def _first_object(text: str) -> dict | None:
    """The first complete top-level {...} object in `text`, ignoring prose before and after it.

    Uses raw_decode from each '{' so braces inside strings, nested objects and trailing prose
    (even prose containing more braces) are handled correctly.
    """
    i = text.find("{")
    while i != -1:
        try:
            obj, _end = _DECODER.raw_decode(text, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)
    return None


def _candidates(text: str) -> list[str]:
    text = (text or "").strip().lstrip("\ufeff").strip()
    return [text, *(b.strip() for b in _FENCE.findall(text))]


def _extract_plain(text: str) -> dict | None:
    for candidate in _candidates(text):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):  # agent outputs are always objects
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        obj = _first_object(candidate)
        if obj is not None:
            return obj
    return None


_ESCAPED = re.compile(r'\\(["\\])')


def _string_literals(text: str) -> list[str]:
    """Decoded JSON string literals in `text` that look like they hold an object."""
    out: list[str] = []
    i = text.find('"')
    while i != -1 and len(out) < 5:
        try:
            val, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find('"', i + 1)
            continue
        if isinstance(val, str) and "{" in val:
            out.append(val)
        i = text.find('"', max(end, i + 1))
    return out


def _unescaped_variants(text: str) -> list[str]:
    """Texts to retry when the model escaped its JSON one level too many, e.g.
    `{\\"argument\\": \\"...\\"}` (escaped object) or `"{\\"argument\\": ...}"` (object inside a JSON string)."""
    variants: list[str] = []
    for c in _candidates(text):
        variants += _string_literals(c)               # a JSON string (alone or in prose) containing an object
        if '\\"' in c or "\\\\" in c:
            try:                                      # proper decoding (also handles \n, \t, \u\u2026)
                v = json.loads('"' + c + '"')
                if isinstance(v, str):
                    variants.append(v)
            except (json.JSONDecodeError, ValueError):
                pass
            variants.append(_ESCAPED.sub(r"\1", c))   # one level of \" and \\ only
    return list(dict.fromkeys(v for v in variants if v and v != text))


def extract_json(text: str, _depth: int = 0) -> Any:
    """Tolerant JSON extraction from model text, tried in order:

    1. the whole text as-is
    2. the contents of ```json / ``` fences (any case)
    3. the first complete outermost {...} object, ignoring leading/trailing prose
    4. the same after undoing one level of escaping: `\\"` / `\\\\`, or a JSON string literal that itself
       contains the object (twice at most, for doubly escaped output)
    """
    obj = _extract_plain(text)
    if obj is not None:
        return obj
    if _depth < 2:
        for v in _unescaped_variants(text):
            try:
                return extract_json(v, _depth + 1)
            except ValueError:
                continue
    raise ValueError("no JSON object found in model output")


def parse_output(text: str, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate(extract_json(text))


def strict_schema(schema: type[BaseModel]) -> dict:
    """JSON schema with every property required and no extras (for strict structured outputs)."""
    s = schema.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(s)
    return s


FINAL_NUDGE = (
    "Tool budget exhausted. Do not call any more tools. Reply now with ONLY the final JSON object "
    "matching the required schema."
)


FORMAT_SYSTEM = (
    "Convert the analysis into JSON that matches the schema exactly. Copy every number, label and claim "
    "from the text; do not invent anything. If the text contains no concrete analysis, set confidence to 0 "
    "and say so in reasoning."
)


def fix_json_prompt(err: Exception) -> str:
    return (f"Your last reply could not be parsed as the required JSON ({str(err)[:200]}). "
            "Reply again with ONLY the JSON object — no prose, no code fence — keeping every number and label "
            "from your analysis. Do not call any more tools.")


def _schema_hint(schema: type[BaseModel]) -> str:
    return (
        "\n\nWhen you are done, reply with ONLY a JSON object (no prose, no code fence) matching this JSON schema:\n"
        + json.dumps(strict_schema(schema), separators=(",", ":"))
    )


# --- Facade ------------------------------------------------------------------------

class LLM:
    def __init__(self, provider: str | None = None, run_id: str | None = None,
                 guard: BudgetGuard | None = None, client: Any = None, think_preset: str | None = None):
        self.provider = (provider or config.LLM_PROVIDER).lower()
        self.run_id = run_id
        self.guard = guard or BudgetGuard()
        self.think_preset = (think_preset or config.LOCAL_THINK_PRESET).lower()  # local provider only
        if self.think_preset not in config.THINK_PRESETS:
            raise ValueError(f"think preset must be one of {config.THINK_PRESETS}")
        self.events: EventSink = NULL_SINK  # set per run by the orchestrator (REDESIGN §2.1)
        if self.provider == "gemini":
            self.backend: _Backend = GeminiBackend(self, client)
        elif self.provider == "claude":
            self.backend = ClaudeBackend(self, client)
        elif self.provider == "local":
            self.backend = OpenAICompatBackend(self, client)
        else:
            raise ValueError(f"unknown LLM_PROVIDER {self.provider!r} (gemini|claude|local)")

    def _account(self, u: Usage, agent: str) -> float:
        return cost.record(u, agent, self.run_id)

    async def supports(self, model: str, feature: str) -> bool:
        """feature: 'search' | 'search+functions'. Cached for 7 days per model."""
        key = f"cap:{self.provider}:{model}:{feature}"
        hit = cache.get(key)
        if hit is not None:
            return bool(hit["ok"])
        ok, definitive = await self.backend.probe(model, feature)
        if definitive:
            cache.set(key, {"ok": ok}, 7 * 24 * 3600)
        return ok

    async def run_agent(self, *, agent: str, system: str, user: str, schema: type[BaseModel], model: str,
                        tools: list[str] | None = None, executor: ToolExecutor | None = None,
                        enable_search: bool = False, max_turns: int = config.MAX_AGENT_TURNS,
                        thinking: str = config.ANALYST_THINKING) -> AgentResult:
        if not model:
            raise ValueError(f"no model configured for agent {agent!r}; set the model IDs in .env")
        t0 = time.perf_counter()
        specs = []
        if tools and executor is not None:
            allowed = set(tools)
            specs = [t for t in await executor.list_tools() if t.name in allowed]
        try:
            res = await self.backend.run(agent=agent, system=system + _schema_hint(schema), user=user,
                                         schema=schema, model=model, specs=specs, executor=executor,
                                         enable_search=enable_search, max_turns=max_turns, thinking=thinking)
        except cost.BudgetExceeded:
            raise
        except Exception as e:  # agent failure → caller drops it from the aggregate
            log.exception("agent %s failed", agent)
            res = AgentResult(output=None, error=f"{type(e).__name__}: {e}"[:400])
        res.elapsed_s = round(time.perf_counter() - t0, 2)
        return res


class _Backend(Protocol):
    async def run(self, **kw) -> AgentResult: ...
    async def probe(self, model: str, feature: str) -> tuple[bool, bool]: ...


# --- Gemini --------------------------------------------------------------------------

class GeminiBackend:
    RETRY_CODES = {429, 500, 502, 503, 504}

    def __init__(self, llm: LLM, client: Any = None):
        self.llm = llm
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google import genai

            if not config.GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY is not set in .env")
            self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        return self._client

    async def _generate(self, model: str, contents, cfg, agent: str):
        from google.genai import errors

        self.llm.guard.check_call()
        delay = 2.0
        for attempt in range(4):
            try:
                async with _semaphore():
                    resp = await self.client.aio.models.generate_content(model=model, contents=contents, config=cfg)
                break
            except errors.APIError as e:
                if getattr(e, "code", None) in self.RETRY_CODES and attempt < 3:
                    log.warning("gemini %s (attempt %d) — backing off %.0fs", e.code, attempt + 1, delay)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
        u = self._usage(resp, model)
        c = self.llm._account(u, agent)
        return resp, u, c

    @staticmethod
    def _search_queries(resp) -> list[str]:
        """Search queries run server-side in this response.

        With include_server_side_tool_invocations the calls arrive as `tool_call` parts
        (tool_type GOOGLE_SEARCH_WEB); otherwise only grounding_metadata has them.
        """
        from_parts: list[str] = []
        from_meta: list[str] = []
        for cand in getattr(resp, "candidates", None) or []:
            for part in (cand.content.parts if cand.content and cand.content.parts else []):
                tc = getattr(part, "tool_call", None)
                if tc is not None and "SEARCH" in str(tc.tool_type or ""):
                    qs = (tc.args or {}).get("queries") or ["(search)"]
                    from_parts += [str(q) for q in qs]
            gm = getattr(cand, "grounding_metadata", None)
            if gm is not None:
                from_meta += list(gm.web_search_queries or []) or (["(search)"] if gm.grounding_chunks else [])
        return from_parts if len(from_parts) >= len(from_meta) else from_meta

    @staticmethod
    def _text(resp) -> str:
        """Concatenate non-thought text parts (avoids the SDK's mixed-parts warning)."""
        cand = (getattr(resp, "candidates", None) or [None])[0]
        if cand is None or cand.content is None or not cand.content.parts:
            return ""
        return "".join(p.text for p in cand.content.parts if p.text and not p.thought)

    @classmethod
    def _usage(cls, resp, model: str) -> Usage:
        m = getattr(resp, "usage_metadata", None)
        searches = len(cls._search_queries(resp))
        if m is None:
            return Usage(model, search_calls=searches)
        return Usage(
            model=model,
            input_tokens=(m.prompt_token_count or 0) + (m.tool_use_prompt_token_count or 0),
            output_tokens=m.candidates_token_count or 0,
            thinking_tokens=m.thoughts_token_count or 0,
            search_calls=searches,
        )

    @staticmethod
    def _thinking(level: str):
        from google.genai import types

        return types.ThinkingConfig(thinking_level=level.upper()) if level else None

    async def run(self, *, agent, system, user, schema, model, specs, executor, enable_search, max_turns,
                  thinking) -> AgentResult:
        from google.genai import types

        tool_objs = []
        if specs:
            tool_objs.append(types.Tool(function_declarations=[
                types.FunctionDeclaration(name=s.name, description=s.description[:1000],
                                          parameters_json_schema=s.input_schema)
                for s in specs
            ]))
        if enable_search:
            tool_objs.append(types.Tool(google_search=types.GoogleSearch()))
        allowed = {s.name for s in specs}

        def cfg(final: bool = False):
            kw: dict[str, Any] = dict(
                system_instruction=system,
                thinking_config=self._thinking(thinking),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            if tool_objs:
                kw["tools"] = tool_objs
                tc: dict[str, Any] = {}
                if enable_search and specs:
                    # required by the API to mix built-in Search with function calling
                    tc["include_server_side_tool_invocations"] = True
                if final and specs:
                    tc["function_calling_config"] = types.FunctionCallingConfig(mode="NONE")
                if tc:
                    kw["tool_config"] = types.ToolConfig(**tc)
            return types.GenerateContentConfig(**kw)

        contents: list = [types.Content(role="user", parts=[types.Part(text=user)])]
        total = Usage(model)
        spent = 0.0
        trace: list[ToolCall] = []
        sources: list[str] = []
        text = ""
        searched = False

        for turn in range(max_turns + 1):
            final = turn == max_turns
            resp, u, c = await self._generate(model, contents, cfg(final), agent)
            total, spent = total + u, spent + c
            cand = (resp.candidates or [None])[0]
            gm = getattr(cand, "grounding_metadata", None) if cand else None
            queries = self._search_queries(resp)
            if queries:
                searched = True
                for q in queries:
                    await self.llm.events.emit("search", agent=agent, query=q)
                trace.append(ToolCall(tool="google_search", args={"queries": queries},
                                      result_bytes=len(gm.grounding_chunks or []) if gm else 0))
            if gm is not None:
                for ch in gm.grounding_chunks or []:
                    web = getattr(ch, "web", None)
                    if web is not None and web.uri:
                        sources.append(f"{web.title or 'web'} | {web.uri}")
            calls = resp.function_calls or []
            if cand is not None and cand.content is not None:
                contents.append(cand.content)  # keeps thought signatures for the next turn
            if not calls or final:
                text = self._text(resp)
                break
            results = await asyncio.gather(*(
                self._exec(executor, fc.name, dict(fc.args or {}), allowed) for fc in calls
            ))
            parts = []
            for fc, (data, tc) in zip(calls, results):
                trace.append(tc)
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=fc.id, name=fc.name, response=data if isinstance(data, dict) else {"result": data})))
            if turn == max_turns - 1:
                parts.append(types.Part(text=FINAL_NUDGE))
            contents.append(types.Content(role="user", parts=parts))

        output, error = None, None
        try:
            output = parse_output(text, schema)
        except (ValueError, ValidationError) as e:
            log.warning("agent %s: output not valid JSON (%s); asking it to fix in-conversation. text=%r",
                        agent, str(e)[:200], text[:600])
            # 1) same conversation: the model still has its tool results, so it can't drift into filler
            contents.append(types.Content(role="user", parts=[types.Part(text=fix_json_prompt(e))]))
            resp, u, c = await self._generate(model, contents, cfg(final=True), agent)
            total, spent = total + u, spent + c
            fixed = self._text(resp)
            try:
                output = parse_output(fixed, schema)
            except (ValueError, ValidationError) as e2:
                # 2) last resort: JSON-mode conversion of the text alone — never on empty text
                source_text = fixed if len(fixed) > len(text) else text
                if len(source_text.strip()) < 40:
                    error = f"no usable output after JSON fix-up: {e2}"[:300]
                else:
                    log.warning("agent %s: fix-up still invalid; JSON-mode conversion", agent)
                    fcfg = types.GenerateContentConfig(
                        system_instruction=FORMAT_SYSTEM, response_mime_type="application/json",
                        response_json_schema=strict_schema(schema), thinking_config=self._thinking("low"))
                    resp, u, c = await self._generate(
                        model, [types.Content(role="user", parts=[types.Part(text=source_text)])], fcfg, agent)
                    total, spent = total + u, spent + c
                    try:
                        output = parse_output(self._text(resp), schema)
                    except (ValueError, ValidationError) as e3:
                        error = f"invalid JSON output after retry: {e3}"[:300]

        return AgentResult(output=output, text=text, trace=trace, sources=list(dict.fromkeys(sources)),
                           usage=total, cost_usd=spent, error=error, searched=searched)

    @staticmethod
    async def _exec(executor, name, args, allowed):
        if name not in allowed or executor is None:
            return {"error": f"tool {name!r} is not available to this agent", "data": None}, ToolCall(
                tool=name, args=args, error="not allowed")
        return await executor.call(name, args)

    async def probe(self, model: str, feature: str) -> tuple[bool, bool]:
        """Cheap request with the tool combination; a 400 means unsupported."""
        from google.genai import errors, types

        tools = [types.Tool(google_search=types.GoogleSearch())]
        if feature == "search+functions":
            tools.append(types.Tool(function_declarations=[types.FunctionDeclaration(
                name="ping", description="Returns pong. Not needed for this request.",
                parameters_json_schema={"type": "object", "properties": {}})]))
        cfg = types.GenerateContentConfig(
            tools=tools, thinking_config=self._thinking("low"),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(include_server_side_tool_invocations=True)
            if feature == "search+functions" else None,
        )
        try:
            await self._generate(model, "Reply with the single word OK. Do not search or call tools.", cfg, "probe")
            return True, True
        except errors.APIError as e:
            if getattr(e, "code", None) == 400:
                log.warning("model %s does not support %s: %s", model, feature, str(e)[:200])
                return False, True
            return False, False


# --- Claude (optional provider) ------------------------------------------------------

class ClaudeBackend:
    def __init__(self, llm: LLM, client: Any = None):
        self.llm = llm
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(max_retries=3)
        return self._client

    async def _create(self, agent: str, **kw):
        self.llm.guard.check_call()
        async with _semaphore():
            resp = await self.client.messages.create(**kw)
        u = resp.usage
        stu = getattr(u, "server_tool_use", None)
        usage = Usage(kw["model"], input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                      search_calls=(getattr(stu, "web_search_requests", 0) or 0) if stu else 0)
        return resp, usage, self.llm._account(usage, agent)

    async def run(self, *, agent, system, user, schema, model, specs, executor, enable_search, max_turns,
                  thinking) -> AgentResult:
        tools: list[dict] = [{"name": s.name, "description": s.description[:1000], "input_schema": s.input_schema}
                             for s in specs]
        if enable_search:
            tools.append({"type": config.CLAUDE_WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 3})
        allowed = {s.name for s in specs}
        messages: list[dict] = [{"role": "user", "content": user}]
        total, spent = Usage(model), 0.0
        trace: list[ToolCall] = []
        sources: list[str] = []
        text, searched = "", False

        for turn in range(max_turns + 1):
            final = turn == max_turns
            kw: dict[str, Any] = dict(model=model, max_tokens=16000, system=system, messages=messages,
                                      output_config={"effort": thinking or "low"})
            if tools:
                kw["tools"] = tools
                if final:
                    kw["tool_choice"] = {"type": "none"}
            resp, u, c = await self._create(agent, **kw)
            total, spent = total + u, spent + c
            messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "server_tool_use":
                    searched = True
                    await self.llm.events.emit("search", agent=agent, query=str((b.input or {}).get("query", "")))
                    trace.append(ToolCall(tool="web_search", args=dict(b.input or {})))
                elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                    sources += [f"{r.title} | {r.url}" for r in b.content if getattr(r, "url", None)]
            if resp.stop_reason == "pause_turn" and not final:
                continue
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses or final:
                text = "".join(b.text for b in resp.content if b.type == "text")
                break
            results = await asyncio.gather(*(GeminiBackend._exec(executor, b.name, dict(b.input or {}), allowed)
                                             for b in uses))
            blocks: list[dict] = []
            for b, (data, tc) in zip(uses, results):
                trace.append(tc)
                blocks.append({"type": "tool_result", "tool_use_id": b.id,
                               "content": json.dumps(data, default=str), "is_error": bool(tc.error)})
            if turn == max_turns - 1:
                blocks.append({"type": "text", "text": FINAL_NUDGE})
            messages.append({"role": "user", "content": blocks})

        output, error = None, None
        try:
            output = parse_output(text, schema)
        except (ValueError, ValidationError) as e:
            log.warning("agent %s: output not valid JSON (%s); asking it to fix in-conversation. text=%r",
                        agent, str(e)[:200], text[:600])
            messages.append({"role": "user", "content": fix_json_prompt(e)})
            kw = dict(model=model, max_tokens=16000, system=system, messages=messages,
                      output_config={"effort": thinking or "low"})
            if tools:
                kw.update(tools=tools, tool_choice={"type": "none"})
            resp, u, c = await self._create(agent, **kw)
            total, spent = total + u, spent + c
            fixed = "".join(b.text for b in resp.content if b.type == "text")
            try:
                output = parse_output(fixed, schema)
            except (ValueError, ValidationError) as e2:
                source_text = fixed if len(fixed) > len(text) else text
                if len(source_text.strip()) < 40:
                    error = f"no usable output after JSON fix-up: {e2}"[:300]
                else:
                    resp, u, c = await self._create(
                        agent, model=model, max_tokens=4000, system=FORMAT_SYSTEM,
                        messages=[{"role": "user", "content": source_text}],
                        output_config={"effort": "low",
                                       "format": {"type": "json_schema", "schema": strict_schema(schema)}},
                    )
                    total, spent = total + u, spent + c
                    try:
                        output = parse_output("".join(b.text for b in resp.content if b.type == "text"), schema)
                    except (ValueError, ValidationError) as e3:
                        error = f"invalid JSON output after retry: {e3}"[:300]

        return AgentResult(output=output, text=text, trace=trace, sources=list(dict.fromkeys(sources)),
                           usage=total, cost_usd=spent, error=error, searched=searched)

    async def probe(self, model: str, feature: str) -> tuple[bool, bool]:
        # Claude supports server web search together with client tools on current models.
        return True, False


# --- OpenAI-compatible local server (LM Studio etc.) — LOCAL_LLM.md -----------------------------

_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S)
_LOCAL_SLOTS: threading.BoundedSemaphore | None = None
_LOCAL_SLOTS_N = 0


def _local_slots() -> threading.BoundedSemaphore:
    """Process-wide limit (lesson 7): duel runs live in different threads / event loops but share one GPU."""
    global _LOCAL_SLOTS, _LOCAL_SLOTS_N
    if _LOCAL_SLOTS is None or _LOCAL_SLOTS_N != config.LOCAL_CONCURRENCY:
        _LOCAL_SLOTS = threading.BoundedSemaphore(max(1, config.LOCAL_CONCURRENCY))
        _LOCAL_SLOTS_N = config.LOCAL_CONCURRENCY
    return _LOCAL_SLOTS


def split_reasoning(msg: dict) -> tuple[str, str]:
    """Lesson 4: (content, reasoning). Reasoning comes in `reasoning_content` (or `reasoning`); some
    templates leak `<think>…</think>` into the content, which is stripped and kept as reasoning."""
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning)
    leaked = _THINK_RE.findall(content)
    if leaked:
        reasoning += "".join(t.removeprefix("<think>").removesuffix("</think>") for t in leaked)
        content = _THINK_RE.sub("", content)
    return content.strip(), reasoning.strip()


def validation_problems(e: Exception | None) -> list[str]:
    if isinstance(e, ValidationError):
        out = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ())) or "(root)"
            out.append(f"{loc}: {err.get('msg')} (got {err.get('input')!r})"[:200])
        return out
    return [str(e)[:200]] if e else []


LOCAL_RULES = (
    "\n\nNumber rules: score is a decimal from -1.0 to 1.0 (e.g. 0.35), confidence a decimal from 0.0 to 1.0 "
    "(e.g. 0.7). They are NOT percentages: never answer 72 or 72.5. Compare numbers carefully before saying one "
    "is above or below another (e.g. 91,250 is ABOVE 84,100)."
)
FINAL_JSON_PROMPT = (
    "Tool phase finished. Using ONLY the tool results above, reply now with the final JSON object matching the "
    "schema. score/confidence are decimals, not percentages. No tool calls, no prose."
)


def feedback_prompt(bad: str, problems: list[str]) -> str:
    return ("Your JSON was invalid:\n" + bad[:1500] + "\n\nProblems:\n" + "\n".join(f"- {p}" for p in problems)
            + "\n\nReply again with ONLY the corrected JSON object. Keep every number that was valid.")


class OpenAICompatBackend:
    """Manual tool loop against an OpenAI-compatible `/chat/completions` endpoint (LM Studio).

    Turn structure (lessons 4-6): tool-selection turns with tools and thinking off → one final turn without
    tools and with `response_format: json_schema`. Thinking is per request: `reasoning_effort: "none"` turns
    it off; omitting the field keeps the server default (LM Studio Thinking ON). Ranges are validated with the
    strict Pydantic schemas and invalid output is sent back with a list of problems (lessons 1-2).
    """

    def __init__(self, llm: "LLM", client: Any = None):
        self.llm = llm
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=httpx.Timeout(config.LOCAL_TIMEOUT_S, connect=10.0))
        return self._client

    def thinks(self, agent: str, final: bool) -> bool:
        """LOCAL_LLM.md thinking policy. quick: never. deep: every final synthesis turn (analysts' AgentReport,
        Bull / Bear, Judge); tool-selection turns stay fast."""
        return self.llm.think_preset == "deep" and final

    async def _chat(self, agent: str, model: str, messages: list[dict], *, think: bool, tools: list | None = None,
                    schema: type[BaseModel] | None = None) -> tuple[dict, str]:
        payload: dict[str, Any] = {
            "model": config.local_model_id(model),
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": config.LOCAL_MAX_TOKENS_THINK if think else config.LOCAL_MAX_TOKENS_FAST,
        }
        if not think:
            payload["reasoning_effort"] = "none"  # the only value that turns thinking off (LOCAL_LLM.md)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "strict": True, "schema": strict_schema(schema)}}
        slots = _local_slots()
        await asyncio.to_thread(slots.acquire)
        try:
            r = await self.client.post(f"{config.LOCAL_BASE_URL}/chat/completions", json=payload,
                                       timeout=config.LOCAL_TIMEOUT_S)
        finally:
            slots.release()
        if r.status_code != 200:
            raise RuntimeError(f"local LLM HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        u = data.get("usage") or {}
        reasoning_tok = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        usage = Usage(model, input_tokens=u.get("prompt_tokens") or 0,
                      output_tokens=max(0, (u.get("completion_tokens") or 0) - reasoning_tok),
                      thinking_tokens=reasoning_tok)
        self.llm._account(usage, agent)  # price 0, but tokens are still logged (lesson 10)
        choice = (data.get("choices") or [{}])[0]
        return choice.get("message") or {}, choice.get("finish_reason") or ""

    async def run(self, *, agent, system, user, schema, model, specs, executor, enable_search, max_turns,
                  thinking) -> AgentResult:
        strict = STRICT_SCHEMAS.get(schema, schema)
        tools = [{"type": "function", "function": {"name": s.name, "description": s.description[:1000],
                                                   "parameters": s.input_schema}} for s in specs]
        allowed = {s.name for s in specs}
        messages: list[dict] = [{"role": "system", "content": system + LOCAL_RULES},
                                {"role": "user", "content": user}]
        trace: list[ToolCall] = []
        reasoning_parts: list[str] = []
        candidate = ""
        seen_calls: set[str] = set()

        # 1) tool-selection turns: thinking off (simple decisions, ~3 s each)
        if tools:
            for _turn in range(max_turns):
                msg, _ = await self._chat(agent, model, messages, think=False, tools=tools)
                content, reasoning = split_reasoning(msg)
                if reasoning:
                    reasoning_parts.append(reasoning)
                calls = msg.get("tool_calls") or []
                if not calls:
                    candidate = content
                    break
                messages.append({"role": "assistant", "content": content or "", "tool_calls": calls})
                parsed = []
                for c in calls:
                    fn = c.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parsed.append((c, fn.get("name", ""), args if isinstance(args, dict) else {}))
                keys = [n + json.dumps(a, sort_keys=True, default=str) for _, n, a in parsed]
                if all(k in seen_calls for k in keys):
                    # small local models tend to repeat the same calls until the turn limit; the results are
                    # already in context, so go straight to the final answer
                    messages.pop()
                    log.info("local agent %s: repeated tool calls, ending the tool phase", agent)
                    break
                seen_calls.update(keys)
                results = await asyncio.gather(*(GeminiBackend._exec(executor, n, a, allowed) for _, n, a in parsed))
                for (c, name, _a), (data, tc) in zip(parsed, results):
                    trace.append(tc)
                    messages.append({"role": "tool", "tool_call_id": c.get("id") or name, "name": name,
                                     "content": json.dumps(data, default=str, separators=(",", ":"))})

        # 2) final turn: JSON only (lesson 6), thinking per preset. Quick mode may reuse a valid direct answer.
        think = self.thinks(agent, final=True)
        output, error = None, None
        if candidate and not think:
            try:
                output = parse_output(candidate, strict)
            except (ValueError, ValidationError):
                output = None
        if output is None:
            if tools:
                messages.append({"role": "user", "content": FINAL_JSON_PROMPT})
            last_err: Exception | None = None
            for attempt in range(1 + config.LOCAL_VALIDATION_RETRIES):
                msg, finish = await self._chat(agent, model, messages, think=think, schema=strict)
                text, reasoning = split_reasoning(msg)
                if reasoning:
                    reasoning_parts.append(reasoning)
                if not text and finish == "length":
                    last_err = ValueError("answer truncated (max_tokens reached while thinking)")
                else:
                    try:
                        output = parse_output(text, strict)
                        break
                    except (ValueError, ValidationError) as e:
                        last_err = e
                problems = validation_problems(last_err)
                log.warning("local agent %s: invalid output (attempt %d): %s", agent, attempt + 1, "; ".join(problems)[:300])
                messages.append({"role": "assistant", "content": text or "(empty)"})
                messages.append({"role": "user", "content": feedback_prompt(text or "(empty)", problems)})
            if output is None:
                error = (f"invalid output after {1 + config.LOCAL_VALIDATION_RETRIES} attempts: "
                         + "; ".join(validation_problems(last_err)))[:400]
        return AgentResult(output=output, text=candidate, trace=trace, cost_usd=0.0, error=error,
                           searched=any(t.tool == "web_search" for t in trace),
                           reasoning="\n\n".join(reasoning_parts))

    async def probe(self, model: str, feature: str) -> tuple[bool, bool]:
        return False, True  # no Google Search grounding (lesson 9): the web_search MCP tool is used instead
