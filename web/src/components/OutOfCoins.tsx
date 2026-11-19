import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { thb } from "../lib/format";

export interface Refusal {
  reason: string;
  spent_thb?: number;
  est_thb?: number;
  budget_thb?: number;
}

export default function OutOfCoins({ refusal, onClose }: { refusal: Refusal | null; onClose: () => void }) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (refusal) btn.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [refusal, onClose]);
  return createPortal(
    <AnimatePresence>
      {refusal && (
        <motion.div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="ooc-title"
            aria-describedby="ooc-desc"
            className="card p-6 max-w-md w-full text-center shadow-glow-gold"
            initial={{ scale: 0.9, y: 20 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-6xl" aria-hidden="true">🪙🔮</div>
            <h2 id="ooc-title" className="font-display text-3xl text-gold mt-3">
              The Oracle is out of coins
            </h2>
            <p id="ooc-desc" className="mt-3 text-ink-dim text-sm">
              {refusal.reason}
            </p>
            {refusal.spent_thb !== undefined && (
              <p className="mt-3 num text-sm">
                spent {thb(refusal.spent_thb)} · this show ~{thb(refusal.est_thb)} · budget {thb(refusal.budget_thb, 0)}
              </p>
            )}
            <p className="mt-3 text-xs text-ink-faint">Replays of stored shows are free — try the Hall of Fame.</p>
            <button ref={btn} onClick={onClose} className="mt-5 px-5 h-10 rounded-full bg-gold text-stage font-bold">
              OK
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
