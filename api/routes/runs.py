"""Predictions, SSE run events, duels and replays."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

import config
from api import mock_runner
from api.runs import MANAGER, Run, TooManyRuns
from core import cost, scoreboard
from core.asset import normalize
from core.cost import BudgetGuard
from core.db import connect
from mcp_server import data_sources as ds

log = logging.getLogger(__name__)
router = APIRouter()


class PredictionRequest(BaseModel):
    ticker: str
    mode: str = "swing"
    horizon: str = "1M"
    analyst_model: str | None = None
    judge_model: str | None = None
    mock: bool | None = None


class DuelRequest(BaseModel):
    ticker: str
    mode: str = "swing"
    horizon: str = "1M"
    model_a: str
    model_b: str
    mock: bool | None = None


def _is_mock(body_flag: bool | None, query_flag: int | None) -> bool:
    if query_flag is not None:
        return bool(query_flag)
    if body_flag is not None:
        return body_flag
    return config.MOCK_LLM


def _validate(mode: str, horizon: str) -> None:
    if mode not in config.HORIZONS:
        raise HTTPException(422, f"mode must be one of {list(config.HORIZONS)}")
    if horizon not in config.HORIZONS[mode]:
        raise HTTPException(422, f"horizon for {mode} must be one of {list(config.HORIZONS[mode])}")


def _models(mode: str, analyst: str | None, judge: str | None) -> dict[str, str]:
    import orchestrator

    analyst = analyst or None
    judge = judge or analyst  # picking one "Oracle brain" means it judges too
    m = orchestrator.resolve_models(mode, config.provider_for(analyst) if analyst else None, analyst, judge)
    for role, mid in m.items():
        if mid and config.provider_for(mid) != config.provider_for(m["analyst"]):
            raise HTTPException(422, "analyst and judge must use the same provider")
    return m


def _budget_refusal(mode: str, models: list[str]) -> JSONResponse | None:
    g = BudgetGuard()
    spent = cost.spent_usd()
    est = sum(g.estimate_usd(mode, m) for m in models)
    if spent + est > g.budget_usd:
        return JSONResponse(status_code=402, content={
            "reason": f"The Oracle is out of coins: spent ฿{cost.usd_to_thb(spent):.2f} + estimated "
                      f"฿{cost.usd_to_thb(est):.2f} would exceed the ฿{g.budget_thb:.0f} budget.",
            "spent_thb": round(cost.usd_to_thb(spent), 2), "est_thb": round(cost.usd_to_thb(est), 2),
            "budget_thb": g.budget_thb})
    return None


def _live_price(ticker: str, interval: str = "1d") -> float | None:
    """Last close at the interval the forecast chart draws (so the fan starts at the last live candle).

    The daily close is only a fallback: it is cached for 15 min and drifts from intraday candles.
    """
    for iv in dict.fromkeys([interval, "1d"]):
        try:
            return float(ds.fetch_ohlcv(ticker, iv, None)["Close"].iloc[-1])
        except Exception as e:
            log.warning("live price %s %s failed: %s", ticker, iv, e)
    return None


def _start_mock(ticker: str, mode: str, horizon: str, models: dict | None, *, pid: int | None = None,
                exclude: set[int] | None = None, duel_id: str | None = None, shift: bool = True) -> Run:
    """shift=True: a mock show happening now (relabelled, clock → now, prices rescaled to the live price).
    shift=False: replay of the original show as it was (original time, prices and result)."""
    pid = pid if pid is not None else mock_runner.pick(ticker, mode, exclude)
    if pid is None:
        raise HTTPException(404, "no stored predictions to replay in mock mode — run one real prediction first")
    stored = mock_runner.load(pid)
    if stored is None:
        raise HTTPException(404, f"prediction {pid} not found")
    live = None
    if shift:  # always rescale "now" shows to the live price at the chart's interval
        iv = config.HORIZONS[stored.mode].get(horizon, {}).get("interval") if mode == stored.mode else None
        live = _live_price(ticker, iv or config.HORIZONS[stored.mode][stored.horizon]["interval"])
    p = mock_runner.adapt(stored, ticker if shift else None, mode, horizon, live, shift_clock=shift)
    if models:
        p.models = models  # the mock show pretends to run on the models the user picked
    params = {"ticker": p.ticker, "mode": p.mode, "horizon": p.horizon, "models": models, "replay_of": pid,
              "shifted": shift}
    rescaled = (live is not None) if shift else None

    async def body(run: Run):
        await mock_runner.play(run, p, replay_of=pid, models=models, shifted=shift, rescaled=rescaled)

    return MANAGER.start_mock(params, body, duel_id=duel_id)


@router.post("/predictions")
async def create_prediction(req: PredictionRequest, mock: int | None = Query(None)):
    _validate(req.mode, req.horizon)
    is_mock = _is_mock(req.mock, mock)
    models = _models(req.mode, req.analyst_model, req.judge_model)
    try:
        MANAGER.ensure_capacity(1)
    except TooManyRuns as e:
        raise HTTPException(429, str(e))
    if is_mock:
        run = _start_mock(req.ticker, req.mode, req.horizon, models)
    else:
        refusal = _budget_refusal(req.mode, [models["analyst"]])
        if refusal:
            return refusal
        run = MANAGER.start_real({"ticker": req.ticker, "mode": req.mode, "horizon": req.horizon,
                                  "analyst_model": models["analyst"], "judge_model": models["judge"]})
    return {"run_id": run.run_id, "mock": is_mock}


def _stored_models(pid: int) -> dict | None:
    """Models from the DB columns (rows saved before R1 have none in their JSON; backfilled by the migration)."""
    with connect() as conn:
        row = conn.execute("SELECT analyst_model, judge_model FROM predictions WHERE id = ?", (pid,)).fetchone()
    if row and row["analyst_model"]:
        return {"analyst": row["analyst_model"], "judge": row["judge_model"] or row["analyst_model"]}
    return None


@router.post("/predictions/{pid}/replay")
async def replay_prediction(pid: int):
    """▶ Replay the show: stream a stored prediction's events through the live view (MockRunner)."""
    stored = mock_runner.load(pid)
    if stored is None:
        raise HTTPException(404, f"prediction {pid} not found")
    try:
        MANAGER.ensure_capacity(1)
    except TooManyRuns as e:
        raise HTTPException(429, str(e))
    models = stored.models or _stored_models(pid)
    run = _start_mock(stored.ticker, stored.mode, stored.horizon, models, pid=pid, shift=False)
    return {"run_id": run.run_id, "mock": True}


