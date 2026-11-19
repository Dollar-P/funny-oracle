import { AnimatePresence, motion } from "framer-motion";
import { member, TOOL_ICON } from "../lib/cast";
import type { AgentState } from "../state/show";
import { signed } from "../lib/format";

function scoreColor(s: number) {
  return s > 0.1 ? "var(--color-bull)" : s < -0.1 ? "var(--color-bear)" : "var(--color-neutral)";
}

export function ToolBubble({ a, compact }: { a: AgentState; compact?: boolean }) {
  const b = a.bubble;
  return (
    <div className={`${compact ? "h-9" : "h-12"} flex items-end justify-center`}>
      <AnimatePresence mode="wait">
        {b && (
          <motion.div
            key={b.text + b.kind}
            initial={{ opacity: 0, y: 6, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
            className={`relative max-w-full whitespace-nowrap flex items-center rounded-xl px-2 py-1 ${compact ? "text-[10px]" : "text-[11px]"} leading-tight num bg-surface-2 border ${
              b.ok === false ? "border-bear/70" : b.ok ? "border-bull/50" : "border-line"
            }`}
            title={b.text}
          >
            <span aria-hidden="true">{b.kind === "search" ? "🔎" : TOOL_ICON[b.tool ?? ""] ?? "📡"} </span>
            <span className="inline-block max-w-[130px] truncate">{b.text}</span>
            <span className="ml-1">
              {b.ok === null ? <span className="inline-block ring-spin">◌</span> : b.ok ? <span className="text-bull">✓</span> : <span className="text-bear">✗</span>}
            </span>
            <span className="absolute left-1/2 -bottom-1 w-2 h-2 -translate-x-1/2 rotate-45 bg-surface-2 border-r border-b border-line" aria-hidden="true" />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function Podium({
  a,
  weight,
  onOpen,
  compact = false,
}: {
  a: AgentState;
  weight?: number;
  onOpen?: () => void;
  compact?: boolean;
}) {
  const m = member(a.id);
  const done = a.status === "done" && a.report;
  const absent = a.status === "absent";
  const active = a.status === "thinking" || a.status === "tool";
  const size = compact ? "w-14 h-14 text-2xl" : "w-20 h-20 sm:w-24 sm:h-24 text-4xl sm:text-5xl";
  const label = `${m.name}: ${
    absent ? `absent (${a.failReason})` : done ? `score ${signed(a.report!.score)}, confidence ${a.report!.confidence.toFixed(2)}` : a.status
  }`;
  return (
    <div className="flex flex-col items-center min-w-0" style={{ ["--ring" as string]: m.color }}>
      <ToolBubble a={a} compact={compact} />
      <button
        onClick={onOpen}
        disabled={!onOpen || !(done || absent)}
        aria-label={`${label}${done || absent ? " — open details" : ""}`}
        className="relative group disabled:cursor-default mt-1"
      >
        {/* podium light */}
        <span
          className="absolute inset-x-[-30%] -bottom-3 h-6 rounded-[50%] blur-md transition-opacity duration-500"
          style={{ background: m.color, opacity: active ? 0.45 : done ? 0.3 : 0.08 }}
          aria-hidden="true"
        />
        <motion.span
          animate={{ scale: active ? 1.06 : 1, opacity: absent ? 0.45 : a.status === "idle" ? 0.55 : 1 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className={`relative grid place-items-center rounded-full ${size} ${absent ? "grayscale" : ""} ${a.status === "thinking" ? "ring-pulse" : ""}`}
          style={{
            background: `radial-gradient(circle at 50% 35%, color-mix(in srgb, ${m.color} 28%, transparent), var(--color-surface) 70%)`,
            border: `3px solid ${absent ? "var(--color-ink-faint)" : m.color}`,
            boxShadow: done ? `0 0 24px color-mix(in srgb, ${m.color} 45%, transparent)` : undefined,
          }}
        >
          <span aria-hidden="true">{m.emoji}</span>
          {a.status === "tool" && (
            <span
              className="absolute -inset-1.5 rounded-full border-2 border-transparent ring-spin"
              style={{ borderTopColor: m.color, borderRightColor: m.color }}
              aria-hidden="true"
            />
          )}
          {absent && (
            <span className="absolute rotate-[-14deg] px-1.5 py-0.5 text-[10px] sm:text-xs font-black tracking-widest border-2 border-bear text-bear bg-stage/80 rounded">
              ABSENT
            </span>
          )}
        </motion.span>
        {done && (
          <motion.span
            initial={{ scale: 0, rotate: -20 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 15 }}
            className="absolute -top-1 -right-3 num text-xs font-bold px-1.5 py-0.5 rounded-full text-stage"
            style={{ background: scoreColor(a.report!.score) }}
            aria-hidden="true"
          >
            {a.report!.score > 0.1 ? "▲" : a.report!.score < -0.1 ? "▼" : "●"}
            {signed(a.report!.score)}
          </motion.span>
        )}
      </button>
      <div className={`mt-3 text-center ${compact ? "text-[11px]" : "text-sm"} font-semibold leading-tight`}>{m.name}</div>
      {!compact && (
        <div className="text-[11px] text-ink-faint text-center">
          {m.role}
          {weight !== undefined && ` · w ${weight.toFixed(2)}`}
        </div>
      )}
      <div className={`${compact ? "text-[10px]" : "text-xs"} text-ink-dim h-4`} aria-hidden="true">
        {a.status === "idle" ? "waiting…" : a.status === "thinking" ? "thinking…" : a.status === "tool" ? "on the phone…" : done ? `conf ${a.report!.confidence.toFixed(2)}` : absent ? a.failReason : ""}
      </div>
    </div>
  );
}
