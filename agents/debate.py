"""Bull vs Bear: one round, no tool calls (§4.5).

Both sides run in parallel and see the same reports, so each rebuts the opponent's
*strongest likely* point (derived from the reports) rather than the opponent's text.
"""
from __future__ import annotations

import asyncio
import json

from agents.base import Context, load_prompt, render
from core.llm import LLM
from core.schemas import AgentReport, DebateArgument


def reports_digest(reports: list[AgentReport], with_trace: bool = False) -> str:
    rows = []
    for r in reports:
        if not r.ok:
            rows.append({"agent": r.agent, "status": f"absent ({r.error})"})
            continue
        row = {"agent": r.agent, "score": round(r.score, 2), "confidence": round(r.confidence, 2),
               "key_points": r.key_points, "reasoning": r.reasoning}
        if with_trace:
            row["tools_called"] = [f"{t.tool}{' (ERROR)' if t.error else ''}" for t in r.trace]
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=1)


class DebateAgent:
    def __init__(self, llm: LLM, model: str, side: str, thinking: str):
        self.llm, self.model, self.side, self.thinking = llm, model, side, thinking

    async def run(self, ctx: Context, reports: list[AgentReport], s_raw: float) -> DebateArgument | None:
        system = render(load_prompt(self.side), ticker=ctx.ticker, asset_type=ctx.asset_type,
                        horizon=ctx.horizon, s_raw=f"{s_raw:+.2f}")
        res = await self.llm.run_agent(agent=self.side, system=system, schema=DebateArgument, model=self.model,
                                       user="Analyst reports:\n" + reports_digest(reports) +
                                            f'\n\nSet "side" to "{self.side}".',
                                       thinking=self.thinking, max_turns=1)
        if res.output is None:
            return None
        arg: DebateArgument = res.output  # type: ignore[assignment]
        arg.side = self.side  # type: ignore[assignment]
        return arg


async def run_debate(llm: LLM, model: str, ctx: Context, reports: list[AgentReport], s_raw: float,
                     thinking: str) -> list[DebateArgument]:
    out = await asyncio.gather(
        DebateAgent(llm, model, "bull", thinking).run(ctx, reports, s_raw),
        DebateAgent(llm, model, "bear", thinking).run(ctx, reports, s_raw),
    )
    return [a for a in out if a is not None]
