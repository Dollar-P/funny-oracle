import { motion } from "framer-motion";
import type { ShowState } from "../state/show";
import VerdictCard, { Drumroll, useReveal } from "./VerdictReveal";
import ForecastChart from "./ForecastChart";
import PrizeCards from "./PrizeCards";
import { useState } from "react";
import Countdown, { OriginalResult } from "./Countdown";
import { shortModel, thb } from "../lib/format";
import { useBudget } from "./BudgetMeter";

export default function VerdictSection({
  s,
  runId,
  reveal = true,
  actions,
  actual: actualProp = null,
}: {
  s: ShowState;
  runId?: string | null;
  reveal?: boolean;
  actions?: React.ReactNode;
  actual?: number | null;
}) {
  const [actualSeen, setActualSeen] = useState<number | null>(null);
  const { phase, animated } = useReveal(runId ?? null, reveal);
  const fx = useBudget().data?.usd_thb ?? 33;
  const q = s.quant;
  const p = s.prediction;
  const m = s.meta;
  // a mock show moved to "now" draws today's market; everything else is anchored to the prediction's own time
  const shifted = !!m?.mock && !!m?.shifted;
  const createdAt = p?.created_at ?? q?.created_at ?? m?.createdAt ?? (m ? new Date(m.startedTs).toISOString() : "");
  const actual = shifted ? null : (actualProp ?? actualSeen);
  return (
    <>
      <Drumroll on={phase === "drumroll"} />
      {phase === "shown" && (
        <div className="space-y-5">
          <VerdictCard verdict={s.verdict} sFinal={s.sFinal ?? 0} sRaw={s.sRaw} confidence={s.confidence ?? 0} animated={animated} />
          {q && m && (
            <motion.section
              initial={animated ? { opacity: 0, y: 30 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: animated ? 0.5 : 0 }}
              className="card p-4 sm:p-6 space-y-5"
              data-testid="forecast"
              aria-label="Forecast"
            >
              <div className="flex flex-wrap items-center gap-3">
                <h3 className="font-display text-2xl">🎲 Forecast</h3>
                <span className="text-xs text-ink-dim">
                  Monte Carlo · 10,000 paths · {q.steps} × {q.step_label} · σ from real history, nudged by S_final
                </span>
              </div>
              <ForecastChart
                ticker={m.ticker}
                mode={m.mode}
                asset={m.assetType}
                fan={q.fan}
                stepLabel={q.step_label}
                createdAt={createdAt}
                targetTime={q.target_time}
                actual={actual}
                animate={animated}
              />
              {shifted && m.rescaled === false && (
                <p className="text-xs text-gold">⚠ Live price unavailable — the fan keeps the original show's prices.</p>
              )}
              <PrizeCards now={q.price_now} p10={q.p10} p50={q.p50} p90={q.p90} probUp={q.prob_up} />
              {(m.mode === "scalp" || p) && (
                <Countdown
                  targetTime={q.target_time}
                  predictionId={s.predictionId}
                  priceNow={q.price_now}
                  probUp={q.prob_up}
                  p10={q.p10}
                  p90={q.p90}
                  kind={shifted ? "shifted" : "track"}
                  onActual={setActualSeen}
                />
              )}
              {shifted && m.replayOf != null && <OriginalResult pid={m.replayOf} />}
              {actions && <div className="flex flex-wrap gap-2 pt-1">{actions}</div>}
              <footer className="text-xs text-ink-faint flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-3">
                <span>
                  🧠 {shortModel(m.models.analyst)}
                  {m.models.judge && m.models.judge !== m.models.analyst ? ` · 🔮 ${shortModel(m.models.judge)}` : ""}
                </span>
                <span>
                  🪙{" "}
                  {m.mock
                    ? "mock replay — ฿0"
                    : p
                      ? `${thb(p.cost_usd * fx)} tracked · ${p.cost_actual_usd != null ? thb(p.cost_actual_usd * fx) : "—"} est. actual`
                      : s.cost
                        ? `${thb(s.cost.run)} tracked`
                        : "—"}
                </span>
                {p && <span>⏱ {p.elapsed_s.toFixed(0)}s</span>}
                {p && p.id && !m.mock && <span>#{p.id}</span>}
                <span className="text-gold/80">⚠ For fun — not investment advice</span>
              </footer>
            </motion.section>
          )}
        </div>
      )}
    </>
  );
}
