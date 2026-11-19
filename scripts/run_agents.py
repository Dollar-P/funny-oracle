"""Run one or more analyst agents (no debate/judge) and print reports + cost.

    python scripts/run_agents.py BTC-USD --agents technical
    python scripts/run_agents.py NVDA --agents all --mode swing --horizon 1M
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # ฿ on Windows consoles

import config  # noqa: E402
from agents import REGISTRY  # noqa: E402
from agents.base import Context  # noqa: E402
from core import cost  # noqa: E402
from core.asset import classify, normalize  # noqa: E402
from core.llm import LLM  # noqa: E402
from core.mcp_client import MarketDataClient  # noqa: E402


async def main(ticker: str, agents: list[str], mode: str, horizon: str) -> int:
    ticker = normalize(ticker)
    asset = classify(ticker)
    hz = config.HORIZONS[mode][horizon]
    ctx = Context(ticker=ticker, asset_type=asset, mode=mode, horizon=horizon, interval=hz["interval"])
    run_id = f"agents-{uuid.uuid4().hex[:8]}"
    llm = LLM(run_id=run_id)
    llm.guard.check_prediction(mode)
    model = config.models_for(mode)["analyst"]
    search_ok = config.ENABLE_SEARCH and mode == "swing" and await llm.supports(model, "search+functions")
    print(f"{ticker} ({asset}) {mode} {horizon} model={model} search+functions={search_ok}")
    async with MarketDataClient() as mc:
        objs = [REGISTRY[a](llm, model, mc, enable_search=search_ok) for a in agents]
        reports = await asyncio.gather(*(o.run(ctx) for o in objs))
    ok = 0
    for r in reports:
        status = "OK " if r.ok else "ERR"
        ok += r.ok
        print(f"\n[{status}] {r.agent:<11} score={r.score:+.2f} conf={r.confidence:.2f} cost=${r.cost_usd:.4f}")
        print("   tools:", ", ".join(f"{t.tool}({t.latency_ms:.0f}ms{' ERR' if t.error else ''})" for t in r.trace))
        for k in r.key_points:
            print("   -", k)
        if r.error:
            print("   error:", r.error)
    total = cost.run_cost(run_id)
    print(f"\nrun cost ${total:.4f} (฿{cost.usd_to_thb(total):.2f}) | total spent ฿{cost.usd_to_thb(cost.total_spent_usd()):.2f}"
          f" / ฿{config.BUDGET_THB:.0f}")
    return 0 if ok == len(reports) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--agents", default="technical")
    ap.add_argument("--mode", default="swing", choices=["swing", "scalp"])
    ap.add_argument("--horizon", default="1M")
    a = ap.parse_args()
    names = list(REGISTRY) if a.agents == "all" else a.agents.split(",")
    raise SystemExit(asyncio.run(main(a.ticker, names, a.mode, a.horizon)))
