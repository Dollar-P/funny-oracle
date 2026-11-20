"""GET /api/pro/positions · /orders · /fills · /journal · /equity · /overview"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body
from fastapi.responses import PlainTextResponse

from api import pro_state
from api.routes.pro.common import bad, experiment, now, one, rows
from core import pro_settings
from evaluation import metrics as M
from evaluation import stats as S
from trading.broker_paper import Bracket
from trading.paper_account import PaperAccount

router = APIRouter()
UTC = timezone.utc


def _mark(ticker: str, asset_type: str, interval: str = "1d") -> float | None:
    d = pro_state.desk()
    if ticker not in d.last_close:
        try:
            d._df(ticker, asset_type, interval, now())
        except Exception:
            return None
    return d.last_close.get(ticker)


def open_positions(exp: int | None = None) -> list[dict]:
    exp = exp or experiment()
    out = []
    t_now = now()
    for r in rows("SELECT * FROM positions WHERE experiment_id = ? AND closed_at IS NULL AND exit_reason IS NULL"
                  " ORDER BY id", (exp,)):
        st = json.loads(r["state_json"])
        b = Bracket.from_state(st["bracket"])
        meta = st.get("meta", {})
        mark = _mark(r["ticker"], r["asset_type"], meta.get("interval", "1d")) or b.entry_price or meta.get("entry_ref")
        d = {k: r[k] for k in ("id", "signal_id", "ticker", "asset_type", "side", "qty", "opened_at")}
        d.update(state=b.state, qty_open=b.qty_open if b.state == "open" else 0.0, entry=b.entry_price,
                 entry_ref=meta.get("entry_ref"), stop=b.stop, stop_initial=b.stop_initial, targets=b.targets,
                 t1_done=b.t1_done, mark=mark, time_exit=b.time_exit.isoformat(), horizon=meta.get("horizon"),
                 time_left_h=round((b.time_exit - t_now).total_seconds() / 3600, 1))
        if b.state == "open" and mark:
            pnl_open = b.sign * b.qty_open * (mark - b.entry_price)   # before exit costs
            d.update(unrealized_pnl=round(pnl_open, 2),
                     r_now=round(pnl_open / (b.risk_per_unit * b.qty) if b.risk_per_unit else 0.0, 2),
                     to_stop_pct=round((b.stop / mark - 1) * 100, 2),
                     to_target_pct=round(((b.targets[1] if b.t1_done else b.targets[0]) / mark - 1) * 100, 2),
                     notional=round(b.qty_open * mark, 2))
        out.append(d)
    return out


@router.get("/positions")
def positions():
    return {"positions": open_positions()}


@router.get("/orders")
def orders(status: str = "working,pending"):
    vals = status.split(",")
    return {"orders": rows(f"SELECT * FROM orders WHERE experiment_id = ? AND status IN ({','.join('?' * len(vals))})"
                           " ORDER BY id DESC LIMIT 500", (experiment(), *vals))}


@router.get("/fills")
def fills(limit: int = 500):
    return {"fills": rows("SELECT * FROM fills WHERE experiment_id = ? ORDER BY id DESC LIMIT ?", (experiment(), limit))}


JOURNAL_COLS = ("id", "signal_id", "ticker", "side", "qty", "opened_at", "closed_at", "avg_entry", "avg_exit",
                "realized_pnl", "r_multiple", "mae_r", "mfe_r", "costs", "exit_reason", "holding_h", "tags",
                "post_mortem", "thesis_held", "lesson", "thesis")


def journal_rows(exp: int | None = None) -> list[dict]:
    out = []
    for r in rows("SELECT p.*, s.thesis FROM positions p LEFT JOIN signals s ON s.id = p.signal_id WHERE"
                  " p.experiment_id = ? AND p.closed_at IS NOT NULL AND p.realized_pnl IS NOT NULL ORDER BY p.closed_at DESC",
                  (exp or experiment(),)):
        b = Bracket.from_state(json.loads(r["state_json"])["bracket"])
        rev = json.loads(r["review"]) if r.get("review") else {}
        out.append({
            **{k: r[k] for k in ("id", "signal_id", "ticker", "side", "qty", "opened_at", "closed_at", "avg_entry",
                                 "avg_exit", "realized_pnl", "r_multiple", "costs", "exit_reason", "thesis")},
            "mae_r": round(b.mae / b.risk_per_unit, 3) if b.risk_per_unit else None,
            "mfe_r": round(b.mfe / b.risk_per_unit, 3) if b.risk_per_unit else None,
            "holding_h": round((datetime.fromisoformat(r["closed_at"]) - datetime.fromisoformat(r["opened_at"])).total_seconds() / 3600, 1),
            "tags": [t for t in (r.get("tags") or "").split(",") if t],
            "post_mortem": rev.get("post_mortem"), "thesis_held": rev.get("thesis_held"), "lesson": rev.get("lesson"),
            "review_model": rev.get("model"),
        })
    return out


@router.get("/journal")
def journal(format: str = "json"):
    j = journal_rows()
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=JOURNAL_COLS, extrasaction="ignore")
        w.writeheader()
        for r in j:
            w.writerow({**r, "tags": ";".join(r["tags"])})
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=journal.csv"})
    return {"journal": j}


@router.post("/journal/{pid}/tags")
def set_tags(pid: int, body: dict = Body(...)):
    tags = [str(t).strip()[:24] for t in body.get("tags", []) if str(t).strip()][:10]
    if not one("SELECT id FROM positions WHERE id = ?", (pid,)):
        bad(404, "unknown position")
    from core.db import connect

    with connect() as conn:
        conn.execute("UPDATE positions SET tags = ? WHERE id = ?", (",".join(tags), pid))
    return {"id": pid, "tags": tags}


def equity_series(exp: int, since: str | None = None, until: str | None = None) -> pd.Series:
    q, a = "SELECT ts, equity FROM equity_snapshots WHERE experiment_id = ?", [exp]
    if since:
        q += " AND ts >= ?"
        a.append(since)
    if until:
        q += " AND ts <= ?"
        a.append(until)
    r = rows(q + " ORDER BY ts", a)
    if not r:
        return pd.Series(dtype=float)
    s = pd.Series([x["equity"] for x in r], index=pd.to_datetime([x["ts"] for x in r], utc=True))
    return s.groupby(s.index.floor("h")).last()


def benchmark(s: dict, start: pd.Timestamp, end: pd.Timestamp, equity0: float) -> list[list]:
    """Equal-weight buy & hold of the watchlist over the same period (daily closes, no costs)."""
    d = pro_state.desk()
    curves = []
    for w in s["watchlist"]:
        try:
            df = d.bars(w["ticker"], w["asset_type"], "1d", now())
        except Exception:
            continue
        if df is None or not len(df):
            continue
        c = df["Close"]
        c.index = pd.DatetimeIndex(c.index).tz_convert(UTC).normalize()
        c = c[~c.index.duplicated(keep="last")]
        base = c[c.index <= start.normalize()]
        if base.empty:
            continue
        c = c[(c.index >= base.index[-1]) & (c.index <= end)]
        curves.append(c / base.iloc[-1])
    if not curves:
        return []
    m = pd.concat(curves, axis=1).sort_index().ffill().dropna(how="all").mean(axis=1) * equity0
    return [[ts.isoformat(), round(float(v), 2)] for ts, v in m.items()]


@router.get("/equity")
def equity(since: str | None = None, until: str | None = None):
    exp = experiment()
    s = pro_settings.load()
    e = equity_series(exp, since, until)
    pts = [[ts.isoformat(), round(float(v), 2)] for ts, v in e.items()]
    bh = benchmark(s, e.index[0], e.index[-1], float(s["starting_equity"])) if len(e) else []
    cash = [[pts[0][0], float(s["starting_equity"])], [pts[-1][0], float(s["starting_equity"])]] if pts else []
    return {"equity": pts, "benchmarks": {"buy_and_hold": bh, "cash": cash}, "starting_equity": s["starting_equity"]}


@router.get("/overview")
def overview():
    from trading import scheduler as SCH

    s = pro_settings.load()
    exp = pro_settings.current_experiment(s)
    acct = PaperAccount(exp, s["starting_equity"])
    pos = open_positions(exp)
    marks = {p["ticker"]: p["mark"] for p in pos if p.get("mark")}
    cash, mv, gross, risk = acct.marked(marks)
    eq = float(cash + mv)
    peak = float(acct.peak())
    t = now()
    day0 = datetime.combine(t.astimezone(UTC).date(), datetime.min.time(), tzinfo=UTC).isoformat()
    prev = one("SELECT equity FROM equity_snapshots WHERE experiment_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
               (exp, day0))
    day_start = prev["equity"] if prev else float(acct.starting)
    daily = equity_series(exp)
    daily = daily.groupby(daily.index.normalize()).last() if len(daily) else daily
    r = M.returns_from_equity(daily.values) if len(daily) > 1 else np.array([])
    ppy = 365 if any(w["asset_type"] == "crypto" for w in s["watchlist"]) else 252
    if len(r) >= 3:
        est, lo, hi = S.block_bootstrap(r, lambda a: M.sharpe(a, ppy), n_boot=500, seed=1)
    else:
        est, lo, hi = None, None, None
    sharpe = S.metric(est, len(r), lo, hi).to_dict()
    tripped = rows("SELECT rule, ts, detail FROM risk_events WHERE experiment_id = ? AND signal_id IS NULL AND"
                   " rule IN ('daily_loss', 'max_drawdown') ORDER BY id DESC LIMIT 5", (exp,))
    counts = {x["status"]: x["n"] for x in rows("SELECT status, COUNT(*) n FROM signals WHERE experiment_id = ?"
                                                " GROUP BY status", (exp,))}
    sch = pro_state._STATE.get("scheduler")
    return {
        "experiment_id": exp, "equity": round(eq, 2), "cash": float(cash), "starting_equity": s["starting_equity"],
        "day_pnl": round(eq - day_start, 2), "day_pnl_pct": (eq / day_start - 1) if day_start else 0.0,
        "total_return": eq / float(acct.starting) - 1, "drawdown": eq / max(peak, eq) - 1 if peak else 0.0,
        "gross_exposure_pct": float(gross) / eq if eq else 0.0, "open_risk_pct": risk / eq if eq else 0.0,
        "open_positions": sum(1 for p in pos if p["state"] == "open"),
        "pending_entries": sum(1 for p in pos if p["state"] == "pending"),
        "sharpe": sharpe, "halted": bool(s.get("halted")),
        "daily_loss_tripped_today": any(x["rule"] == "daily_loss" and x["ts"] >= day0 for x in tripped),
        "breaker_events": tripped, "spent_today_thb": round(SCH.spent_today_thb(t), 2),
        "daily_budget_thb": s["budgets"]["daily_thb"], "approval": s["approval"], "signal_counts": counts,
        "scheduler": {"running": bool(sch and sch.running), "last_tick": sch.last_tick.isoformat() if sch and sch.last_tick else None},
        "risk_limits": {"max_dd": s["risk"]["max_dd"], "daily_loss": s["risk"]["daily_loss"]},
        "as_of": t.isoformat(timespec="seconds"),
    }


@router.get("/bars/{ticker}")
def bars(ticker: str, asset_type: str | None = None, interval: str = "1d", limit: int = 260):
    """Completed bars from the Desk's bar source (synthetic in demo mode) for the trade chart."""
    s = pro_settings.load()
    a = asset_type or next((w["asset_type"] for w in s["watchlist"] if w["ticker"] == ticker), None)
    if a is None:
        from core.asset import classify

        a = classify(ticker)
    df = pro_state.desk().bars(ticker, a, interval, now())
    if df is None or not len(df):
        return {"ticker": ticker, "candles": []}
    df = df.tail(limit)
    return {"ticker": ticker, "asset_type": a, "interval": interval,
            "candles": [{"t": int(ts.timestamp()), "o": float(r.Open), "h": float(r.High), "l": float(r.Low),
                         "c": float(r.Close)} for ts, r in zip(df.index, df.itertuples())]}
