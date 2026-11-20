"""BaseAgent: loads prompts from agents/prompts/<version>/*.md and runs the LLM agent loop.

Prompt versions (PRO.md §3.1): v1 = the Arena's prompts (unchanged), v2 = Pro / Desk prompts. The version is
stored with every prediction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from core.llm import LLM, AgentResult, ToolExecutor
from core.schemas import AgentOutput, AgentOutputPro, AgentReport, NewsBrief

PROMPTS = Path(__file__).resolve().parent / "prompts"
METHODS_DIR = config.ROOT / "methods"     # §10.3 extension point (empty in v1)


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, **values) -> str:
    return template.format_map(_SafeDict(**{k: ("" if v is None else v) for k, v in values.items()}))


DEFAULT_PROMPT_VERSION = "v1"


def prompt_versions() -> list[str]:
    return sorted(p.name for p in PROMPTS.iterdir() if p.is_dir() and p.name.startswith("v"))


def load_prompt(name: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    if version not in prompt_versions():
        raise ValueError(f"unknown prompt version {version!r} (have {prompt_versions()})")
    return (PROMPTS / version / f"{name}.md").read_text(encoding="utf-8")


def is_pro(version: str) -> bool:
    return version != DEFAULT_PROMPT_VERSION


def focus_for(name: str, asset_type: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Pick the '## <asset_type>' section of agents/prompts/<version>/<name>.md."""
    text = load_prompt(name, version)
    sections = dict(re.findall(r"^## (\w+)\s*\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M))
    return sections.get(asset_type, "").strip()


def method_context(agent: str, methods: list[str] | None) -> str:
    """Inject method cards (methods/<name>.md) chosen for this agent. Empty in v1."""
    if not methods:
        return ""
    parts = []
    for m in methods:
        p = METHODS_DIR / f"{m}.md"
        if p.exists():
            parts.append(p.read_text(encoding="utf-8").strip())
    return ("\nApply these analysis methods:\n" + "\n\n".join(parts) + "\n") if parts else ""


@dataclass
class Context:
    ticker: str
    asset_type: str
    mode: str
    horizon: str
    interval: str                # data interval for this horizon
    news: NewsBrief | None = None
    methods: dict[str, list[str]] = field(default_factory=dict)
    prompt_version: str = DEFAULT_PROMPT_VERSION
    as_of: datetime | None = None   # backtest mode: the decision time (PRO.md §6.2); None = live

    @property
    def now(self) -> str:
        return (self.as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")


class BaseAgent:
    name: str = "base"
    role: str = "generalist"
    tools: dict[str, list[str]] = {}       # asset_type → tool names
    uses_search: bool = False
    uses_news_brief: bool = False

    def __init__(self, llm: LLM, model: str, executor: ToolExecutor, max_turns: int = config.MAX_AGENT_TURNS,
                 enable_search: bool = False, thinking: str = config.ANALYST_THINKING):
        self.llm = llm
        self.model = model
        self.executor = executor
        self.max_turns = max_turns
        self.enable_search = enable_search and self.uses_search
        self.thinking = thinking

    def system_prompt(self, ctx: Context) -> str:
        return render(
            load_prompt("_analyst", ctx.prompt_version),
            role=self.role, ticker=ctx.ticker, asset_type=ctx.asset_type, mode=ctx.mode, horizon=ctx.horizon,
            now=ctx.now, focus=focus_for(self.name, ctx.asset_type, ctx.prompt_version), max_turns=self.max_turns,
            context=method_context(self.name, ctx.methods.get(self.name)),
            search_rule=" or Google Search results" if self.enable_search else "",
        )

    def user_prompt(self, ctx: Context) -> str:
        lines = [
            f"Analyze {ctx.ticker} for the next {ctx.horizon} ({ctx.mode} mode).",
            f"Suggested price interval for this horizon: \"{ctx.interval}\".",
        ]
        if ctx.news and self.uses_news_brief:
            lines.append("\nShared news brief (from the News Researcher):")
            lines += [f"- {b}" for b in ctx.news.bullets]
        return "\n".join(lines)

    def tool_names(self, ctx: Context) -> list[str]:
        return self.tools.get(ctx.asset_type, self.tools.get("*", []))

    async def run(self, ctx: Context) -> AgentReport:
        res: AgentResult = await self.llm.run_agent(
            agent=self.name, system=self.system_prompt(ctx), user=self.user_prompt(ctx),
            schema=AgentOutputPro if is_pro(ctx.prompt_version) else AgentOutput,
            model=self.model, tools=self.tool_names(ctx), executor=self.executor,
            enable_search=self.enable_search, max_turns=self.max_turns, thinking=self.thinking,
        )
        return to_report(self.name, res)


def to_report(name: str, res: AgentResult) -> AgentReport:
    if res.output is None:
        return AgentReport(agent=name, score=0, confidence=0, key_points=[], reasoning="", sources=[],
                           trace=res.trace, cost_usd=res.cost_usd, error=res.error or "no output")
    o: AgentOutput = res.output  # type: ignore[assignment]
    tools_used = [t.tool for t in res.trace if not t.error]
    sources = list(dict.fromkeys([*o.sources, *tools_used, *res.sources]))
    pro: dict = {}
    if isinstance(o, AgentOutputPro):
        pro = {"scenario_probs": o.scenario_probs.normalized(), "key_levels": o.key_levels,
               "invalidation": o.invalidation}
    return AgentReport(
        **pro,
        agent=name,
        score=max(-1.0, min(1.0, float(o.score))),
        confidence=max(0.0, min(1.0, float(o.confidence))),
        key_points=o.key_points[:5],
        reasoning=o.reasoning,
        sources=sources[:20],
        trace=res.trace,
        cost_usd=res.cost_usd,
    )
