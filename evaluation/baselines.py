"""Baseline strategies (PRO.md §5.1). Every strategy sees only a PointInTimeView (bars ≤ decision time).

Momentum / SMA cross / random use the Signal Engine's stop rule for a zero-drift forecast (the wider of
k·ATR and the P10/P90 band edge), no targets, and a time exit at the horizon — then the same Risk Engine,
costs and fills as everything else. Quant-only builds a full Prediction (deterministic indicator labels →
score, analytic log-normal forecast identical in expectation to the Monte Carlo) and goes through the full
Signal Engine. Buy & hold and cash live in evaluation/backtest.py (benchmarks, not risk-sized).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

import config
from core.schemas import Forecast, Prediction, Verdict
from evaluation.backtest import Decision, PointInTimeView, StrategyCtx, neutral_stop
from mcp_server import indicators as ind
from trading.costs import cost_model
from trading.signals import build_plan

UTC = timezone.utc


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _k(ctx: StrategyCtx, mode: str = "swing") -> float:
    return ctx.signal.k_atr_scalp if mode == "scalp" else ctx.signal.k_atr_swing


def _allowed(direction: str, ctx: StrategyCtx) -> bool:
    return not (direction == "short" and ctx.asset_type == "crypto" and not ctx.signal.allow_crypto_short)


def _directional(direction: str, view: PointInTimeView, ctx: StrategyCtx) -> Decision | None:
    if not math.isfinite(ctx.sigma_daily) or not math.isfinite(ctx.atr) or not _allowed(direction, ctx):
        return None
    entry = float(view.close[-1])
    stop = neutral_stop(entry, direction, ctx.sigma_daily, ctx.horizon_bars, ctx.atr, _k(ctx))
    return Decision(direction=direction, stop=stop, targets=[], hold_bars=ctx.horizon_bars, entry_ref=entry)


class Momentum:
    """Time-series momentum: sign of the 3-month (63-bar) return."""

    def __init__(self, lookback: int = 63):
        self.lookback = lookback
        self.name = f"momentum_{lookback}"
        self.last_flat = None

    def decide(self, ticker, view, ctx):
        c = view.close
        if len(c) <= self.lookback:
            return None
        r = c[-1] / c[-1 - self.lookback] - 1
        if r == 0:
            return None
        d = _directional("long" if r > 0 else "short", view, ctx)
        self.last_flat = None if d else "short_not_allowed"
        return d


class SmaCross:
    """Long when SMA50 > SMA200, short otherwise."""

    name = "sma_50_200"

    def __init__(self):
        self.last_flat = None

    def decide(self, ticker, view, ctx):
        c = view.close
        if len(c) < 200:
            return None
        d = _directional("long" if c[-50:].mean() > c[-200:].mean() else "short", view, ctx)
        self.last_flat = None if d else "short_not_allowed"
        return d


class RandomStrategy:
    """Random direction at a given trade frequency, same sizing and exits as the other baselines (seeded)."""

    def __init__(self, seed: int, p_trade: float = 1.0):
        self.rng = np.random.default_rng(seed)
        self.p_trade = p_trade
        self.name = f"random_{seed}"
        self.last_flat = None

    def decide(self, ticker, view, ctx):
        if self.rng.random() >= self.p_trade:
            self.last_flat = "random_skip"
            return None
        dirs = ["long", "short"] if _allowed("short", ctx) else ["long"]
        self.last_flat = None
        return _directional(dirs[int(self.rng.integers(0, len(dirs)))], view, ctx)


# --- quant-only: deterministic labels + analytic forecast + full Signal Engine ------------------------------

LABEL_SCORES = {
    "sma_regime": ({"golden_cross_regime": 1, "death_cross_regime": -1}, 0.30),
    "trend_vs_sma200": ({"above": 1, "below": -1}, 0.20),
    "trend_vs_sma50": ({"above": 1, "below": -1}, 0.20),
    "macd_momentum": ({"bullish_strengthening": 1, "bullish_fading": 0.5, "bearish_fading": -0.5,
                       "bearish_strengthening": -1}, 0.20),
    "rsi_zone": ({"bullish": 0.5, "bearish": -0.5, "overbought": -0.5, "oversold": 0.5}, 0.10),
}


def label_score(signals: dict) -> float:
    s = 0.0
    for key, (vals, w) in LABEL_SCORES.items():
        s += w * vals.get(signals.get(key), 0)
    return max(-1.0, min(1.0, s))


def analytic_forecast(price: float, sigma_step: float, steps: int, s_final: float,
                      lam: float = config.LAMBDA) -> Forecast:
    """Closed form of quant/montecarlo.simulate: log return ~ N(S·λ·σ√T, σ√T)."""
    sh = sigma_step * math.sqrt(steps)
    mu = max(-1.0, min(1.0, s_final)) * lam * sh
    return Forecast(price_now=price, p10=price * math.exp(mu - 1.2815515655446004 * sh), p50=price * math.exp(mu),
                    p90=price * math.exp(mu + 1.2815515655446004 * sh), prob_up=_phi(mu / sh) if sh > 0 else 0.5,
                    sigma_per_step=sigma_step, steps=steps, step_label="1d")


class QuantOnly:
    """The existing quant stack with fixed rules and no LLM (§5.1)."""

    name = "quant_only"

    def __init__(self, confidence: float = 0.6, calibrator=None):
        self.confidence = confidence
        self.calibrator = calibrator
        self.last_prediction: dict | None = None
        self.last_flat: str | None = None

    def decide(self, ticker, view, ctx):
        self.last_flat = None
        if not math.isfinite(ctx.sigma_daily) or ctx.sigma_daily <= 0 or not math.isfinite(ctx.atr):
            self.last_flat = "no_data"
            return None
        df = view.df.tail(260)
        sig = ind.compute_all(df)["signals"]
        s = label_score(sig)
        price = float(view.close[-1])
        fc = analytic_forecast(price, ctx.sigma_daily, ctx.horizon_bars, s)
        ts = view.ts.to_pydatetime()
        pred = Prediction(created_at=ts.isoformat(), ticker=ticker, asset_type=ctx.asset_type, mode="swing",
                          horizon=f"{ctx.horizon_bars}d", target_time=(ts + timedelta(days=ctx.horizon_bars)).isoformat(),
                          s_raw=s, s_final=s, confidence=self.confidence, weights={}, reports=[],
                          verdict=Verdict(adjustment=0, final_confidence=self.confidence, headline="", summary="",
                                          key_risks=[]), forecast=fc, prompt_version="quant")
        self.last_prediction = {"prob_up": fc.prob_up, "p10": fc.p10, "p50": fc.p50, "p90": fc.p90, "price": price,
                                "s": s}
        plan = build_plan(pred, atr=ctx.atr, params=ctx.signal, costs=cost_model(ctx.asset_type),
                          calibrator=self.calibrator)
        if plan.direction == "flat":
            self.last_flat = plan.flat_reason
            return None
        return Decision(direction=plan.direction, stop=plan.stop, targets=list(plan.targets),
                        hold_bars=ctx.horizon_bars, plan=plan, entry_ref=price)
