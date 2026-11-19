"""Scoreboard (§4.8): persist predictions, evaluate them once the horizon has passed, score accuracy."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from core import cost
from core.db import connect
from core.schemas import Prediction

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- persistence ---------------------------------------------------------------

def save(pred: Prediction, run_id: str, source: str = "manual") -> int:
    f = pred.forecast
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO predictions (run_id, created_at, ticker, asset_type, mode, horizon, target_time, price_now,"
            " p10, p50, p90, prob_up, s_raw, s_final, confidence, headline, cost_usd, source, prediction_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, pred.created_at, pred.ticker, pred.asset_type, pred.mode, pred.horizon, pred.target_time,
             f.price_now, f.p10, f.p50, f.p90, f.prob_up, pred.s_raw, pred.s_final, pred.confidence,
             pred.verdict.headline if pred.verdict else None, pred.cost_usd, source, "{}"),
        )
        pid = int(cur.lastrowid)
        pred.id = pid
        conn.execute("UPDATE predictions SET prediction_json = ?, analyst_model = ?, judge_model = ?, duel_id = ?,"
                     " cost_actual_usd = ? WHERE id = ?",
                     (pred.model_dump_json(), pred.models.get("analyst"), pred.models.get("judge"), pred.duel_id,
                      pred.cost_actual_usd, pid))
        conn.executemany(
            "INSERT INTO agent_outputs (prediction_id, agent, score, confidence, reasoning, trace_json, error)"
            " VALUES (?,?,?,?,?,?,?)",
            [(pid, r.agent, r.score, r.confidence, r.reasoning,
              json.dumps([t.model_dump() for t in r.trace]), r.error) for r in pred.reports],
        )
    cost.link_run(run_id, pid)
    return pid


def load(pid: int) -> Prediction | None:
    with connect() as conn:
        row = conn.execute("SELECT prediction_json, actual_price FROM predictions WHERE id = ?", (pid,)).fetchone()
    return Prediction.model_validate_json(row[0]) if row and row[0] else None


def recent(limit: int = 50) -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT id, created_at, ticker, mode, horizon, target_time, price_now, p10, p50, p90, prob_up,"
            " s_final, confidence, headline, cost_usd, source, actual_price, evaluated_at"
            " FROM predictions ORDER BY id DESC LIMIT ?", conn, params=(limit,))


# --- evaluation ------------------------------------------------------------------

def _actual_from_bars(df: pd.DataFrame, target: datetime) -> float | None:
    """Close of the last bar starting at or before `target`, if data extends past target."""
    if df.empty or df.index[-1] < target:
        return None
    before = df[df.index <= target]
    return None if before.empty else float(before["Close"].iloc[-1])


def actual_price(ticker: str, mode: str, horizon: str, target: datetime) -> float | None:
    from mcp_server import data_sources as ds

    interval = config.HORIZONS[mode][horizon]["interval"]
    age = utcnow() - target
    if interval == "1d":
        if age < timedelta(days=1):  # wait until the target day's bar is final
            return None
        df = ds.fetch_ohlcv(ticker, "1d", "2y")
        days = df.index.date
        if days[-1] < target.date() and age < timedelta(days=4):
            return None  # data hasn't caught up yet (weekend/holiday tolerance below)
        on_or_before = df[days <= target.date()]
        return None if on_or_before.empty else float(on_or_before["Close"].iloc[-1])
    # intraday: yfinance keeps 1m for ~7 days, 5m/15m for ~60 days
    candidates = [(interval, config.DEFAULT_PERIOD.get(interval, "5d"))]
    if interval == "1m":
        candidates.append(("5m", "1mo"))
    for iv, period in candidates:
        if iv == "1m" and age > timedelta(days=6):
            continue
        try:
            df = ds.fetch_ohlcv(ticker, iv, period)
        except Exception as e:
            log.warning("evaluation fetch failed %s %s: %s", ticker, iv, e)
            continue
        px = _actual_from_bars(df, target)
        if px is not None:
            return px
    return None


def evaluate_due(max_items: int = 50) -> int:
    """Fill actual_price for predictions whose target_time has passed. Returns count evaluated."""
    now = utcnow()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, mode, horizon, target_time FROM predictions"
            " WHERE actual_price IS NULL AND target_time <= ? ORDER BY target_time LIMIT ?",
            (now.isoformat(), max_items),
        ).fetchall()
    n = 0
    for r in rows:
        try:
            px = actual_price(r["ticker"], r["mode"], r["horizon"], datetime.fromisoformat(r["target_time"]))
        except Exception as e:
            log.warning("evaluate %s failed: %s", r["id"], e)
            continue
        if px is None:
            continue
        with connect() as conn:
            conn.execute("UPDATE predictions SET actual_price = ?, evaluated_at = ? WHERE id = ?",
                         (px, now.isoformat(timespec="seconds"), r["id"]))
        n += 1
    return n


def metrics(mode: str | None = None) -> dict:
    q = "SELECT * FROM predictions WHERE actual_price IS NOT NULL"
    params: tuple = ()
    if mode:
        q += " AND mode = ?"
        params = (mode,)
    with connect() as conn:
        df = pd.read_sql_query(q, conn, params=params)
        ag = pd.read_sql_query(
            "SELECT a.agent, a.score, p.price_now, p.actual_price FROM agent_outputs a"
            " JOIN predictions p ON p.id = a.prediction_id"
            " WHERE p.actual_price IS NOT NULL AND a.error IS NULL AND a.score != 0"
            + (" AND p.mode = ?" if mode else ""), conn, params=params)
        pending = conn.execute("SELECT COUNT(*) FROM predictions WHERE actual_price IS NULL").fetchone()[0]
    out: dict = {"n": len(df), "pending": int(pending)}
    if df.empty:
        return out
    actual_up = df["actual_price"] > df["price_now"]
    called = df[(df["prob_up"] - 0.5).abs() > 1e-9]
    if len(called):
        pred_up = called["prob_up"] > 0.5
        out["hit_rate"] = float((pred_up == (called["actual_price"] > called["price_now"])).mean())
        out["n_directional"] = len(called)
    out["band_coverage"] = float(((df["actual_price"] >= df["p10"]) & (df["actual_price"] <= df["p90"])).mean())
    out["mape"] = float(((df["p50"] - df["actual_price"]).abs() / df["actual_price"]).mean())
    out["baseline_hit_rate"] = 0.5
    out["actual_up_share"] = float(actual_up.mean())
    if not ag.empty:
        ag["hit"] = (ag["score"] > 0) == (ag["actual_price"] > ag["price_now"])
        lb = ag.groupby("agent")["hit"].agg(["mean", "count"]).reset_index()
        lb.columns = ["agent", "hit_rate", "n"]
        out["leaderboard"] = lb.sort_values(["hit_rate", "n"], ascending=False).to_dict("records")
    return out
