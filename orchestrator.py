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
from core.events import NULL_SINK, EventSink, OnEvent
from core.llm import LLM, ToolExecutor
from core.mcp_client import MarketDataClient
from core.schemas import AgentReport, Prediction
from mcp_server import data_sources as ds
from quant.montecarlo import sigma_horizon_per_step, sigma_per_bar, simulate, weighted_score

log = logging.getLogger("orchestrator")

Progress = Callable[[str], None]


class _AgentExecutor:
    """Per-agent view of the MCP client that reports live progress ("Technical calling get_indicators…")
    and emits tool_call / tool_result events."""

    def __init__(self, base: ToolExecutor, agent: str, progress: Progress, events: EventSink = NULL_SINK):
        self.base, self.agent, self.progress, self.events = base, agent, progress, events

    async def list_tools(self):
        return await self.base.list_tools()

    async def call(self, name, args=None):
        self.progress(f"{self.agent.title()} agent calling {name}({', '.join(f'{k}={v}' for k, v in (args or {}).items())})")
        await self.events.emit("tool_call", agent=self.agent, tool=name, args=dict(args or {}))
        data, tc = await self.base.call(name, args)
        await self.events.emit("tool_result", agent=self.agent, tool=name, ms=tc.latency_ms, bytes=tc.result_bytes,
                               ok=tc.error is None, error=tc.error)
        return data, tc


SCALP_LOCAL_REFUSAL = ("Local models are too slow for Scalp mode (a 15-minute horizon would be over before the show "
                       "ends). Use Swing, or pick a Gemini model for Scalp.")


def _validate(mode: str, horizon: str) -> None:
    if mode not in config.HORIZONS:
        raise ValueError(f"mode must be one of {list(config.HORIZONS)}")
    if horizon not in config.HORIZONS[mode]:
        raise ValueError(f"horizon for {mode} must be one of {list(config.HORIZONS[mode])}")


def resolve_models(mode: str, provider: str | None = None, analyst_model: str | None = None,
                   judge_model: str | None = None) -> dict[str, str]:
    """Per-run override (REDESIGN §2.1); None falls back to .env for that role."""
    models = dict(config.models_for(mode, provider))
    if analyst_model:
        models["analyst"] = analyst_model
    if judge_model:
        models["judge"] = judge_model
    return models


async def _search_mode(llm: LLM, model: str, mode: str) -> str:
    """grounded | news_search | news_web | news_tool | off"""
    if mode != "swing":
        return "off"  # scalp: latency first, no web search
    if llm.provider == "local":
        return "news_web"  # no grounding: News Researcher with the web_search / fetch_url MCP tools
    if not config.ENABLE_SEARCH:
        return "news_tool"
    if await llm.supports(model, "search+functions"):
        return "grounded"
    if await llm.supports(model, "search"):
        return "news_search"
    return "news_tool"


async def _scalp_sigma(ticker: str, horizon: str, minutes: int, steps: int, fallback: float,
                       fallback_source: str) -> tuple[float, str]:
    """σ per step from horizon-scale returns over a longer lookback (config.SCALP_SIGMA)."""
    spec = config.SCALP_SIGMA.get(horizon)
    if not spec:
        return fallback, fallback_source
    try:
        df = await asyncio.to_thread(ds.fetch_ohlcv, ticker, spec["interval"], spec["period"])
        s = sigma_horizon_per_step(df["Close"], minutes, steps)
    except Exception as e:  # data hiccup → keep the per-bar estimate
        log.warning("horizon σ for %s %s failed: %s", ticker, horizon, e)
        s = None
    if s is None:
        return fallback, fallback_source + " (horizon-scale fallback)"
    return s, f"{horizon} returns from {spec['interval']} bars over {spec['period']}"


async def _with_timeout(coro, agent: str, timeout: float) -> AgentReport:
    try:
        return await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError:
        return AgentReport(agent=agent, score=0, confidence=0, key_points=[], reasoning="", sources=[],
                           error=f"timeout after {timeout:.0f}s")


async def _cost_update(events: EventSink, run_id: str, budget_thb: float) -> None:
    if not events.enabled:
        return
    await events.emit("cost_update", run_cost_thb=cost.usd_to_thb(cost.run_cost(run_id)),
                      run_cost_actual_thb=cost.usd_to_thb(cost.run_cost_actual(run_id)),
                      total_spent_thb=cost.usd_to_thb(cost.total_spent_usd()),
                      total_actual_thb=cost.usd_to_thb(cost.total_actual_usd()), budget_thb=budget_thb)


