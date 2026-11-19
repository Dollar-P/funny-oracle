import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { countdown, dateTime, price } from "../lib/format";
import type { Prediction } from "../lib/types";
import ForecastChart from "./ForecastChart";

function verdictOf(p: { probUp: number; priceNow: number; p10: number; p90: number }, actual: number) {
  const hit = p.probUp !== 0.5 && p.probUp > 0.5 === actual > p.priceNow;
  return { hit, inBand: actual >= p.p10 && actual <= p.p90 };
}

/**
 * "Result checked in 14:32" until target_time; then flips to ✅/❌ with the real price.
 *
 * kind="track":   a real run, or a replay of the original show as it was → the real result of `predictionId`.
 * kind="shifted": a mock show moved to "now" → only the countdown; the original prediction's result is shown
 *                 separately (OriginalResult) with its own chart, so it can't contradict the live chart.
 */
export default function Countdown({
  targetTime,
  predictionId,
  priceNow,
  probUp,
  p10,
  p90,
  kind,
  onActual,
}: {
  targetTime: string;
  predictionId: number | null;
  priceNow: number;
  probUp: number;
  p10: number;
  p90: number;
  kind: "track" | "shifted";
  onActual?: (actual: number) => void;
}) {
  const target = Date.parse(targetTime);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const due = now >= target;
  const track = kind === "track" && predictionId !== null;
  const { data } = useQuery({
    queryKey: ["prediction-eval", predictionId, due],
    queryFn: () => api.prediction(predictionId!, true), // evaluate=1 only acts when due and not yet graded
    enabled: track && due,
    refetchInterval: (q) => (q.state.data?.actual_price != null ? false : 30_000),
  });
  const actual = track ? (data?.actual_price ?? null) : null;
  useEffect(() => {
    if (actual !== null) onActual?.(actual);
  }, [actual]); // eslint-disable-line react-hooks/exhaustive-deps

  if (actual === null || !due) {
    return (
      <div className="card px-4 py-3 flex items-center gap-3" role="timer" aria-live="off">
        <span className="text-2xl" aria-hidden="true">⏳</span>
        <div>
          <div className="text-sm text-ink-dim">{due ? "Waiting for the market to report…" : "Result checked in"}</div>
          {!due && <div className="num text-2xl font-semibold">{countdown(target - now)}</div>}
          <div className="text-[11px] text-ink-faint">target {dateTime(targetTime)}</div>
          {kind === "shifted" && <div className="text-[11px] text-ink-faint">mock show — nothing is checked when this timer ends</div>}
        </div>
      </div>
    );
  }
  const { hit, inBand } = verdictOf({ probUp, priceNow, p10, p90 }, actual);
  return (
    <motion.div
      initial={{ rotateY: 90, opacity: 0 }}
      animate={{ rotateY: 0, opacity: 1 }}
      transition={{ type: "spring", stiffness: 200, damping: 18 }}
      className={`card px-4 py-3 flex items-center gap-3 ${hit ? "border-bull/60" : "border-bear/60"}`}
      role="status"
    >
      <span className="text-3xl" aria-hidden="true">{hit ? "✅" : "❌"}</span>
      <div>
        <div className="font-semibold">{hit ? "Direction called right!" : "Direction missed"}</div>
        <div className="num text-sm text-ink-dim">
          real price {price(actual)} at {dateTime(targetTime)} · {inBand ? "inside" : "outside"} the P10–P90 band
        </div>
      </div>
    </motion.div>
  );
}

/** For mock shows moved to "now": what really happened to the original prediction, on its own mini chart. */
export function OriginalResult({ pid }: { pid: number }) {
  const { data } = useQuery({ queryKey: ["prediction", pid], queryFn: () => api.prediction(pid) });
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const p: Prediction = data.prediction;
  const f = p.forecast;
  const actual = data.actual_price;
  const r = actual !== null ? verdictOf({ probUp: f.prob_up, priceNow: f.price_now, p10: f.p10, p90: f.p90 }, actual) : null;
  return (
    <section className="card px-4 py-3 border-dashed" role="status" aria-label={`Original prediction #${pid}`}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="w-full text-left flex items-center gap-3">
        <span className="text-2xl" aria-hidden="true">🎭</span>
        <span className="flex-1 text-sm">
          <b>Mock show</b> — a replay of prediction <b>#{pid}</b> from {dateTime(p.created_at)}, moved to now.{" "}
          <span className="text-ink-dim">
            What really happened then:{" "}
            {r === null ? (
              "no result yet"
            ) : (
              <>
                {r.hit ? "✅ direction right" : "❌ direction missed"} · real {price(actual)} vs then {price(f.price_now)} ·{" "}
                {r.inBand ? "inside" : "outside"} the band
              </>
            )}
          </span>
        </span>
        <span className="text-ink-faint text-sm">{open ? "▾" : "▸"} original chart</span>
      </button>
      {open && (
        <div className="mt-3" data-testid="original-chart">
          <ForecastChart
            ticker={p.ticker}
            mode={p.mode}
            asset={p.asset_type}
            fan={f.fan}
            stepLabel={f.step_label}
            createdAt={p.created_at}
            targetTime={p.target_time}
            actual={actual}
            height={180}
            animate={false}
            compact
          />
          <p className="text-[11px] text-ink-faint mt-1">
            Original window, original prices ({p.ticker}). The big chart above shows today's market instead.
          </p>
        </div>
      )}
    </section>
  );
}
