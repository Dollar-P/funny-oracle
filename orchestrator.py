"""Main pipeline (§2, §3): one prediction end to end.

    python orchestrator.py NVDA swing 1M
    python orchestrator.py BTC-USD scalp 15m
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

import config
from agents import REGISTRY
from agents.base import Context
from agents.debate import run_debate
from agents.judge import Judge
from agents.news_researcher import NewsResearcher
from core import cost, scoreboard
from core.asset import classify, normalize
from core.cost import BudgetGuard
from core.llm import LLM, ToolExecutor
from core.mcp_client import MarketDataClient
from core.schemas import AgentReport, Prediction
from mcp_server import data_sources as ds
from quant.montecarlo import sigma_per_bar, simulate, weighted_score

log = logging.getLogger("orchestrator")

Progress = Callable[[str], None]


class _AgentExecutor:
    """Per-agent view of the MCP client that reports live progress ("Technical calling get_indicators…")."""

    def __init__(self, base: ToolExecutor, agent: str, progress: Progress):
        self.base, self.agent, self.progress = base, agent, progress

    async def list_tools(self):
        return await self.base.list_tools()

    async def call(self, name, args=None):
        self.progress(f"{self.agent.title()} agent calling {name}({', '.join(f'{k}={v}' for k, v in (args or {}).items())})")
        return await self.base.call(name, args)


def _validate(mode: str, horizon: str) -> None:
    if mode not in config.HORIZONS:
        raise ValueError(f"mode must be one of {list(config.HORIZONS)}")
    if horizon not in config.HORIZONS[mode]:
        raise ValueError(f"horizon for {mode} must be one of {list(config.HORIZONS[mode])}")


async def _search_mode(llm: LLM, model: str, mode: str) -> str:
    """grounded | news_search | news_tool | off"""
    if mode != "swing":
        return "off"  # scalp: latency first, no web search
    if not config.ENABLE_SEARCH:
        return "news_tool"
    if await llm.supports(model, "search+functions"):
        return "grounded"
    if await llm.supports(model, "search"):
        return "news_search"
    return "news_tool"


async def _with_timeout(coro, agent: str, timeout: float) -> AgentReport:
    try:
        return await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError:
        return AgentReport(agent=agent, score=0, confidence=0, key_points=[], reasoning="", sources=[],
                           error=f"timeout after {timeout:.0f}s")


async def run_prediction(ticker: str, mode: str = "swing", horizon: str = "1M", *,
                         progress: Progress | None = None, source: str = "manual",
                         llm: LLM | None = None, executor: ToolExecutor | None = None,
                         save: bool = True, seed: int | None = None) -> Prediction:
    t0 = time.perf_counter()
    say = progress or (lambda m: log.info(m))
    _validate(mode, horizon)
    ticker = normalize(ticker)
    asset = classify(ticker)                                         # [1]
    hz = config.HORIZONS[mode][horizon]
    run_id = uuid.uuid4().hex
    llm = llm or LLM(run_id=run_id)
    llm.run_id = run_id
    guard: BudgetGuard = llm.guard
    est = guard.check_prediction(mode)                                # budget guard BEFORE any API call
    say(f"Budget OK — estimated ฿{cost.usd_to_thb(est):.2f}, remaining ฿{cost.usd_to_thb(guard.remaining_usd()):.2f}")

    weights = config.WEIGHTS[mode][asset]                             # [2]
    active = [a for a in config.AGENTS if weights.get(a, 0) > 0]
    models = config.models_for(mode, llm.provider)
    analyst_kw: dict = {}
    if mode == "scalp":
        analyst_kw = {"max_turns": 3}

    # price data for the quant engine (doesn't need the LLM)
    say(f"Fetching volatility history for {ticker}")
    lookback = config.SIGMA_LOOKBACK[mode]
    px = await asyncio.to_thread(ds.fetch_ohlcv, ticker, hz["interval"], lookback if mode == "scalp" else "1y")
    sigma = sigma_per_bar(px["Close"])
    price_now = float(px["Close"].iloc[-1])

    ctx = Context(ticker=ticker, asset_type=asset, mode=mode, horizon=horizon, interval=hz["interval"])

    own_client = executor is None
    mc = MarketDataClient() if own_client else executor
    if own_client:
        await mc.__aenter__()  # type: ignore[union-attr]
    try:
        search_mode = await _search_mode(llm, models["analyst"], mode)
        say(f"Search mode: {search_mode}")
        if search_mode in ("news_search", "news_tool"):
            say("News Researcher gathering headlines")
            nr = NewsResearcher(llm, models["analyst"], mc, "search" if search_mode == "news_search" else "tool")
            ctx.news, _, _, err = await nr.run(ctx)
            if err:
                say(f"News Researcher failed: {err}")

        say(f"Running {len(active)} analysts in parallel: {', '.join(active)}")      # [3]
        agents = [REGISTRY[a](llm, models["analyst"], _AgentExecutor(mc, a, say),
                              enable_search=search_mode == "grounded", **analyst_kw) for a in active]
        reports = list(await asyncio.gather(*(_with_timeout(ag.run(ctx), ag.name, config.AGENT_TIMEOUT_S)
                                              for ag in agents)))
        for r in reports:
            say(f"{r.agent}: " + (f"score {r.score:+.2f} (conf {r.confidence:.2f})" if r.ok else f"⚠ absent — {r.error}"))
    finally:
        if own_client:
            await mc.__aexit__(None, None, None)  # type: ignore[union-attr]

    ok = [r for r in reports if r.ok]
    s_raw = weighted_score({r.agent: (r.score, r.confidence) for r in ok}, weights)   # [4]
    say(f"S_raw = {s_raw:+.3f}")

    debate, verdict = [], None
    if ok:
        if mode == "swing":                                                            # [5]
            say("Bull vs Bear debate: Bull opens → Bear rebuts → Bull replies")
            debate = await run_debate(llm, models["judge"], ctx, reports, s_raw, config.ANALYST_THINKING)
        say("Judge deliberating")                                                       # [6]
        verdict, err = await Judge(llm, models["judge"]).run(ctx, reports, debate, s_raw)
        if err:
            say(f"Judge failed ({err}); using S_raw unadjusted")
    adj = verdict.adjustment if verdict else 0.0
    s_final = max(-1.0, min(1.0, s_raw + adj))
    if verdict:
        confidence = verdict.final_confidence
    elif ok:
        confidence = sum(r.confidence for r in ok) / len(ok)
    else:
        confidence = 0.0

    steps = hz["steps"][asset] if isinstance(hz["steps"], dict) else hz["steps"]       # [7]
    forecast = simulate(price_now, sigma, steps, s_final, step_label=hz["interval"], seed=seed)
    say(f"Monte Carlo: P10 {forecast.p10:,.2f} | P50 {forecast.p50:,.2f} | P90 {forecast.p90:,.2f}")

    now = datetime.now(timezone.utc)
    pred = Prediction(
        created_at=now.isoformat(timespec="seconds"), ticker=ticker, asset_type=asset, mode=mode,  # type: ignore[arg-type]
        horizon=horizon, target_time=(now + timedelta(minutes=hz["minutes"])).isoformat(timespec="seconds"),
        s_raw=s_raw, s_final=s_final, confidence=confidence, weights=weights, reports=reports, news=ctx.news,
        debate=debate, verdict=verdict, forecast=forecast, cost_usd=cost.run_cost(run_id),
        elapsed_s=round(time.perf_counter() - t0, 1), search_mode=search_mode,
    )
    if save:                                                                            # [8]
        scoreboard.save(pred, run_id, source)
        say(f"Saved prediction #{pred.id} (cost ${pred.cost_usd:.4f})")
    return pred


def format_prediction(p: Prediction) -> str:
    f = p.forecast
    lines = [
        f"🔮 {p.ticker} · {p.mode} · {p.horizon}   (#{p.id}, {p.elapsed_s:.0f}s, ${p.cost_usd:.4f} ≈ ฿{cost.usd_to_thb(p.cost_usd):.2f})",
        f"   {p.verdict.headline if p.verdict else '(no verdict)'}",
        f"   Now {f.price_now:,.2f}",
        f"   Bear P10 {f.p10:,.2f} | Base P50 {f.p50:,.2f} | Bull P90 {f.p90:,.2f} | P(up) {f.prob_up:.0%} | conf {p.confidence:.2f}",
        f"   S_raw {p.s_raw:+.3f} → S_final {p.s_final:+.3f} (judge adj {p.verdict.adjustment if p.verdict else 0:+.2f}); search={p.search_mode}",
        "   Agents:",
    ]
    for r in p.reports:
        w = p.weights.get(r.agent, 0)
        lines.append(f"     {r.agent:<11} w={w:.2f} " + (f"{r.score:+.2f} ({r.confidence:.2f})  tools={len(r.trace)}"
                                                         if r.ok else f"⚠ absent: {r.error}"))
    for d in p.debate:
        lines.append(f"   [{d.side.upper()} {d.turn or ''}] {d.argument[:220]}{'…' if len(d.argument) > 220 else ''}")
    lines.append("   Not investment advice.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Funny Oracle — one prediction")
    ap.add_argument("ticker")
    ap.add_argument("mode", choices=list(config.HORIZONS))
    ap.add_argument("horizon")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    progress = (lambda m: None) if a.quiet else (lambda m: print(f"  · {m}", flush=True))
    try:
        pred = asyncio.run(run_prediction(a.ticker, a.mode, a.horizon, progress=progress, source="cli"))
    except cost.BudgetExceeded as e:
        print(f"🛑 {e}")
        return 2
    print(format_prediction(pred))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
