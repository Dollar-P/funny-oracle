import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import type { DebateTurnState } from "../state/show";
import { useTypewriter } from "../hooks/useTypewriter";
import { member } from "../lib/cast";

const TURN_LABEL = ["", "opening", "rebuttal", "reply"];

function Turn({
  t,
  active,
  instant,
  onDone,
  compact,
}: {
  t: DebateTurnState;
  active: boolean;
  instant: boolean;
  onDone: () => void;
  compact: boolean;
}) {
  const { shown, done, skip } = useTypewriter(t.text, active, 40, instant);
  useEffect(() => {
    if (done) onDone();
  }, [done]); // eslint-disable-line react-hooks/exhaustive-deps
  const bull = t.side === "bull";
  const m = member(t.side);
  return (
    <motion.li
      initial={{ opacity: 0, x: bull ? -24 : 24 }}
      animate={{ opacity: 1, x: 0 }}
      className={`flex gap-3 ${bull ? "" : "flex-row-reverse"}`}
    >
      <div
        className={`shrink-0 ${compact ? "w-8 h-8 text-lg" : "w-11 h-11 text-2xl"} rounded-full grid place-items-center border-2`}
        style={{ borderColor: m.color, background: "var(--color-surface-2)" }}
        aria-hidden="true"
      >
        {m.emoji}
      </div>
      <div className={`max-w-[85%] ${bull ? "" : "text-right"}`}>
        <div className={`text-xs mb-1 ${bull ? "text-bull" : "text-bear"} font-semibold`}>
          {bull ? "▲" : "▼"} {m.name} · {TURN_LABEL[t.turn] ?? `turn ${t.turn}`}
        </div>
        {t.respondingTo && (
          <div className={`text-[11px] text-ink-faint mb-1 italic ${bull ? "" : "ml-auto"}`}>↩ responding to: {t.respondingTo}</div>
        )}
        <button
          onClick={skip}
          disabled={done}
          className={`text-left rounded-2xl px-4 py-2.5 ${compact ? "text-xs" : "text-sm"} leading-relaxed border ${
            bull ? "bg-bull/10 border-bull/30 rounded-tl-sm" : "bg-bear/10 border-bear/30 rounded-tr-sm"
          } ${done ? "cursor-default" : ""}`}
          aria-label={done ? undefined : "Skip typing"}
        >
          <span className={done ? "" : "typing-caret"}>{shown}</span>
          {/* full text for screen readers */}
          <span className="sr-only">{done ? "" : t.text}</span>
        </button>
      </div>
    </motion.li>
  );
}

/** Whole turns arrive from the server; they are revealed one at a time with a typewriter (click to skip). */
export default function DebateThread({
  turns,
  instant = false,
  compact = false,
  waiting,
  onAllShown,
}: {
  turns: DebateTurnState[];
  instant?: boolean;
  compact?: boolean;
  waiting: boolean;
  onAllShown?: () => void;
}) {
  const [typed, setTyped] = useState(instant ? turns.length : 0);
  useEffect(() => {
    if (instant) setTyped(turns.length);
  }, [instant, turns.length]);
  useEffect(() => {
    if (turns.length > 0 && typed >= turns.length) onAllShown?.();
  }, [typed, turns.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const visible = turns.slice(0, Math.min(turns.length, typed + 1));
  return (
    <section className={compact ? "" : "card p-4 sm:p-6"} aria-label="Bull vs Bear debate">
      {!compact && (
        <h3 className="font-display text-xl mb-4 flex items-center gap-2">
          <span aria-hidden="true">🐂</span> Debate stage <span aria-hidden="true">🐻</span>
        </h3>
      )}
      <ul className="space-y-4">
        {visible.map((t, i) => (
          <Turn
            key={t.turn}
            t={t}
            active={i === typed}
            instant={instant}
            compact={compact}
            onDone={() => setTyped((x) => Math.max(x, i + 1))}
          />
        ))}
      </ul>
      {waiting && typed >= turns.length && (
        <div className="mt-4 text-sm text-ink-dim flex items-center gap-2">
          <span className="ring-spin inline-block">◌</span>
          {turns.length === 0 ? "🐂 Bull is preparing the opening…" : turns[turns.length - 1].side === "bull" ? "🐻 Bear is typing…" : "🐂 Bull is typing…"}
        </div>
      )}
    </section>
  );
}