async def run_prediction(ticker: str, mode: str = "swing", horizon: str = "1M", *,
                         progress: Progress | None = None, source: str = "manual",
                         llm: LLM | None = None, executor: ToolExecutor | None = None,
                         save: bool = True, seed: int | None = None,
                         on_event: OnEvent | None = None, analyst_model: str | None = None,
                         judge_model: str | None = None, run_id: str | None = None,
                         duel_id: str | None = None, think_preset: str | None = None) -> Prediction:
    """One prediction end to end.

    on_event: async callback receiving every run event (REDESIGN §2.2); None → no events.
    analyst_model / judge_model: per-run override; None → the model from .env for that role.
    think_preset: local models only — "quick" (thinking off) or "deep" (LOCAL_LLM.md policy).
    """
    run_id = run_id or uuid.uuid4().hex
    events = EventSink(on_event, run_id)
    try:
        return await _run(ticker, mode, horizon, progress=progress, source=source, llm=llm, executor=executor,
                          save=save, seed=seed, events=events, analyst_model=analyst_model,
                          judge_model=judge_model, run_id=run_id, duel_id=duel_id, think_preset=think_preset)
    except cost.BudgetExceeded as e:
        await events.emit("error", message=str(e), fatal=True, kind="budget")
        raise
    except Exception as e:
        await events.emit("error", message=f"{type(e).__name__}: {e}"[:400], fatal=True, kind="error")
        raise


