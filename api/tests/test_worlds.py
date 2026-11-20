"""Arena and Pro are separate worlds over one `predictions` table (core/worlds.py).

1. Arena views (Hall of Fame/scoreboard, predictions list + detail, replay, mock shows, duels, model latency,
   cost estimates, Streamlit helpers, /api/runs) never show source='pro' rows.
2. Pro views (/api/pro/*, report, calibration, scheduler estimates and spend) read only source='pro' rows.
3. With no Pro rows the Arena output is what the unfiltered (main) queries return, and NULL/legacy sources count
   as Arena; adding Pro rows does not change a single Arena number.
4. Guard: every query that reads `predictions` uses a world filter or says `# any-world: <reason>`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
from api.tests.test_pro_api import pro  # noqa: F401  (fixture: a Desk on a simulated day)

ROOT = Path(__file__).resolve().parent.parent.parent
UTC = timezone.utc
PRO_TICKER = "PROX"


def _clone(pred, **over):
    p = pred.model_copy(deep=True)
    for k, v in over.items():
        setattr(p, k, v)
    p.id = None
    return p


@pytest.fixture()
def worlds(tmp_path, monkeypatch, api_db, client):
    """A fresh DB with Arena predictions of every legacy source (one with NULL), one Arena duel, evaluated rows."""
    from core import scoreboard
    from core.db import connect

    stored = [scoreboard.load(i) for i in api_db["ids"]]          # from the session DB, before switching
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "worlds.db")
    ids = []
    for k, (p, src) in enumerate(zip(stored + stored[:2], ["web", "cli", "manual", "auto_scalp", "web", "seed"])):
        q = _clone(p, duel_id="duel1" if k >= 4 else None)
        ids.append(scoreboard.save(q, f"arena-{k}", src))
    with connect() as conn:
        conn.execute("UPDATE predictions SET source = NULL WHERE id = ?", (ids[5],))          # legacy row
        for i in ids[:3]:
            conn.execute("UPDATE predictions SET actual_price = price_now * 1.03, evaluated_at = created_at"
                         " WHERE id = ?", (i,))
    return {"stored": stored, "arena_ids": ids, "client": client}


def add_pro_rows(stored, n=3) -> list[int]:
    """Pro rows that would visibly change every Arena number: other ticker, huge cost, evaluated, same models."""
    from core import scoreboard
    from core.db import connect

    ids = []
    for k in range(n):
        base = stored[k % len(stored)]
        q = _clone(base, ticker=PRO_TICKER, cost_usd=9.99, elapsed_s=999.0, prompt_version="v2", experiment_id=1)
        ids.append(scoreboard.save(q, f"pro-{k}", "pro"))
    with connect() as conn:
        conn.execute("UPDATE predictions SET actual_price = price_now * 0.5, evaluated_at = created_at"
                     " WHERE source = 'pro'")
    return ids


def arena_snapshot(c) -> dict:
    """Everything the Arena shows that is derived from stored predictions (volatile fields dropped)."""
    from api import mock_runner
    from api.routes.models import observed_latency
    from core import cost, scoreboard

    sb = c.get("/api/scoreboard?evaluate=0").json()
    sb.pop("now")
    preds = c.get("/api/predictions?limit=500").json()
    details = {i["id"]: c.get(f"/api/predictions/{i['id']}").json() for i in preds["predictions"]}
    return {
        "scoreboard": sb, "list": preds, "details": details,
        "duel": c.get("/api/duels/duel1").json(),
        "runs": c.get("/api/runs").json(),
        "latency": observed_latency(),
        "mock_pool": mock_runner.stored_ids(),
        "mock_pick": mock_runner.pick(PRO_TICKER, "swing"),
        "recent": scoreboard.recent(100).to_dict("records"),
        "metrics": scoreboard.metrics(),
        "metrics_swing": scoreboard.metrics("swing"),
        "spend_predictions": cost.spend_summary()["predictions"],
        "estimate": [cost.BudgetGuard().estimate_usd(m, mdl) for m in ("swing", "scalp")
                     for mdl in (None, "gemini-3.5-flash-lite")],
    }


# --- 1 + 3: Arena never sees Pro, and is unchanged by it ------------------------------------------------------

def test_arena_views_exclude_pro_and_are_unchanged_by_pro_rows(worlds):
    c = worlds["client"]
    before = arena_snapshot(c)
    assert before["scoreboard"]["metrics"]["n"] == 3 and len(before["list"]["predictions"]) == 6
    assert before["duel"]["comparison"] is not None

    pro_ids = add_pro_rows(worlds["stored"])
    after = arena_snapshot(c)
    assert json.dumps(after, sort_keys=True, default=str) == json.dumps(before, sort_keys=True, default=str)
    assert PRO_TICKER not in json.dumps(after, default=str)

    for pid in pro_ids:                                           # by id: the Arena does not know Pro rows
        assert c.get(f"/api/predictions/{pid}").status_code == 404
        assert c.post(f"/api/predictions/{pid}/replay").status_code == 404
    from api import mock_runner
    from core import scoreboard

    assert mock_runner.load(pro_ids[0]) is None and scoreboard.load(pro_ids[0], "arena") is None
    assert scoreboard.load(pro_ids[0], "pro").ticker == PRO_TICKER     # …but the Desk does
    assert scoreboard.load(worlds["arena_ids"][0], "pro") is None


def test_pro_runs_hidden_from_arena_run_list(worlds):
    from api.runs import MANAGER

    MANAGER._new("real", {"ticker": PRO_TICKER, "source": "pro"}, None)
    arena = MANAGER._new("real", {"ticker": "NVDA"}, None)
    active = worlds["client"].get("/api/runs").json()["active"]
    assert [r["run_id"] for r in active] == [arena.run_id]
    MANAGER.runs.clear()


def test_arena_queries_equal_unfiltered_queries_without_pro_rows(worlds):
    """No Pro rows → the filtered queries return exactly what the unfiltered ones on main return."""
    from api.routes.runs import LIST_COLS
    from core.db import connect

    c = worlds["client"]
    with connect() as conn:
        raw = [dict(r) for r in conn.execute(f"SELECT {LIST_COLS} FROM predictions ORDER BY id DESC").fetchall()]
        n_eval = conn.execute("SELECT COUNT(*) FROM predictions WHERE actual_price IS NOT NULL").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM predictions WHERE actual_price IS NULL").fetchone()[0]
    assert c.get("/api/predictions?limit=500").json()["predictions"] == raw
    m = c.get("/api/scoreboard?evaluate=0").json()["metrics"]
    assert (m["n"], m["pending"]) == (n_eval, pending)
    assert None in {r["source"] for r in raw}                     # the legacy NULL-source row is Arena


# --- 2: Pro views read only Pro rows ----------------------------------------------------------------------------

def test_pro_views_read_only_pro_rows(pro, api_db):
    from api.tests.test_pro_api import at
    from core import scoreboard
    from core.db import connect
    from evaluation import calibration, report
    from trading import scheduler as SCH

    c = pro["client"]
    at(pro, 2026, 3, 3, 21, 45)                                    # one Pro job → one signal
    sid = c.get("/api/pro/signals").json()["signals"][0]["id"]
    with connect() as conn:
        exp = conn.execute("SELECT experiment_id FROM predictions WHERE source = 'pro'").fetchone()[0]
        conn.execute("UPDATE predictions SET actual_price = price_now * 1.01 WHERE source = 'pro'")

    def pro_view():
        o = c.get("/api/pro/overview").json()
        d = c.get(f"/api/pro/signals/{sid}").json()
        return {
            "overview": {k: o[k] for k in ("spent_today_thb", "pro_spent_total_thb", "signal_counts")},
            "signals": c.get("/api/pro/signals").json(),
            "detail_pred": d.get("prediction"), "replay_url": "replay_url" in d,
            "report_preds": [(p.ticker, a) for p, a in report._pro_predictions(exp)],
            "calib": calibration.evaluated_rows("swing", "stock"),
            "est_s": SCH.estimate_s("gemini-3.5-flash-lite", None),
            "spent_day": SCH.spent_today_thb(datetime(2026, 3, 3, 23, tzinfo=UTC)),
        }

    before = pro_view()
    assert before["report_preds"] and before["calib"] and before["replay_url"] is False

    # Arena rows: same ticker, mode, asset class, model, a cost and an outcome, created on the same day
    arena = scoreboard.load(api_db["ids"][0])
    for k in range(3):
        q = _clone(arena, ticker="AAA", asset_type="stock", created_at="2026-03-03T22:00:00+00:00",
                   cost_usd=5.0, elapsed_s=777.0, models={"analyst": "gemini-3.5-flash-lite"}, experiment_id=exp)
        scoreboard.save(q, f"arena-x{k}", "web")
    with connect() as conn:
        conn.execute("UPDATE predictions SET actual_price = price_now * 2 WHERE source = 'web'")
    assert pro_view() == before
    with connect() as conn:                                       # a signal wrongly linked to an Arena row
        arena_id = conn.execute("SELECT id FROM predictions WHERE source = 'web' LIMIT 1").fetchone()[0]
        conn.execute("UPDATE signals SET prediction_id = ? WHERE id = ?", (arena_id, sid))
    assert "prediction" not in c.get(f"/api/pro/signals/{sid}").json()   # never renders an Arena prediction
    # the shared budget cap still sees both worlds
    o = c.get("/api/pro/overview").json()
    assert o["combined_spent_thb"] >= o["pro_spent_total_thb"]


# --- 4: guard -----------------------------------------------------------------------------------------------------

READ = re.compile(r"\b(FROM|JOIN|UPDATE)\s+predictions\b")
OK = re.compile(r"ARENA_ONLY|PRO_ONLY|arena_only\(|pro_only\(|only\(world|# any-world")
SKIP_DIRS = {".venv", "node_modules", "tests", "__pycache__", "web", "data", "docs"}


def test_every_predictions_query_declares_its_world():
    """A query reading `predictions` must carry a world filter within its statement (the line before, this line
    or the next 3) or a `# any-world: <reason>` marker. Add the filter from core/worlds.py; don't just add the
    marker."""
    bad = []
    for f in ROOT.rglob("*.py"):
        rel = f.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS or rel.parts[0] == "scratchpad":
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if READ.search(line) and not line.lstrip().startswith("#") and '"""' not in line:
                window = "\n".join(lines[max(0, i - 1): i + 4])
                if not OK.search(window):
                    bad.append(f"{rel}:{i + 1}: {line.strip()}")
    assert not bad, "queries on `predictions` without a world filter (core/worlds.py):\n" + "\n".join(bad)


