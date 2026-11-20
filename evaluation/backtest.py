"""Portfolio backtester (PRO.md §5.1): one engine for every strategy — same Risk Engine, same Bracket fills,
same costs, same Decimal ledger as the forward paper loop.

Timeline for each union date d (daily bars):
    1. every ticker with a bar on d feeds it to its live brackets (fills at prices inside that bar);
    2. equity is marked at the last known close of every ticker → one snapshot per date;
    3. strategies decide at the close of d, seeing only bars ≤ d through a PointInTimeView;
       accepted plans become brackets submitted at the bar's close time → they fill at the next bar's open.
No strategy ever receives a bar after its decision time (see tests/pro/test_p5_backtest.py::test_no_lookahead).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Literal, Protocol

import numpy as np
import pandas as pd

from evaluation import metrics as M
from trading import calendar as cal
from trading.broker_paper import Bar, Bracket
from trading.costs import cost_model, money
from trading.ledger import Ledger, journal_entry
from trading.risk import OpenPos, Portfolio, RiskParams, size_plan
from trading.signals import SignalParams, TradePlan

UTC = timezone.utc
Z10 = 1.2815515655446004   # standard normal 90th percentile


class PointInTimeView:
    """Read-only view of one ticker's bars up to and including bar i (the decision bar)."""

    __slots__ = ("_df", "_close", "_i")

    def __init__(self, df: pd.DataFrame, close: np.ndarray, i: int):
        self._df, self._close, self._i = df, close, i

    @property
    def i(self) -> int:
        return self._i

    @property
    def ts(self) -> pd.Timestamp:
        return self._df.index[self._i]

    @property
    def close(self) -> np.ndarray:
        return self._close[: self._i + 1]

    @property
    def df(self) -> pd.DataFrame:
        return self._df.iloc[: self._i + 1]


@dataclass
class Decision:
    direction: Literal["long", "short"]
    stop: float
    targets: list[float]                 # [] → no targets (time exit / stop only)
    hold_bars: int
    mode: str = "swing"
    plan: TradePlan | None = None
    entry_ref: float | None = None


@dataclass
class StrategyCtx:
    asset_type: str
    horizon_bars: int
    signal: SignalParams
    rng: np.random.Generator
    sigma_daily: float                   # trailing σ of daily log returns at the decision bar
    atr: float


class Strategy(Protocol):
    name: str

    def decide(self, ticker: str, view: PointInTimeView, ctx: StrategyCtx) -> Decision | None: ...


@dataclass
class BacktestConfig:
    start: str | None = None             # first decision date (bars before it are warm-up only)
    end: str | None = None
    horizon_bars: int | dict = 21        # int, or {asset_type: bars} (e.g. 1M = 21 stock/gold, 30 crypto)
    decision_every: int | None = None    # default: horizon_bars (non-overlapping decisions per ticker)

    def hb(self, asset_type: str) -> int:
        h = self.horizon_bars
        return int(h.get(asset_type, next(iter(h.values())))) if isinstance(h, dict) else int(h)
    starting_equity: float = 100_000.0
    risk: RiskParams = field(default_factory=RiskParams)
    signal: SignalParams = field(default_factory=SignalParams)
    cost_overrides: dict | None = None
    seed: int = 0
    warmup_bars: int = 210               # SMA200 + slack


@dataclass
class BacktestResult:
    strategy: str
    equity: pd.Series                    # float, one point per union date
    trades: list[dict]
    predictions: list[dict]              # forecasts made (prob_up, p10/p50/p90) for prediction-quality metrics
    rejections: dict[str, int]
    flats: dict[str, int]
    in_market: pd.Series
    traded_notional: float
    costs: float
    ppy: int
    reconcile: Decimal = Decimal("0")

    def summary(self) -> dict:
        e = self.equity.values
        r = M.returns_from_equity(e)
        days = (self.equity.index[-1] - self.equity.index[0]).total_seconds() / 86400 if len(e) > 1 else 0
        rs = [t["r_multiple"] for t in self.trades if t["r_multiple"] is not None]
        pnls = [t["pnl"] for t in self.trades]
        years = days / 365.25 if days else 0
        return {
            "strategy": self.strategy, "total_return": M.total_return(e), "cagr": M.cagr(e, days),
            "volatility": M.volatility(r, self.ppy), "sharpe": M.sharpe(r, self.ppy), "sortino": M.sortino(r, self.ppy),
            "max_drawdown": M.max_drawdown(e), "calmar": M.calmar(e, days), "trades": len(self.trades),
            "win_rate": M.win_rate(pnls), "avg_r": M.expectancy_r(rs), "expectancy_r": M.expectancy_r(rs),
            "profit_factor": M.profit_factor(pnls), "exposure": M.exposure(self.in_market.values),
            "turnover": M.turnover(self.traded_notional, float(np.mean(e)) if len(e) else 0, years),
            "total_costs": self.costs, "rejections": dict(self.rejections), "flats": dict(self.flats),
        }