async def _run(ticker, mode, horizon, *, progress, source, llm, executor, save, seed, events: EventSink,
               analyst_model, judge_model, run_id, duel_id, think_preset=None) -> Prediction:
    t0 = time.perf_counter()
    say = progress or (lambda m: log.info(m))
    _validate(mode, horizon)
    ticker = normalize(ticker)
    asset = classify(ticker)                                         # [1]
    hz = config.HORIZONS[mode][horizon]
    if llm is None:
        provider = config.provider_for(analyst_model) if analyst_model else None
        llm = LLM(provider=provider, run_id=run_id, think_preset=think_preset)
    elif think_preset:
        llm.think_preset = think_preset
    llm.run_id = run_id
    local = llm.provider == "local"
    if local and mode == "scalp":
        raise ValueError(SCALP_LOCAL_REFUSAL)
    llm.events = events
    models = resolve_models(mode, llm.provider, analyst_model, judge_model)
    for role, m in models.items():
        if m and config.provider_for(m, llm.provider) != llm.provider:
            raise ValueError(f"{role} model {m!r} is not a {llm.provider} model; one provider per run")
    guard: BudgetGuard = llm.guard
    est = guard.check_prediction(mode, models["analyst"])             # budget guard BEFORE any API call
    say(f"Budget OK — estimated ฿{cost.usd_to_thb(est):.2f}, remaining ฿{cost.usd_to_thb(guard.remaining_usd()):.2f}")

    weights = config.WEIGHTS[mode][asset]                             # [2]
    active = [a for a in config.AGENTS if weights.get(a, 0) > 0]
    analyst_kw: dict = {}
    if mode == "scalp":
        analyst_kw = {"max_turns": 3}
    if local:
        analyst_kw = {"max_turns": config.LOCAL_MAX_TURNS}
    agent_timeout = config.LOCAL_TIMEOUT_S if local else config.AGENT_TIMEOUT_S
    # local: one GPU → agents take turns; each agent's timeout starts when it gets the GPU, not while queued
    agent_slots = asyncio.Semaphore(max(1, config.LOCAL_CONCURRENCY)) if local else None
    await events.emit("run_started", ticker=ticker, asset_type=asset, mode=mode, horizon=horizon, models=models,
                      agents=active, weights=weights, est_cost_thb=cost.usd_to_thb(est), duel_id=duel_id,
                      budget_thb=guard.budget_thb, mock=False,
                      think_preset=llm.think_preset if local else None)

    # price data for the quant engine (doesn't need the LLM)
    say(f"Fetching volatility history for {ticker}")
    lookback = config.SIGMA_LOOKBACK[mode]
    px = await asyncio.to_thread(ds.fetch_ohlcv, ticker, hz["interval"], lookback if mode == "scalp" else "1y")
    sigma = sigma_per_bar(px["Close"])
    price_now = float(px["Close"].iloc[-1])
    sigma_source = f"per-bar {hz['interval']} over {lookback if mode == 'scalp' else '1y'}"
    steps = hz["steps"][asset] if isinstance(hz["steps"], dict) else hz["steps"]
    if mode == "scalp":
        sigma, sigma_source = await _scalp_sigma(ticker, horizon, hz["minutes"], steps, sigma, sigma_source)
    say(f"σ per step {sigma:.5f} ({sigma_source})")

    ctx = Context(ticker=ticker, asset_type=asset, mode=mode, horizon=horizon, interval=hz["interval"])

    async def run_one(ag) -> AgentReport:
        if agent_slots is not None:
            async with agent_slots:
                return await _run_agent(ag)
        return await _run_agent(ag)

    async def _run_agent(ag) -> AgentReport:
        await events.emit("agent_started", agent=ag.name)
        r = await _with_timeout(ag.run(ctx), ag.name, agent_timeout)
        if r.ok:
            await events.emit("agent_done", agent=r.agent, report=r.model_dump(mode="json"))
        else:
            await events.emit("agent_failed", agent=r.agent,
                              reason="timeout" if (r.error or "").startswith("timeout") else "error", detail=r.error)
        await _cost_update(events, run_id, guard.budget_thb)
        return r

    own_client = executor is None
    mc = MarketDataClient() if own_client else executor
    if own_client:
        await mc.__aenter__()  # type: ignore[union-attr]
    try:
        if mode == "swing":
            await events.emit("stage", name="news")
        search_mode = await _search_mode(llm, models["analyst"], mode)
        say(f"Search mode: {search_mode}")
        if search_mode in ("news_search", "news_tool", "news_web"):
            say("News Researcher gathering headlines")
            nr = NewsResearcher(llm, models["analyst"], _AgentExecutor(mc, NewsResearcher.name, say, events),
                                {"news_search": "search", "news_tool": "tool", "news_web": "web"}[search_mode])
            ctx.news, _, _, err = await nr.run(ctx)
            if err:
                say(f"News Researcher failed: {err}")
            if ctx.news is not None:
                await events.emit("news_brief", bullets=ctx.news.bullets, links=ctx.news.links)

        await events.emit("stage", name="analysts")
        say(f"Running {len(active)} analysts in parallel: {', '.join(active)}")      # [3]
        agents = [REGISTRY[a](llm, models["analyst"], _AgentExecutor(mc, a, say, events),
                              enable_search=search_mode == "grounded", **analyst_kw) for a in active]
        reports = list(await asyncio.gather(*(run_one(ag) for ag in agents)))
        for r in reports:
            say(f"{r.agent}: " + (f"score {r.score:+.2f} (conf {r.confidence:.2f})" if r.ok else f"⚠ absent — {r.error}"))
    finally:
        if own_client:
            await mc.__aexit__(None, None, None)  # type: ignore[union-attr]

    ok = [r for r in reports if r.ok]
    s_raw = weighted_score({r.agent: (r.score, r.confidence) for r in ok}, weights)   # [4]
    say(f"S_raw = {s_raw:+.3f}")
    await events.emit("stage", name="aggregate")
    await events.emit("aggregate", s_raw=s_raw, weights=weights, present=[r.agent for r in ok])

    debate, verdict = [], None
    if ok:
        if mode == "swing":                                                            # [5]
            await events.emit("stage", name="debate")
            say("Bull vs Bear debate: Bull opens → Bear rebuts → Bull replies")
            debate = await run_debate(llm, models["judge"], ctx, reports, s_raw, config.ANALYST_THINKING)
            await _cost_update(events, run_id, guard.budget_thb)
        await events.emit("stage", name="judge")
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
    await events.emit("judge", verdict=verdict.model_dump(mode="json") if verdict else None, s_raw=s_raw,
                      s_final=s_final, confidence=confidence)

    await events.emit("stage", name="quant")
    forecast = simulate(price_now, sigma, steps, s_final, step_label=hz["interval"], seed=seed)
    say(f"Monte Carlo: P10 {forecast.p10:,.2f} | P50 {forecast.p50:,.2f} | P90 {forecast.p90:,.2f}")

    now = datetime.now(timezone.utc)
    target_time = (now + timedelta(minutes=hz["minutes"])).isoformat(timespec="seconds")
    await events.emit("quant", price_now=forecast.price_now, p10=forecast.p10, p50=forecast.p50, p90=forecast.p90,
                      prob_up=forecast.prob_up, fan=forecast.fan, steps=forecast.steps,
                      step_label=forecast.step_label, sigma_per_step=forecast.sigma_per_step,
                      target_time=target_time)
    pred = Prediction(
        created_at=now.isoformat(timespec="seconds"), ticker=ticker, asset_type=asset, mode=mode,  # type: ignore[arg-type]
        horizon=horizon, target_time=target_time,
        s_raw=s_raw, s_final=s_final, confidence=confidence, weights=weights, reports=reports, news=ctx.news,
        debate=debate, verdict=verdict, forecast=forecast, cost_usd=cost.run_cost(run_id),
        cost_actual_usd=cost.run_cost_actual(run_id),
        elapsed_s=round(time.perf_counter() - t0, 1), search_mode=search_mode,
        models=models, run_id=run_id, duel_id=duel_id, think_preset=llm.think_preset if local else None,
    )
    if save:                                                                            # [8]
        scoreboard.save(pred, run_id, source)
        say(f"Saved prediction #{pred.id} (cost tracked ${pred.cost_usd:.4f}, est. actual ${pred.cost_actual_usd or 0:.4f})")
    await _cost_update(events, run_id, guard.budget_thb)
    await events.emit("stage", name="done")
    await events.emit("run_done", prediction_id=pred.id, prediction=pred.model_dump(mode="json"))
    return pred


def format_prediction(p: Prediction) -> str:
    f = p.forecast
    lines = [
        f"🔮 {p.ticker} · {p.mode} · {p.horizon}   (#{p.id}, {p.elapsed_s:.0f}s, tracked ${p.cost_usd:.4f} ≈ ฿{cost.usd_to_thb(p.cost_usd):.2f}"
        + (f", est. actual ฿{cost.usd_to_thb(p.cost_actual_usd):.2f})" if p.cost_actual_usd is not None else ")"),
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