def test_guard_catches_an_unfiltered_query(tmp_path):
    assert READ.search('rows = conn.execute("SELECT * FROM predictions WHERE id > 3")')
    assert not OK.search('rows = conn.execute("SELECT * FROM predictions WHERE id > 3")')
    assert OK.search('f"SELECT * FROM predictions WHERE {ARENA_ONLY}"')


def test_arena_prediction_json_has_no_pro_fields_and_round_trips(api_db):
    """Pro fields at their defaults are not serialised (Arena JSON = main's); Pro values survive a round trip."""
    from core import scoreboard
    from core.schemas import Catalyst, KeyLevels, Prediction, ScenarioProbs

    p = scoreboard.load(api_db["ids"][0])
    d = json.loads(p.model_dump_json())
    for k in ("prompt_version", "temperature", "experiment_id", "degraded", "as_of"):
        assert k not in d
    for k in ("thesis", "invalidation", "catalysts", "no_trade_reason"):
        assert k not in d["verdict"]
    for r in d["reports"]:
        assert not {"scenario_probs", "key_levels", "invalidation"} & set(r)
    assert Prediction.model_validate(d) == p

    q = p.model_copy(deep=True)
    q.prompt_version, q.temperature, q.experiment_id, q.degraded = "v2", 0.2, 7, ["stale_price"]
    q.verdict.thesis, q.verdict.catalysts = "t", [Catalyst(date="2026-01-02", event="CPI")]
    q.reports[0].scenario_probs = ScenarioProbs(bull=0.5, base=0.3, bear=0.2)
    q.reports[0].key_levels = KeyLevels(support=[1.0])
    back = Prediction.model_validate_json(q.model_dump_json())
    assert back == q and back.prompt_version == "v2" and back.reports[0].key_levels.support == [1.0]
