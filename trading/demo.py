"""Deterministic Desk demo data (for the e2e UI checks and screenshots) — synthetic bars, no network, no LLM.

    python scripts/pro_demo.py            # builds data/pro_demo/{pro_demo.db, bars/*.csv, demo.json}
    PRO_DEMO_DIR=data/pro_demo ORACLE_DB=data/pro_demo/pro_demo.db uvicorn api.main:app

In demo mode the API's Desk reads these bars, its clock is frozen at the demo's "now", and scheduled jobs use the
DemoRunner below (rule-based predictions dressed as agent reports). Nothing here is presented as a real result:
every demo prediction is labelled `demo` in its model id.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.schemas import AgentReport, Catalyst, Forecast, KeyLevels, Prediction, ScenarioProbs, Verdict
from trading import calendar as cal

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
DEMO_MODEL = "demo:rule-based"
WATCH = [("NVDA", "stock", 150.0, 0.0006, 0.024), ("AAPL", "stock", 210.0, 0.0002, 0.015),
         ("MSFT", "stock", 430.0, 0.0003, 0.014), ("BTC-USD", "crypto", 62_000.0, 0.0004, 0.03),
         ("ETH-USD", "crypto", 3_100.0, 0.0001, 0.036), ("GC=F", "gold", 3_400.0, 0.0003, 0.009)]


def daily_index(asset_type: str, start: str, end: str) -> pd.DatetimeIndex:
    days = cal.sessions(asset_type, start, end)
    if asset_type == "crypto":
        return pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=UTC) for d in days])
    return pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=NY).astimezone(UTC) for d in days])


def make_bars(start: str, end: str, seed: int = 7) -> dict[str, tuple[str, pd.DataFrame]]:
    out = {}
    for k, (t, a, p0, drift, vol) in enumerate(WATCH):
        rng = np.random.default_rng(seed + k)
        idx = daily_index(a, start, end)
        n = len(idx)
        regime = np.repeat(rng.normal(drift, vol / 6, n // 40 + 1), 40)[:n]   # slow trend regimes
        c = p0 * np.exp(np.cumsum(rng.normal(regime, vol, n)))
        prev = np.concatenate([[c[0]], c[:-1]])
        o = prev * np.exp(rng.normal(0, vol * 0.25, n))
        h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, vol * 0.45, n)))
        lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, vol * 0.45, n)))
        v = rng.lognormal(16, 0.3, n)
        out[t] = (a, pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v}, index=idx))
    return out


def save_bars(data: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for t, (a, df) in data.items():
        df.to_csv(folder / f"{t.replace('=', '_')}.csv", index_label="ts")
    (folder / "assets.json").write_text(json.dumps({t: a for t, (a, _df) in data.items()}))


def load_bars(folder: Path) -> dict[str, tuple[str, pd.DataFrame]]:
    assets = json.loads((folder / "assets.json").read_text())
    out = {}
    for t, a in assets.items():
        df = pd.read_csv(folder / f"{t.replace('=', '_')}.csv", index_col="ts", parse_dates=["ts"])
        df.index = pd.DatetimeIndex(df.index, tz=None).tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
        out[t] = (a, df)
    return out


class DemoBars:
    """Desk bar source over saved demo bars: only bars whose close is final at `now`."""

    def __init__(self, data: dict):
        self.data = data
        self.close_t = {t: np.array([cal.bar_close_time(a, "1d", ts.to_pydatetime(),
                                                        df.index[i + 1].to_pydatetime() if i + 1 < len(df) else None)
                                     for i, ts in enumerate(df.index)]) for t, (a, df) in data.items()}

    def __call__(self, ticker, asset_type, interval, now):
        if ticker not in self.data:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return self.data[ticker][1][self.close_t[ticker] <= now]

    def history(self, ticker, asset_type):
        return self.data[ticker][1]


AGENT_TEXT = {
    "business": "Revenue momentum and margins", "technical": "trend_vs_sma50 and macd_momentum",
    "valuation": "Multiple vs its 5-year range", "macro": "Real yields and the dollar", "psychology": "Positioning and fear & greed",
}


class DemoRunner:
    """Rule-based stand-in for an LLM run: indicator labels + noise → agent scores → judge → analytic forecast."""

    def __init__(self, bars: DemoBars, clock, seed: int = 11):
        self.bars, self.clock = bars, clock
        self.rng = np.random.default_rng(seed)
        self.force_s: float | None = None   # demo builder: guarantee a tradeable signal

    async def __call__(self, job, experiment_id, s):
        from core import scoreboard
        from evaluation.baselines import label_score
        from quant.montecarlo import simulate
        from mcp_server import indicators as ind

        now = self.clock()
        a, full = self.bars.data[job.ticker]
        df = full[full.index <= now]                                   # live price includes the bar in progress
        sig = ind.compute_all(df.tail(260))["signals"]
        base = label_score(sig)
        weights = {"business": 0.2, "technical": 0.25, "valuation": 0.15, "macro": 0.2, "psychology": 0.2}
        reports = []
        for ag in weights:
            sc = float(np.clip(base * 0.8 + self.rng.normal(0, 0.35), -1, 1))
            cf = float(np.clip(self.rng.normal(0.66, 0.1), 0.3, 0.95))
            reports.append(AgentReport(
                agent=ag, score=round(sc, 2), confidence=round(cf, 2),
                key_points=[f"{AGENT_TEXT[ag]}: {'supportive' if sc > 0 else 'a headwind'} ({sc:+.2f})",
                            f"sma_regime={sig.get('sma_regime')}", f"rsi_zone={sig.get('rsi_zone')}"],
                reasoning="Demo agent (rule-based, no LLM).",
                scenario_probs=ScenarioProbs(bull=0.5 + sc / 4, base=0.3, bear=0.2 - sc / 4).normalized(),
                key_levels=KeyLevels(support=[round(float(df["Low"].tail(20).min()), 2)],
                                     resistance=[round(float(df["High"].tail(20).max()), 2)]),
                invalidation="a daily close below the 20-day low" if sc > 0 else "a daily close above the 20-day high"))
        s_raw = sum(weights[r.agent] * r.score * r.confidence for r in reports) / sum(weights[r.agent] * r.confidence for r in reports)
        adj = float(np.clip(self.rng.normal(0, 0.06), -0.2, 0.2))
        s_final = float(np.clip(s_raw + adj, -1, 1)) if self.force_s is None else self.force_s
        conf = float(np.clip(np.mean([r.confidence for r in reports]) + self.rng.normal(0, 0.05), 0.3, 0.9))
        lr = np.log(df["Close"]).diff().dropna().tail(252)
        steps = {"stock": 21, "gold": 21, "crypto": 30}[a]
        # the real Monte Carlo (seeded) so demo predictions carry a fan, like live runs (the Arena replay needs it)
        fc = simulate(float(df["Close"].iloc[-1]), float(lr.std(ddof=1)), steps, s_final, step_label="1d",
                      seed=int(self.rng.integers(0, 2**31)), paths=2000)
        cats = []
        if self.rng.random() < 0.15:
            cats.append(Catalyst(date=(now + timedelta(days=int(self.rng.integers(1, 10)))).date().isoformat(),
                                 event="CPI" if a != "stock" else "earnings", impact="high"))
        side = "up" if s_final > 0 else "down"
        verdict = Verdict(adjustment=round(adj, 3), final_confidence=round(conf, 2),
                          headline=f"Committee leans {side} on {job.ticker}", summary="Demo committee summary.",
                          key_risks=["trend reversal", "macro surprise"],
                          thesis=f"Trend labels ({sig.get('sma_regime')}, {sig.get('trend_vs_sma50')} SMA50) and "
                                 f"{'improving' if s_final > 0 else 'weakening'} momentum point {side} over the horizon.",
                          invalidation="price closes beyond the stop level", catalysts=cats,
                          no_trade_reason=None if abs(s_final) > 0.05 else "signals cancel out")
        pred = Prediction(created_at=now.isoformat(timespec="seconds"), ticker=job.ticker, asset_type=a, mode="swing",
                          horizon=job.horizon, target_time=(now + timedelta(days=30)).isoformat(timespec="seconds"),
                          s_raw=s_raw, s_final=s_final, confidence=conf, weights=weights, reports=reports, verdict=verdict,
                          forecast=fc, models={"analyst": DEMO_MODEL, "judge": DEMO_MODEL}, prompt_version="v2",
                          temperature=0.2, experiment_id=experiment_id, think_preset=None, elapsed_s=0.0,
                          run_id=f"demo-{job.key}")
        scoreboard.save(pred, pred.run_id, "pro")
        return pred


async def demo_review(trade, now):
    won = (trade.get("r_multiple") or 0) > 0
    return {"post_mortem": f"{trade['ticker']} {trade['side']} closed by {trade['exit_reason']} at "
                           f"{(trade.get('r_multiple') or 0):+.2f} R. " + ("The trend the thesis described carried the trade."
                                                                            if won else "The move the thesis expected did not come in time."),
            "thesis_held": won, "lesson": "none" if won else "wait for momentum to confirm the trend label",
            "model": DEMO_MODEL}, None


def evaluate_from_bars(bars: DemoBars, now: datetime) -> int:
    """Fill actual_price for demo predictions whose target has passed (from the synthetic bars)."""
    from core.db import connect

    n = 0
    with connect() as conn:
        rows = conn.execute("SELECT id, ticker, target_time FROM predictions WHERE actual_price IS NULL AND target_time <= ?",
                            (now.isoformat(),)).fetchall()
        for r in rows:
            df = bars.data.get(r["ticker"], (None, None))[1]
            if df is None:
                continue
            past = df[df.index <= pd.Timestamp(r["target_time"])]
            if len(past):
                conn.execute("UPDATE predictions SET actual_price = ?, evaluated_at = ? WHERE id = ?",
                             (float(past["Close"].iloc[-1]), now.isoformat(timespec="seconds"), r["id"]))
                n += 1
    return n


def demo_settings() -> dict:
    from core import pro_settings

    s = pro_settings.load()
    s["watchlist"] = [{"ticker": t, "asset_type": a, "horizons": ["1M"]} for t, a, *_ in WATCH]
    s["models"]["swing"] = "gemini-3.5-flash-lite"   # demo routes to a "paid" id; the DemoRunner never calls it
    s["budgets"]["daily_thb"] = 1000.0
    s["local_tz"] = "Asia/Bangkok"
    return pro_settings.save(s)
