"""Headless Auto-Scalp: BTC-USD 15m every --interval seconds, then keep evaluating.

    python scripts/auto_scalp.py --runs 4 --interval 900
    python scripts/auto_scalp.py --runs 0            # evaluate only (no new predictions)

Stops after --runs predictions plus one final evaluation pass once the last target time has passed
(--linger seconds, default horizon + 3 min). Budget guard and AUTO_SCALP_MAX_RUNS still apply.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from core import scoreboard  # noqa: E402
from core.autoscalp import AutoScalpState, tick  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--linger", type=int, default=18 * 60)
    a = ap.parse_args()
    state = AutoScalpState(max_runs=a.runs, interval_s=a.interval)
    print(f"Auto-Scalp: {a.runs} runs every {a.interval}s", flush=True)
    while state.runs < a.runs and state.stopped_reason is None:
        line = tick(state)
        if line:
            print(time.strftime("%H:%M:%S"), line, flush=True)
        time.sleep(15)
    deadline = time.time() + a.linger
    while time.time() < deadline:
        n = scoreboard.evaluate_due()
        if n:
            print(time.strftime("%H:%M:%S"), f"evaluated {n}", flush=True)
        if scoreboard.metrics().get("pending", 1) == 0:
            break
        time.sleep(30)
    m = scoreboard.metrics()
    print("FINAL metrics:", {k: v for k, v in m.items() if k != "leaderboard"}, flush=True)
    print("leaderboard:", m.get("leaderboard"), flush=True)
    if state.stopped_reason:
        print("stopped:", state.stopped_reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
