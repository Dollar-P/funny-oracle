"""LLM backtests with leakage rules (PRO.md §6.2).

1. Decision dates on or before the model's knowledge cutoff (config.MODEL_CUTOFFS; unknown = None) are
   **contaminated — not evidence**. By default they are not run at all; with `allow_contaminated=True` they run,
   are labelled, and the result can never produce a verdict.
2. Runs use `orchestrator.run_prediction(as_of=…)` with the point-in-time executor: no web search, no news,
   no current fundamentals/sentiment/macro — only price history and indicators truncated at the decision date.
3. Paid models need `confirm_cost=True` after seeing the estimate (local models are free; default for backtests).
The trades are simulated by the normal backtest engine (same Signal Engine, Risk Engine, fills, costs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

import pandas as pd

import config
from core import cost
from core.schemas import Prediction
from evaluation import ablation
from evaluation.backtest import BacktestConfig, Decision, prepare, run_backtest
from evaluation.pit_executor import PointInTimeExecutor
from trading import calendar as cal
from trading.costs import cost_model
from trading.signals import build_plan

UTC = timezone.utc
CONTAMINATED = "contaminated — not evidence"


class CostConfirmationRequired(RuntimeError):
    def __init__(self, estimate_thb: float, runs: int, model: str):
        self.estimate_thb, self.runs, self.model = estimate_thb, runs, model
        super().__init__(f"{runs} runs on {model} ≈ ฿{estimate_thb:.2f}; pass confirm_cost=True to run")


def cutoff_for(model: str) -> date | None:
    return config.MODEL_CUTOFFS.get(model)


def contamination(model: str, decision: datetime | date) -> str:
    """"clean" only when the decision date is strictly after a known cutoff."""
    c = cutoff_for(model)
    d = decision.date() if isinstance(decision, datetime) else decision
    return "clean" if c is not None and d > c else "contaminated"


@dataclass
class LLMBacktestConfig:
    tickers: dict[str, str]                       # ticker → asset_type
    start: str
    end: str
    model: str
    horizon: str = "1M"
    think_preset: str | None = "quick"
    prompt_version: str = config.PRO_PROMPT_VERSION
    temperature: float = config.PRO_TEMPERATURE
    allow_contaminated: bool = False
    confirm_cost: bool = False
    max_runs: int | None = None
    anonymize: bool = False
    starting_equity: float = 100_000.0
    variants: list[str] = field(default_factory=lambda: ["full", "team_no_debate"])


def horizon_bars(horizon: str, asset_type: str) -> int:
    st = config.HORIZONS["swing"][horizon]["steps"]
    return st[asset_type] if isinstance(st, dict) else st


def decision_points(cfg: LLMBacktestConfig, history: dict[str, pd.DataFrame], warmup: int = 210) -> list[dict]:
    """Same schedule as the backtest engine: first bar ≥ start (after warm-up), then every horizon bars."""
    out = []
    start, end = pd.Timestamp(cfg.start).date(), pd.Timestamp(cfg.end).date()
    for t, a in cfg.tickers.items():
        df = history[t].sort_index()
        days = [ts.date() for ts in df.index.tz_convert(UTC)]
        h = horizon_bars(cfg.horizon, a)
        first = next((i for i, d in enumerate(days) if d >= start and i >= warmup), None)
        if first is None:
            continue
        for i in range(first, len(days), h):
            if days[i] > end:
                break
            ts = df.index[i].to_pydatetime()
            as_of = cal.bar_close_time(a, "1d", ts, df.index[i + 1].to_pydatetime() if i + 1 < len(df) else None) \
                + timedelta(minutes=1)
            out.append({"ticker": t, "asset_type": a, "i": i, "ts": df.index[i], "as_of": as_of,
                        "label": contamination(cfg.model, as_of)})
    return sorted(out, key=lambda p: (p["as_of"], p["ticker"]))


Runner = Callable[..., Awaitable[Prediction]]


async def run_llm_backtest(cfg: LLMBacktestConfig, history: dict[str, pd.DataFrame], runner: Runner | None = None,
                           progress: Callable[[dict], None] | None = None) -> dict:
    if runner is None:
        import orchestrator

        runner = orchestrator.run_prediction
    points = decision_points(cfg, history)
    todo = [p for p in points if p["label"] == "clean" or cfg.allow_contaminated]
    if cfg.max_runs is not None:
        todo = todo[: cfg.max_runs]
    paid = not cost.is_free(cfg.model)
    est = cost.usd_to_thb(cost.BudgetGuard().estimate_usd("swing", cfg.model, world="pro")) * len(todo) if paid else 0.0
    if paid and todo and not cfg.confirm_cost:
        raise CostConfirmationRequired(est, len(todo), cfg.model)

    preds: dict[tuple[str, pd.Timestamp], dict] = {}
    for k, p in enumerate(todo):
        ex = PointInTimeExecutor(p["ticker"], p["asset_type"], p["as_of"], history[p["ticker"]], anonymize=cfg.anonymize)
        try:
            pred = await runner(p["ticker"], "swing", cfg.horizon, executor=ex, analyst_model=cfg.model,
                                think_preset=cfg.think_preset if cfg.model.startswith(config.LOCAL_PREFIX) else None,
                                prompt_version=cfg.prompt_version, temperature=cfg.temperature, as_of=p["as_of"],
                                save=False, source="backtest")
            refused = [n for n, _a in ex.calls if n not in ("get_price_history", "get_indicators")]
            preds[(p["ticker"], p["ts"])] = {"prediction": pred, "label": p["label"], "refused_calls": refused}
        except Exception as e:
            preds[(p["ticker"], p["ts"])] = {"prediction": None, "label": p["label"], "error": f"{type(e).__name__}: {e}"[:200]}
        if progress:
            progress({"done": k + 1, "total": len(todo), "ticker": p["ticker"], "as_of": p["as_of"].isoformat()})

    data = {t: (a, history[t]) for t, a in cfg.tickers.items()}
    prep = prepare(data)
    hb = {a: horizon_bars(cfg.horizon, a) for a in set(cfg.tickers.values())}
    results = {}
    for v in cfg.variants:
        strat = PrecomputedStrategy(preds, v, allow_contaminated=cfg.allow_contaminated)
        r = run_backtest(strat, prep, BacktestConfig(start=cfg.start, end=cfg.end, horizon_bars=hb,
                                                     starting_equity=cfg.starting_equity))
        results[v] = {"summary": r.summary(), "equity": r.equity, "trades": r.trades}

    labels = {"clean": sum(p["label"] == "clean" for p in points),
              "contaminated": sum(p["label"] == "contaminated" for p in points)}
    rows = []
    for (t, ts), rec in preds.items():
        pred = rec["prediction"]
        df = history[t]
        i = df.index.get_loc(ts)
        h = horizon_bars(cfg.horizon, cfg.tickers[t])
        actual = float(df["Close"].iloc[i + h]) if i + h < len(df) else None
        rows.append({"ticker": t, "ts": ts.isoformat(), "label": rec["label"], "error": rec.get("error"),
                     "refused_calls": rec.get("refused_calls", []),
                     "prob_up": pred.forecast.prob_up if pred else None, "p10": pred.forecast.p10 if pred else None,
                     "p50": pred.forecast.p50 if pred else None, "p90": pred.forecast.p90 if pred else None,
                     "price": pred.forecast.price_now if pred else None, "actual": actual,
                     "s_final": pred.s_final if pred else None, "cost_usd": pred.cost_usd if pred else 0.0})
    clean_rows = [r for r in rows if r["label"] == "clean" and r["prob_up"] is not None]
    return {"model": cfg.model, "cutoff": str(cutoff_for(cfg.model)) if cutoff_for(cfg.model) else None,
            "labels": labels, "runs": len(todo), "estimate_thb": est, "predictions": rows,
            "verdict_eligible": not cfg.allow_contaminated and len(clean_rows) > 0 and labels["clean"] > 0,
            "contamination_note": None if labels["contaminated"] == 0 else
            f"{labels['contaminated']} decision dates on/before the model cutoff are {CONTAMINATED}"
            + (" and were not run" if not cfg.allow_contaminated else " (run, labelled, excluded from verdicts)"),
            "variants": results}


class PrecomputedStrategy:
    """Plays back stored LLM predictions (or an ablation variant of them) through the Signal Engine."""

    def __init__(self, preds: dict, variant_name: str = "full", allow_contaminated: bool = False):
        self.preds, self.variant_name, self.allow = preds, variant_name, allow_contaminated
        self.name = f"llm:{variant_name}"
        self.last_flat: str | None = None
        self.last_prediction: dict | None = None

    def decide(self, ticker, view, ctx):
        rec = self.preds.get((ticker, view.ts))
        self.last_flat = None
        if rec is None:
            self.last_flat = "contaminated" if not self.allow else "no_prediction"
            return None
        if rec["prediction"] is None:
            self.last_flat = "run_failed"
            return None
        if rec["label"] != "clean" and not self.allow:
            self.last_flat = "contaminated"
            return None
        pred = ablation.variant(rec["prediction"], self.variant_name)
        if pred is None:
            self.last_flat = "variant_unavailable"
            return None
        self.last_prediction = {"prob_up": pred.forecast.prob_up, "p10": pred.forecast.p10, "p50": pred.forecast.p50,
                                "p90": pred.forecast.p90, "price": pred.forecast.price_now, "label": rec["label"]}
        if not math.isfinite(ctx.atr):
            self.last_flat = "no_data"
            return None
        plan = build_plan(pred, atr=ctx.atr, params=ctx.signal, costs=cost_model(ctx.asset_type))
        if plan.direction == "flat":
            self.last_flat = plan.flat_reason
            return None
        return Decision(direction=plan.direction, stop=plan.stop, targets=list(plan.targets),
                        hold_bars=ctx.horizon_bars, plan=plan, entry_ref=plan.entry.ref_price)
