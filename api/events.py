"""Event models (REDESIGN §2.2) — documentation + validation of what the SSE stream carries.

The backend emits plain dicts (core/events.py); `validate_event` checks one against these models.
Extra keys are allowed so payloads can grow without breaking old clients.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Ev(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    run_id: str | None
    ts: int
    seq: int


class RunStarted(_Ev):
    ticker: str
    asset_type: str
    mode: Literal["swing", "scalp"]
    horizon: str
    models: dict[str, str]
    agents: list[str]
    est_cost_thb: float
    mock: bool = False


class Stage(_Ev):
    name: Literal["news", "analysts", "aggregate", "debate", "judge", "quant", "done"]


class AgentStarted(_Ev):
    agent: str


class ToolCallEv(_Ev):
    agent: str
    tool: str
    args: dict[str, Any]


class ToolResult(_Ev):
    agent: str
    tool: str
    ms: float
    bytes: int
    ok: bool


class Search(_Ev):
    agent: str
    query: str


class NewsBriefEv(_Ev):
    bullets: list[str]
    links: list[str] = []


class AgentDone(_Ev):
    agent: str
    report: dict[str, Any]


class AgentFailed(_Ev):
    agent: str
    reason: Literal["timeout", "error"]


class Aggregate(_Ev):
    s_raw: float
    weights: dict[str, float]


class DebateTurn(_Ev):
    side: Literal["bull", "bear"]
    turn: int
    text: str
    responding_to: str | None = None


class JudgeEv(_Ev):
    verdict: dict[str, Any] | None
    s_final: float


class Quant(_Ev):
    price_now: float
    p10: float
    p50: float
    p90: float
    prob_up: float
    fan: dict[str, list[float]]


class CostUpdate(_Ev):
    run_cost_thb: float
    total_spent_thb: float
    budget_thb: float


class RunDone(_Ev):
    prediction_id: int | None
    prediction: dict[str, Any]


class ErrorEv(_Ev):
    message: str
    fatal: bool


MODELS: dict[str, type[_Ev]] = {
    "run_started": RunStarted, "stage": Stage, "agent_started": AgentStarted, "tool_call": ToolCallEv,
    "tool_result": ToolResult, "search": Search, "news_brief": NewsBriefEv, "agent_done": AgentDone,
    "agent_failed": AgentFailed, "aggregate": Aggregate, "debate_turn": DebateTurn, "judge": JudgeEv,
    "quant": Quant, "cost_update": CostUpdate, "run_done": RunDone, "error": ErrorEv,
}


def validate_event(ev: dict) -> _Ev:
    model = MODELS.get(ev.get("type", ""))
    if model is None:
        raise ValueError(f"unknown event type {ev.get('type')!r}")
    return model.model_validate(ev)
