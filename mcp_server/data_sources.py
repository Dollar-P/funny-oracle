"""Raw data access: yfinance, FRED, alternative.me, CoinGecko.

Functions here may raise; `server.py` wraps them into `{"error":..., "data": None}`.
Everything is cached in SQLite with TTLs from config.
"""
from __future__ import annotations

import io
import math
from datetime import datetime, timezone

import logging

import httpx
import pandas as pd
import yfinance as yf

import config
from core import cache
from core.asset import coingecko_id, normalize

HTTP_TIMEOUT = 15.0
logging.getLogger("httpx").setLevel(logging.WARNING)
UA = {"User-Agent": "funny-oracle/0.1 (educational project)"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ttl_for(interval: str) -> int:
    return config.TTL_INTRADAY if interval in config.INTRADAY_INTERVALS else config.TTL_DAILY


def _num(x, nd: int = 4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, 2) if abs(x) >= 1000 else round(x, nd)


# --- Price -------------------------------------------------------------------

def fetch_ohlcv(ticker: str, interval: str = "1d", period: str | None = None) -> pd.DataFrame:
    """OHLCV DataFrame from yfinance (cached as JSON records)."""
    ticker = normalize(ticker)
    period = period or config.DEFAULT_PERIOD.get(interval, "1y")
    key = f"ohlcv:{ticker}:{interval}:{period}"

    def load():
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return {"error": f"no price data for {ticker} ({interval}, {period})"}
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        df.index = pd.to_datetime(df.index, utc=True)
        return {
            "index": [ts.isoformat() for ts in df.index],
            "data": df.round(6).values.tolist(),
        }

    raw = cache.cached(key, _ttl_for(interval), load)
    if raw.get("error"):
        raise ValueError(raw["error"])
    return pd.DataFrame(
        raw["data"],
        index=pd.to_datetime(raw["index"], utc=True),
        columns=["Open", "High", "Low", "Close", "Volume"],
    )


# --- Fundamentals ------------------------------------------------------------

FUNDAMENTAL_FIELDS = {
    "name": "shortName",
    "sector": "sector",
    "industry": "industry",
    "currency": "currency",
    "market_cap": "marketCap",
    "price": "currentPrice",
    "pe_trailing": "trailingPE",
    "pe_forward": "forwardPE",
    "peg": "trailingPegRatio",
    "price_to_book": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months",
    "ev_to_ebitda": "enterpriseToEbitda",
    "gross_margin": "grossMargins",
    "operating_margin": "operatingMargins",
    "profit_margin": "profitMargins",
    "revenue_growth_yoy": "revenueGrowth",
    "earnings_growth_yoy": "earningsGrowth",
    "free_cash_flow": "freeCashflow",
    "debt_to_equity": "debtToEquity",
    "roe": "returnOnEquity",
    "dividend_yield": "dividendYield",
    "beta": "beta",
    "52w_high": "fiftyTwoWeekHigh",
    "52w_low": "fiftyTwoWeekLow",
    "analyst_target_mean": "targetMeanPrice",
    "analyst_target_low": "targetLowPrice",
    "analyst_target_high": "targetHighPrice",
    "analyst_recommendation": "recommendationKey",
    "num_analysts": "numberOfAnalystOpinions",
}


def fetch_fundamentals(ticker: str) -> dict:
    ticker = normalize(ticker)

    def load():
        info = yf.Ticker(ticker).info or {}
        if not info or len(info) < 3:
            return {"error": f"no fundamentals for {ticker}"}
        out = {}
        for k, src in FUNDAMENTAL_FIELDS.items():
            v = info.get(src)
            out[k] = v if isinstance(v, str) or v is None else _num(v)
        if out.get("peg") is None:
            out["peg"] = _num(info.get("pegRatio"))
        if out.get("price") is None:
            out["price"] = _num(info.get("regularMarketPrice") or info.get("previousClose"))
        return {k: v for k, v in out.items() if v is not None}

    res = cache.cached(f"fund:{ticker}", config.TTL_SLOW, load)
    if res.get("error"):
        raise ValueError(res["error"])
    return res


# --- News --------------------------------------------------------------------

def fetch_news(ticker: str, limit: int = 8) -> list[dict]:
    ticker = normalize(ticker)

    def load():
        items = yf.Ticker(ticker).news or []
        out = []
        for it in items:
            c = it.get("content", it)  # yfinance >=0.2.5x nests under "content"
            title = c.get("title")
            if not title:
                continue
            provider = c.get("provider") or {}
            link = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link")
            date = c.get("pubDate") or c.get("displayTime")
            if date is None and c.get("providerPublishTime"):
                date = datetime.fromtimestamp(c["providerPublishTime"], timezone.utc).isoformat()
            out.append({
                "headline": title[:160],
                "publisher": provider.get("displayName") if isinstance(provider, dict) else c.get("publisher"),
                "date": date,
                "link": link,
            })
        return {"items": out}

    res = cache.cached(f"news:{ticker}", config.TTL_DAILY, load)
    return res["items"][:limit]


# --- Macro -------------------------------------------------------------------

FRED_SERIES = {
    "FEDFUNDS": "Fed funds rate (%)",
    "CPIAUCSL": "CPI index (for YoY)",
    "DGS10": "10Y Treasury yield (%)",
    "DFII10": "10Y real yield / TIPS (%)",
    "T10Y2Y": "10Y-2Y spread (pp)",
}


def fetch_fred_series(series_id: str) -> pd.Series:
    """Observations for one FRED series. Uses the API if FRED_API_KEY is set, else public CSV."""

    def load():
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=UA, follow_redirects=True) as client:
            if config.FRED_API_KEY:
                r = client.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": config.FRED_API_KEY,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 30,
                    },
                )
                r.raise_for_status()
                obs = [(o["date"], o["value"]) for o in r.json()["observations"]]
            else:
                r = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id})
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                obs = list(zip(df.iloc[-30:, 0].astype(str), df.iloc[-30:, 1].astype(str)))
        clean = []
        for d, v in obs:
            try:
                fv = float(v)
            except ValueError:  # FRED uses "." for missing
                continue
            if math.isfinite(fv):
                clean.append((d, fv))
        clean.sort()
        return {"obs": clean}

    res = cache.cached(f"fred:{series_id}", config.TTL_SLOW, load)
    d, v = zip(*res["obs"]) if res["obs"] else ((), ())
    return pd.Series(v, index=pd.to_datetime(list(d)), name=series_id, dtype=float)


