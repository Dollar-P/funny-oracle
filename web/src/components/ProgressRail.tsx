import type { Mode, StageName } from "../lib/types";

const STEPS: { key: string; label: string; stages: StageName[]; swingOnly?: boolean }[] = [
  { key: "news", label: "News", stages: ["news"], swingOnly: true },
  { key: "analysts", label: "Analysts", stages: ["analysts"] },
  { key: "aggregate", label: "Tug-of-war", stages: ["aggregate"] },
  { key: "debate", label: "Debate", stages: ["debate"], swingOnly: true },
  { key: "judge", label: "Verdict", stages: ["judge"] },
  { key: "quant", label: "Forecast", stages: ["quant", "done"] },
];

export default function ProgressRail({
  mode,
  stage,
  seen,
  done,
  compact = false,
}: {
  mode: Mode;
  stage: StageName | null;
  seen: StageName[];
  done: boolean;
  compact?: boolean;
}) {
  const steps = STEPS.filter((s) => mode === "swing" || !s.swingOnly);
  const activeIdx = steps.findIndex((s) => stage && s.stages.includes(stage));
  return (
    <ol className={`flex items-center ${compact ? "gap-1" : "gap-1 sm:gap-2"} overflow-x-auto`} aria-label="Show progress">
      {steps.map((s, i) => {
        const reached = s.stages.some((x) => seen.includes(x));
        const current = !done && i === activeIdx;
        const complete = done || (reached && i < activeIdx);
        return (
          <li key={s.key} className="flex items-center gap-1 sm:gap-2 shrink-0" aria-current={current ? "step" : undefined}>
            {i > 0 && (
              <span
                className={`h-0.5 ${compact ? "w-3" : "w-4 sm:w-10"} rounded-full transition-colors duration-500 ${
                  reached || done ? "bg-oracle" : "bg-line"
                }`}
                aria-hidden="true"
              />
            )}
            <span
              className={`flex items-center gap-1.5 rounded-full ${compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs sm:text-sm"} font-semibold border transition-all duration-300 ${
                current
                  ? "bg-oracle/25 border-oracle text-ink shadow-glow"
                  : complete
                    ? "bg-surface-2 border-oracle/40 text-ink-dim"
                    : "border-line text-ink-faint"
              }`}
            >
              <span aria-hidden="true" className={current ? "ring-pulse rounded-full" : ""}>
                {complete ? "●" : current ? "◉" : "○"}
              </span>
              {s.label}
              <span className="sr-only">{complete ? " (done)" : current ? " (now)" : " (upcoming)"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
