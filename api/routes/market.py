"""GET /api/tickers/search · /api/quote/{ticker} · /api/history/{ticker}."""
from __future__ import annotations

import asyncio
import logging
import time

import pandas as pd

from fastapi import APIRouter, HTTPException, Query

from core.asset import classify, normalize
from mcp_server import data_sources as ds

log = logging.getLogger(__name__)
router = APIRouter()

CURATED = [
    ("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"),
    ("META", "Meta Platforms"), ("TSLA", "Tesla"), ("AVGO", "Broadcom"), ("AMD", "AMD"), ("NFLX", "Netflix"),
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana"), ("BNB-USD", "BNB"),
    ("XRP-USD", "XRP"), ("DOGE-USD", "Dogecoin"), ("ADA-USD", "Cardano"),
    ("GC=F", "Gold futures"), ("GLD", "SPDR Gold Shares"),
]
PRESETS = ["NVDA", "AAPL", "BTC-USD", "ETH-USD", "GC=F"]
ALIASES = {"GOLD": "GC=F", "BITCOIN": "BTC-USD", "ETHEREUM": "ETH-USD"}


def _entry(sym: str, name: str, source: str = "curated") -> dict:
    return {"symbol": sym, "name": name, "asset_type": classify(sym), "source": source}


def remote_search(q: str, limit: int = 8) -> list[dict]:
    """yfinance search (equities, ETFs, crypto); empty on any failure."""
    try:
        import yfinance as yf

        res = yf.Search(q, max_results=limit, news_count=0)
        out = []
        for it in res.quotes or []:
            sym = it.get("symbol")
            kind = (it.get("quoteType") or "").upper()
            if not sym or kind not in {"EQUITY", "ETF", "CRYPTOCURRENCY", "FUTURE"}:
                continue
            out.append(_entry(sym, it.get("shortname") or it.get("longname") or sym, "yfinance"))
        return out
    except Exception as e:
        log.info("yfinance search failed for %r: %s", q, e)
        return []


@router.get("/tickers/search")
async def search(q: str = "", remote: bool = True, limit: int = Query(10, le=25)):
    qn = q.strip().upper()
    if not qn:
        return {"results": [_entry(s, n) for s, n in CURATED if s in PRESETS], "presets": PRESETS}
    qn = ALIASES.get(qn, qn)
    hits = [_entry(s, n) for s, n in CURATED if qn in s or qn.lower() in n.lower()]
    hits.sort(key=lambda h: (not h["symbol"].startswith(qn), h["symbol"]))
    if remote and len(qn) >= 2 and len(hits) < limit:
        try:
            more = await asyncio.wait_for(asyncio.to_thread(remote_search, q.strip()), 4)
        except asyncio.TimeoutError:
            more = []
        seen = {h["symbol"] for h in hits}
        hits += [m for m in more if m["symbol"] not in seen]
    return {"results": hits[:limit], "presets": PRESETS}


_QUOTES: dict[str, tuple[float, dict]] = {}
QUOTE_TTL = 60


def quote_sync(ticker: str) -> dict:
    t = normalize(ticker)
    hit = _QUOTES.get(t)
    if hit and time.time() - hit[0] < QUOTE_TTL:
        return hit[1]
    daily = ds.fetch_ohlcv(t, "1d", "1mo")
    last = float(daily["Close"].iloc[-1])
    prev = float(daily["Close"].iloc[-2]) if len(daily) > 1 else last
    spark: list[float] = []
    try:
        intra = ds.fetch_ohlcv(t, "30m", "5d")
        spark = [round(float(x), 6) for x in intra["Close"].iloc[-48:]]
        last = float(intra["Close"].iloc[-1])
    except Exception:
        spark = [round(float(x), 6) for x in daily["Close"].iloc[-20:]]
    q = {"symbol": t, "asset_type": classify(t), "price": last, "prev_close": prev,
         "change_pct": (last / prev - 1) * 100 if prev else 0.0, "sparkline": spark,
         "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _QUOTES[t] = (time.time(), q)
    return q


@router.get("/quote/{ticker}")
async def quote(ticker: str):
    try:
        return await asyncio.to_thread(quote_sync, ticker)
    except Exception as e:
        raise HTTPException(404, f"no quote for {ticker}: {e}")


COARSER = ["1m", "5m", "15m", "1h", "1d"]


def _parse_ts(s: str | None) -> pd.Timestamp | None:
    if not s:
        return None
    s = s.strip()
    if len(s) > 19 and s[-6] == " ":  # an unencoded "+00:00" offset arrives as " 00:00"
        s = s[:-6] + "+" + s[-5:]
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def history_window(ticker: str, interval: str, start: pd.Timestamp, end: pd.Timestamp | None) -> tuple[str, pd.DataFrame]:
    """Bars covering [start, end]. yfinance keeps 1m for ~7 days and 5m/15m for ~60, so when the requested
    interval no longer reaches back to `start`, fall back to the next coarser one."""
    chain = COARSER[COARSER.index(interval):] if interval in COARSER else [interval, "1d"]
    last: tuple[str, pd.DataFrame] | None = None
    for iv in chain:
        try:
            df = ds.fetch_ohlcv(ticker, iv, None)
        except Exception as e:
            log.info("history %s %s failed: %s", ticker, iv, e)
            continue
        win = df[(df.index >= start) & ((df.index <= end) if end is not None else True)]
        last = (iv, win)
        if len(df) and df.index[0] <= start and len(win) >= 2:
            return iv, win
    if last is None:
        raise ValueError("no data")
    return last


@router.get("/history/{ticker}")
async def history(ticker: str, interval: str = "1d", period: str | None = None, limit: int = Query(300, le=500),
                  start: str | None = None, end: str | None = None):
    """Latest `limit` bars, or with `start` (ISO) the bars between start and end (default now) — used to
    anchor a replayed forecast to its original time. `interval` in the reply is the one actually used."""
    try:
        if start:
            used, df = await asyncio.to_thread(history_window, ticker, interval, _parse_ts(start), _parse_ts(end))
        else:
            used, df = interval, await asyncio.to_thread(ds.fetch_ohlcv, ticker, interval, period)
    except Exception as e:
        raise HTTPException(404, f"no history for {ticker}: {e}")
    df = df.iloc[-limit:]
    candles = [{"time": int(ts.timestamp()), "open": float(r.Open), "high": float(r.High), "low": float(r.Low),
                "close": float(r.Close), "volume": float(r.Volume)} for ts, r in df.iterrows()]
    return {"symbol": normalize(ticker), "interval": used, "requested_interval": interval, "candles": candles}
