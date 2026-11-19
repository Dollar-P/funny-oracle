"""Auto-Scalp demo (§4.8): predict BTC-USD 15m on a fixed interval so the scoreboard fills with real data.

Used by the Streamlit fragment (one tick at a time) and by scripts/auto_scalp.py (headless loop).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import config
from core import scoreboard
from core.cost import BudgetExceeded

TICKER, MODE, HORIZON = "BTC-USD", "scalp", "15m"


@dataclass
class AutoScalpState:
    runs: int = 0
    last_run: float = 0.0
    max_runs: int = config.AUTO_SCALP_MAX_RUNS
    interval_s: int = config.AUTO_SCALP_INTERVAL_S
    log: list[str] = field(default_factory=list)
    stopped_reason: str | None = None

    def due(self, now: float | None = None) -> bool:
        now = now or time.time()
        return self.stopped_reason is None and self.runs < self.max_runs and now - self.last_run >= self.interval_s

    def seconds_to_next(self) -> int:
        return max(0, int(self.interval_s - (time.time() - self.last_run)))


def tick(state: AutoScalpState, progress=None) -> str | None:
    """Evaluate due predictions; run one new prediction if due. Returns a log line or None."""
    from orchestrator import run_prediction

    evaluated = scoreboard.evaluate_due()
    if not state.due():
        return f"evaluated {evaluated}" if evaluated else None
    if state.runs >= state.max_runs:
        state.stopped_reason = f"reached the per-session cap of {state.max_runs} runs"
        return state.stopped_reason
    state.last_run = time.time()
    try:
        p = asyncio.run(run_prediction(TICKER, MODE, HORIZON, progress=progress, source="auto_scalp"))
    except BudgetExceeded as e:
        state.stopped_reason = str(e)
        return state.stopped_reason
    except Exception as e:  # keep the loop alive; try again next interval
        line = f"run failed: {type(e).__name__}: {e}"[:200]
        state.log.append(line)
        return line
    state.runs += 1
    line = (f"#{p.id} {time.strftime('%H:%M')} BTC 15m: P50 {p.forecast.p50:,.0f} "
            f"P(up) {p.forecast.prob_up:.0%} (${p.cost_usd:.4f}); evaluated {evaluated}")
    state.log.append(line)
    if state.runs >= state.max_runs:
        state.stopped_reason = f"reached the per-session cap of {state.max_runs} runs"
    return line
