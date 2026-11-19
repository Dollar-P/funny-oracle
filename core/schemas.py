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


# --- strict variants for local models (LOCAL_LLM.md lessons 1-2) -------------------------------
# LM Studio's json_schema enforces structure but ignores minimum/maximum (scores came back as 72.5), so the
# openai_compat provider validates with these and retries with feedback. Descriptions repeat the rule in the
# schema the model sees.
DECIMAL_SCORE = "decimal from -1.0 to 1.0, e.g. 0.35 or -0.2, NOT a percentage"
DECIMAL_CONF = "decimal from 0.0 to 1.0, e.g. 0.7, NOT a percentage"


class AgentOutputStrict(AgentOutput):
    score: float = Field(ge=-1.0, le=1.0, description=DECIMAL_SCORE)
    confidence: float = Field(ge=0.0, le=1.0, description=DECIMAL_CONF)
    key_points: list[str] = Field(min_length=1, max_length=6,
                                  description="3-5 short bullet points citing numbers from tools")


class VerdictStrict(Verdict):
    adjustment: float = Field(ge=-1.0, le=1.0, description="decimal from -0.2 to 0.2, e.g. 0.05, NOT a percentage")
    final_confidence: float = Field(ge=0.0, le=1.0, description=DECIMAL_CONF)


STRICT_SCHEMAS: dict[type, type] = {AgentOutput: AgentOutputStrict, Verdict: VerdictStrict}


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
    cost_usd: float = 0.0          # tracked (searches at list price)
    cost_actual_usd: float | None = None  # estimated actual (free monthly search tier applied)
    elapsed_s: float = 0.0
    search_mode: str = "off"  # grounded | news_fallback | off
    models: dict[str, str] = Field(default_factory=dict)  # role → model ID used for this run
    run_id: str | None = None
    duel_id: str | None = None   # shared by both runs of a Model-vs-Model duel
    think_preset: str | None = None  # local models: quick | deep (LOCAL_LLM.md)
