"""P8: /api/pro routes + the Desk event stream for a mocked scheduler day (no network, no LLM)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

import config

UTC = timezone.utc


@pytest.fixture()
def pro(tmp_path, monkeypatch, client):
    """Fresh DB + a Desk/Scheduler on synthetic bars, a simulated clock, a fake runner and a fake reviewer."""
    from api import pro_state
    from tests.pro.test_p6_scheduler import DATA, FakeRunner, SimClock, fake_review, setup_settings, sim_bars
    from trading.desk import Desk
    from trading.scheduler import Scheduler

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pro.db")
    setup_settings(approval="manual")
    clock = SimClock(datetime(2026, 3, 3, 21, 45, tzinfo=UTC))
    desk = Desk(bars=sim_bars, reviewer=fake_review, emit=pro_state.BUS.publish)
    runner = FakeRunner(clock)
    sched = Scheduler(desk=desk, runner=runner, clock=clock, local_available=lambda: True, busy=lambda: (False, ""),
                      emit=pro_state.BUS.publish)
    pro_state.BUS.clear()
    pro_state.set_for_tests(desk=desk, scheduler=sched, history=lambda t, a: DATA[t][1])
    yield {"clock": clock, "runner": runner, "client": client}
    pro_state.set_for_tests()


def at(p, *args):
    p["clock"].t = datetime(*args, tzinfo=UTC)
    r = p["client"].post("/api/pro/scheduler/tick")
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_and_experiments(pro):
    c = pro["client"]
    s = c.get("/api/pro/settings").json()
    assert s["settings"]["approval"] == "manual" and s["experiment_id"]
    r = c.put("/api/pro/settings", json={"approval": "auto"}).json()
    assert r["new_experiment"] is False and r["experiment_id"] == s["experiment_id"]   # operational setting
    r = c.put("/api/pro/settings", json={"risk": {"risk_pct_swing": 0.004}}).json()
    assert r["new_experiment"] is True and r["settings"]["risk"]["risk_pct_swing"] == 0.004
    assert r["settings"]["risk"]["max_open"] == 8                                     # deep merge
    for bad_patch in ({"approval": "yolo"}, {"models": {"scalp": "local:x"}}, {"halted": False},
                      {"local_window": "25:00-07:00"}, {"nope": 1}):
        assert c.put("/api/pro/settings", json=bad_patch).status_code == 422, bad_patch


def test_mocked_scheduler_day_end_to_end(pro):
    c = pro["client"]
    out = at(pro, 2026, 3, 3, 21, 45)                                    # Tue after the US close
    assert any(e["type"] == "job_done" for e in out["events"])
    sigs = c.get("/api/pro/signals?status=new").json()
    assert sigs["counts"].get("new") == 1
    sid = sigs["signals"][0]["id"]
    d = c.get(f"/api/pro/signals/{sid}").json()
    assert d["signal"]["direction"] == "long" and d["signal"]["size"]["qty"] > 0 and d["signal"]["p_win"] == 0.7
    assert d["prediction"]["forecast"]["p90"] > d["prediction"]["forecast"]["p10"] and d["replay_url"]
    assert d["signal"]["entry_deadline"].startswith("2026-03-04T14:30")
    assert c.post(f"/api/pro/signals/{sid}/approve").json()["signal"]["status"] == "approved"
    assert c.post(f"/api/pro/signals/{sid}/approve").status_code == 409
    assert c.post(f"/api/pro/signals/{sid}/reject").status_code == 409
    assert c.post("/api/pro/signals/99999/approve").status_code == 404
    assert c.get("/api/pro/orders").json()["orders"]
    at(pro, 2026, 3, 3, 23, 45)                                           # crypto job (BTC) → new signal
    btc = [s for s in c.get("/api/pro/signals?status=new").json()["signals"] if s["ticker"] == "BTC-USD"][0]
    assert c.post(f"/api/pro/signals/{btc['id']}/reject", json={"reason": "not today"}).json()["signal"]["status_reason"] == "not today"
    at(pro, 2026, 3, 4, 21, 15)                                           # Wednesday's bar complete → entry + T1
    pos = c.get("/api/pro/positions").json()["positions"]
    assert len(pos) == 1 and pos[0]["state"] == "open" and pos[0]["mark"] and pos[0]["unrealized_pnl"] > 0
    assert pos[0]["t1_done"] and pos[0]["stop"] == pytest.approx(pos[0]["entry"])     # breakeven after T1
    assert {f["reason"] for f in c.get("/api/pro/fills").json()["fills"]} == {"entry", "t1"}
    at(pro, 2026, 3, 5, 21, 15)                                           # Thursday → T2, position closed
    j = c.get("/api/pro/journal").json()["journal"]
    assert len(j) == 1 and j[0]["exit_reason"] == "t2" and j[0]["r_multiple"] > 0 and j[0]["thesis_held"] is True
    assert c.post(f"/api/pro/journal/{j[0]['id']}/tags", json={"tags": ["trend", " "]}).json()["tags"] == ["trend"]
    csv = c.get("/api/pro/journal?format=csv")
    assert csv.headers["content-type"].startswith("text/csv") and "AAA" in csv.text and "trend" in csv.text
    assert c.get(f"/api/pro/signals/{sid}").json()["signal"]["status"] == "closed"

    # the event stream of that day, in order, with typed events and seq ids
    evs = c.get("/api/pro/events?once=1").json()["events"]
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    types = [e["type"] for e in evs]
    for t in ("job_started", "job_done", "signal_created", "order_placed", "order_filled", "position_closed",
              "equity_tick"):
        assert t in types, t
    assert types.index("order_placed") < types.index("order_filled") < types.index("position_closed")
    filled = [e for e in evs if e["type"] == "order_filled"]
    assert [e["role"] for e in filled] == ["entry", "t1", "t2"]
    later = c.get(f"/api/pro/events?once=1&after={seqs[-3]}").json()["events"]
    assert [e["seq"] for e in later] == seqs[-2:]                          # resume from a seq
    only = c.get("/api/pro/events?once=1&types=position_closed").json()["events"]
    assert [e["type"] for e in only] == ["position_closed"]
    # real SSE framing (bounded by max_s)
    with c.stream("GET", f"/api/pro/events?after={seqs[-2]}&max_s=0.5") as r:
        body = "".join(r.iter_text())
    assert f"id: {seqs[-1]}" in body and "event: " in body

    ov = c.get("/api/pro/overview").json()
    assert ov["equity"] > 100_000 and ov["open_positions"] == 0 and ov["signal_counts"]["closed"] == 1
    assert ov["sharpe"]["label"] == "insufficient data" and ov["sharpe"]["n"] < 30
    eq = c.get("/api/pro/equity").json()
    assert eq["equity"] and eq["benchmarks"]["buy_and_hold"] and eq["benchmarks"]["cash"][0][1] == 100_000
    rk = c.get("/api/pro/risk").json()
    assert {lim["rule"] for lim in rk["limits"]} >= {"gross_exposure", "class:stock", "drawdown", "open_positions"}
    assert any(r["rule"] == "manual_reject" for r in rk["rejections"])


def test_risk_with_open_positions_and_correlation_matrix(pro):
    c = pro["client"]
    c.put("/api/pro/settings", json={"approval": "auto"})
    at(pro, 2026, 3, 3, 21, 45)
    at(pro, 2026, 3, 3, 23, 45)
    at(pro, 2026, 3, 4, 21, 15)                                           # AAA: Wednesday's bar → entry
    at(pro, 2026, 3, 5, 0, 30)                                            # BTC: Wednesday's 00:00 bar completes at 00:00 Thu
    rk = c.get("/api/pro/risk").json()
    assert set(rk["correlation"]["tickers"]) == {"AAA", "BTC-USD"}
    m = rk["correlation"]["matrix"]
    assert m[0][0] == 1.0 and m[0][1] == m[1][0]
    assert rk["exposure"]["stock"]["long"] > 0 and rk["exposure"]["crypto"]["long"] > 0


def test_account_reset_starts_a_new_experiment(pro):
    c = pro["client"]
    at(pro, 2026, 3, 3, 21, 45)
    sid = c.get("/api/pro/signals?status=new").json()["signals"][0]["id"]
    old = c.get("/api/pro/settings").json()["experiment_id"]
    r = c.post("/api/pro/account/reset").json()
    assert r["experiment_id"] != old and r["settings"]["account_reset"] == 1
    assert c.get("/api/pro/signals").json()["signals"] == []              # the new account starts empty
    assert c.post(f"/api/pro/signals/{sid}/approve").status_code == 409    # old experiment → expired
    assert c.get("/api/pro/overview").json()["equity"] == 100_000


def test_scheduler_start_stop_status(pro):
    c = pro["client"]
    st = c.get("/api/pro/scheduler/status").json()
    assert st["running"] is False and st["approval"] == "manual" and "local_window" in st
    assert c.post("/api/pro/scheduler/start?tick_s=3600").json()["running"] is True
    assert c.post("/api/pro/scheduler/stop").json()["running"] is False


def test_evaluation_report_route(pro):
    c = pro["client"]
    c.put("/api/pro/settings", json={"approval": "auto"})
    for t in ((2026, 3, 3, 21, 45), (2026, 3, 4, 21, 15), (2026, 3, 5, 21, 15)):
        at(pro, *t)
    rid = c.post("/api/pro/evaluation/run", json={"wait": True, "n_random": 20, "n_boot": 100}).json()["report_id"]
    rep = c.get(f"/api/pro/evaluation/reports/{rid}").json()
    r = rep["report"]
    assert r["meta"]["assets"] == ["AAA", "BTC-USD"] and r["meta"]["cost_assumptions_bps"]["crypto"]["fee"] == 10
    assert r["forward"]["trading"]["label"] == "insufficient data"
    assert r["forward"]["prediction_quality"]["label"] == "insufficient data"
    bt = r["backtests"]["strategies"]
    assert set(bt) == {"buy_and_hold", "cash", "momentum_63", "sma_50_200", "quant_only"}
    sh = bt["momentum_63"]["sharpe"]
    assert sh["n"] > 30 and sh["lo"] is not None and sh["lo"] <= sh["value"] <= sh["hi"]
    assert r["backtests"]["random"]["n_runs"] == 20
    assert any(v["verdict"] == "insufficient data" for v in r["verdicts"])
    assert "# Evaluation report" in rep["markdown"] and "insufficient data" in rep["markdown"]
    assert c.get("/api/pro/evaluation/reports").json()["reports"][0]["id"] == rid
    assert c.get("/api/pro/evaluation/latest").json()["id"] == rid


def test_backtest_routes(pro, monkeypatch):
    c = pro["client"]
    b = c.post("/api/pro/backtests", json={"kind": "baselines", "start": "2025-09-01", "n_random": 10, "n_boot": 50,
                                           "wait": True}).json()
    assert b["status"] == "done" and "quant_only" in b["result"]["strategies"]
    evs = c.get("/api/pro/events?once=1&types=backtest_progress,backtest_done").json()["events"]
    assert evs[-1]["type"] == "backtest_done" and evs[-1]["backtest_id"] == b["id"]
    with c.stream("GET", f"/api/pro/backtests/{b['id']}/events") as r:     # ends with backtest_done
        body = "".join(r.iter_text())
    assert "event: backtest_done" in body
    # LLM backtest: paid model without confirmation → needs_confirmation with an estimate, nothing run
    monkeypatch.setitem(config.MODEL_CUTOFFS, "gemini-3.5-flash-lite", date(2025, 6, 30))
    r = c.post("/api/pro/backtests", json={"kind": "llm", "model": "gemini-3.5-flash-lite", "start": "2025-07-01",
                                           "end": "2026-02-01", "tickers": {"AAA": "stock"}, "wait": True}).json()
    assert r["status"] == "needs_confirmation" and r["result"]["estimate_thb"] > 0
    assert c.post("/api/pro/backtests", json={"kind": "llm"}).status_code == 422
    assert [x["id"] for x in c.get("/api/pro/backtests").json()["backtests"]][:2] == [r["id"], b["id"]]


def test_arena_routes_untouched(pro):
    c = pro["client"]
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/api/models").status_code == 200
    assert c.get("/api/pro/nope").status_code == 404


def test_bars_endpoint_returns_completed_bars_only(pro):
    c = pro["client"]
    out = c.get("/api/pro/bars/AAA?limit=50").json()
    assert out["asset_type"] == "stock" and len(out["candles"]) == 50
    last = datetime.fromtimestamp(out["candles"][-1]["t"], UTC)
    assert last.date() <= pro["clock"].t.date()          # nothing after the (simulated) now
