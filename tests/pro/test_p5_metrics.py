"""P5: metrics and statistics against hand-computed values."""
from __future__ import annotations

import math

import numpy as np
import pytest

from evaluation import metrics as M
from evaluation import stats as S

EQ = [100.0, 110.0, 99.0, 108.9]          # returns +10 %, −10 %, +10 %


def test_returns_and_totals():
    assert M.returns_from_equity(EQ) == pytest.approx([0.1, -0.1, 0.1])
    assert M.total_return(EQ) == pytest.approx(0.089)
    assert M.cagr(EQ, days=365.25) == pytest.approx(0.089)
    assert M.cagr(EQ, days=365.25 * 2) == pytest.approx(math.sqrt(1.089) - 1)


def test_sharpe_sortino_vol_by_hand():
    r = M.returns_from_equity(EQ)
    # mean 1/30; deviations 1/15, −2/15, 1/15 → sd (ddof 1) = sqrt((6/225) / 2) = 1/√75 → Sharpe = √75 / 30
    assert M.sharpe(r, ppy=1) == pytest.approx(math.sqrt(75) / 30)
    assert M.sharpe(r, ppy=252) == pytest.approx(math.sqrt(75) / 30 * math.sqrt(252))
    # downside deviation = sqrt(mean([0, 0.01, 0])) = 1/√300 → Sortino = √300 / 30
    assert M.sortino(r, ppy=1) == pytest.approx(math.sqrt(300) / 30)
    assert M.volatility(r, ppy=1) == pytest.approx(1 / math.sqrt(75))
    assert M.sharpe([0.01, 0.01], ppy=1) == 0.0      # zero variance → 0, not inf


def test_drawdown_and_calmar():
    assert M.max_drawdown(EQ) == pytest.approx(-0.1)
    assert M.max_drawdown([100, 120, 90, 130, 65]) == pytest.approx(-0.5)
    assert M.calmar(EQ, days=365.25) == pytest.approx(0.89)
    assert M.max_drawdown([100, 101, 102]) == 0.0


def test_trade_stats():
    rs = [2.0, -1.0, -1.0, 0.5]
    assert M.expectancy_r(rs) == pytest.approx(0.125)
    assert M.win_rate(rs) == 0.5
    assert M.profit_factor([200, -100, -100, 50]) == pytest.approx(1.25)
    assert M.profit_factor([10, 5]) == float("inf") and M.profit_factor([]) == 0.0
    assert M.exposure([0, 1, 1, 0]) == 0.5
    assert M.turnover(200_000, 100_000, 2) == 1.0


def test_benchmark_relative():
    rb = np.array([0.01, -0.02, 0.015, 0.005])
    assert M.beta(2 * rb, rb) == pytest.approx(2.0)
    assert M.information_ratio(rb + 0.001, rb, ppy=1) == 0.0   # constant active return → zero variance → 0


def test_prediction_quality():
    p, y = [0.9, 0.2, 0.6], [1, 0, 0]
    assert M.brier(p, y) == pytest.approx((0.01 + 0.04 + 0.36) / 3)
    assert M.log_loss(p, y) == pytest.approx(-(math.log(0.9) + math.log(0.8) + math.log(0.4)) / 3)
    assert M.hit_rate([0.9, 0.2, 0.6, 0.5], [1, 0, 0, 1]) == (2, 3)          # 0.5 is not a call
    assert M.pinball([10], [12], 0.1) == pytest.approx(0.2)
    assert M.pinball([10], [8], 0.1) == pytest.approx(1.8)
    assert M.pinball([10], [12], 0.9) == pytest.approx(1.8)
    assert M.band_coverage([1, 1, 1], [3, 3, 3], [2, 4, 3]) == pytest.approx(2 / 3)
    rel = M.reliability([0.05, 0.15, 0.12, 0.95], [0, 1, 0, 1], bins=10)
    assert rel[1] == {"lo": 0.1, "hi": pytest.approx(0.2), "n": 2, "mean_p": pytest.approx(0.135), "freq": 0.5}
    assert rel[-1]["n"] == 1 and rel[-1]["freq"] == 1.0


def test_wilson_ci_textbook():
    lo, hi = S.wilson_ci(8, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.490, 0.943)
    lo, hi = S.wilson_ci(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    assert S.wilson_ci(0, 0) == (0.0, 1.0)


def test_block_bootstrap_coverage_on_iid_data():
    covered = 0
    for seed in range(60):
        x = np.random.default_rng(seed).normal(0.0, 1.0, 200)
        _, lo, hi = S.block_bootstrap(x, np.mean, n_boot=400, seed=seed)
        covered += lo <= 0.0 <= hi
    assert covered / 60 >= 0.88


def test_block_bootstrap_is_seeded_and_block_length():
    x = np.random.default_rng(1).normal(0, 1, 125)
    assert S.block_bootstrap(x, np.mean, n_boot=200, seed=5) == S.block_bootstrap(x, np.mean, n_boot=200, seed=5)
    assert S.default_block(125) == 5 and S.default_block(1000) == 10


def test_bootstrap_diff_paired():
    x = np.random.default_rng(2).normal(0.001, 0.01, 300)
    est, lo, hi = S.bootstrap_diff(x, x, lambda a: M.sharpe(a, 1), n_boot=300)
    assert est == 0.0 and lo == 0.0 and hi == 0.0                     # identical series → exactly zero
    est, lo, hi = S.bootstrap_diff(x + 0.01, x, np.mean, n_boot=300)
    assert est == pytest.approx(0.01) and lo == pytest.approx(0.01) and hi == pytest.approx(0.01)


def test_non_overlapping_and_effective_n():
    rows = [(0, "A", 0, 30), (1, "A", 10, 40), (2, "A", 30, 60), (3, "B", 5, 35), (4, "A", 59, 90), (5, "A", 60, 90)]
    assert S.non_overlapping(rows) == [0, 3, 2, 5]
    assert S.effective_n(100, 1.0) == 50


def test_percentile_rank_and_metric_labels():
    assert S.percentile_rank(5, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == pytest.approx(0.45)
    m = S.metric(0.55, n=29, lo=0.4, hi=0.7).to_dict()
    assert m["label"] == "insufficient data" and m["sufficient"] is False and m["n"] == 29
    assert S.metric(0.55, n=30).to_dict()["label"] == "ok"
    assert S.metric(float("inf"), n=40).to_dict()["value"] is None
    assert "Bonferroni" in S.multiple_comparisons_note(10)
