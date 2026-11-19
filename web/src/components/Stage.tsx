import { useState } from "react";
import type { ShowState } from "../state/show";
import Podium from "./Podium";
import AgentDrawer from "./AgentDrawer";

export default function Stage({ s, compact = false }: { s: ShowState; compact?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const ids = s.meta?.agents ?? Object.keys(s.agents);
  return (
    <section
      className={`${compact ? "" : "card bg-surface/60 backdrop-blur px-2 sm:px-6 pt-2 pb-6"} relative overflow-hidden`}
      aria-label="Analyst podiums"
    >
      <div
        className={`grid ${ids.length <= 3 ? "grid-cols-3" : "grid-cols-3 sm:grid-cols-5"} gap-x-2 gap-y-4 ${compact ? "" : "max-w-4xl mx-auto"}`}
      >
        {ids.map((id) =>
          s.agents[id] ? (
            <Podium
              key={id}
              a={s.agents[id]}
              weight={s.meta?.weights[id]}
              compact={compact}
              onOpen={() => setOpen(id)}
            />
          ) : null,
        )}
      </div>
      <AgentDrawer a={open ? s.agents[open] : null} weight={open ? s.meta?.weights[open] : undefined} onClose={() => setOpen(null)} />
    </section>
  );
}

/** Live S estimate from finished agents (same formula as the server) — the rope moves as podiums finish. */
export function provisionalS(s: ShowState): number | null {
  if (s.sRaw !== null) return s.sRaw;
  let num = 0;
  let den = 0;
  for (const a of Object.values(s.agents)) {
    if (a.report && a.status === "done") {
      const w = s.meta?.weights[a.id] ?? 0;
      num += w * a.report.score * a.report.confidence;
      den += w * a.report.confidence;
    }
  }
  return den > 0 ? Math.max(-1, Math.min(1, num / den)) : null;
}
