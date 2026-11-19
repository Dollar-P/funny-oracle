"""R2: REST routes, RunManager, SSE stream, MockRunner — no LLM calls, no network."""
from __future__ import annotations

import json

import pytest

import config
from api.events import validate_event
from core.events import STAGES


def read_sse(client, run_id, headers=None, after=None):
    """Consume the whole SSE stream → list of (event, id, data dict)."""
    url = f"/api/runs/{run_id}/events" + (f"?after={after}" if after else "")
    out, ev, eid, data = [], None, None, []
    with client.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith(":"):
                continue
            if not line:
                if data:
                    out.append((ev, eid, json.loads("\n".join(data))))
                ev, eid, data = None, None, []
                continue
            k, _, v = line.partition(":")
            v = v[1:] if v.startswith(" ") else v
            if k == "event":
                ev = v
            elif k == "id":
                eid = v
            elif k == "data":
                data.append(v)
    return out


def test_health(client):
    r = client.get("/api/health").json()
    assert r["ok"] and "not investment advice" in r["disclaimer"]


def test_models_lists_prices_and_estimates(client):
    r = client.get("/api/models").json()
    ids = {m["id"] for m in r["models"]}
    assert "gemini-3.5-flash-lite" in ids and "gemini-3.8-flash" in ids
    m = next(m for m in r["models"] if m["id"] == "gemini-3.5-flash-lite")
    assert m["provider"] == "gemini" and m["price_in"] == 0.30 and m["speed_tier"] in (1, 2, 3)
    assert set(m["est_cost_thb"]) == {"swing", "scalp"} and m["est_cost_thb"]["swing"] > 0
    assert "swing" in m["avg_elapsed_s"]  # observed from the seeded predictions


def test_ticker_search(client):
    r = client.get("/api/tickers/search?q=btc").json()
    assert r["results"][0]["symbol"] == "BTC-USD" and r["results"][0]["asset_type"] == "crypto"
    r = client.get("/api/tickers/search?q=gold").json()
    assert any(x["symbol"] == "GC=F" and x["asset_type"] == "gold" for x in r["results"])
    r = client.get("/api/tickers/search").json()
    assert [x["symbol"] for x in r["results"]] and r["presets"]


def test_quote_and_history(client):
    q = client.get("/api/quote/NVDA").json()
    assert q["price"] > 0 and isinstance(q["change_pct"], float) and len(q["sparkline"]) > 10
    h = client.get("/api/history/NVDA?interval=1d&limit=100").json()
    assert len(h["candles"]) == 100 and {"time", "open", "high", "low", "close"} <= set(h["candles"][0])


def test_mock_prediction_streams_full_show(client, api_db):
    r = client.post("/api/predictions", json={"ticker": "NVDA", "mode": "swing", "horizon": "1M"})
    assert r.status_code == 200 and r.json()["mock"] is True
    events = read_sse(client, r.json()["run_id"])
    datas = [d for _, _, d in events]
    for d in datas:
        validate_event(d)
    types = [d["type"] for d in datas]
    assert types[0] == "run_started" and types[-1] == "run_done"
    assert [e for e, _, _ in events] == types                       # SSE `event:` = type
    assert [int(i) for _, i, _ in events] == list(range(1, len(events) + 1))  # SSE `id:` = seq
    assert [d["name"] for d in datas if d["type"] == "stage"] == list(STAGES)
    for kind in ("agent_started", "tool_call", "tool_result", "agent_done", "aggregate", "debate_turn",
                 "judge", "quant", "cost_update"):
        assert kind in types, kind
    assert datas[0]["mock"] is True and datas[0]["ticker"] == "NVDA"
    done = datas[-1]
    assert done["prediction_id"] in api_db["ids"] and done["prediction"]["ticker"] == "NVDA"


def test_mock_relabels_other_ticker(client):
    r = client.post("/api/predictions?mock=1", json={"ticker": "TSLA", "mode": "swing", "horizon": "3M"})
    datas = [d for _, _, d in read_sse(client, r.json()["run_id"])]
    assert datas[0]["ticker"] == "TSLA" and datas[0]["asset_type"] == "stock"
    p = datas[-1]["prediction"]
    assert p["ticker"] == "TSLA" and p["horizon"] == "3M"
    # prices rescaled to the (synthetic) live TSLA price
    q = next(d for d in datas if d["type"] == "quant")
    assert q["price_now"] == pytest.approx(p["forecast"]["price_now"])