def ppy_for(asset_types) -> int:
    return 365 if "crypto" in set(asset_types) else 252


def to_bars(df: pd.DataFrame, asset_type: str, interval: str = "1d") -> list[Bar]:
    out = []
    idx = df.index
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    for j, ts in enumerate(idx):
        t = ts.to_pydatetime()
        nxt = idx[j + 1].to_pydatetime() if j + 1 < len(idx) else None
        out.append(Bar(ts=t, open=o[j], high=h[j], low=l[j], close=c[j],
                       open_time=cal.bar_open_time(asset_type, interval, t),
                       close_time=cal.bar_close_time(asset_type, interval, t, nxt)))
    return out


def neutral_stop(entry: float, side: str, sigma_daily: float, hold: int, atr: float, k_atr: float) -> float:
    """The Signal Engine's stop rule for a zero-drift forecast: the wider of k·ATR and the P10/P90 band edge."""
    band = Z10 * sigma_daily * math.sqrt(max(1, hold))
    if side == "long":
        return min(entry - k_atr * atr, entry * math.exp(-band))
    return max(entry + k_atr * atr, entry * math.exp(band))


@dataclass
class _Prepared:
    asset_type: str
    df: pd.DataFrame
    close: np.ndarray
    bars: list[Bar]
    logret: np.ndarray
    atr: np.ndarray
    ret_series: pd.Series
    day_index: dict


def prepare(data: dict[str, tuple[str, pd.DataFrame]], atr_period: int = 14) -> dict[str, _Prepared]:
    from mcp_server import indicators as ind

    out = {}
    for t, (asset_type, df) in data.items():
        df = df.sort_index()
        close = df["Close"].to_numpy(dtype=float)
        lr = np.concatenate([[np.nan], np.diff(np.log(close))])
        out[t] = _Prepared(asset_type=asset_type, df=df, close=close, bars=to_bars(df, asset_type), logret=lr,
                           atr=ind.atr(df, atr_period).to_numpy(dtype=float),
                           ret_series=df["Close"].pct_change(),
                           day_index={ts.date(): j for j, ts in enumerate(df.index.tz_convert(UTC))})
    return out


