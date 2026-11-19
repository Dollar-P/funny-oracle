"""Tracked vs estimated-actual cost: Gemini search free tier (first N queries per UTC month)."""
from __future__ import annotations

import pytest

import config
from core import cost
from core.cost import BudgetExceeded, BudgetGuard, Usage
from core.db import connect

MODEL = "gemini-3.5-flash-lite"
S = config.GEMINI_SEARCH_USD


def _row(created_at, model=MODEL, searches=0, run_id="r", tokens_in=0):
    u = Usage(model, input_tokens=tokens_in, search_calls=searches)
    c = cost.cost_usd(u)
    with connect() as conn:
        conn.execute("INSERT INTO llm_usage (run_id, created_at, agent, model, input_tokens, output_tokens,"
                     " thinking_tokens, search_calls, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
                     (run_id, created_at, "a", model, tokens_in, 0, 0, searches, c))
    return c


def test_searches_within_free_tier_cost_nothing_actual(fresh_db):
    _row("2026-11-01T00:00:00+00:00", searches=3, tokens_in=1_000_000, run_id="a")
    assert cost.total_spent_usd() == pytest.approx(0.30 + 3 * S)      # tracked: list price
    assert cost.total_actual_usd() == pytest.approx(0.30)             # actual: tokens only
    assert cost.run_cost_actual("a") == pytest.approx(0.30)


def test_free_tier_is_per_month_and_chronological(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_SEARCH_FREE_PER_MONTH", 5)
    _row("2026-10-30T00:00:00+00:00", searches=4, run_id="oct")      # 4 of Oct's 5 free
    _row("2026-11-01T00:00:00+00:00", searches=4, run_id="nov1")     # 4 of Nov's 5 free
    _row("2026-11-02T00:00:00+00:00", searches=3, run_id="nov2")     # 1 free, 2 billable
    _row("2026-11-03T00:00:00+00:00", searches=2, run_id="nov3")     # 0 free
    assert cost.run_cost_actual("oct") == pytest.approx(0)
    assert cost.run_cost_actual("nov1") == pytest.approx(0)
    assert cost.run_cost_actual("nov2") == pytest.approx(2 * S)
    assert cost.run_cost_actual("nov3") == pytest.approx(2 * S)
    assert cost.total_spent_usd() == pytest.approx(13 * S)
    assert cost.total_actual_usd() == pytest.approx(4 * S)


def test_claude_searches_are_never_free(fresh_db):
    _row("2026-11-01T00:00:00+00:00", model="claude-haiku-5-5", searches=2, run_id="c")
    assert cost.total_actual_usd() == pytest.approx(2 * config.CLAUDE_SEARCH_USD)


def test_spend_summary_shows_both(fresh_db, monkeypatch):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _row(now, searches=2)
    s = cost.spend_summary()
    assert s["tracked_usd"] == pytest.approx(2 * S) and s["estimated_actual_usd"] == pytest.approx(0)
    assert s["gemini_searches_this_month"] == 2
    assert s["gemini_free_searches_left"] == config.GEMINI_SEARCH_FREE_PER_MONTH - 2


def test_budget_basis(fresh_db, monkeypatch):
    _row("2026-11-01T00:00:00+00:00", searches=100)                 # tracked $1.40, actual $0
    g = BudgetGuard(budget_thb=1.0 * config.USD_THB)                # $1 budget
    monkeypatch.setattr(config, "BUDGET_BASIS", "tracked")
    with pytest.raises(BudgetExceeded):
        g.check_call()
    monkeypatch.setattr(config, "BUDGET_BASIS", "actual")
    g.check_call()                                                   # free tier → still under budget
    assert g.remaining_usd() == pytest.approx(1.0)


def test_agent_timeout_default_is_60(monkeypatch):
    import importlib

    monkeypatch.delenv("AGENT_TIMEOUT_S", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore the real .env
    fresh = importlib.reload(config)
    try:
        assert fresh.AGENT_TIMEOUT_S == 60.0
    finally:
        monkeypatch.undo()          # restore load_dotenv + env before re-reading the real config
        importlib.reload(config)