def test_reconnect_with_last_event_id_replays_the_rest(client):
    rid = client.post("/api/predictions", json={"ticker": "BTC-USD", "mode": "scalp", "horizon": "15m"}).json()["run_id"]
    full = read_sse(client, rid)
    tail = read_sse(client, rid, headers={"Last-Event-ID": "5"})
    assert [int(i) for _, i, _ in tail] == list(range(6, len(full) + 1))
    assert read_sse(client, rid, after=len(full) - 1)[0][2]["type"] == "run_done"
    assert client.get(f"/api/runs/{rid}").json()["done"] is True


def test_unknown_run_404(client):
    assert client.get("/api/runs/nope/events").status_code == 404


def test_validation_errors(client):
    assert client.post("/api/predictions", json={"ticker": "NVDA", "mode": "swing", "horizon": "15m"}).status_code == 422
    assert client.post("/api/predictions", json={"ticker": "NVDA", "mode": "yolo"}).status_code == 422


def test_concurrency_limit_429(client, monkeypatch):
    monkeypatch.setattr(config, "MOCK_SPEED", 50.0)  # keep shows running
    a = client.post("/api/predictions", json={"ticker": "NVDA"})
    b = client.post("/api/predictions", json={"ticker": "NVDA"})
    c = client.post("/api/predictions", json={"ticker": "NVDA"})
    assert a.status_code == b.status_code == 200 and c.status_code == 429
    assert "in progress" in c.json()["detail"]
    from api.runs import MANAGER

    for run in MANAGER.active():  # stop them so the next test starts clean
        run.task.cancel()
        run.done = True


def test_real_prediction_via_mocked_orchestrator(client, real_mode):
    r = client.post("/api/predictions", json={"ticker": "NVDA", "mode": "swing", "horizon": "1M",
                                              "analyst_model": "gemini-3.5-flash-lite"})
    assert r.status_code == 200 and r.json()["mock"] is False
    datas = [d for _, _, d in read_sse(client, r.json()["run_id"])]
    for d in datas:
        validate_event(d)
    assert datas[0]["type"] == "run_started" and datas[0]["mock"] is False
    assert datas[0]["models"] == {"analyst": "gemini-3.5-flash-lite", "judge": "gemini-3.5-flash-lite"}
    assert datas[-1]["type"] == "run_done"
    pid = datas[-1]["prediction_id"]
    got = client.get(f"/api/predictions/{pid}").json()
    assert got["prediction"]["models"]["analyst"] == "gemini-3.5-flash-lite" and got["source"] == "web"


