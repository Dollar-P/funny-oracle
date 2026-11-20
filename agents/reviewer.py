"""Reviewer agent (PRO.md §3.4): a short post-mortem per closed paper trade — max 1 LLM call, cheapest model."""
from __future__ import annotations

from pydantic import BaseModel, Field

import config
from agents.base import load_prompt, render
from core.llm import LLM


class ReviewOutput(BaseModel):
    post_mortem: str = Field(description="at most 60 words: thesis vs what happened")
    thesis_held: bool = Field(description="true if the move happened for the reasons the thesis gave")
    lesson: str = Field(description="one actionable sentence, or 'none'")


def cheapest_gemini() -> str:
    gem = [m for m in config.PRICES if m.startswith("gemini")]
    return min(gem, key=lambda m: config.PRICES[m][-1]["in"] + config.PRICES[m][-1]["out"])


def reviewer_model(setting: str, local_ok: bool) -> str:
    if setting and setting != "auto":
        return setting
    return config.LOCAL_PREFIX + config.LOCAL_MODEL if local_ok else cheapest_gemini()


def trade_digest(trade: dict) -> str:
    keys = ("ticker", "side", "horizon", "opened_at", "closed_at", "avg_entry", "avg_exit", "stop_initial", "targets",
            "exit_reason", "r_multiple", "mae_r", "mfe_r", "realized_pnl", "thesis", "invalidation")
    return "\n".join(f"{k}: {trade.get(k)}" for k in keys if trade.get(k) is not None)


async def review(trade: dict, model: str, llm: LLM | None = None) -> tuple[ReviewOutput | None, str | None]:
    llm = llm or LLM(provider=config.provider_for(model), temperature=config.PRO_TEMPERATURE)
    system = render(load_prompt("reviewer", "v2"), ticker=trade.get("ticker"), asset_type=trade.get("asset_type"))
    res = await llm.run_agent(agent="reviewer", system=system, user=trade_digest(trade), schema=ReviewOutput,
                              model=model, max_turns=1, thinking="low")
    return res.output, res.error  # type: ignore[return-value]
