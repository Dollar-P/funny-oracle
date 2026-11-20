"""P7: LLM backtest leakage rules, point-in-time data, calibration walk-forward."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import anyio
import numpy as np
import pytest

import config
import orchestrator
from core.llm import LLM
from core.schemas import AgentReport, Forecast, Prediction, Verdict
from evaluation import ablation, calibration as CAL
from evaluation.llm_backtest import (CONTAMINATED, CostConfirmationRequired, LLMBacktestConfig, contamination,
                                     decision_points, run_llm_backtest)
from evaluation.pit_executor import PointInTimeExecutor
from tests.pro.fakes_pro import ProRoleFake
from tests.pro.synth import synthetic_daily

UTC = timezone.utc
LOCAL = "local:prism-ml/bonsai-27b"


@pytest.fixture()
def cutoffs(monkeypatch):
    monkeypatch.setitem(config.MODEL_CUTOFFS, LOCAL, date(2024, 6, 30))
    monkeypatch.setitem(config.MODEL_CUTOFFS, "gemini-3.5-flash-lite", date(2024, 6, 30))
    yield


def test_contamination_labels(cutoffs):
    assert contamination(LOCAL, date(2024, 6, 30)) == "contaminated"     # on the cutoff day: contaminated
    assert contamination(LOCAL, date(2024, 7, 1)) == "clean"
    assert contamination("gemini-3.8-flash", date(2030, 1, 1)) == "contaminated"   # unknown cutoff (None)
    assert contamination("some-new-model", date(2030, 1, 1)) == "contaminated"     # not in the table at all


# --- point-in-time executor -----------------------------------------------------------------------------------

HIST = synthetic_daily("stock", start="2023-01-03", end="2025-06-30", seed=3)


def test_pit_executor_never_returns_a_future_bar():
    as_of = datetime(2024, 9, 10, 21, 1, tzinfo=UTC)                   # 1 min after Tuesday's close
    ex = PointInTimeExecutor("NVDA", "stock", as_of, HIST)
    assert ex.bars().index[-1].date() == date(2024, 9, 10) and (ex.bars().index <= as_of).all()
    early = PointInTimeExecutor("NVDA", "stock", datetime(2024, 9, 10, 19, 0, tzinfo=UTC), HIST)  # EDT close 20:00
    assert early.bars().index[-1].date() == date(2024, 9, 9)          # before the close: today's bar isn't final
    data, tc = anyio.run(lambda: ex.call("get_price_history", {"ticker": "NVDA", "period": "1mo"}))
    assert tc.error is None and data["data"]["summary"]["last_bar"].startswith("2024-09-10")
    assert data["data"]["summary"]["last"] == pytest.approx(float(HIST.loc[:"2024-09-10"]["Close"].iloc[-1]), rel=1e-4)
    ind, _ = anyio.run(lambda: ex.call("get_indicators", {"ticker": "NVDA"}))
    assert ind["data"]["last_bar"].startswith("2024-09-10") and "signals" in ind["data"]


def test_pit_executor_refuses_current_data():
    ex = PointInTimeExecutor("NVDA", "stock", datetime(2024, 9, 10, 22, tzinfo=UTC), HIST)
    assert [s.name for s in anyio.run(ex.list_tools)] == ["get_price_history", "get_indicators"]
    for tool in ("get_fundamentals", "get_news", "get_sentiment", "get_macro", "web_search", "fetch_url"):
        data, tc = anyio.run(lambda: ex.call(tool, {"ticker": "NVDA"}))
        assert data["data"] is None and "not available in backtest mode" in tc.error


def test_pit_anonymised_payload_hides_ticker_and_dates():
    ex = PointInTimeExecutor("NVDA", "stock", datetime(2024, 9, 10, 22, tzinfo=UTC), HIST, anonymize=True)
    data, _ = anyio.run(lambda: ex.call("get_price_history", {"ticker": "NVDA"}))
    text = str(data["data"])
    assert "NVDA" not in text and "2024-09-10" not in text and "Asset A" in text and "day 0" in text


# --- orchestrator backtest mode ----------------------------------------------------------------------------------

def test_orchestrator_backtest_mode_uses_only_point_in_time_data(monkeypatch):
    monkeypatch.setattr("mcp_server.data_sources.fetch_ohlcv", lambda t, interval="1d", period=None: HIST)
    as_of = datetime(2024, 9, 10, 21, 1, tzinfo=UTC)
    ex = PointInTimeExecutor("NVDA", "stock", as_of, HIST)
    fake = ProRoleFake()
    p = anyio.run(lambda: orchestrator.run_prediction(
        "NVDA", "swing", "1M", llm=LLM("gemini", client=fake), executor=ex, seed=1, save=False, prompt_version="v2",
        temperature=0.2, as_of=as_of))
    assert p.search_mode == "off" and p.news is None and p.as_of == "2024-09-10T21:01:00+00:00"
    assert p.created_at == "2024-09-10T21:01:00+00:00" and p.target_time.startswith("2024-10-10")
    assert p.forecast.price_now == pytest.approx(float(HIST.loc[:"2024-09-10"]["Close"].iloc[-1]))
    assert {n for n, _ in ex.calls} <= {"get_price_history", "get_indicators"}
    systems = [c.system_instruction or "" for _m, cont, c in fake.requests if c is not None and not isinstance(cont, str)]
    assert systems and all("2024-09-10 21:01" in s for s in systems if "Current time" in s)
    tools_offered = {fd.name for _m, cont, c in fake.requests if c is not None and c.tools
                     for t in c.tools if t.function_declarations for fd in t.function_declarations}
    assert tools_offered <= {"get_price_history", "get_indicators"}
    assert not any(t.google_search for _m, _c, c in fake.requests if c is not None and c.tools for t in c.tools)
    with pytest.raises(ValueError):
        anyio.run(lambda: orchestrator.run_prediction("NVDA", "scalp", "15m", llm=LLM("gemini", client=fake),
                                                      executor=ex, save=False, as_of=as_of))


# --- the backtest runner --------------------------------------------------------------------------------------------

def _fake_runner(calls):
    async def run(ticker, mode, horizon, *, executor, as_of, analyst_model, **kw):
        calls.append(as_of)
        df = executor.bars()
        price = float(df["Close"].iloc[-1])
        mom = price / float(df["Close"].iloc[-63]) - 1
        s = 0.8 if mom > 0 else -0.8
        rep = [AgentReport(agent="technical", score=s, confidence=0.8, key_points=["k"], reasoning="r"),
               AgentReport(agent="macro", score=-s / 2, confidence=0.6, key_points=["k"], reasoning="r")]
        sh = 0.02 * np.sqrt(21)
        mu = s * 0.5 * sh
        return Prediction(created_at=as_of.isoformat(), ticker=ticker, asset_type="stock", mode="swing", horizon=horizon,
                          target_time=(as_of + timedelta(days=30)).isoformat(), s_raw=s * 0.8, s_final=s,
                          confidence=0.8, weights={"technical": 0.5, "macro": 0.5}, reports=rep,
                          verdict=Verdict(adjustment=0.1, final_confidence=0.8, headline="", summary="", key_risks=[]),
                          forecast=Forecast(price_now=price, p10=price * np.exp(mu - 1.2816 * sh), p50=price * np.exp(mu),
                                            p90=price * np.exp(mu + 1.2816 * sh), prob_up=0.7 if s > 0 else 0.3,
                                            sigma_per_step=0.02, steps=21, step_label="1d"),
                          models={"analyst": analyst_model}, as_of=as_of.isoformat())
    return run


def _cfg(**kw):
    base = dict(tickers={"NVDA": "stock"}, start="2023-11-01", end="2025-05-01", model=LOCAL)
    return LLMBacktestConfig(**{**base, **kw})


def test_backtest_refuses_pre_cutoff_dates(cutoffs):
    calls = []
    out = anyio.run(lambda: run_llm_backtest(_cfg(), {"NVDA": HIST}, runner=_fake_runner(calls)))
    assert calls and all(c.date() > date(2024, 6, 30) for c in calls)     # never ran a contaminated date
    assert out["labels"]["contaminated"] > 0 and out["labels"]["clean"] == len(calls)
    assert CONTAMINATED in out["contamination_note"] and "not run" in out["contamination_note"]
    assert out["verdict_eligible"] is True
    full = out["variants"]["full"]["summary"]
    assert full["flats"].get("contaminated", 0) == out["labels"]["contaminated"]   # replayed as FLAT, not traded
    assert all(t["opened_at"] > datetime(2024, 6, 30, tzinfo=UTC) for t in out["variants"]["full"]["trades"])
    assert all(r["label"] == "clean" and r["refused_calls"] == [] for r in out["predictions"])
    assert set(out["variants"]) == {"full", "team_no_debate"}


def test_backtest_with_contaminated_dates_is_labelled_and_not_a_verdict(cutoffs):
    calls = []
    out = anyio.run(lambda: run_llm_backtest(_cfg(allow_contaminated=True), {"NVDA": HIST}, runner=_fake_runner(calls)))
    assert any(c.date() <= date(2024, 6, 30) for c in calls)
    assert out["verdict_eligible"] is False and "excluded from verdicts" in out["contamination_note"]
    assert {r["label"] for r in out["predictions"]} == {"clean", "contaminated"}


def test_unknown_cutoff_means_nothing_runs():
    calls = []
    out = anyio.run(lambda: run_llm_backtest(_cfg(), {"NVDA": HIST}, runner=_fake_runner(calls)))
    assert calls == [] and out["labels"]["clean"] == 0 and out["verdict_eligible"] is False


def test_paid_model_needs_cost_confirmation(cutoffs, fresh_db):
    with pytest.raises(CostConfirmationRequired) as e:
        anyio.run(lambda: run_llm_backtest(_cfg(model="gemini-3.5-flash-lite"), {"NVDA": HIST},
                                           runner=_fake_runner([])))
    assert e.value.estimate_thb > 0 and e.value.runs > 0
    out = anyio.run(lambda: run_llm_backtest(_cfg(model="gemini-3.5-flash-lite", confirm_cost=True, max_runs=2),
                                             {"NVDA": HIST}, runner=_fake_runner([])))
    assert out["runs"] == 2


def test_decision_points_match_the_engine_schedule(cutoffs):
    pts = decision_points(_cfg(), {"NVDA": HIST})
    idx = [p["i"] for p in pts]
    assert all(b - a == 21 for a, b in zip(idx, idx[1:]))
    assert all(p["as_of"] > p["ts"] for p in pts)


def test_ablation_variants():
    pred = anyio.run(lambda: _fake_runner([])("NVDA", "swing", "1M", executor=PointInTimeExecutor(
        "NVDA", "stock", datetime(2024, 9, 10, 22, tzinfo=UTC), HIST), as_of=datetime(2024, 9, 10, 22, tzinfo=UTC),
        analyst_model=LOCAL))
    t = ablation.variant(pred, "team_no_debate")
    assert t.s_final == pred.s_raw and t.verdict is None and t.confidence == pytest.approx(0.7)
    single = ablation.variant(pred, "single:macro")
    assert single.s_final == pytest.approx(-pred.s_final / 2) and single.confidence == 0.6
    drop = ablation.variant(pred, "drop:macro")
    assert drop.s_final == pytest.approx(min(1.0, pred.reports[0].score + 0.1))
    assert ablation.variant(pred, "single:valuation") is None
    assert ablation.variant_names([pred])[:2] == ["full", "team_no_debate"]


# --- calibration ----------------------------------------------------------------------------------------------------

def test_pav_textbook():
    assert list(CAL.pav([1, 3, 2, 4, 3, 5])) == [1, 2.5, 2.5, 3.5, 3.5, 5]
    assert list(CAL.pav([3, 2, 1])) == [2, 2, 2]
    fitted = CAL.pav(np.random.default_rng(0).random(200))
    assert np.all(np.diff(fitted) >= 0)


def test_isotonic_fit_is_monotone_and_corrects_overconfidence():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.2, 0.8, 2000)
    true_p = 0.5 + (raw - 0.5) * 0.4                 # the model is overconfident: truth is pulled to 0.5
    y = (rng.random(2000) < true_p).astype(float)
    c = CAL.fit(raw, y)
    assert c.kind == "isotonic" and c.n == 2000
    grid = np.linspace(0.2, 0.8, 25)
    vals = [c(p) for p in grid]
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert c(0.8) == pytest.approx(0.62, abs=0.05) and c(0.2) == pytest.approx(0.38, abs=0.05)


def test_calibrator_below_min_n_is_identity_and_platt_fallback():
    assert CAL.fit([0.6] * 10, [1] * 10).kind == "identity"
    c = CAL.fit([0.3] * 30 + [0.7] * 30, [0] * 20 + [1] * 10 + [0] * 10 + [1] * 20)   # only 2 distinct levels
    edge = CAL.fit(list(np.linspace(0.3, 0.7, 60)), [0] * 57 + [1] * 3)   # a few 1s at the very top
    assert edge(0.7) < 0.8                                                # pooled + prior: not pinned near 0.99
    assert c.kind == "platt" and 0.3 < c(0.3) < 0.4 and 0.6 < c(0.7) < 0.7


def test_walk_forward_never_uses_future_labels():
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(2)
    rows = [{"created_at": t0 + timedelta(days=i), "target_time": t0 + timedelta(days=i + 30),
             "prob_up": float(rng.uniform(0.3, 0.7)), "up": float(rng.random() < 0.5)} for i in range(200)]
    a = CAL.walk_forward(rows)
    # flip every label whose outcome is only known from day 120 on → a prediction made on or before day 120
    # cannot have seen it, so its calibrated value must not change; later ones legitimately do
    flipped = [dict(r, up=1 - r["up"]) if r["target_time"] >= t0 + timedelta(days=120) else r for r in rows]
    b = CAL.walk_forward(flipped)
    assert a[:121] == b[:121] and a[121:] != b[121:]
    assert a[79] is None and a[81] is not None        # day 80 sees 50 outcomes (days 0..49 → known by day 80)


def test_desk_calibrator_for_bucket(fresh_db):
    from core import scoreboard
    from core.db import connect
    from tests.pro.helpers import make_pred

    get = CAL.calibrator_for(lambda: datetime(2027, 1, 1, tzinfo=UTC))
    assert get("swing", "stock") == (None, 0)
    for i in range(60):
        p = make_pred(prob_up=0.3 + 0.4 * (i % 10) / 10, created=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i))
        pid = scoreboard.save(p, f"r{i}", "pro")
        with connect() as conn:
            conn.execute("UPDATE predictions SET actual_price = ? WHERE id = ?", (110.0 if i % 3 else 90.0, pid))
    cal_fn, n = get("swing", "stock")
    assert n == 60 and cal_fn is not None and 0 < cal_fn(0.5) < 1
