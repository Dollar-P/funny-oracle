import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import confetti from "canvas-confetti";
import type { Verdict } from "../lib/types";
import { direction, signed } from "../lib/format";
import { prefersReducedMotion } from "../state/settings";
import { sfx } from "../lib/sound";

const revealed = new Set<string>(); // play the drumroll once per run, even if the view remounts

function Rain() {
  const drops = Array.from({ length: 70 }, (_, i) => i);
  return createPortal(
    <div className="fixed inset-0 z-40 pointer-events-none overflow-hidden" aria-hidden="true">
      {drops.map((i) => (
        <motion.span
          key={i}
          className="absolute top-0 w-0.5 h-6 rounded-full bg-gradient-to-b from-transparent to-bear/80"
          style={{ left: `${(i * 37) % 100}%` }}
          initial={{ y: -40, opacity: 0 }}
          animate={{ y: "105vh", opacity: [0, 1, 1, 0] }}
          transition={{ duration: 1.2 + ((i * 13) % 10) / 10, delay: ((i * 7) % 20) / 10, ease: "easeIn" }}
        />
      ))}
    </div>,
    document.body,
  );
}

export function useReveal(runKey: string | null, enabled: boolean) {
  // decided once per run key: re-renders after the run is marked "revealed" must keep animating
  const decide = () => !enabled || !runKey || revealed.has(runKey) || prefersReducedMotion();
  const [state, setState] = useState(() => ({ key: runKey, skip: decide() }));
  const current = state.key === runKey ? state : { key: runKey, skip: decide() };
  if (current !== state) setState(current);
  const skip = current.skip;
  const [phase, setPhase] = useState<"drumroll" | "shown">(skip ? "shown" : "drumroll");
  useEffect(() => {
    if (skip) {
      setPhase("shown");
      return;
    }
    setPhase("drumroll");
    revealed.add(runKey!);
    sfx.drumroll(1.2);
    const t = setTimeout(() => setPhase("shown"), 1200);
    return () => clearTimeout(t);
  }, [runKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return { phase, animated: !skip };
}

export function Drumroll({ on }: { on: boolean }) {
  return createPortal(
    <AnimatePresence>
      {on && (
        <motion.div
          className="fixed inset-0 z-40 grid place-items-center"
          style={{ background: "radial-gradient(circle at 50% 45%, rgb(167 139 250 / 0.35), rgb(5 5 15 / 0.92) 40%)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          aria-hidden="true"
        >
          <motion.div
            className="text-8xl"
            animate={{ scale: [1, 1.15, 1, 1.2, 1], rotate: [0, -6, 6, -4, 0] }}
            transition={{ duration: 1.2 }}
          >
            🔮
          </motion.div>
          <div className="absolute bottom-[30%] font-display text-2xl text-oracle tracking-widest">THE ORACLE HAS DECIDED…</div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export default function VerdictCard({
  verdict,
  sFinal,
  sRaw,
  confidence,
  animated,
  compact = false,
}: {
  verdict: Verdict | null;
  sFinal: number;
  sRaw: number | null;
  confidence: number;
  animated: boolean;
  compact?: boolean;
}) {
  const dir = direction(sFinal);
  const [rain, setRain] = useState(false);
  useEffect(() => {
    if (!animated) return;
    if (dir === "bull") {
      sfx.fanfare();
      void confetti({
        particleCount: 140,
        spread: 80,
        origin: { y: 0.35 },
        colors: ["#22c55e", "#86efac", "#facc15", "#ffffff"],
        disableForReducedMotion: true,
      });
    } else if (dir === "bear") {
      sfx.buzzer();
      setRain(true);
      const t = setTimeout(() => setRain(false), 3500);
      return () => clearTimeout(t);
    } else {
      sfx.ding();
    }
  }, [animated, dir]);
  const color = dir === "bull" ? "var(--color-bull)" : dir === "bear" ? "var(--color-bear)" : "var(--color-neutral)";
  const pos = ((sFinal + 1) / 2) * 100;
  return (
    <motion.div
      initial={animated ? { scale: 2.2, opacity: 0, rotate: -4 } : false}
      animate={{ scale: 1, opacity: 1, rotate: 0 }}
      transition={{ type: "spring", stiffness: 380, damping: 18 }}
      className={`card ${compact ? "p-4" : "p-6 sm:p-8"} text-center relative overflow-hidden`}
      style={{ borderColor: "color-mix(in srgb, var(--color-gold) 60%, transparent)", boxShadow: "var(--shadow-glow-gold)" }}
      data-testid="verdict"
      role="status"
      aria-live="polite"
    >
      {rain && <Rain />}
      <div className="text-xs uppercase tracking-[0.3em] text-oracle">🔮 The Oracle's verdict</div>
      <h3 className={`font-display ${compact ? "text-xl" : "text-3xl sm:text-5xl"} mt-3 leading-tight text-gold`}>
        {verdict?.headline ?? "The Oracle abstains — the team score stands."}
      </h3>
      {dir === "neutral" && animated && (
        <motion.div className="text-5xl mt-3" animate={{ y: [0, -14, 0, -8, 0] }} transition={{ duration: 1.2 }} aria-hidden="true">
          🤷
        </motion.div>
      )}
      {/* S_final meter */}
      <div className={`mx-auto ${compact ? "mt-3 max-w-xs" : "mt-6 max-w-lg"}`}>
        <div className="relative h-3 rounded-full bg-gradient-to-r from-bear/70 via-surface-2 to-bull/70">
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-ink-faint" />
          <motion.div
            className="absolute top-1/2 w-5 h-5 -mt-2.5 -ml-2.5 rounded-full border-2 border-stage"
            style={{ background: color, boxShadow: `0 0 16px ${color}` }}
            initial={animated ? { left: "50%" } : false}
            animate={{ left: `${pos}%` }}
            transition={{ delay: 0.3, type: "spring", stiffness: 60, damping: 12 }}
          />
        </div>
        <div className="flex justify-between text-xs text-ink-faint mt-1">
          <span>🐻 ▼</span>
          <span className="num text-sm" style={{ color }}>
            S_final {signed(sFinal)}
            {sRaw !== null && verdict && <span className="text-ink-faint"> (team {signed(sRaw)} {signed(verdict.adjustment)} judge)</span>}
          </span>
          <span>▲ 🐂</span>
        </div>
      </div>
      <div className="mt-3 text-sm text-ink-dim">
        confidence <span className="num text-ink">{confidence.toFixed(2)}</span>
      </div>
      {verdict && !compact && (
        <div className="mt-5 grid sm:grid-cols-2 gap-4 text-left max-w-3xl mx-auto">
          <p className="text-sm text-ink-dim leading-relaxed">{verdict.summary}</p>
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-faint mb-1">Key risks</div>
            <ul className="text-sm space-y-1">
              {verdict.key_risks.map((r, i) => (
                <li key={i}>⚠ {r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </motion.div>
  );
}
