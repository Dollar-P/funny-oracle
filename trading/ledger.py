"""Ledger (PRO.md §3.4): cash, positions, equity curve and journal — in exact cents.

Cash flows use each fill's notional rounded to the cent (`Fill.notional`) plus its fee, so
    cash = starting cash + Σ fill cash flows
holds exactly, and for a closed position its realised P&L equals its cash flows. Equity marks each open
position at qty × mark rounded to the cent (signed: shorts are a liability).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading.broker_paper import Bracket, Fill
from trading.costs import money


def cash_flow(f: Fill) -> Decimal:
    return (f.notional if f.side == "sell" else -f.notional) - f.fee


@dataclass
class EquitySnapshot:
    ts: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
    open_risk: Decimal
    drawdown: float


@dataclass
class JournalEntry:
    key: str
    ticker: str
    side: str
    qty: float
    opened_at: datetime
    closed_at: datetime
    avg_entry: float
    avg_exit: float
    realized_pnl: Decimal
    r_multiple: float | None
    mae: float                   # price units per unit
    mfe: float
    mae_r: float | None
    mfe_r: float | None
    holding_hours: float
    costs: Decimal
    exit_reason: str


@dataclass
class Ledger:
    starting_cash: Decimal
    cash: Decimal = None  # type: ignore[assignment]
    qty: dict[str, float] = field(default_factory=dict)          # key → signed open qty
    fills: list[tuple[str, Fill]] = field(default_factory=list)
    peak: Decimal = None  # type: ignore[assignment]
    snapshots: list[EquitySnapshot] = field(default_factory=list)

    def __post_init__(self):
        self.starting_cash = money(self.starting_cash)
        if self.cash is None:
            self.cash = self.starting_cash
        if self.peak is None:
            self.peak = self.starting_cash

    def apply(self, key: str, f: Fill) -> None:
        self.cash += cash_flow(f)
        signed = f.qty if f.side == "buy" else -f.qty
        q = Decimal(repr(self.qty.get(key, 0.0))) + Decimal(repr(signed))
        if q == 0:
            self.qty.pop(key, None)
        else:
            self.qty[key] = float(q)
        self.fills.append((key, f))

    def apply_all(self, key: str, fills: list[Fill]) -> None:
        for f in fills:
            self.apply(key, f)

    def market_value(self, marks: dict[str, float]) -> tuple[Decimal, Decimal]:
        """(signed market value, gross exposure) of open positions, cents."""
        mv, gross = Decimal("0"), Decimal("0")
        for key, q in self.qty.items():
            v = money(q * marks[key])
            mv += v
            gross += abs(v)
        return mv, gross

    def equity(self, marks: dict[str, float]) -> Decimal:
        return self.cash + self.market_value(marks)[0]

    def snapshot(self, ts: datetime, marks: dict[str, float], open_risk: float = 0.0) -> EquitySnapshot:
        mv, gross = self.market_value(marks)
        eq = self.cash + mv
        self.peak = max(self.peak, eq)
        snap = EquitySnapshot(ts=ts, equity=eq, cash=self.cash, gross_exposure=gross, open_risk=money(open_risk),
                              drawdown=float(eq / self.peak - 1) if self.peak > 0 else 0.0)
        self.snapshots.append(snap)
        return snap

    def reconcile(self) -> Decimal:
        """Starting cash + Σ cash flows − cash. Must be exactly 0.00."""
        return self.starting_cash + sum((cash_flow(f) for _k, f in self.fills), Decimal("0")) - self.cash


def journal_entry(key: str, ticker: str, b: Bracket) -> JournalEntry:
    if b.state != "closed":
        raise ValueError("journal entries are for closed positions")
    rpu = b.risk_per_unit
    return JournalEntry(
        key=key, ticker=ticker, side=b.side, qty=b.qty, opened_at=b.opened_at, closed_at=b.closed_at,
        avg_entry=b.entry_price, avg_exit=b.avg_exit(), realized_pnl=b.realized_pnl(), r_multiple=b.r_multiple(),
        mae=b.mae, mfe=b.mfe, mae_r=b.mae / rpu if rpu else None, mfe_r=b.mfe / rpu if rpu else None,
        holding_hours=(b.closed_at - b.opened_at).total_seconds() / 3600, costs=b.costs_paid(),
        exit_reason=b.exit_reason or "")
