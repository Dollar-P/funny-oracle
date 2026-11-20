"""P4: paper broker fills, ledger reconciliation and the trading calendar — synthetic bars, hand-computed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading import calendar as cal
from trading.broker_paper import Bar, Bracket, minute_resolver
from trading.costs import cost_model, money
from trading.ledger import Ledger, journal_entry

UTC = timezone.utc
T0 = datetime(2026, 3, 2, tzinfo=UTC)
STOCK = cost_model("stock")     # fee 0, slippage 2 bps
CRYPTO = cost_model("crypto")   # fee 10 bps, slippage 5 bps
D = Decimal


def bar(i, o, h, l, c):
    ts = T0 + timedelta(days=i)
    return Bar(ts=ts, open=o, high=h, low=l, close=c, open_time=ts, close_time=ts + timedelta(hours=23))


def long_bracket(qty=50.0, stop=90.0, targets=(104.0, 118.0), costs=STOCK, asset_type="stock", time_exit=None, **kw):
    return Bracket(side="long", qty=qty, stop=stop, targets=list(targets), time_exit=time_exit or T0 + timedelta(days=60),
                   submit_ts=bar(0, 1, 1, 1, 1).close_time, costs=costs, asset_type=asset_type, **kw)


def roles(fills):
    return [f.role for f in fills]


# --- entries -------------------------------------------------------------------------------------

def test_market_entry_at_next_open_never_the_signal_bar():
    b = long_bracket()
    assert b.on_bar(bar(0, 100, 101, 99, 100)) == []          # the signal bar: submit time is its close
    (f,) = b.on_bar(bar(1, 101, 103, 100, 102))
    assert f.role == "entry" and f.side == "buy" and f.raw_price == 101
    assert f.price == pytest.approx(101.0202)                 # + 2 bps
    assert f.notional == D("5051.01") and f.fee == D("0.00") and f.slippage == D("1.01")
    assert b.state == "open" and b.opened_at == bar(1, 0, 0, 0, 0).ts


def test_entry_gap_through_stop_or_target_cancels():
    b = long_bracket()
    assert b.on_bar(bar(1, 89, 95, 88, 94)) == [] and b.state == "cancelled" and b.cancel_reason == "gap_invalidated"
    b = long_bracket()
    assert b.on_bar(bar(1, 105, 106, 104, 105)) == [] and b.cancel_reason == "gap_invalidated"


def test_limit_entry_fills_through_the_limit_without_slippage():
    b = long_bracket(entry_type="limit", limit=99.0)
    assert b.on_bar(bar(1, 100, 101, 99.5, 100)) == []        # never traded down to 99
    (f,) = b.on_bar(bar(2, 100, 101, 98.5, 99))
    assert f.price == 99.0 and f.slippage == D("0.00")
    b = long_bracket(entry_type="limit", limit=99.0)
    (f,) = b.on_bar(bar(1, 98, 99, 97, 98))                   # opened below the limit → the better price
    assert f.price == 98.0


def test_entry_expiry():
    b = long_bracket(expiry=T0 + timedelta(days=1, hours=1))
    b.on_bar(bar(2, 100, 101, 99, 100))
    assert b.state == "cancelled" and b.cancel_reason == "expired"


# --- exits ---------------------------------------------------------------------------------------

def test_gap_through_stop_fills_at_the_open():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))                       # entry 100.02
    (f,) = b.on_bar(bar(2, 85, 88, 84, 86))
    assert f.role == "stop" and f.raw_price == 85 and f.price == pytest.approx(84.983)  # worse than the 90 stop
    assert b.realized_pnl() == D("4249.15") - D("5001.00") == D("-751.85")
    assert b.r_multiple() == pytest.approx(-751.85 / (10.02 * 50))
    assert b.exit_reason == "stop" and b.state == "closed"


def test_t1_then_breakeven_and_oco():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))                       # entry 100.02 × 50
    (t1,) = b.on_bar(bar(2, 101, 105, 100.5, 104.5))
    assert t1.role == "t1" and t1.qty == 25 and t1.price == 104.0 and t1.slippage == D("0.00")
    assert b.stop == pytest.approx(100.02) and b.qty_open == 25    # stop moved to breakeven
    (be,) = b.on_bar(bar(3, 102, 103, 99, 100))
    assert be.role == "stop" and be.price == pytest.approx(100.02 * 0.9998) and be.notional == D("2500.00")
    assert b.realized_pnl() == D("2600.00") + D("2500.00") - D("5001.00") == D("99.00")
    assert b.on_bar(bar(4, 80, 81, 70, 75)) == []                  # OCO: nothing left to fill


def test_t2_cancels_the_stop():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))
    fills = b.on_bar(bar(2, 101, 120, 100.5, 119))
    assert roles(fills) == ["t1", "t2"] and [f.qty for f in fills] == [25, 25] and b.exit_reason == "t2"
    assert b.on_bar(bar(3, 50, 50, 40, 45)) == []


def test_gap_through_targets_fills_at_the_better_open():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))
    fills = b.on_bar(bar(2, 120, 121, 119, 120))
    assert roles(fills) == ["t1", "t2"] and all(f.price == 120 for f in fills)


def test_intrabar_ambiguity_assumes_stop():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))
    (f,) = b.on_bar(bar(2, 100, 105, 89, 100))                # both 90 and 104 inside the bar
    assert f.role == "stop" and f.price == pytest.approx(90 * 0.9998) and f.notional == D("4499.10")


def test_intrabar_ambiguity_resolved_by_minute_bars():
    def minutes(start, end):  # the high came first
        return [Bar(start, 100, 104.5, 99.5, 104), Bar(start + timedelta(minutes=1), 104, 104, 88, 89)]

    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))
    fills = b.on_bar(bar(2, 100, 105, 89, 100), resolver=minute_resolver(minutes))
    # T1 first, then the breakeven stop is live for the rest of the bar and the low reaches it
    assert roles(fills) == ["t1", "stop"] and fills[1].raw_price == pytest.approx(100.02)
    b2 = long_bracket()
    b2.on_bar(bar(1, 100, 101, 99, 100))
    failing = minute_resolver(lambda s, e: (_ for _ in ()).throw(RuntimeError("no 1m data")))
    assert roles(b2.on_bar(bar(2, 100, 105, 89, 100), resolver=failing)) == ["stop"]   # unknown → stop


def test_time_exit_at_open_with_slippage():
    b = long_bracket(time_exit=T0 + timedelta(days=3))
    b.on_bar(bar(1, 100, 101, 99, 100))
    assert b.on_bar(bar(2, 101, 102, 100, 101)) == []
    (f,) = b.on_bar(bar(3, 103, 110, 102, 109))
    assert f.role == "time" and f.price == pytest.approx(103 * 0.9998) and b.state == "closed"


def test_fees_and_slippage_crypto_to_the_cent():
    b = long_bracket(qty=0.1, stop=57_000.0, targets=(62_000.0, 66_000.0), costs=CRYPTO, asset_type="crypto")
    (e,) = b.on_bar(bar(1, 60_000, 60_500, 59_800, 60_200))
    assert e.price == pytest.approx(60_030.0) and e.notional == D("6003.00") and e.fee == D("6.00")
    assert e.slippage == D("3.00")
    t1, t2 = b.on_bar(bar(2, 61_000, 66_500, 60_500, 66_200))
    assert (t1.qty, t1.notional, t1.fee) == (0.05, D("3100.00"), D("3.10"))
    assert (t2.qty, t2.notional, t2.fee) == (0.05, D("3300.00"), D("3.30"))
    assert b.realized_pnl() == D("384.60")                     # 3100 + 3300 − 6003 − 6.00 − 3.10 − 3.30
    assert b.costs_paid() == D("15.40")                        # fees 12.40 + slippage 3.00
    assert b.r_multiple() == pytest.approx(384.60 / (3_030 * 0.1))


def test_short_mirror_with_breakeven():
    b = Bracket(side="short", qty=10.0, stop=110.0, targets=[96.0, 82.0], time_exit=T0 + timedelta(days=60),
                submit_ts=T0 + timedelta(hours=23), costs=STOCK)
    (e,) = b.on_bar(bar(1, 100, 101, 99, 100))
    assert e.side == "sell" and e.price == pytest.approx(99.98)
    (t1,) = b.on_bar(bar(2, 99, 99.5, 95, 96))
    assert t1.side == "buy" and t1.qty == 5 and t1.price == 96 and b.stop == pytest.approx(99.98)
    (st,) = b.on_bar(bar(3, 98, 100.5, 97, 100))
    assert st.side == "buy" and st.price == pytest.approx(99.98 * 1.0002)
    assert b.realized_pnl() == D("999.80") - D("480.00") - D("500.00") == D("19.80")


def test_short_gap_through_stop():
    b = Bracket(side="short", qty=10.0, stop=110.0, targets=[96.0, 82.0], time_exit=T0 + timedelta(days=60),
                submit_ts=T0, costs=STOCK)
    b.on_bar(bar(1, 100, 101, 99, 100))
    (f,) = b.on_bar(bar(2, 115, 116, 112, 114))
    assert f.role == "stop" and f.price == pytest.approx(115 * 1.0002)


def test_mae_mfe():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))                        # entry bar range: mae 1.02, mfe 0.98
    b.on_bar(bar(2, 100, 103, 97, 100))
    assert b.mae == pytest.approx(3.02) and b.mfe == pytest.approx(2.98)
    b.on_bar(bar(3, 95, 96, 85, 86))                           # stop at 90 (not a gap): mae capped at the stop
    assert b.mae == pytest.approx(10.02)
    j = journal_entry("k", "NVDA", b)
    assert j.mae_r == pytest.approx(1.0) and j.mfe_r == pytest.approx(2.98 / 10.02)
    assert j.holding_hours == pytest.approx(2 * 24 + 23)       # entry at bar 1 open → stop at bar 3 close


def test_single_share_t1_closes_everything():
    b = long_bracket(qty=1.0)
    b.on_bar(bar(1, 100, 101, 99, 100))
    (f,) = b.on_bar(bar(2, 101, 105, 100.5, 104))
    assert f.role == "t1" and f.qty == 1.0 and b.state == "closed"


def test_state_round_trip():
    b = long_bracket()
    b.on_bar(bar(1, 100, 101, 99, 100))
    b.on_bar(bar(2, 101, 105, 100.5, 104.5))
    c = Bracket.from_state(b.to_state())
    assert (c.state, c.qty_open, c.stop, c.t1_done, len(c.fills)) == (b.state, b.qty_open, b.stop, True, 2)
    assert c.realized_pnl() == b.realized_pnl()
    assert roles(c.on_bar(bar(3, 102, 103, 99, 100))) == ["stop"]


# --- ledger ----------------------------------------------------------------------------------------

def test_ledger_reconciles_to_the_cent():
    led = Ledger(D("100000"))
    path_a = [bar(1, 100, 101, 99, 100.37), bar(2, 101, 105, 100.5, 104.11), bar(3, 102, 103, 99, 99.73),
              bar(4, 99, 100, 98, 99.5)]
    path_b = [bar(1, 60_000, 60_500, 59_800, 60_211.13), bar(2, 61_000, 62_100, 60_500, 61_999.99),
              bar(3, 61_500, 61_700, 56_000, 56_500.5), bar(4, 56_000, 57_000, 55_000, 56_100)]
    a = long_bracket()
    b = long_bracket(qty=0.1234, stop=57_000.0, targets=(62_000.0, 66_000.0), costs=CRYPTO, asset_type="crypto")
    brs = {"A": (a, path_a), "B": (b, path_b)}
    for i in range(4):
        marks = {}
        for k, (br, path) in brs.items():
            led.apply_all(k, br.on_bar(path[i]))
            marks[k] = path[i].close
        snap = led.snapshot(path_a[i].close_time, marks)
        # equity = cash + Σ qty × mark (each rounded to the cent), recomputed independently
        expect = led.cash + sum((money(q * marks[k]) for k, q in led.qty.items()), D("0"))
        assert snap.equity == expect and led.reconcile() == D("0.00")
    assert a.state == "closed" and b.state == "closed"
    assert led.qty == {}
    assert led.cash - led.starting_cash == a.realized_pnl() + b.realized_pnl()   # exact, no rounding drift
    # cash rebuilt from the raw fills, in cents
    rebuilt = D("100000.00")
    for _k, f in led.fills:
        rebuilt += (f.notional if f.side == "sell" else -f.notional) - f.fee
    assert rebuilt == led.cash
    assert led.snapshots[-1].drawdown <= 0


# --- calendar ---------------------------------------------------------------------------------------

def test_calendar_nyse_holidays_weekends_and_dst():
    assert not cal.is_trading_day("stock", "2026-11-26")                     # Thanksgiving
    assert cal.is_trading_day("stock", "2026-11-27")                         # early close
    assert cal.nyse_session("2026-11-27")[1] == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert not cal.is_trading_day("stock", "2026-11-28")                     # Saturday
    assert cal.is_trading_day("crypto", "2026-11-28")
    assert cal.nyse_session("2026-07-01")[0] == datetime(2026, 7, 1, 13, 30, tzinfo=UTC)   # EDT
    assert cal.nyse_session("2026-12-01")[0] == datetime(2026, 12, 1, 14, 30, tzinfo=UTC)  # EST
    fri_after_close = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)
    assert not cal.is_open("stock", fri_after_close)
    assert cal.next_open("stock", fri_after_close) == datetime(2026, 11, 23, 14, 30, tzinfo=UTC)
    assert cal.next_open("stock", datetime(2026, 11, 25, 22, 0, tzinfo=UTC)) == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert cal.is_open("crypto", datetime(2026, 11, 28, 3, 0, tzinfo=UTC))


def test_calendar_gold_and_bar_open_times():
    assert not cal.is_open("gold", datetime(2026, 11, 21, 12, 0, tzinfo=UTC))          # Saturday
    assert cal.is_open("gold", datetime(2026, 11, 23, 0, 0, tzinfo=UTC))               # Sunday 19:00 NY
    assert cal.next_open("gold", datetime(2026, 11, 21, 12, 0, tzinfo=UTC)) == datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    stock_bar = datetime(2026, 11, 18, 5, 0, tzinfo=UTC)                               # yfinance daily stamp
    assert cal.bar_open_time("stock", "1d", stock_bar) == datetime(2026, 11, 18, 14, 30, tzinfo=UTC)
    assert cal.bar_close_time("stock", "1d", stock_bar) == datetime(2026, 11, 18, 21, 0, tzinfo=UTC)
    assert cal.bar_open_time("gold", "1d", stock_bar) == datetime(2026, 11, 17, 23, 0, tzinfo=UTC)
    assert cal.bar_open_time("crypto", "1d", datetime(2026, 11, 18, tzinfo=UTC)) == datetime(2026, 11, 18, tzinfo=UTC)
    assert len(cal.sessions("stock", "2026-11-23", "2026-11-29")) == 4               # Thanksgiving week


def test_decision_after_close_fills_next_session_open():
    """Stock daily bars: a signal decided after Monday's close fills at Tuesday's open, not Monday's."""
    mon = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)
    bars = [Bar(ts=mon + timedelta(days=i), open=100 + i, high=101 + i, low=99 + i, close=100 + i,
                open_time=cal.bar_open_time("stock", "1d", mon + timedelta(days=i)),
                close_time=cal.bar_close_time("stock", "1d", mon + timedelta(days=i))) for i in range(3)]
    b = Bracket(side="long", qty=1, stop=90, targets=[130, 140], time_exit=mon + timedelta(days=30),
                submit_ts=datetime(2026, 11, 16, 21, 5, tzinfo=UTC), costs=STOCK)
    assert b.on_bar(bars[0]) == []
    (f,) = b.on_bar(bars[1])
    assert f.raw_price == 101 and f.ts == datetime(2026, 11, 17, 14, 30, tzinfo=UTC)