def test_budget_refusal_402(client, real_mode, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_THB", 0.0)
    r = client.post("/api/predictions", json={"ticker": "NVDA"})
    assert r.status_code == 402
    body = r.json()
    assert "out of coins" in body["reason"] and body["est_thb"] > 0 and "spent_thb" in body


def test_mock_ignores_budget(client, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_THB", 0.0)
    assert client.post("/api/predictions", json={"ticker": "NVDA"}).status_code == 200


def test_prediction_history_and_detail(client, api_db):
    rows = client.get("/api/predictions?limit=10").json()["predictions"]
    assert {r["id"] for r in rows} >= set(api_db["ids"])
    nv = client.get("/api/predictions?ticker=nvda").json()["predictions"]
    assert nv and all(r["ticker"] == "NVDA" for r in nv)
    d = client.get(f"/api/predictions/{api_db['ids'][0]}").json()
    assert d["prediction"]["verdict"]["headline"] and "actual_price" in d
    assert d["prediction"]["models"]["analyst"] == "gemini-3.5-flash-lite"
    assert client.get("/api/predictions/999999").status_code == 404


def test_prediction_evaluate_on_demand(client, api_db, monkeypatch):
    from core.db import connect

    pid = api_db["ids"][1]
    with connect() as conn:
        conn.execute("UPDATE predictions SET target_time = '2020-01-01T00:00:00+00:00', actual_price = NULL WHERE id = ?",
                     (pid,))
    monkeypatch.setattr("core.scoreboard.actual_price", lambda *a, **k: 123.0)
    assert client.get(f"/api/predictions/{pid}").json()["actual_price"] is None
    assert client.get(f"/api/predictions/{pid}?evaluate=1").json()["actual_price"] == 123.0


def test_replay_endpoint(client, api_db):
    pid = api_db["ids"][1]  # BTC scalp
    rid = client.post(f"/api/predictions/{pid}/replay").json()["run_id"]
    datas = [d for _, _, d in read_sse(client, rid)]
    assert datas[0]["replay_of"] == pid and datas[0]["ticker"] == "BTC-USD"
    assert datas[-1]["prediction_id"] == pid
    assert "debate_turn" not in [d["type"] for d in datas]


def test_mock_duel(client):
    r = client.post("/api/duels", json={"ticker": "NVDA", "mode": "swing", "horizon": "1M",
                                        "model_a": "gemini-3.5-flash-lite", "model_b": "gemini-3.8-flash"}).json()
    a, b = r["run_ids"]
    ea = [d for _, _, d in read_sse(client, a)]
    eb = [d for _, _, d in read_sse(client, b)]
    assert ea[0]["models"]["analyst"] == "gemini-3.5-flash-lite" and eb[0]["models"]["analyst"] == "gemini-3.8-flash"
    assert ea[0]["duel_id"] == eb[0]["duel_id"] == r["duel_id"]
    assert ea[0]["replay_of"] != eb[0]["replay_of"]  # different stored shows
    d = client.get(f"/api/duels/{r['duel_id']}").json()
    assert d["status"] == ["done", "done"] and d["comparison"]["banner"]
    assert {x["agent"] for x in d["comparison"]["agents"]} >= {"technical"}


def test_real_duel_budget_checks_both(client, real_mode, monkeypatch):
    from core.cost import BudgetGuard

    one = BudgetGuard().estimate_usd("swing", "gemini-3.5-flash-lite")
    monkeypatch.setattr(config, "BUDGET_THB", (one * 1.5 + __import__("core.cost", fromlist=["x"]).total_spent_usd())
                        * config.USD_THB)
    # one run would fit, two do not
    r = client.post("/api/duels", json={"ticker": "NVDA", "model_a": "gemini-3.5-flash-lite",
                                        "model_b": "gemini-3.5-flash-lite"})
    assert r.status_code == 402


def test_real_duel_stores_duel_id(client, real_mode):
    r = client.post("/api/duels", json={"ticker": "GC=F", "mode": "swing", "horizon": "1W",
                                        "model_a": "gemini-3.5-flash-lite", "model_b": "gemini-3.8-flash"}).json()
    for rid in r["run_ids"]:
        assert read_sse(client, rid)[-1][2]["type"] == "run_done"
    d = client.get(f"/api/duels/{r['duel_id']}").json()
    assert [p["models"]["analyst"] for p in d["predictions"]] == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    rows = client.get("/api/predictions?ticker=GC=F").json()["predictions"]
    assert sum(1 for x in rows if x["duel_id"] == r["duel_id"]) == 2


def test_scoreboard_and_budget(client):
    s = client.get("/api/scoreboard").json()
    assert "metrics" in s and isinstance(s["models"], list) and isinstance(s["pending"], list)
    assert any(m["model"] == "gemini-3.5-flash-lite" for m in s["models"])
    assert s["pending"] and "target_time" in s["pending"][0]
    b = client.get("/api/budget").json()
    assert b["budget_thb"] == config.BUDGET_THB and "estimated_actual_thb" in b and "remaining_thb" in b


def test_model_leaderboard_counts_duel_wins(client, monkeypatch):
    from api.routes.scoreboard import model_leaderboard
    from core.db import connect

    with connect() as conn:
        for m, p50 in [("model-x", 101.0), ("model-y", 120.0)]:
            conn.execute("INSERT INTO predictions (created_at,ticker,asset_type,mode,horizon,target_time,price_now,"
                         "p10,p50,p90,prob_up,cost_usd,analyst_model,duel_id,actual_price) VALUES"
                         " ('x','NVDA','stock','swing','1M','x',100,90,?,130,0.6,0.01,?,'duelZ',102)", (p50, m))
    lb = {r["model"]: r for r in model_leaderboard()}
    assert lb["model-x"]["duel_wins"] == 1 and lb["model-y"]["duel_wins"] == 0
    assert lb["model-x"]["hit_rate"] == 1.0 and lb["model-x"]["band_coverage"] == 1.0


def test_autoscalp_blocked_in_mock_and_status(client):
    assert client.post("/api/autoscalp/start").status_code == 409
    s = client.get("/api/autoscalp/status").json()
    assert s["running"] is False and s["runs_left"] == config.AUTO_SCALP_MAX_RUNS


def test_autoscalp_start_stop(client, monkeypatch):
    from api.routes import scoreboard as sb

    monkeypatch.setattr(config, "MOCK_LLM", False)
    ticks = []

    def fake_tick(state, progress=None):
        ticks.append(1)
        return "tick"

    monkeypatch.setattr(sb, "tick", fake_tick)
    sb.AUTOSCALP.poll_s = 0.01
    assert client.post("/api/autoscalp/start").json()["running"] is True
    import time

    time.sleep(0.1)
    client.post("/api/autoscalp/stop")
    sb.AUTOSCALP.thread.join(2)
    assert ticks and client.get("/api/autoscalp/status").json()["running"] is False
