"""News Researcher (§4.4): builds a shared NewsBrief before the analysts run.

Two modes:
- "search": Google Search grounding only (no function tools) — used when the model can't
  combine Search with function calling.
- "tool":   no web search available (or ENABLE_SEARCH=false) — uses the get_news MCP tool.
"""
from __future__ import annotations

from typing import Literal

from agents.base import Context, load_prompt, render
from core.llm import LLM, ToolExecutor
from core.schemas import NewsBrief


class NewsResearcher:
    name = "news_researcher"

    def __init__(self, llm: LLM, model: str, executor: ToolExecutor | None, mode: Literal["search", "tool"]):
        self.llm = llm
        self.model = model
        self.executor = executor
        self.mode = mode

    async def run(self, ctx: Context) -> tuple[NewsBrief | None, list, float, str | None]:
        """Returns (brief, trace, cost_usd, error)."""
        source_rule = (
            "Use Google Search." if self.mode == "search"
            else f'Use the get_news tool (ticker "{ctx.ticker}"; for macro context also try "^GSPC" or "GC=F").'
        )
        system = render(load_prompt("news_researcher"), ticker=ctx.ticker, asset_type=ctx.asset_type,
                        horizon=ctx.horizon, now=ctx.now, source_rule=source_rule)
        res = await self.llm.run_agent(
            agent=self.name, system=system, user=f"Research news for {ctx.ticker}.", schema=NewsBrief,
            model=self.model, tools=["get_news"] if self.mode == "tool" else None,
            executor=self.executor if self.mode == "tool" else None,
            enable_search=self.mode == "search", max_turns=3,
        )
        brief = res.output
        if brief is not None:
            brief.links = list(dict.fromkeys([*brief.links, *[s.split(" | ")[-1] for s in res.sources]]))[:10]
            brief.bullets = brief.bullets[:8]
        return brief, res.trace, res.cost_usd, res.error  # type: ignore[return-value]
