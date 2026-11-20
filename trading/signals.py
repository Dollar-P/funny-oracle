"""Signal Engine (PRO.md §3.2): Prediction → TradePlan (or FLAT).

A prediction is not a trade (§2.1). The plan's geometry is always computed (so the UI can show why a signal is
FLAT), then the rules decide. Reasons are a fixed vocabulary, checked in this order — the first failing one is
`flat_reason`, all failing ones are in `inputs["fails"]`:

    degraded_data · judge_no_trade · weak_signal · low_confidence · short_not_allowed · catalyst ·
    bad_geometry · low_rr · negative_ev

Geometry (long; short mirrors it):
    entry  = forecast.price_now (market order at the next bar's open)
    stop   = the farther of entry − k_atr·ATR and P10 (P10 only counts when it is below entry)
    T1, T2 = P50, P90
    R1, R2 = (T − entry) / (entry − stop)
    reward_risk = R2                                    (full-target convention, docs/pro-plan.md A3)
    cost_R = round-trip fee + slippage per unit / (entry − stop)
    EV_R   = p·(0.5·R1 + 0.5·R2) − (1 − p)·1 − cost_R   (T1 closes half, T2 the rest)
    p      = calibrated P(up) when a calibrator exists, else raw P(up) — flagged `calibrated=False`.
             Short: 1 − P(up). This is a rough proxy for P(target before stop) (§3.2).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from typing import Callable, Literal

import pandas as pd
from pydantic import BaseModel, Field

import config
from core.schemas import Prediction
from mcp_server import indicators as ind
from trading.costs import CostModel, round_trip_cost_per_unit

FLAT_REASONS = ("degraded_data", "judge_no_trade", "weak_signal", "low_confidence", "short_not_allowed",
                "catalyst", "bad_geometry", "low_rr", "negative_ev")
EV_EPS = 1e-9   # |EV| below this is break-even (float noise) → FLAT, never a trade


@dataclass
class SignalParams:
    s_min: float = 0.15
    conf_min: float = 0.5
    rr_min: float = 1.5
    k_atr_swing: float = 1.5
    k_atr_scalp: float = 1.0
    catalyst_window: float = 0.10          # fraction of the horizon
    allow_crypto_short: bool = False
    atr_period: int = 14

    @classmethod
    def from_config(cls, overrides: dict | None = None) -> "SignalParams":
        d = {**config.PRO_SIGNAL, **(overrides or {})}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


class EntrySpec(BaseModel):
    type: Literal["market", "limit"] = "market"
    ref_price: float                      # price the plan was computed from (decision bar close)
    limit: float | None = None
    expiry: datetime | None = None


class SizeSpec(BaseModel):
    qty: float
    notional: float
    risk_amount: float
    risk_pct: float                       # of equity
    capped_by: list[str] = Field(default_factory=list)


class TradePlan(BaseModel):
    prediction_id: int | None
    ticker: str
    asset_type: str
    mode: str
    horizon: str
    direction: Literal["long", "short", "flat"]
    conviction: float
    entry: EntrySpec
    stop: float
    targets: list[float]
    time_exit: datetime
    reward_risk: float
    expected_value_r: float
    size: SizeSpec | None = None
    thesis: str = ""
    invalidation: str = ""
    flat_reason: str | None = None
    # diagnostics
    p_win: float
    calibrated: bool = False
    cost_r: float = 0.0
    created_at: datetime
    inputs: dict = Field(default_factory=dict)

    @property
    def side(self) -> Literal["long", "short"]:
        return self.inputs.get("side", "long")

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry.ref_price - self.stop)


def atr_at(df: pd.DataFrame, at: datetime | pd.Timestamp, period: int = 14) -> float:
    """ATR(period) using only bars with timestamp ≤ `at` (no lookahead)."""
    past = df.loc[:pd.Timestamp(at)]
    if len(past) < 2:
        raise ValueError("not enough bars for ATR")
    return float(ind.atr(past, period).iloc[-1])


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _catalyst_hit(pred: Prediction, created: datetime, horizon_minutes: float, window: float) -> str | None:
    if not pred.verdict or not pred.verdict.catalysts:
        return None
    end = created + timedelta(minutes=horizon_minutes * window)
    for c in pred.verdict.catalysts:
        d = _parse_date(c.date)
        if c.impact == "high" and d is not None and created.date() <= d <= end.date():
            return f"{c.event} on {d.isoformat()}"
    return None


def build_plan(pred: Prediction, *, atr: float, params: SignalParams, costs: CostModel,
               calibrator: Callable[[float], float] | None = None) -> TradePlan:
    if atr is None or not math.isfinite(atr) or atr < 0:
        raise ValueError(f"invalid ATR {atr!r}")
    f = pred.forecast
    created = datetime.fromisoformat(pred.created_at)
    time_exit = datetime.fromisoformat(pred.target_time)
    horizon_min = (time_exit - created).total_seconds() / 60
    s = float(pred.s_final)
    side: Literal["long", "short"] = "long" if s >= 0 else "short"
    entry = float(f.price_now)
    k = params.k_atr_scalp if pred.mode == "scalp" else params.k_atr_swing

    if side == "long":
        atr_stop = entry - k * atr
        band = f.p10 if f.p10 < entry else None
        stop = min(atr_stop, band) if band is not None else atr_stop
        t1, t2 = float(f.p50), float(f.p90)
    else:
        atr_stop = entry + k * atr
        band = f.p90 if f.p90 > entry else None
        stop = max(atr_stop, band) if band is not None else atr_stop
        t1, t2 = float(f.p50), float(f.p10)
    sign = 1.0 if side == "long" else -1.0
    risk = abs(entry - stop)
    r1 = sign * (t1 - entry) / risk if risk > 0 else 0.0
    r2 = sign * (t2 - entry) / risk if risk > 0 else 0.0
    cost_r = round_trip_cost_per_unit(entry, costs) / risk if risk > 0 else float("inf")

    raw_up = float(f.prob_up)
    calibrated = calibrator is not None
    p_up = float(calibrator(raw_up)) if calibrator is not None else raw_up
    p_win = p_up if side == "long" else 1.0 - p_up
    ev = p_win * (0.5 * r1 + 0.5 * r2) - (1 - p_win) * 1.0 - cost_r if risk > 0 else float("-inf")
    confidence = float(pred.confidence)
    conviction = max(0.0, min(1.0, abs(s) * confidence))

    fails: list[str] = []
    detail: dict[str, str] = {}
    if pred.degraded:
        fails.append("degraded_data")
        detail["degraded_data"] = ", ".join(pred.degraded)
    if pred.verdict and pred.verdict.no_trade_reason:
        fails.append("judge_no_trade")
        detail["judge_no_trade"] = pred.verdict.no_trade_reason
    if abs(s) < params.s_min:
        fails.append("weak_signal")
        detail["weak_signal"] = f"|S_final| {abs(s):.3f} < {params.s_min}"
    if confidence < params.conf_min:
        fails.append("low_confidence")
        detail["low_confidence"] = f"confidence {confidence:.2f} < {params.conf_min}"
    if side == "short" and pred.asset_type == "crypto" and not params.allow_crypto_short:
        fails.append("short_not_allowed")
        detail["short_not_allowed"] = "crypto shorts disabled (ALLOW_CRYPTO_SHORT=false)"
    hit = _catalyst_hit(pred, created, horizon_min, params.catalyst_window)
    if hit:
        fails.append("catalyst")
        detail["catalyst"] = f"high-impact {hit} inside the first {params.catalyst_window:.0%} of the horizon"
    if risk <= 0 or r1 <= 0 or r2 <= 0:
        fails.append("bad_geometry")
        detail["bad_geometry"] = f"T1 {t1:.4g} / T2 {t2:.4g} not beyond entry {entry:.4g} for a {side}"
    if r2 < params.rr_min:
        fails.append("low_rr")
        detail["low_rr"] = f"R:R {r2:.2f} < {params.rr_min}"
    if not ev > EV_EPS:
        fails.append("negative_ev")
        detail["negative_ev"] = f"EV {ev:+.3f} R after costs"

    flat_reason = next((r for r in FLAT_REASONS if r in fails), None)
    v = pred.verdict
    return TradePlan(
        prediction_id=pred.id, ticker=pred.ticker, asset_type=pred.asset_type, mode=pred.mode,
        horizon=pred.horizon, direction="flat" if flat_reason else side, conviction=conviction,
        entry=EntrySpec(type="market", ref_price=entry), stop=stop, targets=[t1, t2], time_exit=time_exit,
        reward_risk=r2, expected_value_r=ev, thesis=(v.thesis or v.summary) if v else "",
        invalidation=(v.invalidation or "") if v else "", flat_reason=flat_reason, p_win=p_win,
        calibrated=calibrated, cost_r=cost_r, created_at=created,
        inputs={"side": side, "fails": fails, "detail": detail, "atr": atr, "k_atr": k, "atr_stop": atr_stop,
                "band_stop": band, "r1": r1, "r2": r2, "s_final": s, "confidence": confidence,
                "prob_up_raw": raw_up, "prob_up_used": p_up, "params": params.to_dict(),
                "costs_bps": {"fee": costs.fee_bps, "slippage": costs.slippage_bps}},
    )
