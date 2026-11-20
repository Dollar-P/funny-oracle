"""Persistent paper account for the forward paper loop (PRO.md §3.4). Paper money only.

One account per experiment, stored in SQLite (orders, fills, positions, equity_snapshots). Each position row
keeps its Bracket state plus the timestamp of the last bar it processed, so a restart never feeds a bar twice.
Cash is always rebuilt from the fills table (starting equity + Σ cash flows), in exact cents.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from core.db import connect
from trading.broker_paper import Bar, Bracket, Fill
from trading.costs import money
from trading.ledger import EquitySnapshot

D0 = Decimal("0")


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts else None


class PaperAccount:
    def __init__(self, experiment_id: int, starting_equity: float | Decimal):
        self.experiment_id = experiment_id
        self.starting = money(starting_equity)

    # --- orders ------------------------------------------------------------------------------------
    def open_bracket(self, *, signal_id: int, ticker: str, asset_type: str, bracket: Bracket, now: datetime,
                     meta: dict | None = None) -> int:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO positions (signal_id, experiment_id, ticker, asset_type, side, qty, qty_open, stop,"
                " targets_json, time_exit, state_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, self.experiment_id, ticker, asset_type, bracket.side, bracket.qty, 0.0, bracket.stop,
                 json.dumps(bracket.targets), _iso(bracket.time_exit),
                 json.dumps({"bracket": bracket.to_state(), "last_bar": None, "meta": meta or {}})))
            pid = int(cur.lastrowid)
            buy = bracket.side == "long"
            close_side = "sell" if buy else "buy"
            rows = [
                ("entry", bracket.entry_type, "buy" if buy else "sell", bracket.limit, None, "working"),
                ("stop", "stop", close_side, None, bracket.stop, "pending"),
                ("t1", "limit", close_side, bracket.targets[0], None, "pending"),
                ("t2", "limit", close_side, bracket.targets[1], None, "pending"),
                ("time", "market", close_side, None, None, "pending"),
            ]
            for role, typ, side, lim, stp, status in rows:
                conn.execute(
                    "INSERT INTO orders (signal_id, experiment_id, created_at, ticker, type, role, side, qty, limit_price,"
                    " stop_price, status, oco_group, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (signal_id, self.experiment_id, _iso(now), ticker, typ, role, side, bracket.qty, lim, stp, status,
                     f"pos-{pid}", _iso(now)))
        return pid

    @staticmethod
    def _orders(conn, pid: int) -> dict[str, int]:
        return {r["role"]: r["id"] for r in conn.execute("SELECT id, role FROM orders WHERE oco_group = ?", (f"pos-{pid}",))}

    # --- bar processing ----------------------------------------------------------------------------------
    def open_rows(self, ticker: str | None = None) -> list[dict]:
        q = "SELECT * FROM positions WHERE experiment_id = ? AND closed_at IS NULL AND exit_reason IS NULL"
        args: list = [self.experiment_id]
        if ticker:
            q += " AND ticker = ?"
            args.append(ticker)
        with connect() as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def process(self, ticker: str, bars: list[Bar], resolver=None) -> list[dict]:
        """Feed new bars (ts > the position's last processed bar) to every live bracket on `ticker`.
        Returns events: order_filled / position_closed / entry_cancelled."""
        events: list[dict] = []
        for row in self.open_rows(ticker):
            st = json.loads(row["state_json"])
            b = Bracket.from_state(st["bracket"])
            last = datetime.fromisoformat(st["last_bar"]) if st["last_bar"] else None
            new_fills: list[Fill] = []
            for bar in bars:
                if last is not None and bar.ts <= last:
                    continue
                if bar.t_open < b.submit_ts and b.state == "pending":
                    continue  # not eligible yet; don't advance `last` so it is never skipped by mistake
                new_fills += b.on_bar(bar, resolver)
                last = bar.ts
                if b.state in ("closed", "cancelled"):
                    break
            if new_fills or b.state in ("closed", "cancelled") or last is not None:
                events += self._persist(row["id"], row["signal_id"], ticker, b, new_fills, last, st.get("meta"))
        return events

    def cancel_pending(self, pid: int, reason: str) -> list[dict]:
        with connect() as conn:
            row = dict(conn.execute("SELECT * FROM positions WHERE id = ?", (pid,)).fetchone())
        st = json.loads(row["state_json"])
        b = Bracket.from_state(st["bracket"])
        b.cancel(reason)
        return self._persist(pid, row["signal_id"], row["ticker"], b, [],
                             datetime.fromisoformat(st["last_bar"]) if st["last_bar"] else None, st.get("meta"))

    def force_close(self, pid: int, ts: datetime, price: float, reason: str = "manual") -> list[dict]:
        with connect() as conn:
            row = dict(conn.execute("SELECT * FROM positions WHERE id = ?", (pid,)).fetchone())
        st = json.loads(row["state_json"])
        b = Bracket.from_state(st["bracket"])
        fills = b.close_at(ts, price, reason)
        return self._persist(pid, row["signal_id"], row["ticker"], b, fills,
                             datetime.fromisoformat(st["last_bar"]) if st["last_bar"] else None, st.get("meta"))

    def _persist(self, pid: int, signal_id: int, ticker: str, b: Bracket, new_fills: list[Fill],
                 last: datetime | None, meta: dict | None = None) -> list[dict]:
        events: list[dict] = []
        group = f"pos-{pid}"
        with connect() as conn:
            oid = self._orders(conn, pid)
            for f in new_fills:
                role = f.role if f.role in oid else "time"   # end / manual closes book against the time order
                conn.execute(
                    "INSERT INTO fills (order_id, experiment_id, position_id, ticker, side, ts, price, qty, fee, slippage,"
                    " reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (oid[role], self.experiment_id, pid, ticker, f.side, _iso(f.ts), f.price, f.qty, float(f.fee),
                     float(f.slippage), f.role))
                conn.execute("UPDATE orders SET status = 'filled', updated_at = ? WHERE id = ?", (_iso(f.ts), oid[role]))
                if f.role == "entry":
                    conn.execute("UPDATE orders SET status = 'working', updated_at = ? WHERE oco_group = ?"
                                 " AND role != 'entry'", (_iso(f.ts), group))
                elif f.role == "t1":
                    conn.execute("UPDATE orders SET stop_price = ?, updated_at = ? WHERE id = ?",
                                 (b.stop, _iso(f.ts), oid["stop"]))
                events.append({"type": "order_filled", "position_id": pid, "signal_id": signal_id, "ticker": ticker,
                               "role": f.role, "side": f.side, "qty": f.qty, "price": f.price, "fee": float(f.fee),
                               "ts": _iso(f.ts)})
            if b.state in ("closed", "cancelled"):
                conn.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE oco_group = ?"
                             " AND status IN ('working', 'pending')", (_iso(b.closed_at or last), group))
            conn.execute(
                "UPDATE positions SET opened_at = ?, closed_at = ?, qty_open = ?, avg_entry = ?, avg_exit = ?, stop = ?,"
                " realized_pnl = ?, r_multiple = ?, mae = ?, mfe = ?, costs = ?, exit_reason = ?, state_json = ?"
                " WHERE id = ?",
                (_iso(b.opened_at), _iso(b.closed_at), b.qty_open, b.entry_price, b.avg_exit(), b.stop,
                 float(b.realized_pnl()) if b.state == "closed" else None, b.r_multiple(), b.mae, b.mfe,
                 float(b.costs_paid()), b.exit_reason or b.cancel_reason,
                 json.dumps({"bracket": b.to_state(), "last_bar": _iso(last), "meta": meta or {}}), pid))
        if b.state == "closed":
            events.append({"type": "position_closed", "position_id": pid, "signal_id": signal_id, "ticker": ticker,
                           "exit_reason": b.exit_reason, "realized_pnl": float(b.realized_pnl()),
                           "r_multiple": b.r_multiple(), "ts": _iso(b.closed_at)})
        elif b.state == "cancelled":
            events.append({"type": "entry_cancelled", "position_id": pid, "signal_id": signal_id, "ticker": ticker,
                           "reason": b.cancel_reason})
        return events

    # --- equity ----------------------------------------------------------------------------------------------
    def cash(self) -> Decimal:
        with connect() as conn:
            rows = conn.execute("SELECT side, price, qty, fee FROM fills WHERE experiment_id = ?",
                                (self.experiment_id,)).fetchall()
        total = self.starting
        for r in rows:
            notional = money(r["price"] * r["qty"])
            total += (notional if r["side"] == "sell" else -notional) - money(r["fee"])
        return total

    def live_brackets(self) -> list[tuple[dict, Bracket]]:
        return [(r, Bracket.from_state(json.loads(r["state_json"])["bracket"])) for r in self.open_rows()]

    def marked(self, marks: dict[str, float]) -> tuple[Decimal, Decimal, Decimal, float]:
        """(cash, signed market value, gross exposure, open risk) at the given marks."""
        cash = self.cash()
        mv, gross, risk = D0, D0, 0.0
        for row, b in self.live_brackets():
            if b.state != "open" or b.qty_open <= 0:
                continue
            m = marks.get(row["ticker"], b.entry_price)
            v = money(b.sign * b.qty_open * m)
            mv += v
            gross += abs(v)
            risk += b.qty_open * max(0.0, b.sign * (m - b.stop))
        return cash, mv, gross, risk

    def peak(self) -> Decimal:
        with connect() as conn:
            prev = conn.execute("SELECT MAX(equity) FROM equity_snapshots WHERE experiment_id = ?",
                                (self.experiment_id,)).fetchone()[0]
        return max(money(prev) if prev is not None else self.starting, self.starting)

    def snapshot(self, ts: datetime, marks: dict[str, float]) -> EquitySnapshot:
        cash, mv, gross, risk = self.marked(marks)
        eq = cash + mv
        peak = max(self.peak(), eq)
        snap = EquitySnapshot(ts=ts, equity=eq, cash=cash, gross_exposure=gross, open_risk=money(risk),
                              drawdown=float(eq / peak - 1))
        with connect() as conn:
            conn.execute("INSERT INTO equity_snapshots (experiment_id, ts, equity, cash, gross_exposure, open_risk,"
                         " drawdown) VALUES (?,?,?,?,?,?,?)",
                         (self.experiment_id, _iso(ts), float(eq), float(cash), float(gross), float(snap.open_risk),
                          snap.drawdown))
        return snap
