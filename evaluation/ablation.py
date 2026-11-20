"""Ablation & attribution (PRO.md §5.1, §5.3) derived from stored predictions — no extra LLM calls.

From one full-system prediction we can rebuild what simpler systems would have said:
    team_no_debate   S = S_raw (weighted analysts, no debate/judge adjustment), confidence = mean analyst confidence
    single:<agent>   S = that analyst's score, confidence = its confidence
    drop:<agent>     S = weighted score without that analyst (leave-one-out), judge adjustment kept
The forecast is recomputed with the same closed-form model (evaluation.baselines.analytic_forecast) from the
stored σ per step, so only the score changes between variants.
"""
from __future__ import annotations

from core.schemas import Prediction
from evaluation.baselines import analytic_forecast
from quant.montecarlo import weighted_score

AGENTS = ("business", "technical", "valuation", "macro", "psychology")


def variant(pred: Prediction, name: str) -> Prediction | None:
    ok = {r.agent: r for r in pred.reports if r.ok}
    f = pred.forecast
    if name == "full":
        s, conf, verdict = pred.s_final, pred.confidence, pred.verdict
    elif name == "team_no_debate":
        if not ok:
            return None
        s = pred.s_raw
        conf = sum(r.confidence for r in ok.values()) / len(ok)
        verdict = None
    elif name.startswith("single:"):
        a = ok.get(name.split(":", 1)[1])
        if a is None:
            return None
        s, conf, verdict = a.score, a.confidence, None
    elif name.startswith("drop:"):
        rest = {k: (r.score, r.confidence) for k, r in ok.items() if k != name.split(":", 1)[1]}
        if not rest:
            return None
        adj = pred.verdict.adjustment if pred.verdict else 0.0
        s = max(-1.0, min(1.0, weighted_score(rest, pred.weights) + adj))
        conf, verdict = pred.confidence, pred.verdict
    else:
        raise ValueError(name)
    fc = analytic_forecast(f.price_now, f.sigma_per_step, f.steps, s)
    upd = {"s_final": s, "s_raw": s if verdict is None else pred.s_raw, "confidence": conf, "forecast": fc}
    if verdict is None:
        upd["verdict"] = None
    return pred.model_copy(update=upd)


def variant_names(preds: list[Prediction]) -> list[str]:
    present = sorted({r.agent for p in preds for r in p.reports if r.ok})
    return ["full", "team_no_debate", *[f"single:{a}" for a in present], *[f"drop:{a}" for a in present]]
