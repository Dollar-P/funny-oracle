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

# Pro tables (PRO.md §9), additive. One paper account per experiment: every ledger table carries experiment_id.
# Money columns hold values already rounded to cents by trading/ledger.py (Decimal → float); the ledger
# re-reads them as Decimal(repr(x)) so reconciliation stays exact.
PRO_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_exp_hash ON experiments(config_hash);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_id INTEGER REFERENCES predictions(id),
    experiment_id INTEGER REFERENCES experiments(id), created_at TEXT NOT NULL, ticker TEXT NOT NULL,
    asset_type TEXT, mode TEXT, horizon TEXT, direction TEXT NOT NULL, conviction REAL, entry_json TEXT,
    stop REAL, targets_json TEXT, time_exit TEXT, reward_risk REAL, ev_r REAL, size_json TEXT, thesis TEXT,
    invalidation TEXT, flat_reason TEXT, status TEXT NOT NULL, status_reason TEXT, plan_json TEXT,
    updated_at TEXT, decision_bar TEXT
);
CREATE INDEX IF NOT EXISTS ix_sig_exp ON signals(experiment_id, status);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER REFERENCES signals(id),
    experiment_id INTEGER REFERENCES experiments(id), created_at TEXT NOT NULL, ticker TEXT, type TEXT,
    role TEXT, side TEXT, qty REAL, limit_price REAL, stop_price REAL, status TEXT, oco_group TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER REFERENCES orders(id),
    experiment_id INTEGER REFERENCES experiments(id), position_id INTEGER, ticker TEXT, side TEXT, ts TEXT,
    price REAL, qty REAL, fee REAL, slippage REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER REFERENCES signals(id),
    experiment_id INTEGER REFERENCES experiments(id), ticker TEXT, asset_type TEXT, opened_at TEXT,
    closed_at TEXT, side TEXT, qty REAL, qty_open REAL, avg_entry REAL, avg_exit REAL, stop REAL,
    targets_json TEXT, time_exit TEXT, risk_per_unit REAL, realized_pnl REAL, r_multiple REAL, mae REAL,
    mfe REAL, costs REAL, exit_reason TEXT, review TEXT, tags TEXT, state_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_pos_exp ON positions(experiment_id, closed_at);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER REFERENCES experiments(id), ts TEXT NOT NULL,
    equity REAL, cash REAL, gross_exposure REAL, open_risk REAL, drawdown REAL
);
CREATE INDEX IF NOT EXISTS ix_eq_exp ON equity_snapshots(experiment_id, ts);
CREATE TABLE IF NOT EXISTS tool_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tool TEXT, args_hash TEXT, as_of TEXT, payload_zlib BLOB
);
CREATE INDEX IF NOT EXISTS ix_snap_run ON tool_snapshots(run_id);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER, ts TEXT NOT NULL,
    signal_id INTEGER NULL, rule TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT, config_json TEXT, period TEXT,
    status TEXT, result_json TEXT, progress REAL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, experiment_id INTEGER, period TEXT,
    result_json TEXT, markdown TEXT
);
CREATE TABLE IF NOT EXISTS pro_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS scheduler_runs (
    job_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, ticker TEXT, mode TEXT,
    horizon TEXT, model TEXT, think_preset TEXT, prediction_id INTEGER, signal_id INTEGER, detail TEXT,
    finished_at TEXT, experiment_id INTEGER
);
"""

_initialized: set[str] = set()
ANALYST_AGENTS = ("business", "technical", "valuation", "macro", "psychology", "news_researcher")

# Additive columns (REDESIGN): per-run models and duel grouping.
MIGRATIONS = {
    "predictions": [("analyst_model", "TEXT"), ("judge_model", "TEXT"), ("duel_id", "TEXT"),
                    ("cost_actual_usd", "REAL"), ("prompt_version", "TEXT"), ("experiment_id", "INTEGER")],
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
                conn.executescript(PRO_SCHEMA)
                _migrate(conn)
                _initialized.add(str(path))
            yield conn
            conn.commit()
        finally:
            conn.close()
