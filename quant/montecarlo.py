"""Monte Carlo (GBM) price forecast anchored on historical volatility (§4.6).

σ      = stdev of log returns over the lookback (per bar)
μ_adj  = S_final · λ · σ / √T per step   → total median shift = S·λ·σ·√T  (= S·λ σ_horizon)
paths  = 10,000 log-normal paths, no historical drift (μ_base = 0)

Increments are N(μ_adj, σ) in log space without the −σ²/2 Itô term, so S=0 gives a
median exactly at today's price and P(up) ≈ 50% — the "no edge" baseline.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config
from core.schemas import Forecast


def weighted_score(scores: dict[str, tuple[float, float]], weights: dict[str, float]) -> float:
    """S_raw = Σ(wᵢ·scoreᵢ·confᵢ) / Σ(wᵢ·confᵢ) over agents present (renormalizes for absentees)."""
    num = den = 0.0
    for agent, (score, conf) in scores.items():
        w = weights.get(agent, 0.0)
        num += w * score * conf
        den += w * conf
    return 0.0 if den <= 0 else max(-1.0, min(1.0, num / den))


def sigma_per_bar(close: pd.Series) -> float:
    r = np.log(close.astype(float)).diff().dropna()
    r = r[np.isfinite(r)]
    if len(r) < 10:
        raise ValueError(f"need ≥10 returns to estimate volatility, got {len(r)}")
    return float(r.std(ddof=1))


def horizon_returns(close: pd.Series, horizon_minutes: int) -> pd.Series:
    """Overlapping log returns spanning exactly `horizon_minutes` of wall-clock time.

    A return is kept only if the bar `horizon` minutes earlier exists (so returns across
    overnight / weekend gaps or missing bars are excluded rather than inflating σ).
    """
    c = close.astype(float)
    c = c[np.isfinite(c) & (c > 0)]
    c = c[~c.index.duplicated(keep="last")].sort_index()
    lagged = c.copy()
    lagged.index = lagged.index + pd.Timedelta(minutes=horizon_minutes)
    prev = lagged.reindex(c.index)
    r = np.log(c / prev).dropna()
    return r[np.isfinite(r)]


def sigma_horizon_per_step(close: pd.Series, horizon_minutes: int, steps: int,
                           min_returns: int = config.MIN_HORIZON_RETURNS) -> float | None:
    """σ per simulation step derived from horizon-scale returns: σ_horizon / √steps.

    Returns None when fewer than `min_returns` horizon returns exist (caller falls back).
    """
    r = horizon_returns(close, horizon_minutes)
    if len(r) < min_returns or steps <= 0:
        return None
    s = float(r.std(ddof=1))
    return s / math.sqrt(steps) if s > 0 else None


def simulate(price_now: float, sigma: float, steps: int, s_final: float, *, lam: float = config.LAMBDA,
             paths: int = config.MC_PATHS, step_label: str = "bar", seed: int | None = None,
             fan_points: int = 100) -> Forecast:
    if price_now <= 0 or sigma <= 0 or steps <= 0:
        raise ValueError("price_now, sigma and steps must be positive")
    s = max(-1.0, min(1.0, float(s_final)))
    mu = s * lam * sigma / math.sqrt(steps)
    rng = np.random.default_rng(seed)
    log_paths = np.cumsum(rng.normal(mu, sigma, size=(paths, steps)), axis=1)
    prices = price_now * np.exp(log_paths)
    final = prices[:, -1]
    p10, p50, p90 = np.percentile(final, [10, 50, 90])

    pct = np.percentile(prices, [10, 50, 90], axis=0)  # shape (3, steps)
    idx = np.unique(np.linspace(0, steps - 1, min(steps, fan_points)).round().astype(int))
    fan = {
        "step": [0] + [int(i) + 1 for i in idx],
        "p10": [price_now] + [float(x) for x in pct[0, idx]],
        "p50": [price_now] + [float(x) for x in pct[1, idx]],
        "p90": [price_now] + [float(x) for x in pct[2, idx]],
    }
    return Forecast(
        price_now=float(price_now), p10=float(p10), p50=float(p50), p90=float(p90),
        prob_up=float((final > price_now).mean()), sigma_per_step=float(sigma), steps=int(steps),
        step_label=step_label, fan=fan,
    )
