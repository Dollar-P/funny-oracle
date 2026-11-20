"""P5: backtester, baselines, lookahead guard, speed."""
from __future__ import annotations

import time
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from evaluation import backtest as BT
from evaluation.backtest import BacktestConfig, Decision, buy_and_hold, cash, random_distribution, run_backtest
from evaluation.baselines import Momentum, QuantOnly, RandomStrategy, SmaCross, analytic_forecast, label_score
from quant.montecarlo import simulate
from trading.costs import money
from tests.pro.synth import synthetic_daily

CFG = BacktestConfig(start="2023-01-02", horizon_bars=21)


def three_assets(seed=0):
    return {"NVDA": ("stock", synthetic_daily("stock", seed=seed, drift=0.0005)),
            "BTC-USD": ("crypto", synthetic_daily("crypto", price=30_000, seed=seed + 1, vol=0.03)),
            "GC=F": ("gold", synthetic_daily("gold", price=1_800, seed=seed + 2, vol=0.01))}


def test_buy_and_hold_by_hand():
    n = len(synthetic_daily("stock", start="2023-01-03", end="2023-01-31"))
    df = synthetic_daily("stock", start="2023-01-03", end="2023-01-31", closes=np.linspace(100, 120, n))
    df["Open"] = df["Close"].shift(1).fillna(100.0)
    first_open = float(df["Open"].iloc[0])
    assert first_open == 100.0
    r = buy_and_hold({"X": ("stock", df)}, BacktestConfig(starting_equity=100_000))
    # qty = floor(100000 / (100 × 1.0002)) = 999; cost 999 × 100.02 = 99,919.98 → cash 80.02
    # exit at the last close 120 × 0.9998 = 119.976 → 999 × 119.976 = 119,856.02 (cents)
    assert r.equity.iloc[-1] == pytest.approx(80.02 + 119_856.02, abs=1e-9)
    assert r.reconcile == Decimal("0.00")


def test_cash_is_flat():
    r = cash(three_assets(), CFG)
    assert (r.equity == 100_000).all() and r.trades == []


def test_engine_reconciles_and_trades_close():
    data = three_assets()
    r = run_backtest(Momentum(), data, CFG)
    assert r.reconcile == Decimal("0.00") and len(r.trades) > 10
    # every position is closed at the end → final equity = start + Σ realised P&L (to the cent)
    assert money(r.equity.iloc[-1]) == money(100_000 + sum(t["pnl"] for t in r.trades))
    assert all(t["opened_at"] >= t["decided_at"] for t in r.trades)      # fills at/after the decision bar's close
    assert r.equity.index[0].date().isoformat() >= CFG.start                # warm-up is not measured
    assert len(r.equity) == len(buy_and_hold(data, CFG).equity)              # same period as the benchmark
    s = r.summary()
    assert s["trades"] == len(r.trades) and -1 < s["max_drawdown"] <= 0 and s["total_costs"] > 0
    assert set(s) >= {"sharpe", "sortino", "calmar", "expectancy_r", "profit_factor", "exposure", "turnover"}


def test_momentum_follows_the_trend():
    up = {"UP": ("stock", synthetic_daily("stock", drift=0.003, vol=0.01, seed=4))}
    r = run_backtest(Momentum(), up, CFG)
    sides = [t["side"] for t in r.trades]
    assert sides and sides.count("long") / len(sides) >= 0.9            # a strong uptrend: (almost) always long
    down = {"DN": ("crypto", synthetic_daily("crypto", drift=-0.003, vol=0.01, seed=4))}
    r = run_backtest(Momentum(), down, CFG)
    assert r.trades == [] and r.flats.get("short_not_allowed", 0) > 0   # crypto shorts are off by default


def test_sma_cross_and_random_are_seeded():
    data = three_assets(3)
    assert run_backtest(SmaCross(), data, CFG).trades
    a = run_backtest(RandomStrategy(7), data, CFG)
    b = run_backtest(RandomStrategy(7), data, CFG)
    assert a.equity.equals(b.equity) and [t["side"] for t in a.trades] == [t["side"] for t in b.trades]
    c = run_backtest(RandomStrategy(8), data, CFG)
    assert not a.equity.equals(c.equity)


def test_quant_only_goes_through_the_signal_engine():
    data = three_assets(5)
    r = run_backtest(QuantOnly(), data, CFG)
    assert r.predictions and all(0 < p["prob_up"] < 1 for p in r.predictions)
    assert sum(r.flats.values()) + len(r.trades) + sum(r.rejections.values()) == len(r.predictions)
    assert set(r.flats) <= {"weak_signal", "low_confidence", "low_rr", "negative_ev", "short_not_allowed",
                            "bad_geometry", "no_data"}


