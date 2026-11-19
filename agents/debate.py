"""Bull vs Bear: one sequential round, no tool calls (§4.5).

    1. Bull opens       (≤ 150 words)
    2. Bear rebuts      (reads the Bull's text, ≤ 150 words)
    3. Bull replies     (reads the Bear's rebuttal, ≤ 80 words)

If a turn fails, the debate stops there and the Judge sees whatever was said.
"""
from __future__ import annotations

import json

from agents.base import Context, load_prompt, render
from core.llm import LLM
from core.schemas import AgentReport, DebateArgument, DebateOutput

TURNS = [  # (prompt file, side, turn, word limit)
    ("bull", "bull", "opening", 150),
    ("bear", "bear", "rebuttal", 150),
    ("bull_reply", "bull", "reply", 80),
]


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


def transcript(turns: list[DebateArgument]) -> str:
    return "\n\n".join(f"[{d.side.upper()} — {d.turn or 'argument'}]\n{d.argument}" for d in turns)


def clip_words(text: str, limit: int) -> str:
    words = text.split()
    return text.strip() if len(words) <= limit else " ".join(words[:limit]).rstrip(",;:") + " …"


async def run_debate(llm: LLM, model: str, ctx: Context, reports: list[AgentReport], s_raw: float,
                     thinking: str) -> list[DebateArgument]:
    digest = reports_digest(reports)
    turns: list[DebateArgument] = []
    for prompt, side, turn, limit in TURNS:
        system = render(load_prompt(prompt), ticker=ctx.ticker, asset_type=ctx.asset_type,
                        horizon=ctx.horizon, s_raw=f"{s_raw:+.2f}")
        user = "Analyst reports:\n" + digest
        if turns:
            user += "\n\nDebate so far:\n" + transcript(turns)
        res = await llm.run_agent(agent=f"{side}_{turn}", system=system, user=user, schema=DebateOutput,
                                  model=model, thinking=thinking, max_turns=1)
        if res.output is None:
            break
        out: DebateOutput = res.output  # type: ignore[assignment]
        turns.append(DebateArgument(side=side, turn=turn, argument=clip_words(out.argument, limit),  # type: ignore[arg-type]
                                    strongest_opponent_point=out.strongest_opponent_point))
    return turns
