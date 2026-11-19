"""MockRunner (REDESIGN §2.3): replay a stored Prediction as a timed fake live show. No LLM, no cost.

Timing (scaled by MOCK_SPEED; 0 = instant): tool calls 300–900 ms, agents 3–8 s (in parallel),
debate turns ~2 s. Deterministic per run (seeded RNG) so a replay looks the same each time.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

import config
from core import cost
from core.asset import classify, normalize
from core.db import connect
from core.schemas import Prediction


# --- picking a stored prediction --------------------------------------------------

def stored_ids() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, ticker, asset_type, mode FROM predictions"
                            " WHERE prediction_json IS NOT NULL AND prediction_json != '{}' ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def pick(ticker: str, mode: str, exclude: set[int] | None = None) -> int | None:
    """Same ticker+mode → same asset type+mode → same mode → anything. Newest first."""
    ticker = normalize(ticker)
    asset = classify(ticker)
    rows = [r for r in stored_ids() if r["id"] not in (exclude or set())]
    for ok in (lambda r: r["ticker"] == ticker and r["mode"] == mode,
               lambda r: r["asset_type"] == asset and r["mode"] == mode,
               lambda r: r["mode"] == mode,
               lambda r: r["ticker"] == ticker,
               lambda r: True):
        hit = [r for r in rows if ok(r)]
        if hit:
            return int(hit[0]["id"])
    return None


def load(pid: int) -> Prediction | None:
    with connect() as conn:
        row = conn.execute("SELECT prediction_json FROM predictions WHERE id = ?", (pid,)).fetchone()
    return Prediction.model_validate_json(row[0]) if row and row[0] and row[0] != "{}" else None


def _scale_prices(p: Prediction, factor: float) -> None:
    f = p.forecast
    f.price_now, f.p10, f.p50, f.p90 = (x * factor for x in (f.price_now, f.p10, f.p50, f.p90))
    for k in ("p10", "p50", "p90"):
        if k in f.fan:
            f.fan[k] = [x * factor for x in f.fan[k]]


def adapt(p: Prediction, ticker: str | None, mode: str | None, horizon: str | None,
          live_price: float | None = None) -> Prediction:
    """Relabel a stored prediction for the requested ticker/horizon and shift its clock to now."""
    p = p.model_copy(deep=True)
    if ticker:
        t = normalize(ticker)
        if t != p.ticker:
            p.ticker, p.asset_type = t, classify(t)
        if live_price and p.forecast.price_now > 0:
            _scale_prices(p, live_price / p.forecast.price_now)
    if mode and horizon and mode == p.mode and horizon in config.HORIZONS[mode]:
        p.horizon = horizon
    now = datetime.now(timezone.utc)
    p.created_at = now.isoformat(timespec="seconds")
    p.target_time = (now + timedelta(minutes=config.HORIZONS[p.mode][p.horizon]["minutes"])).isoformat(timespec="seconds")
    return p


# --- building the timeline --------------------------------------------------------------

def _queries(tc) -> list[str]:
    a = tc.args or {}
    qs = a.get("queries") or ([a["query"]] if a.get("query") else [])
    return [str(q) for q in qs] or ["(search)"]


def timeline(p: Prediction, *, replay_of: int | None, models: dict | None = None,
             seed: int = 0) -> list[tuple[float, dict]]:
    """[(t_seconds, event_without_envelope)] for one show, sorted by time."""
    rng = random.Random(seed)
    out: list[tuple[float, dict]] = []

    def at(t: float, type_: str, **kw):
        out.append((round(t, 3), {"type": type_, **kw}))

    agents = [r.agent for r in p.reports]
    run_models = models or p.models or {"analyst": "stored", "judge": "stored"}
    at(0, "run_started", ticker=p.ticker, asset_type=p.asset_type, mode=p.mode, horizon=p.horizon,
       models=run_models, agents=agents, weights=p.weights, est_cost_thb=0.0, mock=True, replay_of=replay_of,
       budget_thb=config.BUDGET_THB)
    t = 0.4
    if p.mode == "swing":
        at(t, "stage", name="news")
        t += 1.0
        if p.news:
            at(t, "news_brief", bullets=p.news.bullets, links=p.news.links)
            t += 0.6
    at(t, "stage", name="analysts")
    t0 = t + 0.2
    ends = []
    for i, r in enumerate(p.reports):
        start = t0 + i * 0.25
        at(start, "agent_started", agent=r.agent)
        c = start + rng.uniform(0.3, 0.8)
        for tc in r.trace:
            if tc.tool in ("google_search", "web_search"):
                for q in _queries(tc):
                    at(c, "search", agent=r.agent, query=q)
                    c += rng.uniform(0.3, 0.6)
                c += rng.uniform(0.3, 0.9)
                continue
            at(c, "tool_call", agent=r.agent, tool=tc.tool, args=tc.args)
            c += rng.uniform(0.3, 0.9)
            at(c, "tool_result", agent=r.agent, tool=tc.tool, ms=tc.latency_ms or round(rng.uniform(300, 900)),
               bytes=tc.result_bytes, ok=tc.error is None, error=tc.error)
            c += rng.uniform(0.2, 0.8)
        end = max(c + 0.4, start + rng.uniform(3, 8))
        if r.ok:
            at(end, "agent_done", agent=r.agent, report=r.model_dump(mode="json"))
        else:
            reason = "timeout" if (r.error or "").startswith("timeout") else "error"
            at(end, "agent_failed", agent=r.agent, reason=reason, detail=r.error)
        at(end + 0.01, "cost_update", run_cost_thb=0.0, run_cost_actual_thb=0.0,
           total_spent_thb=cost.usd_to_thb(cost.total_spent_usd()),
           total_actual_thb=cost.usd_to_thb(cost.total_actual_usd()), budget_thb=config.BUDGET_THB)
        ends.append(end)
    t = max(ends or [t0]) + 0.6
    at(t, "stage", name="aggregate")
    at(t + 0.3, "aggregate", s_raw=p.s_raw, weights=p.weights, present=[r.agent for r in p.reports if r.ok])
    t += 1.5
    if p.debate:
        at(t, "stage", name="debate")
        t += 0.5
        for i, d in enumerate(p.debate, 1):
            at(t, "debate_turn", side=d.side, turn=i, turn_name=d.turn, text=d.argument,
               responding_to=d.strongest_opponent_point if i > 1 else None)
            t += 2.0 + len(d.argument) / 120  # roughly matches the client typewriter
    if p.verdict is not None or p.reports:
        at(t, "stage", name="judge")
        t += 1.5
        at(t, "judge", verdict=p.verdict.model_dump(mode="json") if p.verdict else None, s_raw=p.s_raw,
           s_final=p.s_final, confidence=p.confidence)
        t += 0.8
    f = p.forecast
    at(t, "stage", name="quant")
    t += 0.6
    at(t, "quant", price_now=f.price_now, p10=f.p10, p50=f.p50, p90=f.p90, prob_up=f.prob_up, fan=f.fan,
       steps=f.steps, step_label=f.step_label, sigma_per_step=f.sigma_per_step, target_time=p.target_time)
    t += 0.4
    at(t, "stage", name="done")
    at(t + 0.1, "run_done", prediction_id=replay_of, prediction=json.loads(p.model_dump_json()), mock=True)
    out.sort(key=lambda x: x[0])
    return out


async def play(run, p: Prediction, *, replay_of: int | None, models: dict | None = None,
               speed: float | None = None) -> None:
    """Push the timeline into `run` with real (scaled) delays."""
    speed = config.MOCK_SPEED if speed is None else speed
    last = 0.0
    for t, ev in timeline(p, replay_of=replay_of, models=models, seed=int(run.run_id[:8], 16)):
        if speed > 0 and t > last:
            await asyncio.sleep((t - last) * speed)
        last = t
        await run.push(ev)
