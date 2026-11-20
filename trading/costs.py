"""Cost model (PRO.md §3.4). Configured in bps per side; converted here and nowhere else.

- slippage moves the fill price against the trader: buy pays up, sell receives less;
- fee = fee_bps × fill notional, rounded half-up to the cent (the ledger keeps cents exactly).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import config

BPS = 10_000
CENT = Decimal("0.01")


@dataclass(frozen=True)
class CostModel:
    fee_bps: float
    slippage_bps: float

    @property
    def per_side_frac(self) -> float:
        return (self.fee_bps + self.slippage_bps) / BPS


def cost_model(asset_type: str, overrides: dict | None = None) -> CostModel:
    base = dict(config.PRO_COSTS_BPS.get(asset_type, config.PRO_COSTS_BPS["stock"]))
    if overrides and asset_type in overrides:
        base.update(overrides[asset_type])
    return CostModel(fee_bps=float(base["fee"]), slippage_bps=float(base["slippage"]))


def apply_slippage(price: float, side: Literal["buy", "sell"], cm: CostModel) -> float:
    s = cm.slippage_bps / BPS
    return price * (1 + s) if side == "buy" else price * (1 - s)


def fee(notional: Decimal, cm: CostModel) -> Decimal:
    return (abs(notional) * Decimal(str(cm.fee_bps)) / BPS).quantize(CENT, rounding=ROUND_HALF_UP)


def round_trip_cost_per_unit(price: float, cm: CostModel) -> float:
    """Estimated fee + slippage for entering and exiting one unit at ~price (both sides)."""
    return 2 * price * cm.per_side_frac


def money(x) -> Decimal:
    """Float/str → Decimal cents (half-up). Floats go through repr so 0.1 stays 0.1."""
    # repr(float(x)): numpy 2 scalars repr as "np.float64(…)", so always convert to a plain float first
    d = x if isinstance(x, Decimal) else Decimal(repr(float(x))) if isinstance(x, float) else Decimal(str(x))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)