def run_backtest(strategy: Strategy, data: dict[str, tuple[str, pd.DataFrame]] | dict[str, _Prepared],
                 cfg: BacktestConfig) -> BacktestResult:
    prep = data if data and isinstance(next(iter(data.values())), _Prepared) else prepare(data, cfg.signal.atr_period)
    tickers = list(prep)
    start = pd.Timestamp(cfg.start).date() if cfg.start else None
    end = pd.Timestamp(cfg.end).date() if cfg.end else None
    days = sorted({d for p in prep.values() for d in p.day_index if (end is None or d <= end)})
    # daily returns aligned on the union calendar (NaN where an asset has no bar) for the correlation guard
    aligned = {t: np.array([p.ret_series.iat[p.day_index[d]] if d in p.day_index else np.nan for d in days])
               for t, p in prep.items()}
    rng = np.random.default_rng(cfg.seed)
    led = Ledger(Decimal(repr(cfg.starting_equity)))
    live: dict[str, list[tuple[str, Bracket, dict]]] = {t: [] for t in tickers}
    last_close: dict[str, float] = {}
    trades: list[dict] = []
    preds: list[dict] = []
    rejections: dict[str, int] = {}
    flats: dict[str, int] = {}
    eq_ts, eq_v, in_mkt = [], [], []
    traded = 0.0
    costs = Decimal("0")
    halted = False
    first_decision: dict[str, int] = {}
    n_key = 0
    prev_equity = led.starting_cash
    key_ticker: dict[str, str] = {}

    def close_bracket(t, key, br, meta):
        nonlocal costs
        if br.state == "closed":
            j = journal_entry(key, t, br)
            trades.append({"ticker": t, "side": br.side, "opened_at": j.opened_at, "closed_at": j.closed_at,
                           "pnl": float(j.realized_pnl), "r_multiple": j.r_multiple, "exit": j.exit_reason,
                           "mae_r": j.mae_r, "mfe_r": j.mfe_r, "costs": float(j.costs), **meta})
        costs += br.costs_paid()

    for k_day, d in enumerate(days):
        today = [t for t in tickers if d in prep[t].day_index]
        # 1) bars → fills
        for t in today:
            p = prep[t]
            bar = p.bars[p.day_index[d]]
            keep = []
            for key, br, meta in live[t]:
                fills = br.on_bar(bar)
                for f in fills:
                    led.apply(key, f)
                    traded += float(f.notional)
                if br.state in ("closed", "cancelled"):
                    close_bracket(t, key, br, meta)
                else:
                    keep.append((key, br, meta))
            live[t] = keep
            last_close[t] = bar.close
        # 2) mark
        marks = {k: last_close[key_ticker[k]] for k in led.qty}
        snap_ts = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        snap = led.snapshot(snap_ts, marks)
        if start is None or d >= start:  # warm-up bars are not part of the measured period
            eq_ts.append(snap_ts)
            eq_v.append(float(snap.equity))
            in_mkt.append(1 if led.qty else 0)
        day_start = prev_equity
        prev_equity = snap.equity
        if float(snap.drawdown) <= cfg.risk.max_dd:
            halted = True
        # 3) decisions at the close
        if start is not None and d < start:
            continue
        for t in today:
            p = prep[t]
            i = p.day_index[d]
            if i < cfg.warmup_bars:
                continue
            first_decision.setdefault(t, i)
            every = cfg.decision_every or cfg.hb(p.asset_type)
            if (i - first_decision[t]) % every != 0:
                continue
            if any(True for _ in live[t]):
                continue  # one position per ticker
            sig_w = p.logret[max(1, i - 251): i + 1]
            sig_w = sig_w[np.isfinite(sig_w)]
            sigma_d = float(sig_w.std(ddof=1)) if len(sig_w) > 10 else float("nan")
            ctx = StrategyCtx(asset_type=p.asset_type, horizon_bars=cfg.hb(p.asset_type), signal=cfg.signal, rng=rng,
                              sigma_daily=sigma_d, atr=float(p.atr[i]))
            view = PointInTimeView(p.df, p.close, i)
            dec = strategy.decide(t, view, ctx)
            rec = getattr(strategy, "last_prediction", None)
            if rec is not None:
                preds.append({**rec, "ticker": t, "i": i, "ts": p.df.index[i], "target_i": i + cfg.hb(p.asset_type)})
                strategy.last_prediction = None  # type: ignore[attr-defined]
            if dec is None:
                reason = getattr(strategy, "last_flat", None) or "no_decision"
                flats[reason] = flats.get(reason, 0) + 1
                continue
            entry = dec.entry_ref or float(p.close[i])
            plan = dec.plan or _plan_from_decision(t, p.asset_type, dec, entry, p.bars[i])
            positions = []
            for tt, lst in live.items():
                for key, br, meta in lst:
                    m = last_close.get(tt, br.entry_price or meta["entry_ref"])
                    q = br.qty_open if br.state == "open" else br.qty
                    positions.append(OpenPos(tt, prep[tt].asset_type, br.side, q, br.entry_price or meta["entry_ref"],
                                             br.stop, m))
            eqf = float(snap.equity)
            same_side = [q.ticker for q in positions if q.side == plan.side]
            lo_k = max(0, k_day - 2 * cfg.risk.corr_lookback)
            rets = {tt: aligned[tt][lo_k: k_day + 1] for tt in {t, *same_side}} if same_side else {}
            pf = Portfolio(equity=eqf, peak_equity=float(led.peak), day_start_equity=float(day_start),
                           positions=positions, halted=halted, returns=rets)
            sigma_a = sigma_d * math.sqrt(ppy_for([p.asset_type]))
            decision = size_plan(plan, pf, cfg.risk, sigma_annual=sigma_a)
            if not decision.approved:
                rule = decision.rejections[0].rule
                rejections[rule] = rejections.get(rule, 0) + 1
                if decision.halt:
                    halted = True
                continue
            bar_i = p.bars[i]
            exit_i = min(i + dec.hold_bars, len(p.bars) - 1)
            time_exit = p.bars[exit_i].t_open if i + dec.hold_bars < len(p.bars) else bar_i.t_open + timedelta(days=3650)
            targets = dec.targets or ([float("inf"), float("inf")] if dec.direction == "long" else [0.0, 0.0])
            br = Bracket(side=dec.direction, qty=decision.size.qty, stop=plan.stop, targets=list(targets),
                         time_exit=time_exit, submit_ts=bar_i.t_close,
                         costs=cost_model(p.asset_type, cfg.cost_overrides), asset_type=p.asset_type)
            n_key += 1
            key = f"{t}#{n_key}"
            key_ticker[key] = t
            live[t].append((key, br, {"entry_ref": entry, "decided_at": bar_i.t_close,
                                      "ev_r": plan.expected_value_r, "p_win": plan.p_win}))

    # end: close everything at the last close
    for t, lst in live.items():
        for key, br, meta in lst:
            p = prep[t]
            last_bar = p.bars[-1] if end is None else p.bars[max(j for dd, j in p.day_index.items() if dd <= end)]
            for f in br.close_at(last_bar.t_close, last_bar.close, "end"):
                led.apply(key, f)
                traded += float(f.notional)
            close_bracket(t, key, br, meta)
    if eq_v:
        eq_v[-1] = float(led.equity({k: last_close[key_ticker[k]] for k in led.qty}))
    return BacktestResult(strategy=strategy.name, equity=pd.Series(eq_v, index=pd.DatetimeIndex(eq_ts)),
                          trades=trades, predictions=preds, rejections=rejections, flats=flats,
                          in_market=pd.Series(in_mkt, index=pd.DatetimeIndex(eq_ts)), traded_notional=traded,
                          costs=float(costs), ppy=ppy_for(p.asset_type for p in prep.values()),
                          reconcile=led.reconcile())


