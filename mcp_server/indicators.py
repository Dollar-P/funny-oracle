"""Technical indicators computed from an OHLCV DataFrame (pure pandas/numpy)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["Close"].shift()
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def volume_zscore(volume: pd.Series, period: int = 20) -> pd.Series:
    mean = volume.rolling(period).mean()
    sd = volume.rolling(period).std(ddof=0)
    return (volume - mean) / sd.replace(0, np.nan)


def support_resistance(df: pd.DataFrame, window: int = 5, lookback: int = 120, n: int = 3) -> dict:
    """Nearest swing-low supports below and swing-high resistances above the last close."""
    d = df.tail(lookback)
    last = float(d["Close"].iloc[-1])
    span = 2 * window + 1
    highs = d["High"][d["High"] == d["High"].rolling(span, center=True).max()].dropna()
    lows = d["Low"][d["Low"] == d["Low"].rolling(span, center=True).min()].dropna()
    res = sorted({float(x) for x in highs if x > last})[:n]
    sup = sorted({float(x) for x in lows if x < last}, reverse=True)[:n]
    # Fall back to range extremes so the LLM always gets something.
    if not res:
        res = [float(d["High"].max())]
    if not sup:
        sup = [float(d["Low"].min())]
    return {"support": sup, "resistance": res}


def _r(x, nd: int = 4):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return None
    x = float(x)
    if abs(x) >= 1000:
        return round(x, 2)
    return round(x, nd)


def compute_all(df: pd.DataFrame) -> dict:
    """Summary of the latest indicator values. `df` needs Open/High/Low/Close/Volume."""
    if len(df) < 30:
        raise ValueError(f"need at least 30 bars, got {len(df)}")
    close = df["Close"]
    last = float(close.iloc[-1])

    r = rsi(close)
    m = macd(close).iloc[-1]
    m_prev = macd(close).iloc[-2]
    bb = bollinger(close).iloc[-1]
    a = atr(df).iloc[-1]
    vz = volume_zscore(df["Volume"]).iloc[-1] if df["Volume"].sum() > 0 else None

    sma = {p: close.rolling(p).mean().iloc[-1] if len(close) >= p else None for p in (20, 50, 200)}
    ema = {p: close.ewm(span=p, adjust=False).mean().iloc[-1] for p in (12, 26)}

    def above(v):
        return None if v is None or pd.isna(v) else bool(last > v)

    trend = "n/a"
    if sma[50] is not None and sma[200] is not None:
        trend = "golden_cross_regime" if sma[50] > sma[200] else "death_cross_regime"

    cross = None
    if m_prev["hist"] <= 0 < m["hist"]:
        cross = "bullish_cross"
    elif m_prev["hist"] >= 0 > m["hist"]:
        cross = "bearish_cross"

    rsi_v = r.iloc[-1]
    return {
        "last": _r(last),
        "rsi14": _r(rsi_v, 1),
        "rsi_state": None if pd.isna(rsi_v) else ("overbought" if rsi_v > 70 else "oversold" if rsi_v < 30 else "neutral"),
        "macd": {"line": _r(m["macd"]), "signal": _r(m["signal"]), "hist": _r(m["hist"]), "event": cross},
        "sma": {f"sma{p}": _r(v) for p, v in sma.items()},
        "price_above_sma": {f"sma{p}": above(v) for p, v in sma.items()},
        "ema": {f"ema{p}": _r(v) for p, v in ema.items()},
        "sma_regime": trend,
        "bollinger": {"upper": _r(bb["upper"]), "mid": _r(bb["mid"]), "lower": _r(bb["lower"]), "pct_b": _r(bb["pct_b"], 2)},
        "atr14": _r(a),
        "atr_pct": _r(a / last * 100, 2),
        "volume_z20": _r(vz, 2),
        "levels": {k: [_r(x) for x in v] for k, v in support_resistance(df).items()},
        "bars_used": len(df),
    }
