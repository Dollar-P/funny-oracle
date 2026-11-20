"""Evaluation report (PRO.md §5.4): dated Markdown + JSON; the Lab page reads the JSON.

Every number is a Metric with n and a CI; below MIN_N (30 trades or 30 non-overlapping predictions per bucket)
it is labelled "insufficient data" and no verdict is drawn. Sections:

    meta            period, assets, n, cost assumptions, models / think presets / prompt versions, contamination
    forward         the paper account of the experiment (the only fully clean test of the LLM system, §6.1):
                    prediction quality per bucket, trading performance, vs buy & hold, per model, ablation,
                    per agent, progress towards the minimum sample
    backtests       quant-only and non-LLM baselines over history on the watchlist (no LLM → no contamination),
                    each with a bootstrap CI on Sharpe and on the Sharpe difference vs buy & hold, plus where
                    quant-only ranks inside the random-strategy distribution
    llm_backtests   stored LLM backtests with their contamination labels
    verdicts        plain-language conclusions, only where the sample allows one
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd

import config
from core import pro_settings
from core.db import connect
from core.worlds import PRO_ONLY
from core.schemas import Prediction
from evaluation import ablation
from evaluation import metrics as M
from evaluation import stats as S
from evaluation.backtest import BacktestConfig, buy_and_hold, cash, prepare, random_distribution, run_backtest
from evaluation.baselines import Momentum, QuantOnly, SmaCross

UTC = timezone.utc
HistoryFn = Callable[[str, str], pd.DataFrame]   # (ticker, asset_type) → daily bars (as many years as available)


def _m(value, n, lo=None, hi=None, min_n=S.MIN_N, note="") -> dict:
    return S.metric(value, n, lo, hi, min_n=min_n, note=note).to_dict()


def _sharpe_metric(r: np.ndarray, ppy: int, n_boot: int, min_n: int = S.MIN_N) -> dict:
    if len(r) < 3:
        return _m(None, len(r), min_n=min_n)
    est, lo, hi = S.block_bootstrap(r, lambda a: M.sharpe(a, ppy), n_boot=n_boot, seed=1)
    return _m(est, len(r), lo, hi, min_n=min_n, note=f"block bootstrap, {n_boot} resamples")


# --- forward (paper account) -----------------------------------------------------------------------------------

def _pro_predictions(experiment_id: int) -> list[tuple[Prediction, float | None]]:
    with connect() as conn:
        rows = conn.execute(f"SELECT prediction_json, actual_price FROM predictions WHERE {PRO_ONLY} AND"
                            " experiment_id = ? ORDER BY created_at", (experiment_id,)).fetchall()
    out = []
    for pj, actual in rows:
        try:
            out.append((Prediction.model_validate_json(pj), actual))
        except Exception:
            continue
    return out


def prediction_quality(items: list[tuple[Prediction, float]], n_boot: int = 1000) -> dict:
    """items: (prediction, actual price). Hit rate on non-overlapping samples (Wilson CI); Brier, log loss and
    pinball (in return units, so assets are comparable) with block-bootstrap CIs; band coverage."""
    if not items:
        return {"n": 0, "n_nonoverlap": 0, "sufficient": False, "label": "insufficient data"}
    rows = [(i, p.ticker, p.created_at, p.target_time) for i, (p, _a) in enumerate(items)]
    keep = set(S.non_overlapping(rows))
    p_up = np.array([p.forecast.prob_up for p, _ in items])
    up = np.array([1.0 if a > p.forecast.price_now else 0.0 for p, a in items])
    ret = np.array([a / p.forecast.price_now - 1 for p, a in items])
    q = {k: np.array([getattr(p.forecast, k) / p.forecast.price_now - 1 for p, _ in items]) for k in ("p10", "p50", "p90")}
    k_idx = sorted(keep)
    hits, n_calls = M.hit_rate(p_up[k_idx], up[k_idx])
    lo, hi = S.wilson_ci(hits, n_calls)
    n_eff = len(k_idx)
    brier = S.block_bootstrap((p_up - up) ** 2, np.mean, n_boot=n_boot, seed=2)
    out = {
        "n": len(items), "n_nonoverlap": n_eff, "sufficient": n_eff >= S.MIN_N,
        "label": "ok" if n_eff >= S.MIN_N else "insufficient data",
        "hit_rate": _m(hits / n_calls if n_calls else None, n_calls, lo, hi, note="Wilson 95 %, non-overlapping"),
        "brier": _m(brier[0], len(items), brier[1], brier[2], min_n=S.MIN_N),
        "brier_baseline": 0.25,
        "log_loss": _m(M.log_loss(p_up, up), len(items)),
        "pinball": {f"p{int(t * 100)}": _m(M.pinball(q[f"p{int(t * 100)}"], ret, t), len(items))
                    for t in (0.1, 0.5, 0.9)},
        "band_coverage": _m(M.band_coverage(q["p10"], q["p90"], ret), len(items), note="target 80 %"),
        "reliability": M.reliability(p_up, up, bins=10),
        "progress": min(1.0, n_eff / S.MIN_N),
    }
    return out


def _buckets(items: list[tuple[Prediction, float]]) -> dict:
    by: dict[str, list] = {}
    for p, a in items:
        by.setdefault(f"{p.mode}/{p.horizon}/{p.asset_type}", []).append((p, a))
    return {k: prediction_quality(v, n_boot=500) for k, v in sorted(by.items())}


def _equity(experiment_id: int) -> pd.Series:
    with connect() as conn:
        rows = conn.execute("SELECT ts, equity FROM equity_snapshots WHERE experiment_id = ? ORDER BY ts",
                            (experiment_id,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows], utc=True))
    return s.groupby(s.index.normalize()).last()   # one point per UTC day (the last snapshot of the day)


def _trades(experiment_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM positions WHERE experiment_id = ? AND closed_at IS NOT NULL"
                                              " AND realized_pnl IS NOT NULL ORDER BY closed_at", (experiment_id,))]


def trading_performance(equity: pd.Series, trades: list[dict], ppy: int, n_boot: int = 1000) -> dict:
    e = equity.values
    r = M.returns_from_equity(e)
    rs = np.array([t["r_multiple"] for t in trades if t["r_multiple"] is not None], dtype=float)
    pnl = np.array([t["realized_pnl"] for t in trades], dtype=float)
    days = (equity.index[-1] - equity.index[0]).days if len(equity) > 1 else 0
    wins = int((pnl > 0).sum())
    wl, wh = S.wilson_ci(wins, len(pnl)) if len(pnl) else (None, None)
    exp_ci = S.block_bootstrap(rs, np.mean, n_boot=n_boot, seed=3) if len(rs) >= 3 else (rs.mean() if len(rs) else None, None, None)
    return {
        "days": days, "n_trades": len(trades), "label": "ok" if len(trades) >= S.MIN_N else "insufficient data",
        "total_return": _m(M.total_return(e) if len(e) > 1 else 0.0, len(r), min_n=S.MIN_N),
        "cagr": _m(M.cagr(e, days) if days else None, len(r)),
        "volatility": _m(M.volatility(r, ppy), len(r)),
        "sharpe": _sharpe_metric(r, ppy, n_boot),
        "sortino": _m(M.sortino(r, ppy), len(r)),
        "max_drawdown": _m(M.max_drawdown(e) if len(e) else 0.0, len(r)),
        "calmar": _m(M.calmar(e, days) if days else None, len(r)),
        "win_rate": _m(wins / len(pnl) if len(pnl) else None, len(pnl), wl, wh, note="Wilson 95 %"),
        "expectancy_r": _m(exp_ci[0], len(rs), exp_ci[1], exp_ci[2], note="mean R, block bootstrap"),
        "profit_factor": _m(M.profit_factor(pnl) if len(pnl) else None, len(pnl)),
        "total_costs": float(sum(t.get("costs") or 0 for t in trades)),
        "progress": min(1.0, len(trades) / S.MIN_N),
    }


def _per_model(items) -> dict:
    by: dict[str, list] = {}
    for p, a in items:
        key = p.models.get("analyst", "?") + (f" ({p.think_preset})" if p.think_preset else "")
        by.setdefault(key, []).append((p, a))
    return {k: prediction_quality(v, n_boot=300) for k, v in sorted(by.items())}


def _ablation(items) -> dict:
    names = ablation.variant_names([p for p, _ in items]) if items else []
    out = {}
    for v in names:
        vi = [(q, a) for p, a in items if (q := ablation.variant(p, v)) is not None]
        pq = prediction_quality(vi, n_boot=300) if vi else {"n": 0}
        out[v] = {"n": pq.get("n", 0), "brier": pq.get("brier"), "hit_rate": pq.get("hit_rate"),
                  "label": pq.get("label", "insufficient data")}
    full = out.get("full", {}).get("brier", {}) or {}
    for v, d in out.items():
        b = (d.get("brier") or {}).get("value")
        d["delta_brier_vs_full"] = None if b is None or full.get("value") is None else b - full["value"]
    return out


def _per_agent(items) -> dict:
    by: dict[str, list[tuple[int, int]]] = {}
    for p, a in items:
        up = 1 if a > p.forecast.price_now else 0
        for r in p.reports:
            if r.ok and r.score != 0:
                by.setdefault(r.agent, []).append((1 if r.score > 0 else 0, up))
    out = {}
    for agent, v in sorted(by.items()):
        k = sum(1 for d, u in v if d == u)
        lo, hi = S.wilson_ci(k, len(v))
        out[agent] = _m(k / len(v), len(v), lo, hi, note="sign(score) vs outcome, Wilson 95 %")
    return out


# --- backtests (quant & baselines) ---------------------------------------------------------------------------

def run_baseline_backtests(data: dict[str, tuple[str, pd.DataFrame]], start: str, n_random: int = 1000,
                           n_boot: int = 1000, workers: int | None = None) -> dict:
    cfg = BacktestConfig(start=start, horizon_bars={"stock": 21, "gold": 21, "crypto": 30})
    prep = prepare(data)
    res = {"buy_and_hold": buy_and_hold(prep, cfg), "cash": cash(prep, cfg), "momentum_63": run_backtest(Momentum(), prep, cfg),
           "sma_50_200": run_backtest(SmaCross(), prep, cfg), "quant_only": run_backtest(QuantOnly(), prep, cfg)}
    bh = res["buy_and_hold"]
    bh_r = M.returns_from_equity(bh.equity.values)
    table = {}
    for name, r in res.items():
        s = r.summary()
        rr = M.returns_from_equity(r.equity.values)
        n = min(len(rr), len(bh_r))
        diff = S.bootstrap_diff(rr[-n:], bh_r[-n:], lambda a: M.sharpe(a, r.ppy), n_boot=n_boot, seed=4) if n > 2 else (None, None, None)
        rs = [t["r_multiple"] for t in r.trades if t["r_multiple"] is not None]
        table[name] = {
            "sharpe": _sharpe_metric(rr, r.ppy, n_boot),
            "sharpe_diff_vs_bh": _m(diff[0], n, diff[1], diff[2], note="paired block bootstrap"),
            "total_return": s["total_return"], "cagr": s["cagr"], "max_drawdown": s["max_drawdown"],
            "calmar": s["calmar"], "sortino": s["sortino"], "volatility": s["volatility"],
            "trades": _m(s["trades"], s["trades"]), "win_rate": s["win_rate"],
            "expectancy_r": _m(s["expectancy_r"], len(rs)), "profit_factor": s["profit_factor"],
            "exposure": s["exposure"], "turnover": s["turnover"], "total_costs": s["total_costs"],
            "flats": s["flats"], "rejections": s["rejections"],
            "equity": [[ts.isoformat(), float(v)] for ts, v in r.equity.iloc[::max(1, len(r.equity) // 300)].items()],
        }
    dist = random_distribution(data, cfg, n_runs=n_random, workers=workers) if n_random else []
    sh = [d["sharpe"] for d in dist]
    q_sh = table["quant_only"]["sharpe"]["value"]
    pq = [p for p in res["quant_only"].predictions]
    qitems = []
    for p in pq:
        t, i, ti = p["ticker"], p["i"], p["target_i"]
        df = data[t][1]
        if ti < len(df):
            qitems.append((p, float(df["Close"].iloc[ti])))
    qq = None
    if qitems:
        up = np.array([1.0 if a > p["price"] else 0.0 for p, a in qitems])
        pu = np.array([p["prob_up"] for p, _ in qitems])
        k, n = M.hit_rate(pu, up)
        lo, hi = S.wilson_ci(k, n)
        qq = {"n": len(qitems), "brier": _m(M.brier(pu, up), len(qitems)),
              "hit_rate": _m(k / n if n else None, n, lo, hi, note="Wilson 95 % (decisions are non-overlapping)")}
    return {
        "start": start, "end": max(df.index[-1] for _a, df in data.values()).isoformat(),
        "strategies": table, "quant_only_prediction_quality": qq,
        "random": {"n_runs": len(dist), "sharpe_p05": float(np.percentile(sh, 5)) if sh else None,
                   "sharpe_p50": float(np.percentile(sh, 50)) if sh else None,
                   "sharpe_p95": float(np.percentile(sh, 95)) if sh else None,
                   "histogram": np.histogram(sh, bins=30)[0].tolist() if sh else [],
                   "bin_edges": np.histogram(sh, bins=30)[1].tolist() if sh else [],
                   "quant_only_percentile": S.percentile_rank(q_sh, sh) if sh and q_sh is not None else None},
        "contamination": "none — no LLM involved",
    }


def _llm_backtests() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, created_at, period, status, result_json FROM backtests WHERE kind = 'llm'"
                            " AND status = 'done' ORDER BY id DESC LIMIT 20").fetchall()
    out = []
    for r in rows:
        res = json.loads(r["result_json"] or "{}")
        out.append({"id": r["id"], "created_at": r["created_at"], "period": r["period"], "model": res.get("model"),
                    "labels": res.get("labels"), "verdict_eligible": res.get("verdict_eligible"),
                    "contamination_note": res.get("contamination_note"),
                    "summary": {k: v.get("summary") for k, v in (res.get("variants") or {}).items()}})
    return out


# --- verdicts ----------------------------------------------------------------------------------------------------

def _verdicts(rep: dict) -> list[dict]:
    out = []
    fq = rep["forward"]["prediction_quality"]
    if not fq.get("sufficient"):
        out.append({"topic": "Forward test — LLM predictions", "verdict": "insufficient data",
                    "detail": f"{fq.get('n_nonoverlap', 0)} non-overlapping evaluated predictions; at least {S.MIN_N} "
                              "are needed before any conclusion."})
    else:
        b = fq["brier"]
        better = b["hi"] is not None and b["hi"] < 0.25
        out.append({"topic": "Forward test — LLM predictions", "verdict": "beats a coin flip" if better else
                    "no evidence of skill", "detail": f"Brier {b['value']:.3f} (95 % CI {b['lo']:.3f}–{b['hi']:.3f}) vs "
                                                       "0.250 for always saying 50 %."})
    ft = rep["forward"]["trading"]
    out.append({"topic": "Forward test — paper trading", "verdict": ft["label"] if ft["label"] != "ok" else
                ("positive expectancy" if (ft["expectancy_r"]["lo"] or 0) > 0 else "no evidence of an edge"),
                "detail": f"{ft['n_trades']} closed trades (minimum {S.MIN_N})."})
    bt = rep.get("backtests") or {}
    if bt:
        q = bt["strategies"]["quant_only"]["sharpe_diff_vs_bh"]
        if q["value"] is None:
            v = "insufficient data"
        elif q["lo"] is not None and q["lo"] > 0:
            v = "quant-only beat buy & hold (risk-adjusted)"
        elif q["hi"] is not None and q["hi"] < 0:
            v = "quant-only lost to buy & hold (risk-adjusted)"
        else:
            v = "no evidence of a difference vs buy & hold"
        out.append({"topic": "Backtest — quant-only vs buy & hold", "verdict": v,
                    "detail": f"Sharpe difference {q['value']:+.2f} (95 % CI {q['lo']:+.2f} to {q['hi']:+.2f})"
                    if q["value"] is not None and q["lo"] is not None else ""})
    return out


# --- build -----------------------------------------------------------------------------------------------------------

def build_report(experiment_id: int | None = None, *, history: HistoryFn | None = None, backtest_years: int = 3,
                 n_random: int = 1000, n_boot: int = 1000, now: datetime | None = None, include_backtests: bool = True,
                 evaluate: bool = True, workers: int | None = None) -> dict:
    now = now or datetime.now(UTC)
    s = pro_settings.load()
    exp = experiment_id or pro_settings.current_experiment(s)
    if evaluate:
        from core import scoreboard

        try:
            scoreboard.evaluate_due(200)
        except Exception:
            pass
    preds = _pro_predictions(exp)
    evaluated = [(p, a) for p, a in preds if a is not None]
    eq = _equity(exp)
    trades = _trades(exp)
    assets = [w["ticker"] for w in s["watchlist"]]
    ppy = 365 if any(w["asset_type"] == "crypto" for w in s["watchlist"]) else 252
    period = (preds[0][0].created_at[:10] if preds else now.date().isoformat(), now.date().isoformat())
    rep: dict = {
        "created_at": now.isoformat(timespec="seconds"), "experiment_id": exp,
        "meta": {
            "period": {"from": period[0], "to": period[1]}, "assets": assets,
            "n_predictions": len(preds), "n_evaluated": len(evaluated), "n_trades": len(trades),
            "cost_assumptions_bps": s["costs"], "risk": s["risk"], "signal": s["signal"],
            "models": sorted({(p.models.get("analyst") or "?") + (f" · {p.think_preset}" if p.think_preset else "")
                              for p, _ in preds}),
            "prompt_versions": sorted({p.prompt_version for p, _ in preds}) or [s["prompt_version"]],
            "contamination": {"forward": "clean — forward test, decisions made before the outcome existed",
                              "llm_backtests": "see llm_backtests (dates on/before a model's cutoff are excluded)"},
            "min_n": S.MIN_N, "disclaimer": "Paper trading only. Not investment advice.",
        },
        "forward": {
            "prediction_quality": prediction_quality(evaluated, n_boot),
            "buckets": _buckets(evaluated),
            "trading": trading_performance(eq, trades, ppy, n_boot) if len(eq) else
            {"n_trades": len(trades), "label": "insufficient data", "progress": 0.0,
             "expectancy_r": _m(None, 0), "sharpe": _m(None, 0)},
            "equity": [[ts.isoformat(), float(v)] for ts, v in eq.items()],
            "per_model": _per_model(evaluated), "ablation": _ablation(evaluated), "per_agent": _per_agent(evaluated),
        },
    }
    if include_backtests and history is not None:
        start = (now - timedelta(days=365 * backtest_years)).date().isoformat()
        data = {}
        for w in s["watchlist"]:
            try:
                df = history(w["ticker"], w["asset_type"])
            except Exception:
                continue
            if df is not None and len(df) > 260:
                data[w["ticker"]] = (w["asset_type"], df)
        if data:
            rep["backtests"] = run_baseline_backtests(data, start, n_random=n_random, n_boot=n_boot, workers=workers)
            rep["backtests"]["assets"] = list(data)
    rep["llm_backtests"] = _llm_backtests()
    k = len((rep.get("backtests") or {}).get("strategies", {})) + len(rep["forward"]["ablation"]) + len(rep["forward"]["per_model"])
    rep["multiple_comparisons"] = S.multiple_comparisons_note(k)
    rep["verdicts"] = _verdicts(rep)
    return rep


def to_markdown(rep: dict) -> str:
    def fm(m, pct=False, nd=2):
        if not m or m.get("value") is None:
            return "–"
        v = m["value"]
        f = (lambda x: f"{x * 100:.1f} %") if pct else (lambda x: f"{x:.{nd}f}")
        ci = f" [{f(m['lo'])}, {f(m['hi'])}]" if m.get("lo") is not None and m.get("hi") is not None else ""
        badge = "" if m.get("sufficient") else " ⚠ insufficient data"
        return f"{f(v)}{ci} (n={m['n']}){badge}"

    meta = rep["meta"]
    lines = [f"# Evaluation report — experiment {rep['experiment_id']}", "",
             f"Generated {rep['created_at']} · period {meta['period']['from']} → {meta['period']['to']}",
             f"Assets: {', '.join(meta['assets'])}", f"Models: {', '.join(meta['models']) or '–'} · prompts "
             f"{', '.join(meta['prompt_versions'])}", f"Costs (bps per side): {json.dumps(meta['cost_assumptions_bps'])}",
             f"Contamination: forward = {meta['contamination']['forward']}", "",
             "> Paper trading only. Not investment advice. Small samples are labelled; no verdict below "
             f"n = {meta['min_n']}.", "", "## Verdicts", ""]
    lines += [f"- **{v['topic']}**: {v['verdict']}. {v['detail']}" for v in rep["verdicts"]]
    fq, ft = rep["forward"]["prediction_quality"], rep["forward"]["trading"]
    lines += ["", "## Forward test", "", f"Predictions evaluated: {fq.get('n', 0)} ({fq.get('n_nonoverlap', 0)} non-overlapping)",
              f"- Hit rate: {fm(fq.get('hit_rate'), pct=True)}", f"- Brier: {fm(fq.get('brier'), nd=3)} (coin flip 0.250)",
              f"- Band coverage P10–P90: {fm(fq.get('band_coverage'), pct=True)} (target 80 %)",
              f"- Closed trades: {ft.get('n_trades', 0)} · expectancy {fm(ft.get('expectancy_r'))} R · Sharpe {fm(ft.get('sharpe'))}"]
    bt = rep.get("backtests")
    if bt:
        lines += ["", f"## Backtests (no LLM) — {bt['start']} → {bt['end'][:10]}, {', '.join(bt.get('assets', []))}", "",
                  "| Strategy | Sharpe [95 % CI] | ΔSharpe vs B&H [95 % CI] | Total return | Max DD | Trades |",
                  "|---|---|---|---|---|---|"]
        for name, t in bt["strategies"].items():
            lines.append(f"| {name} | {fm(t['sharpe'])} | {fm(t['sharpe_diff_vs_bh'])} | {t['total_return'] * 100:.1f} % |"
                         f" {t['max_drawdown'] * 100:.1f} % | {t['trades']['value']:.0f} |")
        rnd = bt["random"]
        if rnd["n_runs"]:
            lines += ["", f"Random strategy ({rnd['n_runs']} seeded runs): Sharpe 5th/50th/95th percentile "
                          f"{rnd['sharpe_p05']:.2f} / {rnd['sharpe_p50']:.2f} / {rnd['sharpe_p95']:.2f}; quant-only ranks at "
                          f"the {rnd['quant_only_percentile'] * 100:.0f}th percentile." if rnd["quant_only_percentile"] is not None else ""]
    if rep.get("llm_backtests"):
        lines += ["", "## LLM backtests", ""]
        lines += [f"- #{b['id']} {b['model']} {b['period']}: {b['labels']} — "
                  f"{'eligible' if b['verdict_eligible'] else 'not eligible for a verdict'}. {b['contamination_note'] or ''}"
                  for b in rep["llm_backtests"]]
    if rep.get("multiple_comparisons"):
        lines += ["", f"_Multiple comparisons: {rep['multiple_comparisons']}_"]
    return "\n".join(lines) + "\n"


def save_report(rep: dict) -> int:
    md = to_markdown(rep)
    with connect() as conn:
        cur = conn.execute("INSERT INTO reports (created_at, experiment_id, period, result_json, markdown) VALUES (?,?,?,?,?)",
                           (rep["created_at"], rep["experiment_id"],
                            f"{rep['meta']['period']['from']}..{rep['meta']['period']['to']}",
                            json.dumps(rep, default=str), md))
        return int(cur.lastrowid)


def write_files(rep: dict, rid: int, folder) -> tuple[str, str]:
    """Dated Markdown + JSON files (e.g. docs/pro/reports/2026-11-21-report-3.md)."""
    from pathlib import Path

    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{rep['created_at'][:10]}-report-{rid}"
    (d / f"{stem}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (d / f"{stem}.md").write_text(to_markdown(rep), encoding="utf-8")
    return str(d / f"{stem}.md"), str(d / f"{stem}.json")


def live_history(ticker: str, asset_type: str) -> pd.DataFrame:
    from mcp_server import data_sources as ds

    return ds.fetch_ohlcv(ticker, "1d", "5y")
