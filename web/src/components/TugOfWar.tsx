import { motion } from "framer-motion";
import { signed } from "../lib/format";

/** Rope with a knot at S (−1 bear … +1 bull). `final` shows the Judge-adjusted S_final marker. */
export default function TugOfWar({
  s,
  provisional,
  final,
  compact = false,
}: {
  s: number | null;
  provisional: boolean;
  final?: number | null;
  compact?: boolean;
}) {
  const v = s ?? 0;
  const pos = ((Math.max(-1, Math.min(1, v)) + 1) / 2) * 100;
  const fpos = final !== null && final !== undefined ? ((Math.max(-1, Math.min(1, final)) + 1) / 2) * 100 : null;
  const color = v > 0.1 ? "var(--color-bull)" : v < -0.1 ? "var(--color-bear)" : "var(--color-neutral)";
  return (
    <div className={compact ? "" : "card px-4 sm:px-6 py-4"} role="group" aria-label="Tug-of-war: team score">
      <div className="flex items-center gap-3">
        <motion.span
          className={compact ? "text-xl" : "text-3xl"}
          animate={{ x: v < -0.05 ? -4 : 0, rotate: v < -0.05 ? -8 : 0 }}
          aria-hidden="true"
        >
          🐻
        </motion.span>
        <div className="relative flex-1 h-8">
          {/* track: bear half / bull half */}
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2 rounded-full overflow-hidden flex">
            <div className="flex-1 bg-gradient-to-r from-bear/70 to-bear/10" />
            <div className="flex-1 bg-gradient-to-r from-bull/10 to-bull/70" />
          </div>
          {/* rope texture */}
          <div
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-1 opacity-50"
            style={{ backgroundImage: "repeating-linear-gradient(60deg, #d6c49b 0 4px, #8a7a55 4px 8px)" }}
            aria-hidden="true"
          />
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-ink-faint" aria-hidden="true" />
          {fpos !== null && (
            <motion.div
              className="absolute top-0 bottom-0 w-0.5 bg-gold"
              initial={{ left: `${pos}%`, opacity: 0 }}
              animate={{ left: `${fpos}%`, opacity: 1 }}
              transition={{ type: "spring", stiffness: 80, damping: 14 }}
              aria-hidden="true"
              title="S_final (after the Judge)"
            >
              <span className="absolute -top-4 left-1/2 -translate-x-1/2 text-[10px] text-gold font-bold">🔮</span>
            </motion.div>
          )}
          <motion.div
            className="absolute top-1/2 w-5 h-5 -ml-2.5 -mt-2.5 rounded-full border-2 border-stage"
            style={{ background: color, boxShadow: `0 0 12px ${color}` }}
            animate={{ left: `${pos}%` }}
            transition={{ type: "spring", stiffness: 60, damping: 12 }}
            aria-hidden="true"
          />
        </div>
        <motion.span
          className={compact ? "text-xl" : "text-3xl"}
          animate={{ x: v > 0.05 ? 4 : 0, rotate: v > 0.05 ? 8 : 0 }}
          aria-hidden="true"
        >
          🐂
        </motion.span>
      </div>
      <div className={`flex justify-between ${compact ? "text-[10px]" : "text-xs"} text-ink-faint mt-1`}>
        <span>▼ bear −1</span>
        <span className="num text-sm text-ink" aria-live="polite">
          {s === null ? "waiting for analysts…" : `${provisional ? "live " : ""}S_raw ${signed(v)}`}
          {fpos !== null && <span className="text-gold"> → S_final {signed(final!)}</span>}
        </span>
        <span>bull +1 ▲</span>
      </div>
    </div>
  );
}
