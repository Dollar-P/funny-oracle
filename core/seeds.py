"""Demo seed predictions: export from a local DB to `data/seeds.json`, load into a fresh one.

`data/oracle.db` is git-ignored, so a fresh clone starts empty. The seeds (real, stored predictions) let mock
mode and `/p/<id>` replays work without any API call. Original ids are kept so links like `/p/10` match the
README. Loading is idempotent: ids that already exist are skipped, never overwritten.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from core.db import connect

SEEDS_PATH = config.ROOT / "data" / "seeds.json"
FORMAT = 1
PRED_COLS = ("id", "run_id", "created_at", "ticker", "asset_type", "mode", "horizon", "target_time", "price_now",
             "p10", "p50", "p90", "prob_up", "s_raw", "s_final", "confidence", "headline", "cost_usd", "source",
             "prediction_json", "actual_price", "evaluated_at", "analyst_model", "judge_model", "duel_id",
             "cost_actual_usd")
AGENT_COLS = ("prediction_id", "agent", "score", "confidence", "reasoning", "trace_json", "error")


def export(ids: list[int], path: Path = SEEDS_PATH) -> dict:
    with connect() as conn:
        marks = ",".join("?" * len(ids))
        preds = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(PRED_COLS)} FROM predictions WHERE id IN ({marks}) ORDER BY id", ids)]
        agents = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(AGENT_COLS)} FROM agent_outputs WHERE prediction_id IN ({marks})"
            " ORDER BY prediction_id, rowid", ids)]
    missing = sorted(set(ids) - {p["id"] for p in preds})
    if missing:
        raise ValueError(f"predictions not found: {missing}")
    for p in preds:  # store the full Prediction as an object, not a JSON string inside JSON
        p["prediction"] = json.loads(p.pop("prediction_json"))
    doc = {
        "format": FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Demo seed predictions for mock mode and /p/<id> replays. For fun - not investment advice.",
        "predictions": preds,
        "agent_outputs": agents,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"predictions": len(preds), "agent_outputs": len(agents), "path": str(path)}


def load(path: Path = SEEDS_PATH) -> dict:
    """Insert seeds whose id is free. Returns {'loaded': [...ids], 'skipped': [...ids]}."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"unsupported seeds format {doc.get('format')!r}")
    loaded, skipped = [], []
    with connect() as conn:
        taken = {r[0] for r in conn.execute("SELECT id FROM predictions")}
        runs = {r[0] for r in conn.execute("SELECT run_id FROM predictions WHERE run_id IS NOT NULL")}
        for p in doc["predictions"]:
            if p["id"] in taken or (p.get("run_id") and p["run_id"] in runs):
                skipped.append(p["id"])
                continue
            row = {**p, "prediction_json": json.dumps(p["prediction"], ensure_ascii=False)}
            conn.execute(f"INSERT INTO predictions ({', '.join(PRED_COLS)}) VALUES ({', '.join('?' * len(PRED_COLS))})",
                         [row.get(c) for c in PRED_COLS])
            conn.executemany(
                f"INSERT INTO agent_outputs ({', '.join(AGENT_COLS)}) VALUES ({', '.join('?' * len(AGENT_COLS))})",
                [[a.get(c) for c in AGENT_COLS] for a in doc["agent_outputs"] if a["prediction_id"] == p["id"]])
            loaded.append(p["id"])
    return {"loaded": loaded, "skipped": skipped}


def load_if_empty(path: Path = SEEDS_PATH) -> dict | None:
    """Load the seeds only into a DB without any prediction (fresh clone). Never touches a DB in use."""
    if not Path(path).exists():
        return None
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    return load(path) if n == 0 else None
