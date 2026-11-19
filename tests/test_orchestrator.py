"""End-to-end pipeline + scoreboard with a role-aware fake Gemini (no network, no API)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import anyio
import numpy as np
import pandas as pd
import pytest
from google.genai import errors

import config
import orchestrator
from core import cost, scoreboard
from core.cost import BudgetExceeded, BudgetGuard
from core.llm import LLM
from tests.fakes import FakeExecutor, fn_call_response, text_response

SCORES = {"business": 0.5, "technical": -0.4, "valuation": 0.2, "macro": 0.1, "psychology": 0.6}


class RoleFake:
    """Answers by inspecting the system prompt, so parallel agents get the right reply."""

    def __init__(self, fail_agents=(), judge_adj=0.15, search_ok=True):
        self.fail_agents = set(fail_agents)
        self.judge_adj = judge_adj
        self.search_ok = search_ok
        self.requests = []
        from types import SimpleNamespace

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

    async def _gen(self, model, contents, config):
        self.requests.append((model, contents, config))
        sys_ = (config.system_instruction or "") if config else ""
        if isinstance(contents, str):  # capability probe
            if not self.search_ok:
                raise errors.APIError(400, {"error": {"message": "unsupported", "status": "INVALID_ARGUMENT"}})
            return text_response("OK", prompt=5, out=1, thoughts=0)
        if "JUDGE" in sys_:
            return text_response(json.dumps({"adjustment": self.judge_adj, "final_confidence": 0.55,
                                             "headline": "Bulls win by a whisker", "summary": "Close call.",
                                             "key_risks": ["macro", "valuation"]}))
        for side in ("BULL", "BEAR"):
            if f"You are the {side}" in sys_:
                return text_response(json.dumps({"side": side.lower(), "argument": f"{side} case",
                                                 "strongest_opponent_point": "x"}))
        if "news researcher" in sys_:
            return text_response(json.dumps({"bullets": ["News A"], "links": []}))
        role = next(a for a in SCORES if f"senior {a}" in sys_ or
                    (a == "business" and "business / fundamental" in sys_) or
                    (a == "psychology" and "market psychology" in sys_))
        if role in self.fail_agents:
            return text_response("garbage")
        has_fn_response = any(p.function_response for c in contents for p in (c.parts or []))
        if not has_fn_response:
            tool = config.tools[0].function_declarations[0].name
            return fn_call_response((tool, {"ticker": "NVDA"}))
        return text_response(json.dumps({"score": SCORES[role], "confidence": 0.8,
                                          "key_points": ["a", "b", "c"], "reasoning": "r", "sources": []}))


@pytest.fixture()
def synthetic_prices(monkeypatch):
    def fake_fetch(ticker, interval="1d", period=None):
        rng = np.random.default_rng(0)
        n = 260
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        idx = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="D")
        return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1}, index=idx)

    monkeypatch.setattr("mcp_server.data_sources.fetch_ohlcv", fake_fetch)


def run_pred(fake, ticker="NVDA", mode="swing", horizon="1M", **kw):
    llm = LLM("gemini", client=fake)
    return anyio.run(lambda: orchestrator.run_prediction(ticker, mode, horizon, llm=llm, executor=FakeExecutor(),
                                                         seed=7, **kw))


def test_swing_pipeline_end_to_end(fresh_db, synthetic_prices):
    fake = RoleFake()
    p = run_pred(fake)
    assert p.id is not None and p.search_mode == "grounded"
    assert {r.agent for r in p.reports} == set(config.AGENTS) and all(r.ok for r in p.reports)
    w = config.WEIGHTS["swing"]["stock"]
    expected = sum(w[a] * s * 0.8 for a, s in SCORES.items()) / sum(w[a] * 0.8 for a in SCORES)
    assert p.s_raw == pytest.approx(expected)
    assert p.s_final == pytest.approx(expected + 0.15)
    assert {d.side for d in p.debate} == {"bull", "bear"}
    assert p.verdict.headline == "Bulls win by a whisker"
    assert p.forecast.p10 < p.forecast.p50 < p.forecast.p90
    assert p.cost_usd > 0
    # persisted and reloadable
    again = scoreboard.load(p.id)
    assert again.verdict.headline == p.verdict.headline and len(again.reports) == 5
    # usage rows linked to the prediction
    from core.db import connect

    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM llm_usage WHERE prediction_id = ?", (p.id,)).fetchone()[0]
    assert n >= 5 + 5 + 2 + 1  # 2 turns × 5 analysts (≥), bull, bear, judge


def test_failed_agent_is_dropped_and_weights_renormalize(fresh_db, synthetic_prices):
    p = run_pred(RoleFake(fail_agents={"valuation", "business"}, judge_adj=0.0))
    absent = {r.agent for r in p.reports if not r.ok}
    assert absent == {"valuation", "business"}
    w = config.WEIGHTS["swing"]["stock"]
    rest = {a: s for a, s in SCORES.items() if a not in absent}
    expected = sum(w[a] * s for a, s in rest.items()) / sum(w[a] for a in rest)
    assert p.s_raw == pytest.approx(expected)


def test_judge_adjustment_is_capped(fresh_db, synthetic_prices):
    p = run_pred(RoleFake(judge_adj=0.9))
    assert p.verdict.adjustment == pytest.approx(0.2)


def test_scalp_skips_debate_and_business_valuation(fresh_db, synthetic_prices):
    p = run_pred(RoleFake(), ticker="BTC-USD", mode="scalp", horizon="15m")
    assert {r.agent for r in p.reports} == {"technical", "macro", "psychology"}
    assert p.debate == [] and p.verdict is not None and p.search_mode == "off"
    assert p.forecast.steps == 15


def test_no_search_support_uses_news_researcher(fresh_db, synthetic_prices):
    fake = RoleFake(search_ok=False)
    p = run_pred(fake)
    assert p.search_mode == "news_tool" and p.news.bullets == ["News A"]
    analyst_cfgs = [c for _, _, c in fake.requests if c is not None and "senior" in (c.system_instruction or "")]
    assert analyst_cfgs and not any(t.google_search for c in analyst_cfgs for t in (c.tools or []))


def test_budget_guard_blocks_before_any_call(fresh_db, synthetic_prices):
    fake = RoleFake()
    llm = LLM("gemini", client=fake, guard=BudgetGuard(budget_thb=0.0))
    with pytest.raises(BudgetExceeded):
        anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "1M", llm=llm, executor=FakeExecutor()))
    assert fake.requests == []


def test_invalid_mode_or_horizon():
    with pytest.raises(ValueError):
        anyio.run(lambda: orchestrator.run_prediction("NVDA", "swing", "15m", llm=LLM("gemini", client=RoleFake())))


# --- scoreboard evaluation ----------------------------------------------------

def _backdate(pid, minutes):
    from core.db import connect

    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("UPDATE predictions SET target_time = ? WHERE id = ?", (t, pid))


def test_evaluation_and_metrics(fresh_db, synthetic_prices, monkeypatch):
    preds = [run_pred(RoleFake(), ticker="BTC-USD", mode="scalp", horizon="15m") for _ in range(3)]
    for p in preds:
        _backdate(p.id, 5)
    # actual: first two above price_now, last one far below the band
    actuals = iter([preds[0].forecast.price_now * 1.001, preds[1].forecast.price_now * 1.002,
                    preds[2].forecast.price_now * 0.5])
    monkeypatch.setattr(scoreboard, "actual_price", lambda *a, **k: next(actuals))
    assert scoreboard.evaluate_due() == 3
    m = scoreboard.metrics()
    assert m["n"] == 3 and m["pending"] == 0
    assert m["band_coverage"] == pytest.approx(2 / 3)
    assert 0 <= m["hit_rate"] <= 1 and m["baseline_hit_rate"] == 0.5
    agents = {row["agent"] for row in m["leaderboard"]}
    assert agents == {"technical", "macro", "psychology"}
    tech = next(r for r in m["leaderboard"] if r["agent"] == "technical")
    assert tech["hit_rate"] == pytest.approx(1 / 3)  # technical said down (-0.4); 1 of 3 went down


def test_actual_from_bars():
    idx = pd.date_range("2026-11-19 10:00", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame({"Close": [1, 2, 3, 4, 5]}, index=idx)
    assert scoreboard._actual_from_bars(df, idx[2].to_pydatetime()) == 3
    assert scoreboard._actual_from_bars(df, (idx[-1] + pd.Timedelta(minutes=5)).to_pydatetime()) is None
