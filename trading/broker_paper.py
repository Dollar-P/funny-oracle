"""Paper broker (PRO.md §3.4). Paper money only — there is no live broker anywhere in this project.

`Bracket` is the single implementation of fill logic, shared by the forward paper loop (PaperBroker, which
persists it) and the backtester. It is fed one bar at a time and never sees a future bar.

Rules
- Entry: market orders fill at the open of the first bar whose *open time* is ≥ the submit time (never the
  signal bar) ± slippage. Limit entries fill when the bar trades through the limit, at min(open, limit) for a
  buy / max(open, limit) for a sell. If the entry price is already beyond the stop or T1, the entry is
  cancelled (`gap_invalidated`).
- Exits form one OCO group: stop, T1, T2, time exit. T1 closes 50 % (rounded down to the lot; all of it if half
  rounds to zero) and moves the stop to breakeven (the entry fill price). T2 closes the rest.
- Gaps: a stop gapped through fills at the open (worse); a target gapped through fills at the open (better,
  as a resting limit would).
- Intrabar ambiguity: stop and target both inside one bar → the stop is assumed first, unless a resolver
  (1m bars) says otherwise. After T1 fills inside a bar, the breakeven stop is live for the rest of that bar.
- Time exit: at the open of the first bar whose open time is ≥ time_exit (market, with slippage).
- Prices: stops, market entries and time exits pay slippage; target (limit) fills get exactly the limit.
  Fees are charged on every fill's notional (cents, half-up).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Literal

from trading.costs import CostModel, apply_slippage, fee as fee_for, money
from trading.risk import LOT, _floor_lot

Side = Literal["long", "short"]


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    open_time: datetime | None = None   # when the bar's first trade happened (calendar.bar_open_time)
    close_time: datetime | None = None  # when the bar's close is final (calendar.bar_close_time)

    @property
    def t_open(self) -> datetime:
        return self.open_time or self.ts

    @property
    def t_close(self) -> datetime:
        """Intrabar fills have no exact time; they are stamped at the bar's close."""
        return self.close_time or self.t_open


@dataclass
class Fill:
    ts: datetime
    role: str                     # entry | stop | t1 | t2 | time | end
    side: Literal["buy", "sell"]
    qty: float
    price: float                  # executed price (after slippage)
    raw_price: float              # price before slippage
    fee: Decimal
    slippage: Decimal             # cost of slippage in cents (≥ 0)

    @property
    def notional(self) -> Decimal:
        return money(self.price * self.qty)

    def to_dict(self) -> dict:
        return {"ts": self.ts.isoformat(), "role": self.role, "side": self.side, "qty": self.qty,
                "price": self.price, "raw_price": self.raw_price, "fee": str(self.fee),
                "slippage": str(self.slippage)}


# resolver(bar, side, stop, target) → "stop" | "target" | None  (None = unknown → stop)
Resolver = Callable[[Bar, Side, float, float], str | None]


