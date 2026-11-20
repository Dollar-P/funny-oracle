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


class ScenarioProbs(BaseModel):
    """Pro (v2 prompts): probabilities of the bull / base / bear outcome; normalised to sum 1 in code."""

    bull: float = Field(description="decimal 0..1")
    base: float = Field(description="decimal 0..1")
    bear: float = Field(description="decimal 0..1")

    def normalized(self) -> "ScenarioProbs":
        vals = [max(0.0, float(v)) for v in (self.bull, self.base, self.bear)]
        tot = sum(vals)
        if tot <= 0:
            return ScenarioProbs(bull=1 / 3, base=1 / 3, bear=1 / 3)
        return ScenarioProbs(bull=vals[0] / tot, base=vals[1] / tot, bear=vals[2] / tot)


class KeyLevels(BaseModel):
    support: list[float] = Field(default_factory=list, description="support prices from tool results")
    resistance: list[float] = Field(default_factory=list, description="resistance prices from tool results")


class Catalyst(BaseModel):
    date: str = Field(description="ISO date YYYY-MM-DD")
    event: str = Field(description="e.g. earnings, CPI, FOMC")
    impact: Literal["high", "medium", "low"] = Field(default="medium", description="high | medium | low")


class AgentOutputPro(AgentOutput):
    """LLM-facing schema for Pro (v2) prompts. The Arena keeps AgentOutput, so its JSON schema is unchanged."""

    scenario_probs: ScenarioProbs = Field(description="bull/base/bear probabilities, decimals summing to 1")
    key_levels: KeyLevels = Field(description="support / resistance prices (empty lists if not applicable)")
    invalidation: str = Field(description="one sentence: the observable fact that would prove this view wrong")


class AgentReport(AgentOutput):
    agent: str
    # Pro (v2) fields — optional, the Arena never sets them
    scenario_probs: ScenarioProbs | None = None
    key_levels: KeyLevels | None = None
    invalidation: str | None = None
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


# What the Judge LLM returns in the Arena (v1). Its JSON schema must not change (no docstring: pydantic would
# put it in the schema as a description).
class VerdictOutput(BaseModel):
    adjustment: float = Field(description="-0.2..+0.2 adjustment to the team score")
    final_confidence: float = Field(description="0..1")
    headline: str = Field(description="one witty line summarising the debate outcome")
    summary: str = Field(description="2-4 sentences")
    key_risks: list[str] = Field(description="2-4 key risks")


class Verdict(VerdictOutput):
    """Stored verdict. Pro (v2) fields are optional; the Arena never sets them."""

    thesis: str | None = None
    invalidation: str | None = None
    catalysts: list[Catalyst] = Field(default_factory=list)
    no_trade_reason: str | None = None


class VerdictPro(VerdictOutput):
    """LLM-facing judge schema for Pro (v2) prompts."""

    headline: str = Field(description="one plain, factual line summarising the committee view")
    thesis: str = Field(description="the committee view in at most 80 words")
    invalidation: str = Field(description="one sentence: what would prove the thesis wrong within the horizon")
    catalysts: list[Catalyst] = Field(description="dated events inside the horizon; empty list if none")
    no_trade_reason: str = Field(description="why not to act, if the evidence is too weak; else empty string")


def to_verdict(v: VerdictOutput) -> Verdict:
    d = v.model_dump()
    if "no_trade_reason" in d:
        d["no_trade_reason"] = (d["no_trade_reason"] or "").strip() or None
    return Verdict(**d)


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


class VerdictStrict(VerdictOutput):
    adjustment: float = Field(ge=-1.0, le=1.0, description="decimal from -0.2 to 0.2, e.g. 0.05, NOT a percentage")
    final_confidence: float = Field(ge=0.0, le=1.0, description=DECIMAL_CONF)


class AgentOutputProStrict(AgentOutputPro):
    score: float = Field(ge=-1.0, le=1.0, description=DECIMAL_SCORE)
    confidence: float = Field(ge=0.0, le=1.0, description=DECIMAL_CONF)
    key_points: list[str] = Field(min_length=1, max_length=6,
                                  description="3-5 short bullet points citing numbers from tools")


class VerdictProStrict(VerdictPro):
    adjustment: float = Field(ge=-1.0, le=1.0, description="decimal from -0.2 to 0.2, e.g. 0.05, NOT a percentage")
    final_confidence: float = Field(ge=0.0, le=1.0, description=DECIMAL_CONF)


STRICT_SCHEMAS: dict[type, type] = {AgentOutput: AgentOutputStrict, Verdict: VerdictStrict, VerdictOutput: VerdictStrict,
                                    AgentOutputPro: AgentOutputProStrict, VerdictPro: VerdictProStrict}


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
    # Pro (additive; defaults reproduce the Arena)
    prompt_version: str = "v1"
    temperature: float | None = None
    experiment_id: int | None = None
    degraded: list[str] = Field(default_factory=list)  # data-quality flags (PRO.md §4) → Signal Engine forces FLAT
    as_of: str | None = None          # backtest mode: the decision time the run was restricted to