@router.get("/runs/{run_id}")
async def run_status(run_id: str):
    run = MANAGER.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run.summary()


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = 0):
    run = MANAGER.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run (the server may have restarted)")
    last = request.headers.get("last-event-id")
    start = max(after, int(last) if last and last.isdigit() else 0)

    async def gen():
        async for ev in run.stream(start):
            if await request.is_disconnected():
                break
            yield ServerSentEvent(data=json.dumps(ev, default=str), event=ev["type"], id=str(ev["seq"]))

    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})


@router.get("/runs")
async def runs():
    return {"active": [r.summary() for r in MANAGER.active()], "max_concurrent": MANAGER.max_concurrent}


# --- stored predictions -----------------------------------------------------------

LIST_COLS = ("id, run_id, created_at, ticker, asset_type, mode, horizon, target_time, price_now, p10, p50, p90,"
             " prob_up, s_final, confidence, headline, cost_usd, cost_actual_usd, source, actual_price,"
             " evaluated_at, analyst_model, judge_model, duel_id")


@router.get("/predictions")
async def list_predictions(limit: int = Query(50, le=500), ticker: str | None = None, mode: str | None = None,
                           model: str | None = None):
    q, params = f"SELECT {LIST_COLS} FROM predictions WHERE 1=1", []
    if ticker:
        q += " AND ticker = ?"
        params.append(normalize(ticker))
    if mode:
        q += " AND mode = ?"
        params.append(mode)
    if model:
        q += " AND analyst_model = ?"
        params.append(model)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    return {"predictions": rows}


@router.get("/predictions/{pid}")
async def get_prediction(pid: int, evaluate: bool = False):
    """evaluate=1: if the target time has passed and there is no actual price yet, try to fetch it now."""
    p = scoreboard.load(pid)
    if p is None:
        raise HTTPException(404, f"prediction {pid} not found")
    if evaluate:
        with connect() as conn:
            row = conn.execute("SELECT actual_price, target_time FROM predictions WHERE id = ?", (pid,)).fetchone()
        if row["actual_price"] is None and row["target_time"] <= scoreboard.utcnow().isoformat():
            try:
                await asyncio.wait_for(asyncio.to_thread(scoreboard.evaluate_due, 20), 20)
            except Exception:  # network trouble: the client simply retries later
                pass
    with connect() as conn:
        row = conn.execute("SELECT actual_price, evaluated_at, source, analyst_model, judge_model FROM predictions"
                           " WHERE id = ?", (pid,)).fetchone()
    if not p.models and row["analyst_model"]:  # saved before R1: models backfilled from llm_usage
        p.models = {"analyst": row["analyst_model"], "judge": row["judge_model"] or row["analyst_model"]}
    return {"prediction": json.loads(p.model_dump_json()), "actual_price": row["actual_price"],
            "evaluated_at": row["evaluated_at"], "source": row["source"]}


