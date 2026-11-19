"""Cost tracker & budget guard (§4.10).

- `price_for(model)`      → per-1M-token prices from config.PRICES
- `cost_usd(usage)`       → USD for one LLM call
- `record(...)`           → insert a row into llm_usage
- `BudgetGuard.check_prediction(mode)` → raise BudgetExceeded before a prediction
- `BudgetGuard.check_call()`           → raise BudgetExceeded before each LLM call

Two figures are kept:
- **tracked**: every search query at list price (what llm_usage.cost_usd stores; conservative).
- **estimated actual**: the first `GEMINI_SEARCH_FREE_PER_MONTH` Gemini search queries of each
  calendar month (UTC) are free, allocated in call order. Token costs are identical.
The budget guard uses tracked unless `BUDGET_BASIS=actual`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import config
from core.db import connect

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    """Raised instead of calling the API when the budget would be exceeded."""


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    search_calls: int = 0

    def __add__(self, o: "Usage") -> "Usage":
        return Usage(self.model, self.input_tokens + o.input_tokens, self.output_tokens + o.output_tokens,
                     self.thinking_tokens + o.thinking_tokens, self.search_calls + o.search_calls)


_warned: set[str] = set()


def price_for(model: str, on: date | None = None) -> dict:
    on = on or datetime.now(timezone.utc).date()
    key = model.removeprefix("models/")
    entries = config.PRICES.get(key)
    if not entries:
        # tolerate suffixed IDs like "gemini-3.8-flash-preview-11-2026"
        for k, v in config.PRICES.items():
            if key.startswith(k):
                entries = v
                break
    if not entries:
        if key not in _warned:
            log.warning("no price for model %r; using pessimistic fallback price", key)
            _warned.add(key)
        return config.FALLBACK_PRICE
    for e in entries:
        if e["until"] is None or on <= e["until"]:
            return e
    return entries[-1]


def cost_usd(u: Usage, on: date | None = None) -> float:
    p = price_for(u.model, on)
    return (
        u.input_tokens * p["in"] / 1e6
        + (u.output_tokens + u.thinking_tokens) * p["out"] / 1e6
        + u.search_calls * p["search"]
    )


def record(u: Usage, agent: str, run_id: str | None = None) -> float:
    c = cost_usd(u)
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_usage (run_id, created_at, agent, model, input_tokens, output_tokens, thinking_tokens,"
            " search_calls, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), agent, u.model,
             u.input_tokens, u.output_tokens, u.thinking_tokens, u.search_calls, c),
        )
    return c


def link_run(run_id: str, prediction_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE llm_usage SET prediction_id = ? WHERE run_id = ?", (prediction_id, run_id))


def run_cost(run_id: str) -> float:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage WHERE run_id = ?", (run_id,)).fetchone()
    return float(row[0])


def total_spent_usd() -> float:
    with connect() as conn:
        return float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage").fetchone()[0])


def _is_gemini(model: str) -> bool:
    return (model or "").removeprefix("models/").startswith("gemini")


def _actual_rows() -> list[tuple[int, str | None, float, float, int, int]]:
    """Every llm_usage row as (id, run_id, tracked_usd, actual_usd, search_calls, free_searches).

    Free searches are allocated chronologically within each UTC month across all Gemini models
    (Google's free tier is shared across Gemini 3+ models).
    """
    with connect() as conn:
        rows = conn.execute("SELECT id, run_id, created_at, model, search_calls, cost_usd FROM llm_usage"
                            " ORDER BY created_at, id").fetchall()
    used: dict[str, int] = {}
    out = []
    for r in rows:
        tracked = float(r["cost_usd"] or 0)
        n = int(r["search_calls"] or 0)
        free = 0
        if n and _is_gemini(r["model"]):
            month = (r["created_at"] or "")[:7]
            left = max(0, config.GEMINI_SEARCH_FREE_PER_MONTH - used.get(month, 0))
            free = min(n, left)
            used[month] = used.get(month, 0) + n
        on = None
        try:
            on = datetime.fromisoformat(r["created_at"]).date()
        except (TypeError, ValueError):
            pass
        actual = max(0.0, tracked - free * price_for(r["model"] or "", on)["search"]) if free else tracked
        out.append((int(r["id"]), r["run_id"], tracked, actual, n, free))
    return out


def total_actual_usd() -> float:
    return sum(r[3] for r in _actual_rows())


def run_cost_actual(run_id: str) -> float:
    return sum(r[3] for r in _actual_rows() if r[1] == run_id)


def spent_usd(basis: str | None = None) -> float:
    """Spend on the configured budget basis (tracked | actual)."""
    return total_actual_usd() if (basis or config.BUDGET_BASIS) == "actual" else total_spent_usd()


def searches_this_month(now: datetime | None = None) -> int:
    month = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
    with connect() as conn:
        rows = conn.execute("SELECT model, COALESCE(SUM(search_calls),0) s FROM llm_usage"
                            " WHERE substr(created_at,1,7) = ? GROUP BY model", (month,)).fetchall()
    return sum(int(r["s"]) for r in rows if _is_gemini(r["model"]))


def usd_to_thb(usd: float) -> float:
    return usd * config.USD_THB


def spend_summary() -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT model, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, SUM(thinking_tokens) t,"
            " SUM(search_calls) s, SUM(cost_usd) c FROM llm_usage GROUP BY model"
        ).fetchall()
        n_pred = conn.execute("SELECT mode, COUNT(*), AVG(cost_usd) FROM predictions GROUP BY mode").fetchall()
    spent = sum(r["c"] or 0 for r in rows)
    actual = total_actual_usd()
    month_searches = searches_this_month()
    return {
        "spent_usd": spent,
        "spent_thb": usd_to_thb(spent),
        "tracked_usd": spent,
        "tracked_thb": usd_to_thb(spent),
        "estimated_actual_usd": actual,
        "estimated_actual_thb": usd_to_thb(actual),
        "budget_basis": config.BUDGET_BASIS,
        "gemini_searches_this_month": month_searches,
        "gemini_free_searches_left": max(0, config.GEMINI_SEARCH_FREE_PER_MONTH - month_searches),
        "budget_thb": config.BUDGET_THB,
        "by_model": [dict(r) for r in rows],
        "predictions": {r[0]: {"count": r[1], "avg_cost_usd": r[2]} for r in n_pred},
    }


class BudgetGuard:
    def __init__(self, budget_thb: float | None = None):
        self.budget_thb = config.BUDGET_THB if budget_thb is None else budget_thb

    @property
    def budget_usd(self) -> float:
        return self.budget_thb / config.USD_THB

    def estimate_usd(self, mode: str) -> float:
        """Observed average of the last 10 predictions of this mode × 1.25, else a default."""
        with connect() as conn:
            row = conn.execute(
                "SELECT AVG(cost_usd), COUNT(*) FROM (SELECT cost_usd FROM predictions WHERE mode = ? AND cost_usd > 0"
                " ORDER BY id DESC LIMIT 10)", (mode,)
            ).fetchone()
        if row and row[1]:
            return max(float(row[0]) * 1.25, 0.001)
        return config.DEFAULT_ESTIMATE_USD.get(mode, 0.2)

    def remaining_usd(self) -> float:
        return self.budget_usd - spent_usd()

    def check_prediction(self, mode: str) -> float:
        spent = spent_usd()
        est = self.estimate_usd(mode)
        if spent + est > self.budget_usd:
            raise BudgetExceeded(
                f"Budget guard: spent ฿{usd_to_thb(spent):.2f} + estimated ฿{usd_to_thb(est):.2f} for this "
                f"{mode} prediction would exceed the ฿{self.budget_thb:.0f} budget. Raise BUDGET_THB in .env "
                f"or replay a stored prediction instead."
            )
        return est

    def check_call(self) -> None:
        spent = spent_usd()
        if spent >= self.budget_usd:
            raise BudgetExceeded(f"Budget guard: ฿{usd_to_thb(spent):.2f} spent, budget ฿{self.budget_thb:.0f} reached.")
