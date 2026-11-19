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
    bb = bollinger(close).iloc[-1]
    a = atr(df).iloc[-1]
    vz = volume_zscore(df["Volume"]).iloc[-1] if df["Volume"].sum() > 0 else None

    sma = {p: close.rolling(p).mean().iloc[-1] if len(close) >= p else None for p in (20, 50, 200)}
    ema = {p: close.ewm(span=p, adjust=False).mean().iloc[-1] for p in (12, 26)}

    hist = macd(close)["hist"]
    atr_pct_series = atr(df) / close * 100
    levels = support_resistance(df)
    rsi_v = r.iloc[-1]
    signals = {
        "price_vs_bollinger": label_bollinger(bb["pct_b"]),
        "rsi_zone": label_rsi(rsi_v),
        **label_macd(hist),
        "trend_vs_sma20": label_vs(last, sma[20]),
        "trend_vs_sma50": label_vs(last, sma[50]),
        "trend_vs_sma200": label_vs(last, sma[200]),
        "sma_regime": label_regime(sma[50], sma[200]),
        "volume_state": label_volume(vz),
        "volatility_state": label_volatility(atr_pct_series),
    }
    return {
        "last": _r(last),
        # Deterministic labels computed in code — agents should quote these rather than re-derive them.
        "signals": signals,
        "rsi14": _r(rsi_v, 1),
        "macd": {"line": _r(m["macd"]), "signal": _r(m["signal"]), "hist": _r(m["hist"])},
        "sma": {f"sma{p}": _r(v) for p, v in sma.items()},
        "pct_vs_sma": {f"sma{p}": _r((last / v - 1) * 100, 2) if v else None for p, v in sma.items()},
        "ema": {f"ema{p}": _r(v) for p, v in ema.items()},
        "bollinger": {"upper": _r(bb["upper"]), "mid": _r(bb["mid"]), "lower": _r(bb["lower"]), "pct_b": _r(bb["pct_b"], 2)},
        "atr14": _r(a),
        "atr_pct": _r(a / last * 100, 2),
        "volume_z20": _r(vz, 2),
        "levels": {k: [_r(x) for x in v] for k, v in levels.items()},
        "pct_to_nearest": {"support": _r((levels["support"][0] / last - 1) * 100, 2),
                           "resistance": _r((levels["resistance"][0] / last - 1) * 100, 2)},
        "bars_used": len(df),
    }


# --- deterministic signal labels ----------------------------------------------------------

def _missing(x) -> bool:
    return x is None or pd.isna(x)


def label_bollinger(pct_b) -> str:
    """%B: 0 = lower band, 1 = upper band."""
    if _missing(pct_b):
        return "n/a"
    if pct_b > 1:
        return "above_upper_band"
    if pct_b >= 0.8:
        return "near_upper_band"
    if pct_b > 0.2:
        return "mid_band"
    if pct_b >= 0:
        return "near_lower_band"
    return "below_lower_band"


def label_rsi(v) -> str:
    if _missing(v):
        return "n/a"
    if v >= 70:
        return "overbought"
    if v >= 55:
        return "bullish"
    if v > 45:
        return "neutral"
    if v > 30:
        return "bearish"
    return "oversold"


def label_macd(hist: pd.Series, lookback: int = 3) -> dict:
    """macd_cross within the last `lookback` bars + momentum of the histogram."""
    h = hist.dropna()
    out = {"macd_cross": "none", "macd_cross_bars_ago": None, "macd_momentum": "n/a"}
    if len(h) < 2:
        return out
    for ago in range(0, min(lookback, len(h) - 1)):
        cur, prev = h.iloc[-1 - ago], h.iloc[-2 - ago]
        if prev <= 0 < cur:
            out.update(macd_cross="bullish_cross", macd_cross_bars_ago=ago)
            break
        if prev >= 0 > cur:
            out.update(macd_cross="bearish_cross", macd_cross_bars_ago=ago)
            break
    cur, prev = h.iloc[-1], h.iloc[-2]
    if cur > 0:
        out["macd_momentum"] = "bullish_strengthening" if cur > prev else "bullish_fading"
    elif cur < 0:
        out["macd_momentum"] = "bearish_strengthening" if cur < prev else "bearish_fading"
    else:
        out["macd_momentum"] = "flat"
    return out


def label_vs(price: float, ma) -> str:
    if _missing(ma):
        return "n/a"
    return "above" if price > ma else "below" if price < ma else "at"


def label_regime(sma50, sma200) -> str:
    if _missing(sma50) or _missing(sma200):
        return "n/a"
    return "golden_cross_regime" if sma50 > sma200 else "death_cross_regime"


def label_volume(z) -> str:
    if _missing(z):
        return "n/a"
    if z >= 2:
        return "very_high"
    if z >= 1:
        return "high"
    if z <= -1:
        return "low"
    return "normal"


def label_volatility(atr_pct: pd.Series, window: int = 100) -> str:
    s = atr_pct.dropna().tail(window)
    if len(s) < 20:
        return "n/a"
    ratio = s.iloc[-1] / s.median()
    return "elevated" if ratio > 1.3 else "compressed" if ratio < 0.75 else "normal"
