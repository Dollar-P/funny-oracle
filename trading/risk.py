"""Risk Engine (PRO.md §3.3): sizing, hard limits and circuit breakers — enforced before any paper order.

`size_plan` is a pure function of (plan, portfolio, params). Order of checks:

    flat_plan → halted → max_drawdown → daily_loss → max_open_positions → duplicate_position → no_volatility
    → base size = risk_amount / |entry − stop|
    → caps (min of): vol_cap, max_position, gross_cap, class_cap, kelly        (each recorded in capped_by)
    → lot rounding DOWN → size_below_lot
    → correlation guard
    → re-check every limit on the final quantity (defence in depth) → internal_limit_check

Every rejection is a RiskEvent(rule, detail); `record_rejections` writes them to `risk_events`.
Exposure is measured at the current mark; open risk = qty × distance from mark to the current stop (≥ 0).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

import config
from core.db import connect
from trading.signals import SizeSpec, TradePlan

RULES = {"flat_plan", "halted", "max_drawdown", "daily_loss", "max_open_positions", "duplicate_position",
         "no_volatility", "vol_cap", "max_position", "gross_cap", "class_cap", "kelly", "kelly_negative",
         "size_below_lot", "correlation", "internal_limit_check"}
LOT = {"stock": Decimal("1"), "gold": Decimal("0.01"), "crypto": Decimal("0.0001")}
TOL = 1e-9


@dataclass
class RiskParams:
    risk_pct_swing: float = 0.005
    risk_pct_scalp: float = 0.0025
    vol_target: float = 0.15
    max_position: float = 0.20
    max_gross: float = 1.0
    max_class: dict = field(default_factory=lambda: {"stock": 0.60, "crypto": 0.30, "gold": 0.30})
    max_open: int = 8
    corr_max: float = 0.8
    corr_combined_risk: float = 0.01
    corr_lookback: int = 60
    daily_loss: float = -0.02
    max_dd: float = -0.10
    kelly_fraction: float = 0.25
    kelly_min_n: int = 100

    @classmethod
    def from_config(cls, overrides: dict | None = None) -> "RiskParams":
        d = {**config.PRO_RISK, **(overrides or {})}
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OpenPos:
    ticker: str
    asset_type: str
    side: Literal["long", "short"]
    qty: float
    entry: float
    stop: float
    mark: float

    @property
    def notional(self) -> float:
        return abs(self.qty * self.mark)

    @property
    def open_risk(self) -> float:
        d = (self.mark - self.stop) if self.side == "long" else (self.stop - self.mark)
        return abs(self.qty) * max(0.0, d)


@dataclass
class Portfolio:
    equity: float
    peak_equity: float
    day_start_equity: float
    positions: list[OpenPos] = field(default_factory=list)
    halted: bool = False                       # max-drawdown breaker tripped earlier → manual reset needed
    returns: dict[str, pd.Series] = field(default_factory=dict)  # daily returns up to the decision time

    @property
    def gross(self) -> float:
        return sum(p.notional for p in self.positions)

    def class_gross(self, asset_type: str) -> float:
        return sum(p.notional for p in self.positions if p.asset_type == asset_type)

    @property
    def drawdown(self) -> float:
        return self.equity / self.peak_equity - 1 if self.peak_equity > 0 else 0.0

    @property
    def day_return(self) -> float:
        return self.equity / self.day_start_equity - 1 if self.day_start_equity > 0 else 0.0


class RiskEvent(BaseModel):
    rule: str
    detail: str


class RiskDecision(BaseModel):
    approved: bool
    size: SizeSpec | None = None
    rejections: list[RiskEvent] = Field(default_factory=list)
    halt: bool = False                          # the max-drawdown breaker tripped on this decision
    notes: dict = Field(default_factory=dict)


def _reject(rule: str, detail: str, halt: bool = False, **notes) -> RiskDecision:
    return RiskDecision(approved=False, rejections=[RiskEvent(rule=rule, detail=detail)], halt=halt, notes=notes)


def breaker_state(pf: Portfolio, params: RiskParams) -> list[str]:
    out = []
    if pf.halted or pf.drawdown <= params.max_dd:
        out.append("max_drawdown")
    if pf.day_return <= params.daily_loss:
        out.append("daily_loss")
    return out


def kelly_fraction_star(plan: TradePlan) -> float:
    b = 0.5 * plan.inputs.get("r1", 0.0) + 0.5 * plan.inputs.get("r2", 0.0)
    if b <= 0:
        return -1.0
    p = plan.p_win
    return p - (1 - p) / b


def _floor_lot(qty: float, lot: Decimal) -> float:
    if qty <= 0 or not math.isfinite(qty):
        return 0.0
    q = (Decimal(repr(float(qty))) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    return float(q)


def _corr(a, b, n: int) -> float | None:
    """Pearson ρ of the last n overlapping returns (≥ 20 needed). Series are aligned on their index; numpy arrays
    must already be aligned (same length, NaN where an asset has no bar)."""
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        m = np.isfinite(a) & np.isfinite(b)
        x, y = a[m][-n:], b[m][-n:]
        if len(x) < 20 or x.std() == 0 or y.std() == 0:
            return None
        return float(np.corrcoef(x, y)[0, 1])
    j = pd.concat([a, b], axis=1, join="inner").dropna().tail(n)
    if len(j) < 20:
        return None
    c = j.iloc[:, 0].corr(j.iloc[:, 1])
    return None if pd.isna(c) else float(c)


def size_plan(plan: TradePlan, pf: Portfolio, params: RiskParams, *, sigma_annual: float,
              calibration_n: int = 0) -> RiskDecision:
    if plan.direction == "flat":
        return _reject("flat_plan", f"plan is FLAT ({plan.flat_reason})")
    if pf.halted:
        return _reject("halted", "max-drawdown breaker tripped earlier; manual reset required")
    if pf.drawdown <= params.max_dd:
        return _reject("max_drawdown", f"drawdown {pf.drawdown:.2%} ≤ {params.max_dd:.0%} from peak", halt=True)
    if pf.day_return <= params.daily_loss:
        return _reject("daily_loss", f"day P&L {pf.day_return:.2%} ≤ {params.daily_loss:.0%}; no new trades today")
    if len(pf.positions) >= params.max_open:
        return _reject("max_open_positions", f"{len(pf.positions)} open ≥ max {params.max_open}")
    if any(p.ticker == plan.ticker for p in pf.positions):
        return _reject("duplicate_position", f"{plan.ticker} already has an open position")
    if sigma_annual is None or not math.isfinite(sigma_annual) or sigma_annual <= 0:
        return _reject("no_volatility", f"annual σ unavailable ({sigma_annual!r})")

    eq = pf.equity
    entry = plan.entry.ref_price
    rpu = abs(entry - plan.stop)
    if rpu <= 0 or entry <= 0:
        return _reject("internal_limit_check", f"zero risk per unit (entry {entry}, stop {plan.stop})")
    risk_pct = params.risk_pct_scalp if plan.mode == "scalp" else params.risk_pct_swing
    risk_amount = risk_pct * eq
    qty_risk = risk_amount / rpu

    caps: dict[str, float] = {
        "vol_cap": params.vol_target * eq / sigma_annual / entry,
        "max_position": params.max_position * eq / entry,
        "gross_cap": (params.max_gross * eq - pf.gross) / entry,
        "class_cap": (params.max_class.get(plan.asset_type, 0.0) * eq - pf.class_gross(plan.asset_type)) / entry,
    }
    kelly_on = plan.calibrated and calibration_n >= params.kelly_min_n
    if kelly_on:
        f = kelly_fraction_star(plan)
        if f <= 0:
            return _reject("kelly_negative", f"Kelly f* {f:.3f} ≤ 0 with calibrated p {plan.p_win:.2f}")
        caps["kelly"] = params.kelly_fraction * f * eq / rpu
    for rule, q in caps.items():
        if q <= 0:
            return _reject(rule, f"{rule} leaves no room (max qty {q:.6g})")
    qty = min([qty_risk, *caps.values()])
    capped_by = [r for r, q in caps.items() if q < qty_risk * (1 - TOL)]

    lot = LOT.get(plan.asset_type, Decimal("1"))
    qty = _floor_lot(qty, lot)
    if qty <= 0:
        return _reject("size_below_lot", f"size rounds to 0 lots of {lot} (binding: {', '.join(capped_by) or 'risk'})")
    new_risk = qty * rpu

    for p in pf.positions:
        if p.side != plan.side or plan.ticker not in pf.returns or p.ticker not in pf.returns:
            continue
        c = _corr(pf.returns[plan.ticker], pf.returns[p.ticker], params.corr_lookback)
        combined = (p.open_risk + new_risk) / eq
        if c is not None and c > params.corr_max and combined > params.corr_combined_risk:
            return _reject("correlation", f"ρ {c:.2f} with {p.ticker} ({p.side}) > {params.corr_max} and combined "
                                          f"risk {combined:.2%} > {params.corr_combined_risk:.0%}")

    notional = qty * entry
    checks = {
        "risk_per_trade": new_risk <= risk_amount * (1 + TOL),
        "vol_cap": notional <= params.vol_target * eq / sigma_annual * (1 + TOL),
        "max_position": notional <= params.max_position * eq * (1 + TOL),
        "gross_cap": pf.gross + notional <= params.max_gross * eq * (1 + TOL),
        "class_cap": pf.class_gross(plan.asset_type) + notional <= params.max_class.get(plan.asset_type, 0) * eq * (1 + TOL),
        "max_open": len(pf.positions) < params.max_open,
    }
    if kelly_on:
        checks["kelly"] = new_risk <= params.kelly_fraction * kelly_fraction_star(plan) * eq * (1 + TOL)
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        return _reject("internal_limit_check", f"final size breaks {', '.join(bad)}")
    return RiskDecision(approved=True, size=SizeSpec(qty=qty, notional=notional, risk_amount=new_risk,
                                                     risk_pct=new_risk / eq, capped_by=capped_by),
                        notes={"qty_risk": qty_risk, "caps": caps, "lot": str(lot), "kelly_on": kelly_on})


def record_rejections(decision: RiskDecision, signal_id: int | None, experiment_id: int | None,
                      ts: datetime | None = None) -> None:
    if decision.approved or not decision.rejections:
        return
    ts = (ts or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.executemany("INSERT INTO risk_events (experiment_id, ts, signal_id, rule, detail) VALUES (?,?,?,?,?)",
                         [(experiment_id, ts, signal_id, r.rule, r.detail) for r in decision.rejections])


def record_event(rule: str, detail: str, experiment_id: int | None, signal_id: int | None = None,
                 ts: datetime | None = None) -> None:
    """Breaker trips and other risk events that are not a sizing rejection."""
    ts = (ts or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("INSERT INTO risk_events (experiment_id, ts, signal_id, rule, detail) VALUES (?,?,?,?,?)",
                     (experiment_id, ts, signal_id, rule, detail))