def _plan_from_decision(ticker: str, asset_type: str, dec: Decision, entry: float, bar: Bar) -> TradePlan:
    """Wrap a baseline decision in a TradePlan so it goes through the same Risk Engine."""
    from trading.signals import EntrySpec

    t = list(dec.targets) or [entry, entry]
    return TradePlan(prediction_id=None, ticker=ticker, asset_type=asset_type, mode=dec.mode, horizon="",
                     direction=dec.direction, conviction=1.0, entry=EntrySpec(ref_price=entry), stop=dec.stop,
                     targets=t, time_exit=bar.t_close, reward_risk=0.0, expected_value_r=0.0, p_win=0.5,
                     created_at=bar.t_close, inputs={"side": dec.direction, "r1": 0.0, "r2": 0.0})


# --- buy & hold / cash (benchmarks, not sized by the Risk Engine) --------------------------------------

def buy_and_hold(data: dict[str, tuple[str, pd.DataFrame]] | dict[str, _Prepared], cfg: BacktestConfig,
                 name: str | None = None) -> BacktestResult:
    """Equal-weight basket bought at the first open after `start`, held to the end, same costs. A benchmark:
    not sized by the Risk Engine (docs/pro-plan.md A8)."""
    prep = data if data and isinstance(next(iter(data.values())), _Prepared) else prepare(data)
    start = pd.Timestamp(cfg.start).date() if cfg.start else None
    end = pd.Timestamp(cfg.end).date() if cfg.end else None
    days = sorted({d for p in prep.values() for d in p.day_index if (start is None or d >= start)
                   and (end is None or d <= end)})
    led = Ledger(Decimal(repr(cfg.starting_equity)))
    alloc = cfg.starting_equity / len(prep)
    bought: dict[str, Bracket] = {}
    last_close: dict[str, float] = {}
    eq_ts, eq_v = [], []
    traded = 0.0
    for d in days:
        for t, p in prep.items():
            if d not in p.day_index:
                continue
            bar = p.bars[p.day_index[d]]
            if t not in bought:
                cm = cost_model(p.asset_type, cfg.cost_overrides)
                px = bar.open * (1 + cm.slippage_bps / 1e4) * (1 + cm.fee_bps / 1e4)
                from trading.risk import LOT, _floor_lot

                qty = _floor_lot(alloc / px, LOT.get(p.asset_type, Decimal("1")))
                br = Bracket(side="long", qty=qty, stop=0.0, targets=[float("inf"), float("inf")],
                             time_exit=bar.t_open + timedelta(days=36500), submit_ts=bar.t_open, costs=cm,
                             asset_type=p.asset_type)
                for f in br.on_bar(bar):
                    led.apply(t, f)
                    traded += float(f.notional)
                bought[t] = br
            last_close[t] = bar.close
        snap = led.snapshot(datetime.combine(d, datetime.min.time(), tzinfo=UTC),
                            {k: last_close[k] for k in led.qty})
        eq_ts.append(snap.ts)
        eq_v.append(float(snap.equity))
    for t, br in bought.items():
        p = prep[t]
        lb = p.bars[-1] if end is None else p.bars[max(j for dd, j in p.day_index.items() if dd <= end)]
        for f in br.close_at(lb.t_close, lb.close, "end"):
            led.apply(t, f)
            traded += float(f.notional)
    if eq_v:
        eq_v[-1] = float(led.cash)
    costs = float(sum((b.costs_paid() for b in bought.values()), Decimal("0")))
    return BacktestResult(strategy=name or "buy_and_hold", equity=pd.Series(eq_v, index=pd.DatetimeIndex(eq_ts)),
                          trades=[], predictions=[], rejections={}, flats={},
                          in_market=pd.Series(1, index=pd.DatetimeIndex(eq_ts)), traded_notional=traded, costs=costs,
                          ppy=ppy_for(p.asset_type for p in prep.values()), reconcile=led.reconcile())


