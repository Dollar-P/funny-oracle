"""P2: Signal Engine + cost model, against hand-computed numbers."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading import costs as C
from trading.signals import SignalParams, atr_at, build_plan
from tests.pro.helpers import T0, make_pred

STOCK = C.cost_model("stock")
CRYPTO = C.cost_model("crypto")
P = SignalParams()


# --- cost model ---------------------------------------------------------------------------

def test_cost_defaults_bps():
    assert (STOCK.fee_bps, STOCK.slippage_bps) == (0, 2)
    assert (C.cost_model("gold").fee_bps, C.cost_model("gold").slippage_bps) == (0, 3)
    assert (CRYPTO.fee_bps, CRYPTO.slippage_bps) == (10, 5)
    assert C.cost_model("crypto", {"crypto": {"fee": 20}}).fee_bps == 20


def test_slippage_direction_and_size():
    assert C.apply_slippage(100.0, "buy", STOCK) == pytest.approx(100.02)     # 2 bps worse for the buyer
    assert C.apply_slippage(100.0, "sell", STOCK) == pytest.approx(99.98)
    assert C.apply_slippage(60000.0, "buy", CRYPTO) == pytest.approx(60030.0)  # 5 bps


def test_fee_in_cents():
    assert C.fee(Decimal("12345.67"), CRYPTO) == Decimal("12.35")   # 10 bps = 12.345667 → 12.35 (half up)
    assert C.fee(Decimal("12345.67"), STOCK) == Decimal("0.00")
    assert C.fee(Decimal("5.00"), CRYPTO) == Decimal("0.01")         # 0.005 → 0.01 half up


def test_round_trip_cost_per_unit():
    assert C.round_trip_cost_per_unit(100.0, STOCK) == pytest.approx(0.04)     # 2 × 2 bps × 100
    assert C.round_trip_cost_per_unit(100.0, CRYPTO) == pytest.approx(0.30)    # 2 × 15 bps × 100


# --- geometry -------------------------------------------------------------------------------

def test_long_geometry_and_ev_by_hand():
    # price 100, P10 90, P50 104, P90 118, ATR 4 → ATR stop 94, band stop 90 → stop 90 (the wider)
    plan = build_plan(make_pred(), atr=4.0, params=P, costs=STOCK)
    assert plan.direction == "long" and plan.flat_reason is None
    assert plan.stop == pytest.approx(90.0) and plan.targets == pytest.approx([104.0, 118.0])
    assert plan.reward_risk == pytest.approx(1.8)                     # (118 − 100) / 10
    assert plan.cost_r == pytest.approx(0.004)                        # 0.04 / 10
    # EV = 0.62 × (0.5·0.4 + 0.5·1.8) − 0.38 − 0.004 = 0.298
    assert plan.expected_value_r == pytest.approx(0.298)
    assert plan.p_win == pytest.approx(0.62) and plan.calibrated is False
    assert plan.conviction == pytest.approx(0.6 * 0.7)
    assert plan.entry.type == "market" and plan.entry.ref_price == 100.0
    assert plan.time_exit == T0 + timedelta(days=30)
    assert plan.thesis == "thesis text" and plan.invalidation == "close below 90"


def test_short_geometry_and_ev_by_hand():
    pred = make_pred(s_final=-0.6, prob_up=0.35, p10=82.0, p50=96.0, p90=108.0)
    plan = build_plan(pred, atr=3.0, params=P, costs=STOCK)
    # ATR stop 104.5, band stop 108 → 108; T1 96, T2 82; risk 8
    assert plan.direction == "short" and plan.stop == pytest.approx(108.0)
    assert plan.targets == pytest.approx([96.0, 82.0]) and plan.reward_risk == pytest.approx(2.25)
    # EV = 0.65 × (0.5·0.5 + 0.5·2.25) − 0.35 − 0.04/8 = 0.53875
    assert plan.expected_value_r == pytest.approx(0.53875)


def test_atr_stop_used_when_wider_than_band():
    plan = build_plan(make_pred(p10=97.0, p90=130.0), atr=4.0, params=P, costs=STOCK)
    assert plan.stop == pytest.approx(94.0)                           # 100 − 1.5 × 4 < P10 97


def test_band_edge_on_the_wrong_side_is_ignored():
    # P10 above entry can't be a long stop → ATR stop
    plan = build_plan(make_pred(p10=100.5, p50=110.0, p90=125.0), atr=2.0, params=P, costs=STOCK)
    assert plan.stop == pytest.approx(97.0)


def test_scalp_uses_k_atr_1():
    pred = make_pred(mode="scalp", horizon="1h", minutes=60, p10=99.0, p50=100.5, p90=103.0)
    plan = build_plan(pred, atr=2.0, params=P, costs=STOCK)
    assert plan.stop == pytest.approx(98.0)                           # 100 − 1.0 × 2 (wider than P10 99)


# --- FLAT rules -------------------------------------------------------------------------------

@pytest.mark.parametrize("kw,reason", [
    ({"s_final": 0.14}, "weak_signal"),
    ({"s_final": -0.149}, "weak_signal"),
    ({"confidence": 0.49}, "low_confidence"),
    ({"p90": 112.0}, "low_rr"),                                       # (112−100)/10 = 1.2 < 1.5
    ({"prob_up": 0.30}, "negative_ev"),                               # 0.30·1.1 − 0.70 − 0.004 < 0
    ({"no_trade_reason": "evidence too thin"}, "judge_no_trade"),
    ({"degraded": ["stale"]}, "degraded_data"),
    ({"p50": 99.0}, "bad_geometry"),                                  # long with T1 below entry
])
def test_flat_rules(kw, reason):
    plan = build_plan(make_pred(**kw), atr=4.0, params=P, costs=STOCK)
    assert plan.direction == "flat" and plan.flat_reason == reason
    assert reason in plan.inputs["fails"]


def test_thresholds_are_inclusive_at_the_boundary():
    plan = build_plan(make_pred(s_final=0.15, confidence=0.5, p90=115.0), atr=4.0, params=P, costs=STOCK)
    assert plan.flat_reason is None and plan.reward_risk == pytest.approx(1.5)


def test_ev_exactly_zero_is_flat():
    # choose p so EV == 0: p·1.1 − (1 − p) − 0.004 = 0 → p = 1.004 / 2.1
    plan = build_plan(make_pred(prob_up=1.004 / 2.1), atr=4.0, params=P, costs=STOCK)
    assert plan.expected_value_r == pytest.approx(0.0, abs=1e-12) and plan.flat_reason == "negative_ev"


def test_costs_can_flip_ev_negative():
    pred = make_pred(ticker="BTC-USD", asset_type="crypto", prob_up=0.485)
    assert build_plan(pred, atr=4.0, params=P, costs=STOCK).expected_value_r > 0     # 0.485·1.1 − 0.515 − 0.004 = +0.0145
    plan = build_plan(pred, atr=4.0, params=P, costs=CRYPTO)                         # cost_R = 0.30 / 10 = 0.03
    assert plan.cost_r == pytest.approx(0.03) and plan.flat_reason == "negative_ev"


def test_short_rules_by_asset():
    kw = dict(s_final=-0.6, prob_up=0.35, p10=82.0, p50=96.0, p90=108.0)
    assert build_plan(make_pred(**kw, asset_type="gold", ticker="GC=F"), atr=3, params=P, costs=STOCK).direction == "short"
    crypto = make_pred(**kw, asset_type="crypto", ticker="BTC-USD")
    plan = build_plan(crypto, atr=3, params=P, costs=CRYPTO)
    assert plan.direction == "flat" and plan.flat_reason == "short_not_allowed"
    allowed = SignalParams(allow_crypto_short=True)
    assert build_plan(crypto, atr=3, params=allowed, costs=CRYPTO).direction == "short"


def test_catalyst_window_first_10_percent():
    # 1M = 30 days → window 3 days from T0 (2026-11-02 21:00) → until 2026-11-05 21:00
    inside = make_pred(catalysts=[{"date": "2026-11-05", "event": "CPI", "impact": "high"}])
    assert build_plan(inside, atr=4, params=P, costs=STOCK).flat_reason == "catalyst"
    later = make_pred(catalysts=[{"date": "2026-11-06", "event": "CPI", "impact": "high"}])
    assert build_plan(later, atr=4, params=P, costs=STOCK).flat_reason is None
    medium = make_pred(catalysts=[{"date": "2026-11-03", "event": "PMI", "impact": "medium"}])
    assert build_plan(medium, atr=4, params=P, costs=STOCK).flat_reason is None
    bad_date = make_pred(catalysts=[{"date": "next week", "event": "?", "impact": "high"}])
    assert build_plan(bad_date, atr=4, params=P, costs=STOCK).flat_reason is None      # unparseable → ignored
    past = make_pred(catalysts=[{"date": "2026-11-01", "event": "NFP", "impact": "high"}])
    assert build_plan(past, atr=4, params=P, costs=STOCK).flat_reason is None


def test_calibrator_is_used_and_flagged():
    plan = build_plan(make_pred(prob_up=0.62), atr=4.0, params=P, costs=STOCK, calibrator=lambda p: 0.55)
    assert plan.calibrated and plan.p_win == pytest.approx(0.55)
    assert plan.expected_value_r == pytest.approx(0.55 * 1.1 - 0.45 - 0.004)
    short = make_pred(s_final=-0.6, prob_up=0.35, p10=82.0, p50=96.0, p90=108.0)
    assert build_plan(short, atr=3, params=P, costs=STOCK, calibrator=lambda p: 0.4).p_win == pytest.approx(0.6)


def test_flat_plan_keeps_geometry_for_display_and_no_size():
    plan = build_plan(make_pred(s_final=0.1), atr=4.0, params=P, costs=STOCK)
    assert plan.direction == "flat" and plan.size is None
    assert plan.inputs["side"] == "long" and plan.stop == pytest.approx(90.0)


def test_zero_signal_and_bad_atr():
    assert build_plan(make_pred(s_final=0.0), atr=4, params=P, costs=STOCK).flat_reason == "weak_signal"
    with pytest.raises(ValueError):
        build_plan(make_pred(), atr=float("nan"), params=P, costs=STOCK)


def test_atr_at_uses_only_bars_up_to_decision_time():
    idx = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    c = np.full(40, 100.0)
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c}, index=idx)
    df.iloc[30:, df.columns.get_loc("High")] = 200.0              # a huge range after the decision bar
    assert atr_at(df, idx[29]) == pytest.approx(2.0)
    assert atr_at(df, idx[35]) > 2.0
