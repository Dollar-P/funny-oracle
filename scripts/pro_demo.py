"""Build the Desk demo dataset (synthetic, deterministic, no network, no LLM) for the e2e UI checks.

    python scripts/pro_demo.py [--out data/pro_demo] [--days 75]

Then serve it:
    PRO_DEMO_DIR=data/pro_demo ORACLE_DB=data/pro_demo/pro_demo.db MOCK_LLM=true uvicorn api.main:app --port 8000

What it does: 6 synthetic assets, ~75 days of the real scheduler + Desk forward loop on a simulated clock (auto
approval, then manual for the last two days so some signals are waiting for approval at the demo's "now"),
evaluation of finished predictions from the synthetic bars, one evaluation report and one baseline backtest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
UTC = timezone.utc
DEMO_NOW = datetime(2026, 11, 20, 23, 50, tzinfo=UTC)   # Friday night: weekend signals still approvable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "pro_demo"))
    ap.add_argument("--days", type=int, default=75)
    ap.add_argument("--n-random", type=int, default=300)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    db = out / "pro_demo.db"
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if p.exists():
            p.unlink()
    os.environ["ORACLE_DB"] = str(db)

    import anyio

    import config

    config.DB_PATH = db
    from core import pro_settings
    from trading import demo
    from trading.desk import Desk
    from trading.scheduler import Scheduler

    t0 = time.perf_counter()
    data = demo.make_bars("2022-06-01", DEMO_NOW.date().isoformat())
    demo.save_bars(data, out / "bars")
    bars = demo.DemoBars(data)
    s = demo.demo_settings()
    s["approval"] = "auto"
    pro_settings.save(s)

    class Clock:
        t = DEMO_NOW - timedelta(days=a.days)

        def __call__(self):
            return self.t

    clock = Clock()
    desk = Desk(bars=bars, reviewer=demo.demo_review)
    sched = Scheduler(desk=desk, runner=demo.DemoRunner(bars, clock), clock=clock, local_available=lambda: False,
                      busy=lambda: (False, ""))
    manual_from = DEMO_NOW - timedelta(days=2)
    while clock.t <= DEMO_NOW:
        if clock.t >= manual_from and pro_settings.load()["approval"] != "manual":
            pro_settings.update({"approval": "manual"})
        anyio.run(sched.tick)
        demo.evaluate_from_bars(bars, clock.t)
        clock.t += timedelta(hours=1) if clock.t.hour in (21, 22, 23) else timedelta(hours=3)
    clock.t = DEMO_NOW
    anyio.run(lambda: desk.market_step(DEMO_NOW))
    # make sure the demo always ends with signals waiting for approval (deterministic e2e): strong demo
    # predictions on stock tickers without a live position, decided now (their entry bar opens on Monday)
    from core.db import connect as _c
    from trading.scheduler import Job

    runner = sched.runner
    for t, cls, *_ in demo.WATCH:
        with _c() as conn:
            n_new = conn.execute("SELECT COUNT(*) FROM signals WHERE status = 'new'").fetchone()[0]
            live = conn.execute("SELECT COUNT(*) FROM positions WHERE ticker = ? AND closed_at IS NULL AND exit_reason IS NULL",
                                (t,)).fetchone()[0]
        if n_new >= 2:
            break
        if cls != "stock" or live:
            continue
        for s_force in (0.9, -0.9):
            runner.force_s = s_force
            job = Job(f"demo-topup:{t}:{s_force}", t, cls, "swing", "1M", DEMO_NOW, DEMO_NOW)
            pred = anyio.run(lambda: runner(job, pro_settings.current_experiment(), pro_settings.load()))
            if desk.process_prediction(pred, DEMO_NOW)["status"] == "new":
                break
        runner.force_s = None
    print(f"simulated {a.days} days in {time.perf_counter() - t0:.0f}s")

    from core.db import connect
    from evaluation.report import build_report, run_baseline_backtests, save_report, write_files

    rep = build_report(history=bars.history, n_random=a.n_random, n_boot=1000, now=DEMO_NOW, evaluate=False)
    rid = save_report(rep)
    write_files(rep, rid, out / "reports")
    bt = run_baseline_backtests({t: v for t, v in data.items()}, "2024-01-01", n_random=0, n_boot=500)
    with connect() as conn:
        conn.execute("INSERT INTO backtests (created_at, kind, config_json, period, status, result_json, progress)"
                     " VALUES (?,?,?,?,?,?,1)", (DEMO_NOW.isoformat(), "baselines", json.dumps({"kind": "baselines",
                                                 "start": "2024-01-01"}), "2024-01-01..", "done", json.dumps(bt, default=str)))
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM signals GROUP BY status").fetchall())
        closed = conn.execute("SELECT COUNT(*) FROM positions WHERE closed_at IS NOT NULL").fetchone()[0]
    (out / "demo.json").write_text(json.dumps({"now": DEMO_NOW.isoformat(), "db": str(db), "report_id": rid}))
    print(json.dumps({"signals": counts, "closed_trades": closed, "report_id": rid,
                      "elapsed_s": round(time.perf_counter() - t0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
