"""GET /api/pro/risk — exposure, limit usage, correlations of open positions, rejected-signal log."""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter

from api import pro_state
from api.routes.pro.common import experiment, now, rows
from api.routes.pro.ledger import open_positions
from core import pro_settings
from trading.paper_account import PaperAccount
from trading.risk import _corr

router = APIRouter()


@router.get("/risk")
def risk():
    s = pro_settings.load()
    exp = experiment()
    rp = s["risk"]
    pos = open_positions(exp)
    acct = PaperAccount(exp, s["starting_equity"])
    live = [p for p in pos if p["state"] == "open" and p.get("mark")]
    cash, mv, gross, open_risk = acct.marked({p["ticker"]: p["mark"] for p in live})
    eq = float(cash + mv) or 1.0
    by_class: dict[str, dict] = {}
    for p in live:
        c = by_class.setdefault(p["asset_type"], {"long": 0.0, "short": 0.0})
        c[p["side"]] += p["qty_open"] * p["mark"] / eq
    peak = float(acct.peak())
    limits = [
        {"rule": "gross_exposure", "used": float(gross) / eq, "limit": rp["max_gross"]},
        *[{"rule": f"class:{a}", "used": sum(by_class.get(a, {}).values()), "limit": cap}
          for a, cap in rp["max_class"].items()],
        {"rule": "open_positions", "used": float(len([p for p in pos])), "limit": float(rp["max_open"]), "unit": "count"},
        {"rule": "open_risk", "used": open_risk / eq, "limit": None},
        {"rule": "drawdown", "used": eq / max(peak, eq) - 1, "limit": rp["max_dd"]},
        {"rule": "max_position", "used": max([p["qty_open"] * p["mark"] / eq for p in live], default=0.0),
         "limit": rp["max_position"]},
    ]
    tickers = [p["ticker"] for p in live]
    rets = {}
    d = pro_state.desk()
    for p in live:
        try:
            df = d.bars(p["ticker"], p["asset_type"], "1d", now())
            r = df["Close"].pct_change()
            r.index = pd.DatetimeIndex(r.index).tz_convert("UTC").normalize()
            rets[p["ticker"]] = r
        except Exception:
            pass
    corr = [[(1.0 if a == b else _corr(rets[a], rets[b], rp.get("corr_lookback", 60)))
             if a in rets and b in rets else None for b in tickers] for a in tickers]
    rejected = rows("SELECT e.ts, e.signal_id, e.rule, e.detail, s.ticker FROM risk_events e LEFT JOIN signals s ON"
                    " s.id = e.signal_id WHERE e.experiment_id = ? ORDER BY e.id DESC LIMIT 200", (exp,))
    return {"equity": eq, "exposure": by_class, "limits": limits, "correlation": {"tickers": tickers, "matrix": corr},
            "rejections": rejected, "halted": bool(s.get("halted")), "params": rp}