# --- persistent paper account ------------------------------------------------------------------------

def test_paper_account_persists_restarts_and_reconciles(fresh_db):
    from core.db import connect
    from trading.paper_account import PaperAccount

    acct = PaperAccount(experiment_id=1, starting_equity=100_000)
    path = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100.5), bar(2, 101, 105, 100.5, 104.2),
            bar(3, 102, 103, 99, 99.9), bar(4, 80, 81, 70, 75)]
    pid = acct.open_bracket(signal_id=11, ticker="NVDA", asset_type="stock", bracket=long_bracket(),
                            now=path[0].close_time)
    ev = acct.process("NVDA", path[:3])
    assert [e["role"] for e in ev if e["type"] == "order_filled"] == ["entry", "t1"]
    acct.snapshot(path[2].close_time, {"NVDA": path[2].close})
    # restart: a new object, the same bars again plus one more → only the new bar is processed
    acct2 = PaperAccount(experiment_id=1, starting_equity=100_000)
    ev2 = acct2.process("NVDA", path[:4])
    assert [e["type"] for e in ev2] == ["order_filled", "position_closed"] and ev2[0]["role"] == "stop"
    assert acct2.process("NVDA", path) == []                      # closed: nothing more, ever
    snap = acct2.snapshot(path[3].close_time, {"NVDA": path[3].close})
    with connect() as conn:
        fills = conn.execute("SELECT reason, side, price, qty, fee FROM fills ORDER BY id").fetchall()
        orders = {r["role"]: r["status"] for r in conn.execute("SELECT role, status FROM orders WHERE oco_group = ?",
                                                                (f"pos-{pid}",))}
        pos = dict(conn.execute("SELECT * FROM positions WHERE id = ?", (pid,)).fetchone())
        eq = [r[0] for r in conn.execute("SELECT equity FROM equity_snapshots ORDER BY id")]
    assert [f["reason"] for f in fills] == ["entry", "t1", "stop"]
    assert orders == {"entry": "filled", "t1": "filled", "stop": "filled", "t2": "cancelled", "time": "cancelled"}
    assert pos["exit_reason"] == "stop" and pos["realized_pnl"] == pytest.approx(99.00)
    # equity snapshot (open position, marked at 104.2) reconciles with the fills to the cent:
    # cash = 100000 − 5001.00 + 2600.00 = 97599.00; 25 × 104.2 = 2605.00 → 100204.00
    assert eq[0] == pytest.approx(100_204.00, abs=0) and money(eq[0]) == D("100204.00")
    assert snap.equity == D("100099.00") == snap.cash and snap.gross_exposure == D("0.00")


def test_paper_account_cancelled_entry_and_force_close(fresh_db):
    from trading.paper_account import PaperAccount

    acct = PaperAccount(experiment_id=2, starting_equity=50_000)
    p1 = acct.open_bracket(signal_id=1, ticker="AAA", asset_type="stock", bracket=long_bracket(), now=T0)
    ev = acct.process("AAA", [bar(1, 89, 90, 88, 89)])              # gaps below the stop before entry
    assert ev == [{"type": "entry_cancelled", "position_id": p1, "signal_id": 1, "ticker": "AAA",
                   "reason": "gap_invalidated"}]
    p2 = acct.open_bracket(signal_id=2, ticker="BBB", asset_type="stock", bracket=long_bracket(), now=T0)
    acct.process("BBB", [bar(1, 100, 101, 99, 100)])
    ev = acct.force_close(p2, bar(2, 0, 0, 0, 0).ts, 101.0, "manual")
    assert ev[-1]["type"] == "position_closed" and ev[-1]["exit_reason"] == "manual"
    assert acct.open_rows() == [] and acct.cash() == D("50000") - D("5001.00") + money(101 * 0.9998 * 50)
