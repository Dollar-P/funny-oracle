"""Calibration layer (PRO.md §6.5): raw P(up) → calibrated P(up), fitted walk-forward.

- Isotonic regression by pool-adjacent-violators (own implementation, no scikit-learn). Outcomes are first
  pooled into ≤ 20 quantile bins of the raw probability (≥ 5 observations each; equal raw values stay together)
  so a handful of extreme 0/1 outcomes cannot pin the curve to 0 or 1; weighted PAV then runs on the bin means.
  Predicted by linear interpolation between block centres, clipped to [ε, 1 − ε]. Falls back to Platt scaling
  (logistic on logit p) when isotonic finds fewer than 3 distinct levels.
- Active only once a bucket (mode × asset class) has n ≥ MIN_CAL evaluated predictions.
- Walk-forward: a prediction made at time t is calibrated only with predictions whose outcome was known
  before t (target_time < t). Parameters are never fitted on the window they are evaluated on (§6.3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

import numpy as np

MIN_CAL = 50
EPS = 0.01


def pav(y: Sequence[float], w: Sequence[float] | None = None) -> np.ndarray:
    """Pool-adjacent-violators: the non-decreasing sequence closest to y in weighted least squares."""
    y = np.asarray(y, dtype=float)
    w = np.ones_like(y) if w is None else np.asarray(w, dtype=float)
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi)
        wts.append(wi)
        cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, c2 = vals.pop(), wts.pop(), cnt.pop()
            v1, w1, c1 = vals.pop(), wts.pop(), cnt.pop()
            wt = w1 + w2
            vals.append((v1 * w1 + v2 * w2) / wt)
            wts.append(wt)
            cnt.append(c1 + c2)
    return np.repeat(vals, cnt)


@dataclass
class Calibrator:
    kind: str                     # isotonic | platt | identity
    n: int
    xs: np.ndarray | None = None  # block centres (isotonic)
    ys: np.ndarray | None = None
    a: float = 1.0                # platt: sigmoid(a·logit(p) + b)
    b: float = 0.0

    def __call__(self, p: float) -> float:
        p = float(p)
        if self.kind == "isotonic":
            out = float(np.interp(p, self.xs, self.ys))
        elif self.kind == "platt":
            out = 1 / (1 + math.exp(-(self.a * _logit(p) + self.b)))
        else:
            out = p
        return min(1 - EPS, max(EPS, out))

    def describe(self) -> dict:
        d = {"kind": self.kind, "n": self.n}
        if self.kind == "isotonic":
            d["points"] = [[float(x), float(y)] for x, y in zip(self.xs, self.ys)]
        elif self.kind == "platt":
            d.update(a=self.a, b=self.b)
        return d


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def fit_platt(p: Sequence[float], y: Sequence[float], iters: int = 50, lam: float = 1.0) -> tuple[float, float]:
    """Logistic regression of y on logit(p) by Newton's method, with an L2 prior toward the identity map
    (a = 1, b = 0) so small or separable samples shrink toward the raw probability instead of a step."""
    x = np.array([_logit(v) for v in p])
    yy = np.asarray(y, dtype=float)
    a, b = 1.0, 0.0
    for _ in range(iters):
        z = a * x + b
        s = 1 / (1 + np.exp(-z))
        g = np.array([np.sum((s - yy) * x) + lam * (a - 1.0), np.sum(s - yy) + lam * b])
        wv = s * (1 - s)
        h = np.array([[np.sum(wv * x * x) + lam, np.sum(wv * x)], [np.sum(wv * x), np.sum(wv) + lam]])
        try:
            step = np.linalg.solve(h, g)
        except np.linalg.LinAlgError:
            break
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


def fit(p: Sequence[float], y: Sequence[float], min_n: int = MIN_CAL) -> Calibrator:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    n = len(p)
    if n < min_n:
        return Calibrator("identity", n)
    bx, by, bw = _bins(p, y)
    fitted = pav(by, bw)
    # block centres: weighted mean p of each run of equal fitted values
    xs, vs = [], []
    i, m = 0, len(bx)
    while i < m:
        j = i
        while j + 1 < m and fitted[j + 1] == fitted[i]:
            j += 1
        w = bw[i: j + 1]
        xs.append(float(np.average(bx[i: j + 1], weights=w)))
        vs.append(float(fitted[i]))
        i = j + 1
    if len(set(vs)) < 3:
        a, b = fit_platt(p, y)
        return Calibrator("platt", n, a=a, b=b)
    return Calibrator("isotonic", n, xs=np.asarray(xs), ys=np.asarray(vs))


def _bins(p: np.ndarray, y: np.ndarray, max_bins: int = 20, min_size: int = 5):
    """Quantile bins of raw p (equal values never split): (mean p, mean y, count) per bin, sorted by p."""
    order = np.argsort(p, kind="mergesort")
    ps, ys = p[order], y[order]
    n = len(ps)
    size = max(min_size, int(math.ceil(n / max_bins)))
    bx, by, bw = [], [], []
    i = 0
    while i < n:
        j = min(n, i + size)
        while j < n and ps[j] == ps[j - 1]:
            j += 1
        if n - j < min_size:       # fold a too-small tail into this bin
            j = n
        bx.append(ps[i:j].mean())
        by.append(ys[i:j].mean())
        bw.append(j - i)
        i = j
    return np.asarray(bx), np.asarray(by), np.asarray(bw, dtype=float)


def walk_forward(rows: Sequence[dict], min_n: int = MIN_CAL) -> list[float | None]:
    """rows: dicts with created_at, target_time (ISO or datetime), prob_up, up (0/1).
    For each row, fit on rows whose outcome was known before it was created; None = not enough history."""
    def t(v):
        return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))

    out: list[float | None] = []
    for r in rows:
        created = t(r["created_at"])
        train = [q for q in rows if t(q["target_time"]) < created]
        if len(train) < min_n:
            out.append(None)
            continue
        c = fit([q["prob_up"] for q in train], [q["up"] for q in train], min_n)
        out.append(c(r["prob_up"]))
    return out


# --- live buckets (Desk) ----------------------------------------------------------------------------------

def evaluated_rows(mode: str, asset_type: str, before: datetime | None = None,
                   prompt_version: str | None = None) -> list[dict]:
    """Evaluated Pro predictions in a bucket (outcome known before `before`)."""
    from core.db import connect
    from core.worlds import PRO_ONLY

    q = ("SELECT created_at, target_time, prob_up, price_now, actual_price FROM predictions WHERE mode = ? AND"
         f" asset_type = ? AND actual_price IS NOT NULL AND {PRO_ONLY}")
    a: list = [mode, asset_type]
    if prompt_version:
        q += " AND prompt_version = ?"
        a.append(prompt_version)
    if before is not None:
        q += " AND target_time < ?"
        a.append(before.isoformat(timespec="seconds"))
    with connect() as conn:
        rows = conn.execute(q + " ORDER BY created_at", a).fetchall()
    return [{"created_at": r[0], "target_time": r[1], "prob_up": r[2], "up": 1.0 if r[4] > r[3] else 0.0}
            for r in rows if r[2] is not None and r[3] is not None]


def calibrator_for(now_fn: Callable[[], datetime]) -> Callable[[str, str], tuple[Calibrator | None, int]]:
    """For the Desk: a fitted calibrator for (mode, asset class) once n ≥ MIN_CAL, else (None, n)."""

    def get(mode: str, asset_type: str):
        rows = evaluated_rows(mode, asset_type, before=now_fn())
        if len(rows) < MIN_CAL:
            return None, len(rows)
        c = fit([r["prob_up"] for r in rows], [r["up"] for r in rows])
        return c, c.n

    return get