def fetch_macro() -> dict:
    out: dict = {}
    errors: dict = {}
    for sid in ("FEDFUNDS", "DGS10", "DFII10", "T10Y2Y"):
        try:
            s = fetch_fred_series(sid)
            out[sid] = {"value": _num(s.iloc[-1], 2), "date": s.index[-1].date().isoformat()}
            if sid != "FEDFUNDS":  # daily series: ~22 business days ≈ 1 month
                out[sid]["chg_1m"] = _num(s.iloc[-1] - s.iloc[max(0, len(s) - 22)], 2)
        except Exception as e:  # keep going; partial macro is still useful
            errors[sid] = str(e)[:120]
    try:
        cpi = fetch_fred_series("CPIAUCSL")
        last_date = cpi.index[-1]
        year_ago = last_date - pd.DateOffset(years=1)
        if cpi.index[0] <= year_ago:
            # FRED can skip months (e.g. Oct 2025); interpolate monthly by date, not position.
            monthly = cpi.resample("MS").mean().interpolate()
            out["CPI_YOY"] = {
                "value": _num((cpi.iloc[-1] / monthly.loc[year_ago] - 1) * 100, 2),
                "date": last_date.date().isoformat(),
            }
    except Exception as e:
        errors["CPIAUCSL"] = str(e)[:120]

    for name, sym in (("DXY", "DX-Y.NYB"), ("VIX", "^VIX")):
        try:
            df = fetch_ohlcv(sym, "1d", "3mo")
            c = df["Close"]
            out[name] = {
                "value": _num(c.iloc[-1], 2),
                "chg_1m_pct": _num((c.iloc[-1] / c.iloc[max(0, len(c) - 22)] - 1) * 100, 2),
                "date": df.index[-1].date().isoformat(),
            }
        except Exception as e:
            errors[name] = str(e)[:120]

    if "DGS10" in out and "T10Y2Y" in out:
        out["curve_state"] = "inverted" if out["T10Y2Y"]["value"] < 0 else "normal"
    if errors:
        out["partial_errors"] = errors
    if not any(k for k in out if k not in ("partial_errors",)):
        raise ValueError(f"all macro sources failed: {errors}")
    return out


# --- Sentiment ---------------------------------------------------------------

def fetch_fear_greed() -> dict:
    def load():
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=UA) as client:
            r = client.get("https://api.alternative.me/fng/", params={"limit": 7})
            r.raise_for_status()
            rows = r.json()["data"]
        return {
            "value": int(rows[0]["value"]),
            "label": rows[0]["value_classification"],
            "avg_7d": round(sum(int(x["value"]) for x in rows) / len(rows), 1),
        }

    return cache.cached("fng", config.TTL_DAILY, load)


# --- CoinGecko ---------------------------------------------------------------

def _coingecko_get(path: str, params: dict | None = None) -> dict:
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=UA) as client:
        r = client.get(f"https://api.coingecko.com/api/v3{path}", params=params)
        r.raise_for_status()
        return r.json()


def fetch_onchain_lite(coin: str) -> dict:
    cid = coingecko_id(coin)

    def load():
        d = _coingecko_get(
            f"/coins/{cid}",
            {"localization": "false", "tickers": "false", "community_data": "false",
             "developer_data": "false", "sparkline": "false"},
        )
        md = d["market_data"]
        g = _coingecko_get("/global")["data"]
        mcap = md["market_cap"]["usd"]
        vol = md["total_volume"]["usd"]
        return {
            "coin": cid,
            "symbol": d.get("symbol", "").upper(),
            "price_usd": _num(md["current_price"]["usd"]),
            "market_cap_usd": _num(mcap),
            "market_cap_rank": d.get("market_cap_rank"),
            "volume_24h_usd": _num(vol),
            "volume_to_mcap": _num(vol / mcap if mcap else None),
            "dominance_pct": _num(g["market_cap_percentage"].get(d.get("symbol", "").lower()), 2),
            "ath_usd": _num(md["ath"]["usd"]),
            "ath_distance_pct": _num(md["ath_change_percentage"]["usd"], 2),
            "ath_date": (md.get("ath_date") or {}).get("usd", "")[:10],
            "chg_7d_pct": _num(md.get("price_change_percentage_7d"), 2),
            "chg_30d_pct": _num(md.get("price_change_percentage_30d"), 2),
            "circulating_supply": _num(md.get("circulating_supply")),
            "max_supply": _num(md.get("max_supply")),
            "total_mcap_usd": _num(g["total_market_cap"]["usd"]),
            "total_mcap_chg_24h_pct": _num(g.get("market_cap_change_percentage_24h_usd"), 2),
        }

    return cache.cached(f"cg:{cid}", config.TTL_DAILY, load)
