import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { countdown, price } from "../lib/format";

/** "Result checked in 14:32" until target_time; then flips to ✅/❌ with the real price (scalp). */
export default function Countdown({
  targetTime,
  predictionId,
  priceNow,
  probUp,
  p10,
  p90,
  mock,
}: {
  targetTime: string;
  predictionId: number | null;
  priceNow: number;
  probUp: number;
  p10: number;
  p90: number;
  mock: boolean;
}) {
  const target = Date.parse(targetTime);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const due = now >= target;
  // live run: poll with evaluate=1 once due; mock replay: show the original prediction's stored result
  const { data } = useQuery({
    queryKey: ["prediction-eval", predictionId, due, mock],
    queryFn: () => api.prediction(predictionId!, due && !mock),
    enabled: predictionId !== null && (due || mock),
    refetchInterval: (q) => (q.state.data?.actual_price != null || !due || mock ? false : 30_000),
  });
  const actual = data?.actual_price ?? null;

  const showResult = actual !== null && (due || mock);
  if (!showResult) {
    return (
      <div className="card px-4 py-3 flex items-center gap-3" role="timer" aria-live="off">
        <span className="text-2xl" aria-hidden="true">⏳</span>
        <div>
          <div className="text-sm text-ink-dim">{due ? "Waiting for the market to report…" : "Result checked in"}</div>
          {!due && <div className="num text-2xl font-semibold">{countdown(target - now)}</div>}
          {mock && <div className="text-[11px] text-ink-faint">mock show — the original prediction has no result yet</div>}
        </div>
      </div>
    );
  }
  const calledUp = probUp > 0.5;
  const wentUp = actual! > priceNow;
  const hit = probUp !== 0.5 && calledUp === wentUp;
  const inBand = actual! >= p10 && actual! <= p90;
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
          real price {price(actual)} · {inBand ? "inside" : "outside"} the P10–P90 band
        </div>
        {mock && <div className="text-[11px] text-ink-faint">mock show — result of the original prediction</div>}
      </div>
    </motion.div>
  );
}
