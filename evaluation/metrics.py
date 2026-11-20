"""Evaluation metrics (PRO.md §5.2) — pure functions, units stated explicitly.

Returns are simple fractions per period (0.01 = 1 %). `ppy` = periods per year used for annualisation
(daily snapshots incl. weekends → 365; see docs/pro-plan.md A9). Sharpe/Sortino use rf = 0.
R values are trade results in multiples of the planned risk.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

PPY_DAILY = 365
SD_EPS = 1e-12   # a per-period σ below this is float noise around a constant series → ratio reported as 0


def _arr(x) -> np.ndarray:
    return np.asarray(list(x) if not isinstance(x, (np.ndarray, pd.Series)) else x, dtype=float)


def returns_from_equity(equity: Sequence[float]) -> np.ndarray:
    e = _arr(equity)
    return e[1:] / e[:-1] - 1 if len(e) > 1 else np.array([])


def total_return(equity) -> float:
    e = _arr(equity)
    return float(e[-1] / e[0] - 1) if len(e) > 1 else 0.0


def cagr(equity, days: float) -> float:
    e = _arr(equity)
    if len(e) < 2 or days <= 0 or e[0] <= 0 or e[-1] <= 0:
        return 0.0
    return float((e[-1] / e[0]) ** (365.25 / days) - 1)


def volatility(r, ppy: float = PPY_DAILY) -> float:
    r = _arr(r)
    return float(r.std(ddof=1) * math.sqrt(ppy)) if len(r) > 1 else 0.0


def sharpe(r, ppy: float = PPY_DAILY) -> float:
    r = _arr(r)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if sd > SD_EPS else 0.0


def sortino(r, ppy: float = PPY_DAILY) -> float:
    """mean / downside deviation (target 0, over all periods)."""
    r = _arr(r)
    if len(r) < 2:
        return 0.0
    dd = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2)))
    return float(r.mean() / dd * math.sqrt(ppy)) if dd > SD_EPS else 0.0


def max_drawdown(equity) -> float:
    """Most negative E/peak − 1 (≤ 0)."""
    e = _arr(equity)
    if len(e) == 0:
        return 0.0
    return float(np.min(e / np.maximum.accumulate(e) - 1))


def calmar(equity, days: float) -> float:
    mdd = max_drawdown(equity)
    return cagr(equity, days) / abs(mdd) if mdd < 0 else 0.0


def win_rate(values) -> float:
    v = _arr(values)
    return float((v > 0).mean()) if len(v) else 0.0


def expectancy_r(rs) -> float:
    r = _arr(rs)
    return float(r.mean()) if len(r) else 0.0


def profit_factor(pnls) -> float:
    p = _arr(pnls)
    wins, losses = p[p > 0].sum(), -p[p < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def exposure(in_market) -> float:
    """Fraction of periods with at least one open position."""
    v = _arr(in_market)
    return float((v > 0).mean()) if len(v) else 0.0


def turnover(traded_notional: float, avg_equity: float, years: float) -> float:
    """Traded notional (both sides) per year as a multiple of average equity."""
    return traded_notional / avg_equity / years if avg_equity > 0 and years > 0 else 0.0


def beta(r, rb) -> float:
    r, rb = _arr(r), _arr(rb)
    if len(r) < 2 or rb.var(ddof=1) == 0:
        return 0.0
    return float(np.cov(r, rb, ddof=1)[0, 1] / rb.var(ddof=1))


def information_ratio(r, rb, ppy: float = PPY_DAILY) -> float:
    a = _arr(r) - _arr(rb)
    return sharpe(a, ppy)


# --- prediction quality ---------------------------------------------------------------------------

def hit_rate(prob_up, up) -> tuple[int, int]:
    """(hits, n) with predicted direction = P(up) > 0.5; exactly 0.5 is excluded (no call)."""
    p, y = _arr(prob_up), _arr(up)
    m = p != 0.5
    return int(((p[m] > 0.5) == (y[m] > 0.5)).sum()), int(m.sum())


def brier(p, y) -> float:
    p, y = _arr(p), _arr(y)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def log_loss(p, y, eps: float = 1e-12) -> float:
    p, y = np.clip(_arr(p), eps, 1 - eps), _arr(y)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else float("nan")


def pinball(q, y, tau: float) -> float:
    """Quantile (pinball) loss of quantile forecasts q at level tau, in price units."""
    d = _arr(y) - _arr(q)
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d))) if len(d) else float("nan")


def band_coverage(lo, hi, y) -> float:
    lo, hi, y = _arr(lo), _arr(hi), _arr(y)
    return float(((y >= lo) & (y <= hi)).mean()) if len(y) else float("nan")


def reliability(p, y, bins: int = 10) -> list[dict]:
    """Calibration curve: per probability bin, mean predicted vs observed frequency."""
    p, y = _arr(p), _arr(y)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        if m.any():
            out.append({"lo": float(edges[i]), "hi": float(edges[i + 1]), "n": int(m.sum()),
                        "mean_p": float(p[m].mean()), "freq": float(y[m].mean())})
    return out
