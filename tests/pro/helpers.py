"""Shared builders for Pro unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.schemas import Catalyst, Forecast, Prediction, Verdict

T0 = datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)   # a Monday, after the US close


def make_pred(*, s_final=0.6, confidence=0.7, prob_up=0.62, price=100.0, p10=90.0, p50=104.0, p90=118.0,
              ticker="NVDA", asset_type="stock", mode="swing", horizon="1M", created=T0, minutes=30 * 24 * 60,
              catalysts=(), no_trade_reason=None, degraded=(), pid=1) -> Prediction:
    v = Verdict(adjustment=0.0, final_confidence=confidence, headline="h", summary="s", key_risks=["r"],
                thesis="thesis text", invalidation="close below 90", no_trade_reason=no_trade_reason,
                catalysts=[Catalyst(**c) if isinstance(c, dict) else c for c in catalysts])
    return Prediction(
        id=pid, created_at=created.isoformat(), ticker=ticker, asset_type=asset_type, mode=mode, horizon=horizon,
        target_time=(created + timedelta(minutes=minutes)).isoformat(), s_raw=s_final, s_final=s_final,
        confidence=confidence, weights={}, reports=[], verdict=v,
        forecast=Forecast(price_now=price, p10=p10, p50=p50, p90=p90, prob_up=prob_up, sigma_per_step=0.01,
                          steps=21, step_label="1d"),
        prompt_version="v2", degraded=list(degraded))
