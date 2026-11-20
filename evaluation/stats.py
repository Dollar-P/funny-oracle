"""Statistics for small, correlated samples (PRO.md §5.2).

- Wilson score interval for hit rates.
- Moving-block bootstrap (circular) for time series: block length ⌈n^(1/3)⌉ by default, 2,000 resamples, seeded.
- Paired block bootstrap for the difference of a statistic between two aligned series (e.g. Sharpe vs a baseline).
- Non-overlapping sample selection (overlapping horizons are correlated) and the effective n.
- Every user-facing number is a `Metric(value, n, lo, hi, sufficient)`; below MIN_N it is "insufficient data".
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

import numpy as np

MIN_N = 30          # trades or non-overlapping predictions per bucket (§5.2)
N_BOOT = 2000
Z95 = 1.959963984540054


@dataclass
class Metric:
    value: float | None
    n: int
    lo: float | None = None
    hi: float | None = None
    sufficient: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("value", "lo", "hi"):
            v = d[k]
            if v is not None and (isinstance(v, float) and not math.isfinite(v)):
                d[k] = None
        d["label"] = "ok" if self.sufficient else "insufficient data"
        return d


def metric(value, n: int, lo=None, hi=None, min_n: int = MIN_N, note: str = "") -> Metric:
    return Metric(value=None if value is None else float(value), n=int(n),
                  lo=None if lo is None else float(lo), hi=None if hi is None else float(hi),
                  sufficient=n >= min_n, note=note)


def wilson_ci(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def default_block(n: int) -> int:
    return max(1, math.ceil(n ** (1 / 3)))


def _block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=nb)
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n   # circular
    return idx[:n]


def block_bootstrap(x: Sequence[float], stat: Callable[[np.ndarray], float], n_boot: int = N_BOOT,
                    block: int | None = None, seed: int = 0, alpha: float = 0.05) -> tuple[float, float, float]:
    """(estimate, lo, hi) percentile CI of stat(x) under a circular moving-block bootstrap."""
    a = np.asarray(x, dtype=float)
    n = len(a)
    if n < 2:
        est = stat(a) if n else float("nan")
        return est, float("nan"), float("nan")
    block = block or default_block(n)
    rng = np.random.default_rng(seed)
    boots = np.array([stat(a[_block_indices(n, block, rng)]) for _ in range(n_boot)])
    boots = boots[np.isfinite(boots)]
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)]) if len(boots) else (np.nan, np.nan)
    return float(stat(a)), float(lo), float(hi)


def bootstrap_diff(a: Sequence[float], b: Sequence[float], stat: Callable[[np.ndarray], float],
                   n_boot: int = N_BOOT, block: int | None = None, seed: int = 0,
                   alpha: float = 0.05) -> tuple[float, float, float]:
    """Paired block bootstrap of stat(a) − stat(b) for two aligned series (same resampled indices)."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if len(x) != len(y):
        raise ValueError("series must be aligned")
    n = len(x)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    block = block or default_block(n)
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n_boot):
        i = _block_indices(n, block, rng)
        d.append(stat(x[i]) - stat(y[i]))
    d = np.asarray(d)
    d = d[np.isfinite(d)]
    lo, hi = np.percentile(d, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(stat(x) - stat(y)), float(lo), float(hi)


def non_overlapping(rows: Iterable[tuple]) -> list[int]:
    """rows: (index, key, start, end). Greedy per key: keep a row only if it starts at/after the end of the
    previously kept row for that key. Returns the kept indices (sorted by start)."""
    kept: list[int] = []
    last_end: dict = {}
    for idx, key, start, end in sorted(rows, key=lambda r: (r[2], r[3])):
        if key not in last_end or start >= last_end[key]:
            kept.append(idx)
            last_end[key] = end
    return kept


def effective_n(n: int, avg_overlap: float) -> float:
    """Rough effective sample size for overlapping horizons: n / (1 + average overlap ratio)."""
    return n / (1 + max(0.0, avg_overlap))


def percentile_rank(x: float, dist: Sequence[float]) -> float:
    """Share of the distribution below x (ties count half), 0..1."""
    d = np.asarray(dist, dtype=float)
    if len(d) == 0:
        return float("nan")
    return float(((d < x).sum() + 0.5 * (d == x).sum()) / len(d))


def multiple_comparisons_note(k: int, alpha: float = 0.05) -> str:
    if k <= 1:
        return ""
    return (f"{k} strategy variants compared: at α = {alpha:g} about {alpha * k:.1f} would look 'significant' by "
            f"chance; Bonferroni threshold α/k = {alpha / k:.4f}.")


def to_dt(s) -> datetime:
    return s if isinstance(s, datetime) else datetime.fromisoformat(str(s))
