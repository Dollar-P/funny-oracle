from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quant.montecarlo import sigma_per_bar, simulate, weighted_score


def test_neutral_score_is_coin_flip():
    f = simulate(100, 0.02, 21, 0.0, seed=1)
    assert f.prob_up == pytest.approx(0.5, abs=0.02)
    assert f.p50 == pytest.approx(100, rel=0.005)
    assert f.p10 < f.p50 < f.p90


def test_full_confidence_shifts_median_half_sigma():
    sigma, T = 0.02, 21
    f = simulate(100, sigma, T, 1.0, seed=2)
    expected = 100 * math.exp(0.5 * sigma * math.sqrt(T))
    assert f.p50 == pytest.approx(expected, rel=0.005)
    assert f.prob_up == pytest.approx(0.6915, abs=0.02)  # Φ(0.5)
    neg = simulate(100, sigma, T, -1.0, seed=2)
    assert neg.prob_up == pytest.approx(1 - 0.6915, abs=0.02)


def test_band_width_matches_sigma():
    sigma, T = 0.03, 30
    f = simulate(100, sigma, T, 0.0, seed=3)
    z = 1.2816
    assert f.p90 == pytest.approx(100 * math.exp(z * sigma * math.sqrt(T)), rel=0.02)
    assert f.p10 == pytest.approx(100 * math.exp(-z * sigma * math.sqrt(T)), rel=0.02)


def test_score_is_clipped_and_fan_shape():
    f = simulate(50, 0.01, 15, 5.0, seed=4, step_label="1m")
    g = simulate(50, 0.01, 15, 1.0, seed=4, step_label="1m")
    assert f.p50 == pytest.approx(g.p50)
    assert f.fan["step"][0] == 0 and f.fan["step"][-1] == 15
    assert len(f.fan["p10"]) == len(f.fan["step"])
    assert all(a <= b <= c for a, b, c in zip(f.fan["p10"], f.fan["p50"], f.fan["p90"]))


def test_invalid_inputs():
    with pytest.raises(ValueError):
        simulate(0, 0.01, 10, 0)
    with pytest.raises(ValueError):
        simulate(100, 0.0, 10, 0)


def test_sigma_per_bar():
    rng = np.random.default_rng(0)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.015, 2000))))
    assert sigma_per_bar(close) == pytest.approx(0.015, rel=0.05)
    with pytest.raises(ValueError):
        sigma_per_bar(pd.Series([1.0, 2.0]))


def test_weighted_score_renormalizes():
    w = {"a": 0.5, "b": 0.5, "c": 0.0}
    assert weighted_score({"a": (1.0, 1.0), "b": (-1.0, 1.0)}, w) == 0
    assert weighted_score({"a": (1.0, 1.0)}, w) == 1.0           # b absent → renormalized
    assert weighted_score({"a": (1.0, 0.2), "b": (-1.0, 0.8)}, w) == pytest.approx(-0.6)
    assert weighted_score({"c": (1.0, 1.0)}, w) == 0.0            # zero weight only
    assert weighted_score({}, w) == 0.0


# --- horizon-scale σ (scalp) ------------------------------------------------------

from quant.montecarlo import horizon_returns, sigma_horizon_per_step  # noqa: E402


def _bars(n, freq_min, sigma_bar, seed=0, start="2026-09-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=f"{freq_min}min", tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, sigma_bar, n))), index=idx)


def test_horizon_returns_span_exactly_the_horizon():
    c = _bars(100, 15, 0.002)
    r = horizon_returns(c, 15)
    assert len(r) == 99
    assert r.iloc[0] == pytest.approx(math.log(c.iloc[1] / c.iloc[0]))
    r4h = horizon_returns(_bars(100, 60, 0.004), 240)
    assert len(r4h) == 96


def test_horizon_returns_skip_gaps():
    c = _bars(40, 15, 0.002)
    gapped = pd.concat([c.iloc[:20], c.iloc[20:].shift(1, freq="16h")])  # overnight gap
    r = horizon_returns(gapped, 15)
    assert len(r) == 38  # the return across the gap is excluded


def test_sigma_horizon_per_step_matches_random_walk():
    # 1h horizon on 1h bars with σ_bar 0.004 → σ_h ≈ 0.004; 12 sim steps → per step 0.004/√12
    c = _bars(3000, 60, 0.004, seed=3)
    s = sigma_horizon_per_step(c, 60, 12)
    assert s == pytest.approx(0.004 / math.sqrt(12), rel=0.05)
    # 4h horizon from 1h bars: σ_h ≈ 0.004·√4
    s4 = sigma_horizon_per_step(c, 240, 16)
    assert s4 == pytest.approx(0.004 * 2 / 4, rel=0.08)


def test_sigma_horizon_captures_fat_horizon_moves_better_than_per_bar():
    # trending/clustered horizon moves: per-bar σ × √steps underestimates when bars autocorrelate
    rng = np.random.default_rng(5)
    n = 2000
    shocks = np.repeat(rng.normal(0, 0.003, n // 4), 4)  # 4 identical 15m moves per hour → momentum
    idx = pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(shocks)), index=idx)
    per_bar = sigma_per_bar(c)
    s = sigma_horizon_per_step(c, 60, 4)
    assert s > per_bar * 1.3


def test_sigma_horizon_returns_none_when_insufficient():
    assert sigma_horizon_per_step(_bars(10, 15, 0.002), 15, 15) is None
    daily = _bars(260, 24 * 60, 0.02)
    assert sigma_horizon_per_step(daily, 15, 15) is None  # no 15-minute pairs in daily data
