"""Pydantic schemas shared across agents, orchestrator, UI and scoreboard."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    result_bytes: int = 0
    error: str | None = None


class AgentOutput(BaseModel):
    """What the LLM must return (JSON schema enforced via response_schema)."""

    score: float = Field(description="-1 (strong down) .. +1 (strong up) for the horizon")
    confidence: float = Field(description="0..1; lower if data is conflicting or insufficient")
    key_points: list[str] = Field(description="3-5 short bullet points citing numbers from tools")
    reasoning: str = Field(description="<= 120 words")
    sources: list[str] = Field(default_factory=list, description="tool names or URLs used")


class AgentReport(AgentOutput):
    agent: str
    trace: list[ToolCall] = Field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None  # set when the agent failed and was dropped

    @property
    def ok(self) -> bool:
        return self.error is None


class NewsBrief(BaseModel):
    bullets: list[str] = Field(description="5-8 concise news bullets relevant to the asset")
    links: list[str] = Field(default_factory=list)


class DebateOutput(BaseModel):
    """What a debater LLM returns."""

    argument: str = Field(description="the argument; respect the word limit in the instructions")
    strongest_opponent_point: str = Field(description="the opponent point being rebutted, in one short sentence")


DebateTurn = Literal["opening", "rebuttal", "reply"]


class DebateArgument(DebateOutput):
    """One turn of the sequential debate: Bull opens → Bear rebuts → Bull replies."""

    side: Literal["bull", "bear"]
    turn: DebateTurn | None = None  # None = stored before the debate became sequential


class Verdict(BaseModel):
    adjustment: float = Field(description="-0.2..+0.2 adjustment to the team score")
    final_confidence: float = Field(description="0..1")
    headline: str = Field(description="one witty line summarising the debate outcome")
    summary: str = Field(description="2-4 sentences")
    key_risks: list[str] = Field(description="2-4 key risks")


class Forecast(BaseModel):
    price_now: float
    p10: float
    p50: float
    p90: float
    prob_up: float
    sigma_per_step: float
    steps: int
    step_label: str
    fan: dict[str, list[float]] = Field(default_factory=dict)  # p10/p50/p90 per step


class Prediction(BaseModel):
    id: int | None = None
    created_at: str
    ticker: str
    asset_type: str
    mode: Literal["swing", "scalp"]
    horizon: str
    target_time: str
    s_raw: float
    s_final: float
    confidence: float
    weights: dict[str, float]
    reports: list[AgentReport]
    news: NewsBrief | None = None
    debate: list[DebateArgument] = Field(default_factory=list)
    verdict: Verdict | None = None
    forecast: Forecast
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    search_mode: str = "off"  # grounded | news_fallback | off
