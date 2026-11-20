"""P3: Risk Engine — property tests (no approved plan ever breaks a limit) + hand-checked unit tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from trading import costs as C
from trading.risk import RULES, OpenPos, Portfolio, RiskParams, record_rejections, size_plan
from trading.signals import SignalParams, build_plan
from tests.pro.helpers import make_pred

RP = RiskParams()
TOL = 1e-9


def long_plan(price=100.0, stop_dist=10.0, asset_type="stock", ticker="NVDA", mode="swing", **kw):
    pred = make_pred(price=price, p10=price - stop_dist, p50=price * 1.04, p90=price + 1.8 * stop_dist,
                     asset_type=asset_type, ticker=ticker, mode=mode, **kw)
    plan = build_plan(pred, atr=stop_dist / 3, params=SignalParams(), costs=C.cost_model(asset_type))
    assert plan.direction == "long", plan.flat_reason
    return plan


def short_plan(price=100.0, stop_dist=8.0, asset_type="stock", ticker="NVDA"):
    pred = make_pred(s_final=-0.6, prob_up=0.35, price=price, p10=price - 2.25 * stop_dist, p50=price * 0.96,
                     p90=price + stop_dist, asset_type=asset_type, ticker=ticker)
    plan = build_plan(pred, atr=stop_dist / 3, params=SignalParams(), costs=C.cost_model(asset_type))
    assert plan.direction == "short", plan.flat_reason
    return plan


def pf(equity=100_000.0, positions=(), peak=None, day_start=None, halted=False, returns=None):
    return Portfolio(equity=equity, peak_equity=peak or equity, day_start_equity=day_start or equity,
                     positions=list(positions), halted=halted, returns=returns or {})


# --- hand-checked sizing -----------------------------------------------------------------------

def test_base_size_is_risk_over_stop_distance():
    d = size_plan(long_plan(), pf(), RP, sigma_annual=0.10)
    # 0.5 % of 100k = 500 risk / 10 per share = 50 shares; notional 5,000 (5 %); vol cap 0.15/0.10 = 150 % → not binding
    assert d.approved and d.size.qty == 50 and d.size.risk_amount == pytest.approx(500.0)
    assert d.size.notional == pytest.approx(5_000.0) and d.size.capped_by == []


def test_scalp_risk_is_quarter_percent():
    d = size_plan(long_plan(mode="scalp"), pf(), RP, sigma_annual=0.10)
    assert d.size.qty == 25  # 250 / 10


def test_max_position_cap_binds():
    # tight stop: 500 / 0.5 = 1,000 shares = 100k notional → capped at 20 % = 200 shares
    d = size_plan(long_plan(stop_dist=0.5), pf(), RP, sigma_annual=0.10)
    assert d.size.qty == 200 and "max_position" in d.size.capped_by
    assert d.size.risk_amount == pytest.approx(100.0)  # actual risk after the cap


def test_vol_cap_binds():
    # σ 1.5 → notional ≤ 0.15 / 1.5 × 100k = 10k → 100 shares (base size 500/2 = 250)
    d = size_plan(long_plan(stop_dist=2.0), pf(), RP, sigma_annual=1.5)
    assert d.size.qty == 100 and "vol_cap" in d.size.capped_by


def test_lot_rounding_is_down():
    d = size_plan(long_plan(price=333.0, stop_dist=30.0), pf(), RP, sigma_annual=0.2)
    assert d.size.qty == 16                 # 500 / 30 = 16.67 → 16 shares
    c = size_plan(long_plan(price=60_000.0, stop_dist=3_000.0, asset_type="crypto", ticker="BTC-USD"), pf(), RP,
                  sigma_annual=0.6)
    assert c.size.qty == pytest.approx(0.1666)  # 500 / 3000 = 0.16667 → 4 dp, down


def test_class_cap_and_gross_cap():
    crypto_pos = [OpenPos("ETH-USD", "crypto", "long", 10.0, 2_800.0, 2_500.0, 2_800.0)]  # 28k = 28 %
    d = size_plan(long_plan(price=60_000.0, stop_dist=3_000.0, asset_type="crypto", ticker="BTC-USD"),
                  pf(positions=crypto_pos), RP, sigma_annual=0.6)
    # crypto cap 30 % → 2k left → 2,000 / 60,000 = 0.0333 BTC
    assert d.approved and d.size.qty == pytest.approx(0.0333) and "class_cap" in d.size.capped_by
    full = [OpenPos(f"S{i}", "stock", "long", 100.0, 190.0, 180.0, 192.0) for i in range(5)]  # 5 × 19.2k = 96 %
    g = size_plan(long_plan(), pf(positions=full), RP, sigma_annual=0.1)
    assert not g.approved and g.rejections[0].rule == "class_cap"   # stock 60 % already exceeded
    gold = size_plan(long_plan(asset_type="gold", ticker="GC=F"), pf(positions=full), RP, sigma_annual=0.1)
    assert gold.approved and gold.size.notional <= 4_000 + 1e-6 and "gross_cap" in gold.size.capped_by


def test_breakers_and_open_count():
    plan = long_plan()
    d = size_plan(plan, pf(equity=97_900.0, day_start=100_000.0), RP, sigma_annual=0.1)
    assert not d.approved and d.rejections[0].rule == "daily_loss"
    d = size_plan(plan, pf(equity=89_000.0, peak=100_000.0, day_start=89_000.0), RP, sigma_annual=0.1)
    assert not d.approved and d.rejections[0].rule == "max_drawdown" and d.halt
    d = size_plan(plan, pf(halted=True), RP, sigma_annual=0.1)
    assert not d.approved and d.rejections[0].rule == "halted"
    many = [OpenPos(f"S{i}", "stock", "long", 1.0, 10.0, 9.0, 10.0) for i in range(8)]
    d = size_plan(plan, pf(positions=many), RP, sigma_annual=0.1)
    assert not d.approved and d.rejections[0].rule == "max_open_positions"
    same = [OpenPos("NVDA", "stock", "long", 1.0, 10.0, 9.0, 10.0)]
    assert size_plan(plan, pf(positions=same), RP, sigma_annual=0.1).rejections[0].rule == "duplicate_position"


def test_flat_and_bad_vol_rejected():
    flat = build_plan(make_pred(s_final=0.05), atr=4, params=SignalParams(), costs=C.cost_model("stock"))
    assert size_plan(flat, pf(), RP, sigma_annual=0.1).rejections[0].rule == "flat_plan"
    assert size_plan(long_plan(), pf(), RP, sigma_annual=float("nan")).rejections[0].rule == "no_volatility"


def test_correlation_guard():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.02, 80)
    idx = pd.date_range("2026-01-01", periods=80, freq="D", tz="UTC")
    rets = {"AMD": pd.Series(base, idx), "NVDA": pd.Series(base + rng.normal(0, 0.002, 80), idx),
            "XOM": pd.Series(rng.normal(0, 0.02, 80), idx)}
    amd = OpenPos("AMD", "stock", "long", 60.0, 100.0, 90.0, 100.0)            # risk 600 = 0.6 %
    d = size_plan(long_plan(), pf(positions=[amd], returns=rets), RP, sigma_annual=0.1)
    assert not d.approved and d.rejections[0].rule == "correlation"            # 0.6 % + 0.5 % > 1 %, ρ ≈ 0.99
    small = OpenPos("AMD", "stock", "long", 40.0, 100.0, 90.0, 100.0)          # 0.4 % + 0.5 % ≤ 1 %
    assert size_plan(long_plan(), pf(positions=[small], returns=rets), RP, sigma_annual=0.1).approved
    opposite = OpenPos("AMD", "stock", "short", 60.0, 100.0, 110.0, 100.0)     # different direction → allowed
    assert size_plan(long_plan(), pf(positions=[opposite], returns=rets), RP, sigma_annual=0.1).approved
    xom = OpenPos("XOM", "stock", "long", 60.0, 100.0, 90.0, 100.0)            # uncorrelated → allowed
    assert size_plan(long_plan(), pf(positions=[xom], returns=rets), RP, sigma_annual=0.1).approved


def test_kelly_cap_only_after_100_calibrated():
    plan = long_plan(prob_up=0.5)   # p 0.5, b = 0.5·0.4 + 0.5·1.8 = 1.1 → f* = 0.5 − 0.5/1.1 = 0.04545
    plan = plan.model_copy(update={"calibrated": True})
    off = size_plan(plan, pf(), RP, sigma_annual=0.1, calibration_n=99)
    assert off.size.qty == 50
    on = size_plan(plan, pf(), RP, sigma_annual=0.1, calibration_n=100)
    # 0.25 × 0.04545 × 100k = 1,136 risk ≥ 500 → not binding
    assert on.size.qty == 50
    tight = RiskParams(kelly_fraction=0.05)  # 0.05 × 0.04545 × 100k = 227 risk → 22 shares
    k = size_plan(plan, pf(), tight, sigma_annual=0.1, calibration_n=100)
    assert k.size.qty == 22 and "kelly" in k.size.capped_by
    neg = plan.model_copy(update={"p_win": 0.3})
    assert size_plan(neg, pf(), RP, sigma_annual=0.1, calibration_n=100).rejections[0].rule == "kelly_negative"


def test_rejections_are_logged_with_rule(fresh_db):
    d = size_plan(long_plan(), pf(halted=True), RP, sigma_annual=0.1)
    record_rejections(d, signal_id=7, experiment_id=3)
    from core.db import connect

    with connect() as conn:
        rows = conn.execute("SELECT signal_id, experiment_id, rule, detail FROM risk_events").fetchall()
    assert [tuple(r)[:3] for r in rows] == [(7, 3, "halted")] and rows[0][3]


# --- property tests ------------------------------------------------------------------------------

ASSETS = ["stock", "crypto", "gold"]


@st.composite
def positions(draw):
    n = draw(st.integers(0, 10))
    out = []
    for i in range(n):
        a = draw(st.sampled_from(ASSETS))
        side = draw(st.sampled_from(["long", "short"]))
        entry = draw(st.floats(1, 50_000))
        dist = entry * draw(st.floats(0.001, 0.3))
        qty = draw(st.floats(0.001, 5_000)) / entry * draw(st.floats(1, 100))
        stop = entry - dist if side == "long" else entry + dist
        out.append(OpenPos(f"T{i}", a, side, qty, entry, stop, entry * draw(st.floats(0.8, 1.2))))
    return out


@st.composite
def risk_params(draw):
    return RiskParams(
        risk_pct_swing=draw(st.floats(0.0005, 0.03)), risk_pct_scalp=draw(st.floats(0.0005, 0.02)),
        vol_target=draw(st.floats(0.02, 0.5)), max_position=draw(st.floats(0.01, 0.5)),
        max_gross=draw(st.floats(0.1, 2.0)), max_open=draw(st.integers(1, 12)),
        max_class={a: draw(st.floats(0.05, 1.0)) for a in ASSETS},
        kelly_fraction=draw(st.floats(0.001, 1.0)))


@settings(max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(equity=st.floats(1_000, 10_000_000), pos=positions(), params=risk_params(),
       price=st.floats(0.5, 100_000), stop_frac=st.floats(0.01, 0.4), short=st.booleans(),
       asset=st.sampled_from(ASSETS), mode=st.sampled_from(["swing", "scalp"]), sigma=st.floats(0.02, 3.0),
       cal_n=st.integers(0, 300), dd=st.floats(-0.12, 0.0), day=st.floats(-0.025, 0.02))
def test_property_no_plan_exceeds_any_limit(equity, pos, params, price, stop_frac, short, asset, mode, sigma,
                                            cal_n, dd, day):
    dist = price * stop_frac
    if short and asset != "crypto":
        plan = short_plan(price=price, stop_dist=dist, asset_type=asset, ticker="NEW")
    else:
        plan = long_plan(price=price, stop_dist=dist, asset_type=asset, ticker="NEW", mode=mode)
    plan = plan.model_copy(update={"calibrated": cal_n % 2 == 0})
    p = Portfolio(equity=equity, peak_equity=equity / (1 + dd), day_start_equity=equity / (1 + day),
                  positions=pos, halted=False, returns={})
    d = size_plan(plan, p, params, sigma_annual=sigma, calibration_n=cal_n)
    event("approved" if d.approved else f"rejected: {d.rejections[0].rule}")
    if d.approved:
        event("capped by " + (",".join(d.size.capped_by) or "nothing"))
    if not d.approved:
        assert d.rejections and all(r.rule in RULES and r.detail for r in d.rejections)
        return
    s = d.size
    rpu = abs(plan.entry.ref_price - plan.stop)
    notional = s.qty * plan.entry.ref_price
    risk_pct = params.risk_pct_scalp if plan.mode == "scalp" else params.risk_pct_swing
    gross_before = sum(abs(q.notional) for q in pos)
    class_before = sum(abs(q.notional) for q in pos if q.asset_type == plan.asset_type)
    assert s.qty > 0
    assert s.qty * rpu <= risk_pct * equity * (1 + TOL)                                   # risk per trade
    assert notional <= params.max_position * equity * (1 + TOL)                          # max position
    assert notional <= params.vol_target * equity / sigma * (1 + TOL)                    # volatility cap
    assert gross_before + notional <= params.max_gross * equity * (1 + TOL)              # gross exposure
    assert class_before + notional <= params.max_class[plan.asset_type] * equity * (1 + TOL)  # asset class
    assert len(pos) < params.max_open                                                    # open positions
    assert equity / p.peak_equity - 1 > params.max_dd and equity / p.day_start_equity - 1 > params.daily_loss
    if plan.calibrated and cal_n >= params.kelly_min_n:
        b = 0.5 * plan.inputs["r1"] + 0.5 * plan.inputs["r2"]
        f_star = plan.p_win - (1 - plan.p_win) / b
        assert s.qty * rpu <= params.kelly_fraction * f_star * equity * (1 + TOL)          # Kelly cap
    assert abs(s.notional - notional) <= 1e-6 * max(1.0, notional)


@settings(max_examples=100, deadline=None)
@given(equity=st.floats(1_000, 1_000_000), pos=positions())
def test_property_halted_account_never_approves(equity, pos):
    d = size_plan(long_plan(), Portfolio(equity, equity, equity, pos, True, {}), RP, sigma_annual=0.2)
    assert not d.approved and d.rejections[0].rule == "halted"


@settings(max_examples=300, deadline=None)
@given(kf=st.floats(0.001, 1.0), p_win=st.floats(0.05, 0.95), equity=st.floats(1_000, 1_000_000),
       stop_frac=st.floats(0.01, 0.3))
def test_property_kelly_cap(kf, p_win, equity, stop_frac):
    plan = long_plan(stop_dist=100.0 * stop_frac).model_copy(update={"calibrated": True, "p_win": p_win})
    d = size_plan(plan, pf(equity=equity), RiskParams(kelly_fraction=kf), sigma_annual=0.1, calibration_n=150)
    b = 0.5 * plan.inputs["r1"] + 0.5 * plan.inputs["r2"]
    f_star = p_win - (1 - p_win) / b
    event("approved" if d.approved else d.rejections[0].rule)
    if f_star <= 0:
        assert not d.approved and d.rejections[0].rule == "kelly_negative"
    elif d.approved:
        assert d.size.qty * abs(plan.entry.ref_price - plan.stop) <= kf * f_star * equity * (1 + TOL)
