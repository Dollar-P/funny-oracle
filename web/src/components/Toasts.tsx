import { AnimatePresence, motion } from "framer-motion";
import { useToasts } from "../state/settings";

export default function Toasts() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-[min(92vw,360px)]" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40 }}
            className={`card px-4 py-3 text-sm flex gap-3 items-start shadow-lg ${
              t.kind === "error" ? "border-bear/60" : t.kind === "success" ? "border-bull/60" : ""
            }`}
            role={t.kind === "error" ? "alert" : "status"}
          >
            <span aria-hidden="true">{t.kind === "error" ? "🛑" : t.kind === "success" ? "✅" : "💬"}</span>
            <span className="flex-1">{t.text}</span>
            <button onClick={() => dismiss(t.id)} className="text-ink-faint hover:text-ink" aria-label="Dismiss">
              ✕
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