# --- duels -----------------------------------------------------------------------------

@router.post("/duels")
async def create_duel(req: DuelRequest, mock: int | None = Query(None)):
    _validate(req.mode, req.horizon)
    is_mock = _is_mock(req.mock, mock)
    ma = _models(req.mode, req.model_a, None)
    mb = _models(req.mode, req.model_b, None)
    try:
        MANAGER.ensure_capacity(2)
    except TooManyRuns as e:
        raise HTTPException(429, str(e))
    duel_id = uuid.uuid4().hex[:12]
    if is_mock:
        a = _start_mock(req.ticker, req.mode, req.horizon, ma, duel_id=duel_id)
        b = _start_mock(req.ticker, req.mode, req.horizon, mb, duel_id=duel_id,
                        exclude={a.params["replay_of"]})  # a different stored show for model B
    else:
        refusal = _budget_refusal(req.mode, [ma["analyst"], mb["analyst"]])
        if refusal:
            return refusal
        common = {"ticker": req.ticker, "mode": req.mode, "horizon": req.horizon}
        a = MANAGER.start_real({**common, "analyst_model": ma["analyst"], "judge_model": ma["judge"]}, duel_id)
        b = MANAGER.start_real({**common, "analyst_model": mb["analyst"], "judge_model": mb["judge"]}, duel_id)
    MANAGER.duels[duel_id] = {"run_ids": [a.run_id, b.run_id], "models": [ma, mb], "mock": is_mock,
                              "ticker": normalize(req.ticker), "mode": req.mode, "horizon": req.horizon}
    return {"duel_id": duel_id, "run_ids": [a.run_id, b.run_id], "mock": is_mock}


def compare(a: dict | None, b: dict | None) -> dict | None:
    """Head-to-head summary of two Prediction dicts."""
    if not a or not b:
        return None

    def side(p):
        f = p["forecast"]
        return {"p50": f["p50"], "p10": f["p10"], "p90": f["p90"], "prob_up": f["prob_up"],
                "price_now": f["price_now"], "confidence": p["confidence"], "s_final": p["s_final"],
                "cost_usd": p.get("cost_usd", 0), "elapsed_s": p.get("elapsed_s", 0),
                "headline": (p.get("verdict") or {}).get("headline"),
                "direction": "bull" if f["prob_up"] > 0.5 else "bear" if f["prob_up"] < 0.5 else "flat"}

    sa, sb = side(a), side(b)
    agents = sorted({r["agent"] for r in a["reports"]} | {r["agent"] for r in b["reports"]})
    ra = {r["agent"]: r for r in a["reports"]}
    rb = {r["agent"]: r for r in b["reports"]}
    diffs = [{"agent": ag,
              "a": ra[ag]["score"] if ag in ra and not ra[ag].get("error") else None,
              "b": rb[ag]["score"] if ag in rb and not rb[ag].get("error") else None} for ag in agents]
    agree = sa["direction"] == sb["direction"]
    if agree:
        banner = {"bull": "🤝 Both bullish", "bear": "🤝 Both bearish"}.get(sa["direction"], "🤝 Both on the fence")
    else:
        banner = "⚔️ They disagree!"
    return {"a": sa, "b": sb, "agents": diffs, "agree": agree, "banner": banner,
            "p50_gap_pct": (sa["p50"] / sb["p50"] - 1) * 100 if sb["p50"] else 0.0}


@router.get("/duels/{duel_id}")
async def get_duel(duel_id: str):
    meta = MANAGER.duels.get(duel_id)
    preds: list[dict | None] = [None, None]
    status = ["unknown", "unknown"]
    if meta:
        for i, rid in enumerate(meta["run_ids"]):
            run = MANAGER.get(rid)
            if run:
                preds[i] = run.prediction
                status[i] = "done" if run.prediction else ("failed" if run.done else "running")
    if not meta or (not meta["mock"] and not all(preds)):
        with connect() as conn:
            rows = conn.execute("SELECT prediction_json FROM predictions WHERE duel_id = ? ORDER BY id",
                                (duel_id,)).fetchall()
        stored = [json.loads(r[0]) for r in rows if r[0]]
        if meta:  # match by run_id so A stays A
            by_run = {p.get("run_id"): p for p in stored}
            preds = [preds[i] or by_run.get(rid) for i, rid in enumerate(meta["run_ids"])]
        elif stored:
            preds = (stored + [None, None])[:2]
            status = ["done" if p else "unknown" for p in preds]
        else:
            raise HTTPException(404, "unknown duel")
    return {"duel_id": duel_id, "meta": meta, "status": status, "predictions": preds, "comparison": compare(*preds)}
