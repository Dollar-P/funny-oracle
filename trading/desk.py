"""The Desk's forward paper loop (PRO.md §3, §6.1): prediction → plan → risk → approval → paper broker → ledger.

Everything external is injected so the loop runs identically live and in a simulated 3-day test:
    bars(ticker, asset_type, interval, now) → DataFrame of *completed* bars (close time ≤ now)
    reviewer(trade dict, now) → (review dict | None, error | None)
    emit(event dict)                                 (SSE bus, P8)

Signal statuses: flat · rejected (risk or manual) · new (awaiting approval) · approved (order working) ·
filled · closed · cancelled (entry gap-invalidated) · expired (not approved before its entry bar opened).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import numpy as np
import pandas as pd

from core import pro_settings
from core.db import connect
from core.schemas import Prediction
from evaluation.backtest import to_bars
from trading import calendar as cal
from trading.broker_paper import Bracket
from trading.costs import cost_model
from trading.paper_account import PaperAccount
from trading.risk import OpenPos, Portfolio, RiskParams, record_event, record_rejections, size_plan
from trading.signals import SignalParams, TradePlan, atr_at, build_plan

log = logging.getLogger("desk")
UTC = timezone.utc

BarsFn = Callable[[str, str, str, datetime], pd.DataFrame]
ReviewerFn = Callable[[dict, datetime], Awaitable[tuple[dict | None, str | None]]]


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat(timespec="seconds") if ts else None


def live_bars(ticker: str, asset_type: str, interval: str, now: datetime) -> pd.DataFrame:
    """yfinance bars that are complete at `now` (a partial bar would later be skipped by the broker)."""
    from mcp_server import data_sources as ds

    df = ds.fetch_ohlcv(ticker, interval, "1y" if interval == "1d" else None)
    if df is None or df.empty:
        return df
    idx = list(df.index)
    keep = [cal.bar_close_time(asset_type, interval, ts.to_pydatetime(),
                               idx[i + 1].to_pydatetime() if i + 1 < len(idx) else None) <= now
            for i, ts in enumerate(idx)]
    # the last bar has no successor: complete only if its nominal close has passed
    return df[np.array(keep, dtype=bool)]


def entry_deadline(asset_type: str, interval: str, decided_at: datetime) -> datetime:
    """Open time of the bar the plan was computed for (the next bar after the decision)."""
    if interval == "1d":
        if asset_type == "crypto":
            return datetime.combine(decided_at.astimezone(UTC).date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        return cal.next_open(asset_type, decided_at + timedelta(minutes=1))
    step = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}.get(interval, 60)
    nxt = decided_at.replace(second=0, microsecond=0) + timedelta(minutes=step - decided_at.minute % step)
    return cal.next_open(asset_type, nxt) if asset_type != "crypto" else nxt


@dataclass
class Desk:
    bars: BarsFn = live_bars
    reviewer: ReviewerFn | None = None
    emit: Callable[[dict], None] = lambda ev: None
    calibrator_for: Callable[[str, str], tuple[Callable[[float], float] | None, int]] | None = None
    last_close: dict[str, float] = field(default_factory=dict)

    # --- helpers --------------------------------------------------------------------------------------
    def settings(self) -> dict:
        return pro_settings.load()

    def account(self, s: dict | None = None) -> tuple[PaperAccount, int, dict]:
        s = s or self.settings()
        exp = pro_settings.current_experiment(s)
        return PaperAccount(exp, s["starting_equity"]), exp, s

    def _event(self, type_: str, **payload) -> dict:
        ev = {"type": type_, **payload}
        try:
            self.emit(ev)
        except Exception:  # a broken subscriber must not stop the desk
            log.exception("emit failed")
        return ev

    def _set_status(self, signal_id: int, status: str, reason: str | None = None, now: datetime | None = None,
                    **cols) -> None:
        sets = ["status = ?", "status_reason = COALESCE(?, status_reason)", "updated_at = ?"]
        vals: list = [status, reason, _iso(now or datetime.now(UTC))]
        for k, v in cols.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        with connect() as conn:
            conn.execute(f"UPDATE signals SET {', '.join(sets)} WHERE id = ?", (*vals, signal_id))

    def signal(self, signal_id: int) -> dict | None:
        with connect() as conn:
            r = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        return dict(r) if r else None

    def _df(self, ticker: str, asset_type: str, interval: str, now: datetime) -> pd.DataFrame:
        df = self.bars(ticker, asset_type, interval, now)
        if df is not None and len(df):
            self.last_close[ticker] = float(df["Close"].iloc[-1])
        return df

    def portfolio(self, acct: PaperAccount, s: dict, now: datetime, for_ticker: str | None = None,
                  side: str | None = None) -> Portfolio:
        positions, same = [], []
        for row, b in acct.live_brackets():
            mark = self.last_close.get(row["ticker"]) or b.entry_price or json.loads(row["state_json"]).get("meta", {}).get("entry_ref", 0.0)
            q = b.qty_open if b.state == "open" else b.qty
            positions.append(OpenPos(row["ticker"], row["asset_type"], b.side, q, b.entry_price or mark, b.stop, mark))
            if side and b.side == side:
                same.append((row["ticker"], row["asset_type"]))
        cash, mv, _g, _r = acct.marked({p.ticker: p.mark for p in positions})
        equity = float(cash + mv)
        with connect() as conn:
            peak = conn.execute("SELECT MAX(equity) FROM equity_snapshots WHERE experiment_id = ?",
                                (acct.experiment_id,)).fetchone()[0]
            day0 = datetime.combine(now.astimezone(UTC).date(), datetime.min.time(), tzinfo=UTC)
            prev = conn.execute("SELECT equity FROM equity_snapshots WHERE experiment_id = ? AND ts < ? ORDER BY ts DESC"
                                " LIMIT 1", (acct.experiment_id, _iso(day0))).fetchone()
        returns = {}
        if for_ticker and same:
            for t, a in {(for_ticker, None), *same}:
                at = a or next((w["asset_type"] for w in s["watchlist"] if w["ticker"] == t), "stock")
                d = self._df(t, at, "1d", now)
                if d is not None and len(d):
                    r = d["Close"].pct_change()
                    r.index = pd.DatetimeIndex(r.index).normalize()
                    returns[t] = r
        return Portfolio(equity=equity, peak_equity=max(float(peak or 0), float(acct.starting), equity),
                         day_start_equity=float(prev[0]) if prev else float(acct.starting), positions=positions,
                         halted=bool(s.get("halted")), returns=returns)

    # --- predictions → signals -----------------------------------------------------------------------------
    def process_prediction(self, pred: Prediction, now: datetime, job_key: str | None = None) -> dict:
        acct, exp, s = self.account()
        interval = "1d" if pred.mode == "swing" else {"15m": "1m", "1h": "5m", "4h": "15m"}[pred.horizon]
        df = self._df(pred.ticker, pred.asset_type, "1d", now)
        atr = atr_at(df, df.index[-1], s["signal"].get("atr_period", 14)) if df is not None and len(df) > 15 else float("nan")
        lr = np.log(df["Close"]).diff().dropna().tail(252) if df is not None else pd.Series(dtype=float)
        sigma_a = float(lr.std(ddof=1) * math.sqrt(365 if pred.asset_type == "crypto" else 252)) if len(lr) > 20 else float("nan")
        cal_fn, cal_n = (self.calibrator_for(pred.mode, pred.asset_type) if self.calibrator_for else (None, 0))
        params = SignalParams.from_config(s["signal"])
        costs = cost_model(pred.asset_type, s["costs"])
        if not math.isfinite(atr):
            pred.degraded = [*pred.degraded, "no_atr"]
            atr = 0.0
        plan = build_plan(pred, atr=atr, params=params, costs=costs, calibrator=cal_fn)
        deadline = entry_deadline(pred.asset_type, interval, now)
        plan.inputs.update({"sigma_annual": sigma_a, "calibration_n": cal_n, "entry_deadline": _iso(deadline),
                            "interval": interval, "job_key": job_key, "model": pred.models.get("analyst"),
                            "think_preset": pred.think_preset, "prompt_version": pred.prompt_version})
        status, reason, size_json = "flat", plan.flat_reason, None
        decision = None
        if plan.direction != "flat":
            pf = self.portfolio(acct, s, now, for_ticker=pred.ticker, side=plan.side)
            decision = size_plan(plan, pf, RiskParams.from_config(s["risk"]), sigma_annual=sigma_a, calibration_n=cal_n)
            if decision.approved:
                plan.size = decision.size
                status, reason, size_json = "new", None, decision.size.model_dump_json()
            else:
                status, reason = "rejected", f"risk: {decision.rejections[0].rule}"
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO signals (prediction_id, experiment_id, created_at, ticker, asset_type, mode, horizon, direction,"
                " conviction, entry_json, stop, targets_json, time_exit, reward_risk, ev_r, size_json, thesis, invalidation,"
                " flat_reason, status, status_reason, plan_json, updated_at, decision_bar) VALUES"
                " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pred.id, exp, _iso(now), pred.ticker, pred.asset_type, pred.mode, pred.horizon, plan.direction,
                 plan.conviction, plan.entry.model_dump_json(), plan.stop, json.dumps(plan.targets),
                 _iso(plan.time_exit), plan.reward_risk, plan.expected_value_r, size_json, plan.thesis,
                 plan.invalidation, plan.flat_reason, status, reason, plan.model_dump_json(), _iso(now),
                 _iso(df.index[-1].to_pydatetime()) if df is not None and len(df) else None))
            sid = int(cur.lastrowid)
        base = {"signal_id": sid, "ticker": pred.ticker, "mode": pred.mode, "horizon": pred.horizon,
                "prediction_id": pred.id}
        if status == "flat":
            self._event("signal_flat", **base, reason=plan.flat_reason)
        elif status == "rejected":
            record_rejections(decision, sid, exp, now)
            if decision.halt:
                self._trip_halt(s, exp, now, decision.rejections[0].detail, sid)
            self._event("risk_rejected", **base, rule=decision.rejections[0].rule, detail=decision.rejections[0].detail)
        else:
            self._event("signal_created", **base, direction=plan.direction, conviction=plan.conviction,
                        rr=plan.reward_risk, ev_r=plan.expected_value_r, qty=plan.size.qty)
            if s.get("approval") == "auto":
                self.approve(sid, now)
        return self.signal(sid)

    # --- approval ------------------------------------------------------------------------------------------
    def approve(self, signal_id: int, now: datetime) -> dict:
        sig = self.signal(signal_id)
        if not sig:
            raise KeyError(signal_id)
        if sig["status"] != "new":
            raise ValueError(f"signal {signal_id} is {sig['status']}, not new")
        plan = TradePlan.model_validate_json(sig["plan_json"])
        deadline = datetime.fromisoformat(plan.inputs["entry_deadline"])
        if now > deadline:
            self._set_status(signal_id, "expired", "not approved before the entry bar opened", now)
            raise ValueError("signal expired: its entry bar has already opened")
        acct, exp, s = self.account()
        if sig["experiment_id"] != exp:
            self._set_status(signal_id, "expired", "experiment changed (settings or account reset)", now)
            raise ValueError("signal belongs to an older experiment")
        pf = self.portfolio(acct, s, now, for_ticker=plan.ticker, side=plan.side)
        d = size_plan(plan, pf, RiskParams.from_config(s["risk"]), sigma_annual=plan.inputs["sigma_annual"],
                      calibration_n=plan.inputs.get("calibration_n", 0))
        if not d.approved:   # the portfolio changed since the signal was created
            record_rejections(d, signal_id, exp, now)
            self._set_status(signal_id, "rejected", f"risk: {d.rejections[0].rule}", now)
            self._event("risk_rejected", signal_id=signal_id, ticker=plan.ticker, rule=d.rejections[0].rule,
                        detail=d.rejections[0].detail)
            return self.signal(signal_id)
        br = Bracket(side=plan.side, qty=d.size.qty, stop=plan.stop, targets=list(plan.targets),
                     time_exit=plan.time_exit, submit_ts=now, costs=cost_model(plan.asset_type, s["costs"]),
                     asset_type=plan.asset_type)
        pid = acct.open_bracket(signal_id=signal_id, ticker=plan.ticker, asset_type=plan.asset_type, bracket=br, now=now,
                                meta={"interval": plan.inputs.get("interval", "1d"), "mode": plan.mode,
                                      "entry_ref": plan.entry.ref_price, "horizon": plan.horizon})
        self._set_status(signal_id, "approved", None, now, size_json=d.size.model_dump_json())
        self._event("order_placed", signal_id=signal_id, position_id=pid, ticker=plan.ticker, side=plan.side,
                    qty=d.size.qty, stop=plan.stop, targets=plan.targets)
        return self.signal(signal_id)

    def reject(self, signal_id: int, now: datetime, reason: str = "manual") -> dict:
        sig = self.signal(signal_id)
        if not sig:
            raise KeyError(signal_id)
        if sig["status"] != "new":
            raise ValueError(f"signal {signal_id} is {sig['status']}, not new")
        self._set_status(signal_id, "rejected", reason, now)
        record_event("manual_reject", reason, sig["experiment_id"], signal_id, now)
        return self.signal(signal_id)

    def expire_stale(self, now: datetime) -> list[int]:
        out = []
        with connect() as conn:
            rows = conn.execute("SELECT id, plan_json FROM signals WHERE status = 'new'").fetchall()
        for r in rows:
            dl = json.loads(r["plan_json"])["inputs"].get("entry_deadline")
            if dl and now > datetime.fromisoformat(dl):
                self._set_status(r["id"], "expired", "not approved before the entry bar opened", now)
                out.append(r["id"])
        return out

    # --- market step: bars → fills → journal, breakers, equity ----------------------------------------------
    async def market_step(self, now: datetime) -> list[dict]:
        acct, exp, s = self.account()
        events: list[dict] = []
        self.expire_stale(now)
        by_ticker: dict[tuple[str, str, str], None] = {}
        for row, _b in acct.live_brackets():
            meta = json.loads(row["state_json"]).get("meta", {})
            by_ticker[(row["ticker"], row["asset_type"], meta.get("interval", "1d"))] = None
        for t, a, interval in by_ticker:
            df = self._df(t, a, interval, now)
            if df is None or not len(df):
                continue
            evs = acct.process(t, to_bars(df.tail(400), a, interval))
            for ev in evs:
                events.append(self._event(ev.pop("type"), **ev))
                if events[-1]["type"] == "order_filled" and ev.get("role") == "entry":
                    self._set_status(ev["signal_id"], "filled", None, now)
                elif events[-1]["type"] == "entry_cancelled":
                    self._set_status(ev["signal_id"], "cancelled", ev.get("reason"), now)
                elif events[-1]["type"] == "position_closed":
                    self._set_status(ev["signal_id"], "closed", ev.get("exit_reason"), now)
                    await self._review(ev["position_id"], now)
        snap = acct.snapshot(now, dict(self.last_close))
        events.append(self._event("equity_tick", ts=_iso(now), equity=float(snap.equity), cash=float(snap.cash),
                                  drawdown=snap.drawdown, gross=float(snap.gross_exposure),
                                  open_risk=float(snap.open_risk)))
        rp = RiskParams.from_config(s["risk"])
        if snap.drawdown <= rp.max_dd and not s.get("halted"):
            events.append(self._trip_halt(s, exp, now, f"drawdown {snap.drawdown:.2%} from peak"))
        pf = self.portfolio(acct, s, now)
        if pf.day_return <= rp.daily_loss and not self._tripped_today(exp, "daily_loss", now):
            record_event("daily_loss", f"day P&L {pf.day_return:.2%}", exp, None, now)
            events.append(self._event("breaker_tripped", rule="daily_loss", detail=f"day P&L {pf.day_return:.2%}"))
        return events

    def _tripped_today(self, exp: int, rule: str, now: datetime) -> bool:
        day0 = _iso(datetime.combine(now.astimezone(UTC).date(), datetime.min.time(), tzinfo=UTC))
        with connect() as conn:
            return conn.execute("SELECT 1 FROM risk_events WHERE experiment_id = ? AND rule = ? AND ts >= ? AND"
                                " signal_id IS NULL", (exp, rule, day0)).fetchone() is not None

    def _trip_halt(self, s: dict, exp: int, now: datetime, detail: str, signal_id: int | None = None) -> dict:
        s["halted"] = True
        pro_settings.save(s)
        record_event("max_drawdown", detail + "; new trades halted until a manual reset", exp, None, now)
        return self._event("breaker_tripped", rule="max_drawdown", detail=detail, halted=True)

    async def _review(self, position_id: int, now: datetime) -> None:
        with connect() as conn:
            row = dict(conn.execute("SELECT p.*, s.thesis, s.invalidation, s.horizon FROM positions p LEFT JOIN signals s"
                                    " ON s.id = p.signal_id WHERE p.id = ?", (position_id,)).fetchone())
        if row.get("review"):
            return  # max one review per closed trade
        st = json.loads(row["state_json"])
        b = Bracket.from_state(st["bracket"])
        trade = {**{k: row[k] for k in ("ticker", "asset_type", "side", "opened_at", "closed_at", "avg_entry", "avg_exit",
                                        "exit_reason", "r_multiple", "realized_pnl", "thesis", "invalidation", "horizon")},
                 "stop_initial": b.stop_initial, "targets": b.targets,
                 "mae_r": b.mae / b.risk_per_unit if b.risk_per_unit else None,
                 "mfe_r": b.mfe / b.risk_per_unit if b.risk_per_unit else None}
        review, err = (None, "no reviewer configured")
        if self.reviewer is not None:
            try:
                review, err = await self.reviewer(trade, now)
            except Exception as e:
                review, err = None, f"{type(e).__name__}: {e}"[:200]
        payload = review if review is not None else {"post_mortem": f"review unavailable ({err})", "thesis_held": None,
                                                     "lesson": ""}
        with connect() as conn:
            conn.execute("UPDATE positions SET review = ? WHERE id = ?", (json.dumps(payload), position_id))


def default_reviewer(local_ok: Callable[[], bool], setting: str = "auto", budget_ok: Callable[[], bool] = lambda: True):
    """Reviewer on the cheapest model: local when reachable, else the cheapest Gemini — if the money budget allows."""
    from agents import reviewer as R

    async def run(trade: dict, now: datetime):
        model = R.reviewer_model(setting, local_ok())
        if not model.startswith("local:") and not budget_ok():
            return None, "daily money budget exhausted"
        out, err = await R.review(trade, model)
        return (out.model_dump() | {"model": model}) if out else None, err

    return run
