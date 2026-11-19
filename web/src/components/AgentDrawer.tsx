import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { AgentState } from "../state/show";
import { member, TOOL_ICON } from "../lib/cast";
import { signed } from "../lib/format";
import { quoteOnce } from "../state/show";

function ScoreMeter({ score }: { score: number }) {
  const pos = ((score + 1) / 2) * 100;
  const color = score > 0.1 ? "var(--color-bull)" : score < -0.1 ? "var(--color-bear)" : "var(--color-neutral)";
  return (
    <div>
      <div className="relative h-3 rounded-full bg-gradient-to-r from-bear/60 via-surface-2 to-bull/60">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-ink-faint" />
        <div className="absolute top-1/2 w-4 h-4 -mt-2 -ml-2 rounded-full border-2 border-stage" style={{ left: `${pos}%`, background: color }} />
      </div>
      <div className="flex justify-between text-[11px] text-ink-faint mt-1">
        <span>▼ −1</span>
        <span className="num text-sm" style={{ color }}>
          {signed(score)}
        </span>
        <span>+1 ▲</span>
      </div>
    </div>
  );
}

export default function AgentDrawer({ a, weight, onClose }: { a: AgentState | null; weight?: number; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!a) return;
    const prev = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      prev?.focus?.();
    };
  }, [a, onClose]);
  const m = a ? member(a.id) : null;
  const r = a?.report;
  // portal: ancestors with backdrop-filter would otherwise clip a fixed drawer
  return createPortal(
    <AnimatePresence>
      {a && m && (
        <>
          <motion.div className="fixed inset-0 z-40 bg-black/50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-labelledby="drawer-title"
            className="fixed right-0 top-0 bottom-0 z-50 w-full sm:w-[480px] bg-stage-2 border-l border-line overflow-y-auto"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
          >
            <div className="sticky top-0 bg-stage-2/95 backdrop-blur px-5 py-4 border-b border-line flex items-center gap-3">
              <span className="w-12 h-12 rounded-full grid place-items-center text-2xl border-2" style={{ borderColor: m.color }} aria-hidden="true">
                {m.emoji}
              </span>
              <div className="flex-1">
                <h2 id="drawer-title" className="font-display text-2xl leading-none">
                  {m.name}
                </h2>
                <div className="text-xs text-ink-dim mt-1">
                  {m.role} analyst{weight !== undefined && ` · weight ${weight.toFixed(2)}`}
                </div>
              </div>
              <button ref={close} onClick={onClose} className="w-9 h-9 rounded-full hover:bg-surface-2" aria-label="Close">
                ✕
              </button>
            </div>
            <div className="p-5 space-y-6">
              {r ? (
                <>
                  <section>
                    <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-2">Score</h3>
                    <ScoreMeter score={r.score} />
                    <div className="mt-3 text-sm">
                      Confidence <span className="num">{r.confidence.toFixed(2)}</span>
                      <div className="mt-1 h-1.5 rounded-full bg-surface-2">
                        <div className="h-full rounded-full bg-oracle" style={{ width: `${r.confidence * 100}%` }} />
                      </div>
                    </div>
                  </section>
                  <section>
                    <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-2">Key points</h3>
                    <ul className="space-y-1.5 text-sm list-disc pl-5">
                      {r.key_points.map((k, i) => (
                        <li key={i}>{k}</li>
                      ))}
                    </ul>
                  </section>
                  <section>
                    <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-2">Reasoning</h3>
                    <p className="text-sm text-ink-dim leading-relaxed">{r.reasoning}</p>
                  </section>
                </>
              ) : (
                <p className="text-sm text-bear">
                  ⚠ Absent ({a.failReason}). {a.failDetail}
                  <br />
                  <span className="text-ink-dim">Dropped from the tug-of-war; the other weights were renormalized.</span>
                </p>
              )}
              <section>
                <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-2">Tool trace ({a.trace.length})</h3>
                <ol className="space-y-1 num text-xs">
                  {a.trace.map((t, i) => (
                    <li key={i} className="flex gap-2 items-baseline">
                      <span aria-hidden="true">{t.kind === "search" ? "🔎" : TOOL_ICON[t.tool] ?? "📡"}</span>
                      <span className="flex-1 break-all">{t.kind === "search" ? quoteOnce(t.text) : t.text}</span>
                      {t.ms !== undefined && <span className="text-ink-faint">{Math.round(t.ms)} ms</span>}
                      {t.bytes !== undefined && t.bytes > 0 && <span className="text-ink-faint">{(t.bytes / 1024).toFixed(1)} KB</span>}
                      <span className={t.ok === false ? "text-bear" : "text-bull"}>{t.ok === false ? "✗" : t.ok ? "✓" : "…"}</span>
                    </li>
                  ))}
                  {a.trace.length === 0 && <li className="text-ink-faint">No tools called.</li>}
                </ol>
              </section>
              {r && r.sources.length > 0 && (
                <section>
                  <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-2">Sources</h3>
                  <ul className="space-y-1 text-xs">
                    {r.sources.map((s, i) => {
                      const [title, url] = s.includes(" | ") ? s.split(" | ") : [s, s.startsWith("http") ? s : ""];
                      return (
                        <li key={i} className="truncate">
                          {url ? (
                            <a href={url} target="_blank" rel="noreferrer" className="text-wizard underline">
                              {title}
                            </a>
                          ) : (
                            <span className="num text-ink-dim">{title}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