def test_analytic_forecast_matches_monte_carlo():
    a = analytic_forecast(100.0, 0.02, 21, 0.6)
    m = simulate(100.0, 0.02, 21, 0.6, seed=1, paths=200_000)
    for k in ("p10", "p50", "p90", "prob_up"):
        assert getattr(a, k) == pytest.approx(getattr(m, k), rel=5e-3), k
    assert label_score({"sma_regime": "golden_cross_regime", "trend_vs_sma200": "above", "trend_vs_sma50": "above",
                        "macd_momentum": "bullish_strengthening", "rsi_zone": "bullish"}) == pytest.approx(0.95)


class AlwaysLong:
    name = "always_long"

    def decide(self, ticker, view, ctx):
        e = float(view.close[-1])
        return Decision("long", e * 0.97, [], 5, entry_ref=e)


def test_max_drawdown_halts_new_trades_for_good():
    from trading.risk import RiskParams

    crash = np.concatenate([np.full(220, 100.0), np.linspace(100, 30, 200), np.full(330, 30.0)])
    data = {f"S{i}": ("stock", synthetic_daily("stock", closes=crash * (1 + i / 10), seed=i, vol=0.002))
            for i in range(4)}
    loose = RiskParams(risk_pct_swing=0.02, max_position=0.5, max_class={"stock": 1.0, "crypto": 1, "gold": 1},
                       max_gross=2.0, corr_combined_risk=1.0)
    r = run_backtest(AlwaysLong(), data, BacktestConfig(horizon_bars=5, warmup_bars=30, risk=loose))
    assert r.rejections.get("halted", 0) > 100                   # every later decision is refused
    halt_day = min(t["closed_at"] for t in r.trades if t["pnl"] < 0)
    assert r.summary()["max_drawdown"] == pytest.approx(-0.10, abs=0.02)   # stops near −10 %, not at −70 %
    assert r.rejections.get("no_volatility", 0) > 0              # flat prices: σ = 0 → refused, not sized
    assert halt_day is not None


# --- lookahead ---------------------------------------------------------------------------------------------

class NextBarOracle:
    """Calls the direction of the *next* close if the view leaks it; otherwise it can only guess from the past."""

    name = "oracle"

    def __init__(self):
        self.calls: list[tuple[int, int, pd.Timestamp, pd.Timestamp]] = []

    def decide(self, ticker, view, ctx):
        c = view.close
        self.calls.append((view.i, int(np.sign(c[-1] - c[-2])), view.df.index[-1], view.ts))
        return None


class LeakyView(BT.PointInTimeView):
    """A deliberately broken view (one bar too many) — proves the test below can detect a leak."""

    @property
    def close(self):
        return self._close[: self._i + 2]

    @property
    def df(self):
        return self._df.iloc[: self._i + 2]


def _oracle_accuracy(monkeypatch, view_cls):
    df = synthetic_daily("crypto", start="2023-01-01", end="2024-06-30", seed=11)
    monkeypatch.setattr(BT, "PointInTimeView", view_cls)
    o = NextBarOracle()
    run_backtest(o, {"X": ("crypto", df)}, BacktestConfig(horizon_bars=1, warmup_bars=30))
    close = df["Close"].to_numpy()
    calls = [(i, d) for i, d, _seen, _ts in o.calls if i + 1 < len(close)]
    hits = sum(d == np.sign(close[i + 1] - close[i]) for i, d in calls)
    return hits / len(calls), o.calls


def test_no_lookahead(monkeypatch):
    acc, calls = _oracle_accuracy(monkeypatch, BT.PointInTimeView)
    assert all(seen == ts for _i, _d, seen, ts in calls)       # the last bar a strategy sees is the decision bar
    assert 0.40 < acc < 0.60                                   # a random walk can't be called from the past


def test_leak_detector_works(monkeypatch):
    acc, _ = _oracle_accuracy(monkeypatch, LeakyView)
    assert acc > 0.95                                          # with one future bar, the oracle is ~perfect


# --- speed (P5 "done when") ----------------------------------------------------------------------------------

def test_baselines_over_three_years_run_under_a_minute():
    data = three_assets(21)
    cfg = BacktestConfig(start="2022-01-03", horizon_bars=21, warmup_bars=0)
    assert (data["NVDA"][1].index[-1] - data["NVDA"][1].index[0]).days >= 3 * 365 - 10
    t0 = time.perf_counter()
    buy_and_hold(data, cfg)
    run_backtest(Momentum(), data, cfg)
    dist = random_distribution(data, cfg, n_runs=1000)
    elapsed = time.perf_counter() - t0
    assert len(dist) == 1000 and len({d["seed"] for d in dist}) == 1000
    assert np.std([d["sharpe"] for d in dist]) > 0
    assert elapsed < 60, f"took {elapsed:.1f}s"
