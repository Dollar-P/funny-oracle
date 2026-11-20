"""News Researcher (§4.4): builds a shared NewsBrief before the analysts run.

Three modes:
- "search": Google Search grounding only (no function tools) — used when the model can't
  combine Search with function calling.
- "tool":   no web search available (or ENABLE_SEARCH=false) — uses the get_news MCP tool.
- "web":    providers without built-in search (local LLMs): the web_search + fetch_url MCP tools.
            It searches once and the brief is shared with Business / Macro / Psychology (LOCAL_LLM.md #9).
"""
from __future__ import annotations

from typing import Literal

from agents.base import Context, load_prompt, render
from core.llm import LLM, ToolExecutor
from core.schemas import NewsBrief


TOOLS = {"tool": ["get_news"], "web": ["web_search", "fetch_url", "get_news"]}


class NewsResearcher:
    name = "news_researcher"

    def __init__(self, llm: LLM, model: str, executor: ToolExecutor | None, mode: Literal["search", "tool", "web"]):
        self.llm = llm
        self.model = model
        self.executor = executor
        self.mode = mode

    async def run(self, ctx: Context) -> tuple[NewsBrief | None, list, float, str | None]:
        """Returns (brief, trace, cost_usd, error)."""
        if self.mode == "search":
            source_rule = "Use Google Search."
        elif self.mode == "web":
            source_rule = (f'Call web_search once or twice with specific queries (e.g. "{ctx.ticker} news", plus one '
                           "macro query), then fetch_url on the 1-2 most relevant results. get_news is also available. "
                           "Cite the urls you used in links.")
        else:
            source_rule = f'Use the get_news tool (ticker "{ctx.ticker}"; for macro context also try "^GSPC" or "GC=F").' 
        system = render(load_prompt("news_researcher", ctx.prompt_version), ticker=ctx.ticker, asset_type=ctx.asset_type,
                        horizon=ctx.horizon, now=ctx.now, source_rule=source_rule)
        res = await self.llm.run_agent(
            agent=self.name, system=system, user=f"Research news for {ctx.ticker}.", schema=NewsBrief,
            model=self.model, tools=TOOLS.get(self.mode),
            executor=self.executor if self.mode in TOOLS else None,
            enable_search=self.mode == "search", max_turns=3,
        )
        brief = res.output
        if brief is not None:
            brief.links = list(dict.fromkeys([*brief.links, *[s.split(" | ")[-1] for s in res.sources]]))[:10]
            brief.bullets = brief.bullets[:8]
        return brief, res.trace, res.cost_usd, res.error  # type: ignore[return-value]