@dataclass
class Bracket:
    side: Side
    qty: float
    stop: float
    targets: list[float]
    time_exit: datetime
    submit_ts: datetime
    costs: CostModel
    asset_type: str = "stock"
    entry_type: Literal["market", "limit"] = "market"
    limit: float | None = None
    expiry: datetime | None = None
    # state
    state: str = "pending"        # pending | open | closed | cancelled
    qty_open: float = 0.0
    entry_price: float | None = None
    stop_initial: float | None = None
    t1_done: bool = False
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_reason: str | None = None
    cancel_reason: str | None = None
    mae: float = 0.0              # max adverse excursion, price units per unit (≥ 0)
    mfe: float = 0.0              # max favourable excursion, price units per unit (≥ 0)
    bars_held: int = 0
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self):
        if self.stop_initial is None:
            self.stop_initial = self.stop

    # --- helpers -------------------------------------------------------------------------------
    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def lot(self) -> Decimal:
        return LOT.get(self.asset_type, Decimal("1"))

    def _fill(self, ts, role: str, qty: float, raw: float, slip: bool) -> Fill:
        opening = role == "entry"
        buy = (self.side == "long") == opening
        side = "buy" if buy else "sell"
        price = apply_slippage(raw, side, self.costs) if slip else raw
        f = Fill(ts=ts, role=role, side=side, qty=qty, price=price, raw_price=raw,
                 fee=fee_for(money(price * qty), self.costs), slippage=money(abs(price - raw) * qty))
        self.fills.append(f)
        return f

    def _exit(self, ts, role: str, qty: float, raw: float, slip: bool) -> Fill:
        qty = min(qty, self.qty_open)
        f = self._fill(ts, role, qty, raw, slip)
        self.qty_open = float(Decimal(repr(self.qty_open)) - Decimal(repr(qty)))
        if self.qty_open <= 0:
            self.qty_open = 0.0
            self.state = "closed"
            self.closed_at = ts
            self.exit_reason = role
        return f

    def _adverse(self, price: float) -> float:
        return max(0.0, self.sign * (self.entry_price - price))

    def _favourable(self, price: float) -> float:
        return max(0.0, self.sign * (price - self.entry_price))

    @property
    def next_target(self) -> float:
        return self.targets[1] if self.t1_done else self.targets[0]

    def _beyond_stop(self, price: float) -> bool:
        return price <= self.stop if self.side == "long" else price >= self.stop

    def _reaches(self, price: float, target: float) -> bool:
        return price >= target if self.side == "long" else price <= target

    def _take_target(self, ts, raw: float) -> None:
        if not self.t1_done:
            half = _floor_lot(self.qty_open / 2, self.lot)
            self._exit(ts, "t1", half if half > 0 else self.qty_open, raw, slip=False)
            self.t1_done = True
            self.stop = self.entry_price  # breakeven
        else:
            self._exit(ts, "t2", self.qty_open, raw, slip=False)

    def cancel(self, reason: str) -> None:
        if self.state == "pending":
            self.state, self.cancel_reason = "cancelled", reason

    # --- the bar loop ----------------------------------------------------------------------------
    def on_bar(self, bar: Bar, resolver: Resolver | None = None) -> list[Fill]:
        if self.state in ("closed", "cancelled"):
            return []
        n0 = len(self.fills)
        entered = False
        if self.state == "pending":
            if bar.t_open < self.submit_ts:
                return []
            if self.expiry is not None and bar.t_open > self.expiry:
                self.cancel("expired")
                return []
            if self.entry_type == "market":
                raw = bar.open
            else:
                lim = float(self.limit)
                touched = bar.low <= lim if self.side == "long" else bar.high >= lim
                if not touched:
                    return []
                raw = min(bar.open, lim) if self.side == "long" else max(bar.open, lim)
            if self._beyond_stop(raw) or self._reaches(raw, self.targets[0]):
                self.cancel("gap_invalidated")
                return []
            f = self._fill(bar.t_open, "entry", self.qty, raw, slip=self.entry_type == "market")
            self.entry_price = f.price
            self.qty_open = self.qty
            self.state = "open"
            self.opened_at = bar.t_open
            entered = True

        hi_fav = bar.high if self.side == "long" else bar.low
        lo_adv = bar.low if self.side == "long" else bar.high
        if not entered:
            self.bars_held += 1
            if bar.t_open >= self.time_exit:                                   # time exit at the open
                self._exit(bar.t_open, "time", self.qty_open, bar.open, slip=True)
                return self.fills[n0:]
            if self._beyond_stop(bar.open):                                    # gap through the stop
                self.mae = max(self.mae, self._adverse(bar.open))
                self._exit(bar.t_open, "stop", self.qty_open, bar.open, slip=True)
                return self.fills[n0:]
            while self.state == "open" and self._reaches(bar.open, self.next_target):   # gap through target(s)
                self.mfe = max(self.mfe, self._favourable(bar.open))
                self._take_target(bar.t_open, bar.open)

        while self.state == "open":
            tgt = self.next_target
            hit_stop = self._beyond_stop(lo_adv)
            hit_tgt = self._reaches(hi_fav, tgt)
            if hit_stop and hit_tgt:
                first = (resolver(bar, self.side, self.stop, tgt) if resolver else None) or "stop"
            else:
                first = "stop" if hit_stop else "target" if hit_tgt else None
            if first is None:
                self.mae = max(self.mae, self._adverse(lo_adv))
                self.mfe = max(self.mfe, self._favourable(hi_fav))
                break
            if first == "stop":
                self.mae = max(self.mae, self._adverse(self.stop))
                self._exit(bar.t_close, "stop", self.qty_open, self.stop, slip=True)
            else:
                self.mfe = max(self.mfe, self._favourable(tgt))
                if not hit_stop:
                    self.mae = max(self.mae, self._adverse(lo_adv))
                self._take_target(bar.t_close, tgt)
        return self.fills[n0:]

    def close_at(self, ts: datetime, price: float, reason: str = "end") -> list[Fill]:
        """Force-close (end of a backtest, manual close). Market, with slippage."""
        if self.state == "pending":
            self.cancel(reason)
            return []
        if self.state != "open":
            return []
        return [self._exit(ts, reason, self.qty_open, price, slip=True)]

    # --- results ---------------------------------------------------------------------------------
    @property
    def risk_per_unit(self) -> float:
        return abs((self.entry_price or 0.0) - (self.stop_initial or 0.0))

    def realized_pnl(self) -> Decimal:
        """Cash P&L of this bracket's fills (sells − buys − fees); exact cents."""
        total = Decimal("0")
        for f in self.fills:
            total += f.notional if f.side == "sell" else -f.notional
            total -= f.fee
        return total

    def costs_paid(self) -> Decimal:
        return sum((f.fee + f.slippage for f in self.fills), Decimal("0"))

    def r_multiple(self) -> float | None:
        risk = self.risk_per_unit * self.qty
        if self.state != "closed" or risk <= 0:
            return None
        return float(self.realized_pnl()) / risk

    def avg_exit(self) -> float | None:
        ex = [f for f in self.fills if f.role != "entry"]
        q = sum(f.qty for f in ex)
        return sum(f.price * f.qty for f in ex) / q if q else None

    def to_state(self) -> str:
        d = {k: getattr(self, k) for k in ("side", "qty", "stop", "targets", "asset_type", "entry_type", "limit",
                                           "state", "qty_open", "entry_price", "stop_initial", "t1_done",
                                           "exit_reason", "cancel_reason", "mae", "mfe", "bars_held")}
        for k in ("time_exit", "submit_ts", "expiry", "opened_at", "closed_at"):
            v = getattr(self, k)
            d[k] = v.isoformat() if v else None
        d["costs"] = {"fee_bps": self.costs.fee_bps, "slippage_bps": self.costs.slippage_bps}
        d["fills"] = [f.to_dict() for f in self.fills]
        return json.dumps(d)

    @classmethod
    def from_state(cls, s: str) -> "Bracket":
        d = json.loads(s)
        dt = {k: datetime.fromisoformat(d[k]) if d.get(k) else None
              for k in ("time_exit", "submit_ts", "expiry", "opened_at", "closed_at")}
        fills = [Fill(ts=datetime.fromisoformat(f["ts"]), role=f["role"], side=f["side"], qty=f["qty"],
                      price=f["price"], raw_price=f["raw_price"], fee=Decimal(f["fee"]),
                      slippage=Decimal(f["slippage"])) for f in d.pop("fills")]
        c = d.pop("costs")
        for k in dt:
            d.pop(k, None)
        b = cls(**d, **dt, costs=CostModel(c["fee_bps"], c["slippage_bps"]))
        b.fills = fills
        return b


def minute_resolver(fetch_minutes: Callable[[datetime, datetime], list[Bar]]) -> Resolver:
    """Resolve stop-vs-target inside a bar from 1m bars (yfinance keeps ~7 days). Same minute → stop."""

    def resolve(bar: Bar, side: Side, stop: float, target: float) -> str | None:
        try:
            minutes = fetch_minutes(bar.t_open, bar.ts)
        except Exception:
            return None
        for m in minutes:
            s = m.low <= stop if side == "long" else m.high >= stop
            t = m.high >= target if side == "long" else m.low <= target
            if s:
                return "stop"
            if t:
                return "target"
        return None

    return resolve
