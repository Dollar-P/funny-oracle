"""Judge (§4.5): reports + debate + S_raw → Verdict, adjustment capped at ±0.2."""
from __future__ import annotations

import config
from agents.base import Context, is_pro, load_prompt, render
from agents.debate import reports_digest, transcript
from core.llm import LLM
from core.schemas import AgentReport, DebateArgument, Verdict, VerdictOutput, VerdictPro, to_verdict


class Judge:
    def __init__(self, llm: LLM, model: str, thinking: str = config.JUDGE_THINKING):
        self.llm, self.model, self.thinking = llm, model, thinking

    async def run(self, ctx: Context, reports: list[AgentReport], debate: list[DebateArgument],
                  s_raw: float) -> tuple[Verdict | None, str | None]:
        system = render(load_prompt("judge", ctx.prompt_version), ticker=ctx.ticker, asset_type=ctx.asset_type, mode=ctx.mode,
                        horizon=ctx.horizon, s_raw=f"{s_raw:+.3f}")
        parts = ["Analyst reports (with tool trace):", reports_digest(reports, with_trace=True)]
        if debate:
            parts.append("\nDebate (Bull opens → Bear rebuts → Bull replies):")
            parts.append(transcript(debate))
        else:
            parts.append("\n(No debate in this mode.)")
        res = await self.llm.run_agent(agent="judge", system=system, user="\n".join(parts), schema=VerdictPro if is_pro(ctx.prompt_version) else VerdictOutput,
                                       model=self.model, thinking=self.thinking, max_turns=1)
        if res.output is None:
            return None, res.error
        v: Verdict = to_verdict(res.output)  # type: ignore[arg-type]
        cap = config.JUDGE_MAX_ADJ
        v.adjustment = max(-cap, min(cap, float(v.adjustment)))
        v.final_confidence = max(0.0, min(1.0, float(v.final_confidence)))
        return v, None