def cash(data, cfg: BacktestConfig) -> BacktestResult:
    prep = data if data and isinstance(next(iter(data.values())), _Prepared) else prepare(data)
    start = pd.Timestamp(cfg.start).date() if cfg.start else None
    days = sorted({d for p in prep.values() for d in p.day_index if start is None or d >= start})
    idx = pd.DatetimeIndex([datetime.combine(d, datetime.min.time(), tzinfo=UTC) for d in days])
    return BacktestResult(strategy="cash", equity=pd.Series(cfg.starting_equity, index=idx), trades=[], predictions=[],
                          rejections={}, flats={}, in_market=pd.Series(0, index=idx), traded_notional=0.0, costs=0.0,
                          ppy=ppy_for(p.asset_type for p in prep.values()))


StrategyFactory = Callable[[int], Strategy]


# --- random-strategy distribution (§5.1: seeded, 1,000 runs) ------------------------------------------------

_WORKER: dict = {}


def _init_worker(data, cfg):
    _WORKER["prep"] = prepare(data, cfg.signal.atr_period)
    _WORKER["cfg"] = cfg


def _run_random(args) -> dict:
    from evaluation.baselines import RandomStrategy

    seeds, p_trade = args
    out = []
    for s in seeds:
        r = run_backtest(RandomStrategy(s, p_trade), _WORKER["prep"], _WORKER["cfg"])
        e = r.equity.values
        out.append({"seed": s, "sharpe": M.sharpe(M.returns_from_equity(e), r.ppy), "total_return": M.total_return(e),
                    "trades": len(r.trades)})
    return out


def random_distribution(data: dict[str, tuple[str, pd.DataFrame]], cfg: BacktestConfig, n_runs: int = 1000,
                        p_trade: float = 1.0, workers: int | None = None, base_seed: int = 10_000) -> list[dict]:
    """Final Sharpe / total return of `n_runs` seeded random strategies (run in parallel processes)."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    seeds = list(range(base_seed, base_seed + n_runs))
    workers = workers or max(1, min(12, (os.cpu_count() or 2) - 1))
    if workers == 1:
        _init_worker(data, cfg)
        return _run_random((seeds, p_trade))
    chunks = [seeds[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(data, cfg)) as ex:
        res = [r for part in ex.map(_run_random, [(c, p_trade) for c in chunks]) for r in part]
    return sorted(res, key=lambda r: r["seed"])
