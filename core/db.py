"""SQLite connection + schema (§4.8). One file: data/oracle.db (or $ORACLE_DB)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    horizon TEXT NOT NULL,
    target_time TEXT NOT NULL,
    price_now REAL, p10 REAL, p50 REAL, p90 REAL, prob_up REAL,
    s_raw REAL, s_final REAL, confidence REAL, headline TEXT,
    cost_usd REAL DEFAULT 0,
    source TEXT DEFAULT 'manual',          -- manual | auto_scalp | cli
    prediction_json TEXT,                   -- full Prediction for replay in the UI
    actual_price REAL, evaluated_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_outputs (
    prediction_id INTEGER REFERENCES predictions(id),
    agent TEXT, score REAL, confidence REAL, reasoning TEXT, trace_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER REFERENCES predictions(id),
    run_id TEXT,                            -- links usage to a prediction before it is saved
    created_at TEXT NOT NULL,
    agent TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, thinking_tokens INTEGER,
    search_calls INTEGER, cost_usd REAL
);
CREATE INDEX IF NOT EXISTS ix_usage_run ON llm_usage(run_id);
CREATE INDEX IF NOT EXISTS ix_pred_target ON predictions(target_time, actual_price);
"""

_initialized: set[str] = set()
ANALYST_AGENTS = ("business", "technical", "valuation", "macro", "psychology", "news_researcher")

# Additive columns (REDESIGN): per-run models and duel grouping.
MIGRATIONS = {
    "predictions": [("analyst_model", "TEXT"), ("judge_model", "TEXT"), ("duel_id", "TEXT"),
                    ("cost_actual_usd", "REAL")],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, typ in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_pred_duel ON predictions(duel_id)")
    # Backfill models for rows saved before the columns existed: most-used model in llm_usage.
    for col, agents in (("analyst_model", ANALYST_AGENTS), ("judge_model", ("judge",))):
        marks = ",".join("?" * len(agents))
        conn.execute(
            f"UPDATE predictions SET {col} = (SELECT u.model FROM llm_usage u WHERE u.prediction_id = predictions.id"
            f" AND u.agent IN ({marks}) GROUP BY u.model ORDER BY COUNT(*) DESC LIMIT 1) WHERE {col} IS NULL",
            agents)


def db_path() -> Path:
    return Path(config.DB_PATH)


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            if str(path) not in _initialized:
                conn.executescript(SCHEMA)
                _migrate(conn)
                _initialized.add(str(path))
            yield conn
            conn.commit()
        finally:
            conn.close()
