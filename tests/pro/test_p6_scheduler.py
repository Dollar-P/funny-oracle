"""P6: scheduler + forward paper loop — a mocked 3-day simulation on a simulated clock, restarts, routing."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import anyio
import numpy as np
import pandas as pd
import pytest

from core import pro_settings, scoreboard
from core.db import connect
from core.schemas import Forecast, Prediction, Verdict
from trading import calendar as cal
from trading import scheduler as S
from trading.desk import Desk
from trading.paper_account import PaperAccount
from tests.pro.synth import daily_index

UTC = timezone.utc
SIM_START = datetime(2026, 3, 3, 0, 0, tzinfo=UTC)          # Tuesday (EST: NYSE closes 21:00 UTC)
SIM_END = datetime(2026, 3, 6, 23, 59, tzinfo=UTC)


def _history(asset_type: str, price: float, seed: int) -> pd.DataFrame:
    """Random walk until the simulation starts, then a steady rally (so trades hit T1 and T2)."""
    idx = daily_index(asset_type, "2025-03-01", "2026-03-12")
    rng = np.random.default_rng(seed)
    n = len(idx)
    c = np.empty(n); o = np.empty(n); h = np.empty(n); lo = np.empty(n)
    p = price
    for i, ts in enumerate(idx):
        if ts < pd.Timestamp(SIM_START - timedelta(days=1)):
            r = rng.normal(0, 0.01)
            o[i] = p * np.exp(rng.normal(0, 0.003)); c[i] = p * np.exp(r)
            h[i] = max(o[i], c[i]) * 1.006; lo[i] = min(o[i], c[i]) * 0.994
        else:
            o[i], h[i], lo[i], c[i] = p * 1.002, p * 1.05, p * 0.995, p * 1.04
        p = c[i]
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1e6}, index=idx)


DATA = {"AAA": ("stock", _history("stock", 100.0, 1)), "BTC-USD": ("crypto", _history("crypto", 30_000.0, 2))}
CLOSE_T = {t: np.array([cal.bar_close_time(a, "1d", ts.to_pydatetime(),
                                           df.index[i + 1].to_pydatetime() if i + 1 < len(df) else None)
                        for i, ts in enumerate(df.index)]) for t, (a, df) in DATA.items()}


def sim_bars(ticker, asset_type, interval, now):
    """Only bars whose close is final at `now` — exactly what the live bar source returns."""
    return DATA[ticker][1][CLOSE_T[ticker] <= now]


class FakeRunner:
    def __init__(self, clock):
        self.clock = clock
        self.calls: list[str] = []

    async def __call__(self, job, experiment_id, s):
        self.calls.append(job.key)
        now = self.clock()
        df = sim_bars(job.ticker, job.asset_type, "1d", now)
        price = float(df["Close"].iloc[-1])
        pred = Prediction(
            created_at=now.isoformat(), ticker=job.ticker, asset_type=job.asset_type, mode="swing", horizon=job.horizon,
            target_time=(now + timedelta(days=7)).isoformat(), s_raw=0.8, s_final=0.8, confidence=0.8, weights={},
            reports=[], verdict=Verdict(adjustment=0, final_confidence=0.8, headline="h", summary="s", key_risks=[],
                                        thesis="rally continues", invalidation="close below the stop"),
            forecast=Forecast(price_now=price, p10=price * 0.97, p50=price * 1.02, p90=price * 1.06, prob_up=0.7,
                              sigma_per_step=0.01, steps=5, step_label="1d"),
            models={"analyst": job.model, "judge": job.model}, prompt_version="v2", experiment_id=experiment_id,
            think_preset=job.think_preset, run_id=job.key)
        scoreboard.save(pred, job.key, "pro")
        return pred


async def fake_review(trade, now):
    return {"post_mortem": f"{trade['ticker']} {trade['exit_reason']}", "thesis_held": True, "lesson": "none"}, None


def setup_settings(**over):
    s = pro_settings.load()
    s["watchlist"] = [{"ticker": "AAA", "asset_type": "stock", "horizons": ["1W"]},
                      {"ticker": "BTC-USD", "asset_type": "crypto", "horizons": ["1W"]}]
    s["approval"] = "auto"
    s["models"]["swing"] = "local:prism-ml/bonsai-27b"
    s["local_window"] = "00:00-23:59"
    s["local_tz"] = "UTC"
    for k, v in over.items():
        s[k] = v
    pro_settings.save(s)
    return s


class SimClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def run_sim(sched, clock, start, end, step=timedelta(minutes=30)):
    clock.t = start
    while clock.t <= end:
        anyio.run(sched.tick)
        clock.t += step


def make(clock, local_ok=True, busy=lambda: (False, "")):
    desk = Desk(bars=sim_bars, reviewer=fake_review)
    runner = FakeRunner(clock)
    sched = S.Scheduler(desk=desk, runner=runner, clock=clock, local_available=lambda: local_ok, busy=busy)
    return desk, runner, sched


def test_three_day_simulation(fresh_db):
    setup_settings()
    clock = SimClock(SIM_START)
    desk, runner, sched = make(clock)
    run_sim(sched, clock, SIM_START, SIM_END)
    with connect() as conn:
        sigs = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id")]
        fills = [dict(r) for r in conn.execute("SELECT * FROM fills ORDER BY id")]
        pos = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY id")]
        runs = [dict(r) for r in conn.execute("SELECT * FROM scheduler_runs ORDER BY job_key")]
        snaps = conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()[0]
    # one job per asset per day: AAA Mon (caught up — valid until Tuesday's open) + Tue–Fri, BTC Tue–Fri
    assert sorted(runner.calls) == sorted(r["job_key"] for r in runs if r["status"] == "done")
    assert len(runner.calls) == 9 and len(set(runner.calls)) == 9 and "swing:AAA:1W:2026-03-02" in runner.calls
    assert all("local quick" in r["detail"] for r in runs)
    statuses = {s["status"] for s in sigs}
    assert "closed" in statuses and "rejected" in statuses             # duplicates rejected while a trade is open
    assert any(s["status_reason"] == "risk: duplicate_position" for s in sigs)
    roles = [f["reason"] for f in fills]
    assert roles.count("entry") >= 2 and "t1" in roles and "t2" in roles
    closed = [p for p in pos if p["closed_at"]]
    assert len(closed) >= 2 and all(p["r_multiple"] > 0 for p in closed)
    assert all(json.loads(p["review"])["thesis_held"] is True for p in closed)      # journal + reviewer
    # fills only ever at or after the decision; stock entries at the 14:30 UTC open
    first_aaa = next(f for f in fills if f["ticker"] == "AAA" and f["reason"] == "entry")
    assert first_aaa["ts"].endswith("14:30:00+00:00")
    assert snaps >= 150
    # equity reconciles: cash from fills = start + realised P&L of closed + flows of open positions
    exp = pro_settings.current_experiment()
    acct = PaperAccount(exp, 100_000)
    realized = sum(p["realized_pnl"] for p in closed)
    open_flows = 0.0
    for p in pos:
        if not p["closed_at"]:
            for f in fills:
                if f["position_id"] == p["id"]:
                    open_flows += (1 if f["side"] == "sell" else -1) * round(f["price"] * f["qty"], 2) - f["fee"]
    assert float(acct.cash()) == pytest.approx(100_000 + realized + open_flows, abs=0.005)


def test_restart_does_not_double_run(fresh_db):
    setup_settings()
    clock = SimClock(SIM_START)
    _d, r1, s1 = make(clock)
    mid = datetime(2026, 3, 4, 23, 45, tzinfo=UTC)
    run_sim(s1, clock, SIM_START, mid)
    _d, r2, s2 = make(clock)                                              # "restart": fresh objects, same DB
    run_sim(s2, clock, mid - timedelta(hours=3), SIM_END)                 # overlapping ticks around the restart
    assert set(r1.calls).isdisjoint(r2.calls)
    assert len(r1.calls) + len(r2.calls) == 9
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions WHERE source = 'pro'").fetchone()[0] == 9
        fills = conn.execute("SELECT position_id, reason, COUNT(*) FROM fills GROUP BY position_id, reason"
                             " HAVING COUNT(*) > 1").fetchall()
    assert fills == []                                                    # no bar processed twice


def test_lm_studio_down_falls_back_to_gemini(fresh_db):
    setup_settings()
    clock = SimClock(SIM_START)
    _d, runner, sched = make(clock, local_ok=False)
    run_sim(sched, clock, datetime(2026, 3, 3, 21, 0, tzinfo=UTC), datetime(2026, 3, 3, 22, 0, tzinfo=UTC))
    runs = S.recent_runs()
    assert runs and all("LM Studio unreachable" in r["detail"] and r["model"] == "gemini-3.5-flash-lite" for r in runs)


def test_money_budget_exhausted_skips_with_reason(fresh_db):
    s = setup_settings(budgets={"daily_thb": 0.0})
    clock = SimClock(SIM_START)
    _d, runner, sched = make(clock, local_ok=False)
    run_sim(sched, clock, datetime(2026, 3, 3, 21, 0, tzinfo=UTC), datetime(2026, 3, 3, 22, 0, tzinfo=UTC))
    runs = S.recent_runs()
    assert runner.calls == [] and runs and all(r["status"] == "skipped" and "money budget" in r["detail"] for r in runs)
    assert s["budgets"]["daily_thb"] == 0.0


def test_arena_runs_take_priority(fresh_db):
    setup_settings()
    clock = SimClock(datetime(2026, 3, 3, 21, 45, tzinfo=UTC))
    _d, runner, sched = make(clock, busy=lambda: (True, "an interactive Arena/Duel run is in progress"))
    events = anyio.run(sched.tick)
    assert runner.calls == [] and S.recent_runs() == []                   # nothing claimed: retried next tick
    assert any(e["type"] == "job_deferred" and "Arena" in e["reason"] for e in events)
    sched.busy = lambda: (False, "")
    anyio.run(sched.tick)
    assert len(runner.calls) == 1                                          # AAA runs once the Arena is idle


def test_manual_approval_and_expiry(fresh_db):
    setup_settings(approval="manual")
    clock = SimClock(datetime(2026, 3, 3, 21, 45, tzinfo=UTC))
    desk, runner, sched = make(clock)
    anyio.run(sched.tick)
    with connect() as conn:
        sid, status = conn.execute("SELECT id, status FROM signals WHERE ticker = 'AAA'").fetchone()
    assert status == "new"
    clock.t = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)                     # after Wednesday's open
    with pytest.raises(ValueError, match="expired"):
        desk.approve(sid, clock.t)
    assert desk.signal(sid)["status"] == "expired"


def test_manual_approve_then_fill_and_reject(fresh_db):
    setup_settings(approval="manual")
    clock = SimClock(datetime(2026, 3, 3, 21, 45, tzinfo=UTC))
    desk, runner, sched = make(clock)
    anyio.run(sched.tick)
    with connect() as conn:
        sid = conn.execute("SELECT id FROM signals WHERE ticker = 'AAA'").fetchone()[0]
    assert desk.approve(sid, clock.t)["status"] == "approved"
    with pytest.raises(ValueError):
        desk.reject(sid, clock.t)                                          # only new signals can be rejected
    clock.t = datetime(2026, 3, 4, 21, 30, tzinfo=UTC)
    anyio.run(lambda: desk.market_step(clock.t))
    assert desk.signal(sid)["status"] == "filled"


# --- routing unit tests ----------------------------------------------------------------------------------------

def _job(valid_hours=10):
    now = datetime(2026, 3, 3, 21, 30, tzinfo=UTC)
    return S.Job("swing:X:1M:2026-03-03", "X", "stock", "swing", "1M", now, now + timedelta(hours=valid_hours))


def _s(window="23:00-07:00", budget=10.0):
    s = pro_settings.load()
    s["local_window"], s["local_tz"], s["budgets"] = window, "UTC", {"daily_thb": budget}
    s["models"]["swing"] = "local:prism-ml/bonsai-27b"
    return s


def test_route_local_inside_window(fresh_db):
    r = S.route(_job(), _s("21:00-07:00"), datetime(2026, 3, 3, 21, 30, tzinfo=UTC), local_ok=True, queue_eta_s=0,
                spent_thb=0)
    assert r.model == "local:prism-ml/bonsai-27b" and r.think_preset == "quick" and r.est_s == 400


def test_route_wont_finish_in_window_falls_back(fresh_db):
    now = datetime(2026, 3, 4, 6, 55, tzinfo=UTC)                         # window ends 07:00, a run takes ~6.7 min
    r = S.route(_job(), _s(), now, local_ok=True, queue_eta_s=0, spent_thb=0)
    assert r.model == "gemini-3.5-flash-lite" and "won't finish in the local window" in r.route_reason
    r = S.route(_job(), _s(budget=0.0), now, local_ok=True, queue_eta_s=0, spent_thb=0)
    assert isinstance(r, S.Skip) and "won't finish" in r.reason and "money budget" in r.reason
    r = S.route(_job(), _s(), datetime(2026, 3, 3, 23, 30, tzinfo=UTC), local_ok=True, queue_eta_s=7.4 * 3600,
                spent_thb=0)                                               # a long queue ahead also overruns
    assert r.model == "gemini-3.5-flash-lite"


def test_route_outside_window_waits_or_falls_back(fresh_db):
    now = datetime(2026, 3, 3, 21, 30, tzinfo=UTC)                        # window opens at 23:00
    r = S.route(_job(valid_hours=10), _s(), now, local_ok=True, queue_eta_s=0, spent_thb=0)
    assert isinstance(r, S.Skip) and r.defer and "waiting until" in r.reason
    r = S.route(_job(valid_hours=1), _s(), now, local_ok=True, queue_eta_s=0, spent_thb=0)
    assert r.model == "gemini-3.5-flash-lite" and "expires before it opens" in r.route_reason


def test_route_scalp_is_gemini_only(fresh_db):
    s = _s()
    job = S.Job("scalp:BTC-USD:1h:x", "BTC-USD", "crypto", "scalp", "1h", datetime.now(UTC), datetime.now(UTC))
    assert S.route(job, s, datetime.now(UTC), local_ok=True, queue_eta_s=0, spent_thb=0).model == "gemini-3.5-flash-lite"
    s["models"]["scalp"] = "local:prism-ml/bonsai-27b"
    assert isinstance(S.route(job, s, datetime.now(UTC), local_ok=True, queue_eta_s=0, spent_thb=0), S.Skip)


def test_window_bounds_across_midnight_and_tz():
    s = {"local_window": "23:00-07:00", "local_tz": "Asia/Bangkok"}
    w = S.window_bounds(datetime(2026, 3, 3, 18, 0, tzinfo=UTC), s)      # 01:00 Bangkok
    assert w == (datetime(2026, 3, 3, 16, 0, tzinfo=UTC), datetime(2026, 3, 4, 0, 0, tzinfo=UTC))
    assert S.window_bounds(datetime(2026, 3, 3, 6, 0, tzinfo=UTC), s) is None   # 13:00 Bangkok


def test_due_job_times():
    s = pro_settings.DEFAULTS | {"watchlist": [{"ticker": "AAA", "asset_type": "stock", "horizons": ["1M"]},
                                               {"ticker": "GC=F", "asset_type": "gold", "horizons": ["1M"]},
                                               {"ticker": "BTC-USD", "asset_type": "crypto", "horizons": ["1M"]}]}
    jobs = {j.ticker: j for j in S.due_jobs(s, datetime(2026, 3, 3, 22, 30, tzinfo=UTC), set())}
    assert set(jobs) == {"AAA", "GC=F"}
    assert jobs["AAA"].due_at == datetime(2026, 3, 3, 21, 30, tzinfo=UTC)
    assert jobs["AAA"].valid_until == datetime(2026, 3, 4, 14, 30, tzinfo=UTC)
    assert jobs["GC=F"].due_at == datetime(2026, 3, 3, 22, 15, tzinfo=UTC)               # CME close 17:00 EST + 15
    assert jobs["GC=F"].valid_until == datetime(2026, 3, 3, 23, 0, tzinfo=UTC)           # reopens 18:00 New York
    at = datetime(2026, 3, 3, 23, 40, tzinfo=UTC)
    jobs = {j.ticker: j for j in S.due_jobs(s, at, set())}
    assert set(jobs) == {"AAA", "BTC-USD"}                                               # gold's slot has passed
    assert jobs["BTC-USD"].due_at == datetime(2026, 3, 3, 23, 30, tzinfo=UTC)
    assert S.due_jobs(s, at, {j.key for j in jobs.values()}) == []
    sat = datetime(2026, 3, 7, 23, 40, tzinfo=UTC)
    jobs = S.due_jobs(s, sat, set())
    assert {j.ticker for j in jobs if j.key.endswith("2026-03-07")} == {"BTC-USD"}   # Saturday slots: crypto only
    # Friday's stock and gold jobs, if they never ran, stay due until the next open (Monday / Sunday evening)
    assert {j.key for j in jobs if not j.key.endswith("2026-03-07")} == {"swing:AAA:1M:2026-03-06",
                                                                          "swing:GC=F:1M:2026-03-06"}
