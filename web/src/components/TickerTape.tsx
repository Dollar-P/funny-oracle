import { useEffect, useRef, useState } from "react";
import type { TapeItem } from "../state/show";
import { clock } from "../lib/format";
import { member } from "../lib/cast";

export default function TickerTape({ items, compact = false }: { items: TapeItem[]; compact?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  useEffect(() => {
    if (stick && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [items.length, stick]);
  return (
    <section className="card overflow-hidden" aria-label="Ticker tape: every step of the show">
      <div className="px-4 py-2 border-b border-line flex items-center gap-2 text-xs text-ink-dim">
        <span className="w-2 h-2 rounded-full bg-bear animate-pulse" aria-hidden="true" />
        <span className="font-semibold tracking-wider uppercase">Ticker tape</span>
        <span className="ml-auto">{items.length} lines</span>
      </div>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
        }}
        className={`${compact ? "h-32" : "h-48"} overflow-y-auto px-4 py-2 num text-[11px] sm:text-xs leading-relaxed`}
        tabIndex={0}
      >
        {items.map((t) => (
          <div key={t.seq} className="flex gap-2 whitespace-nowrap">
            <span className="text-ink-faint">{clock(t.ts)}</span>
            <span aria-hidden="true">{t.icon}</span>
            {t.agent && <span className="text-ink-dim">{member(t.agent).name}</span>}
            <span className="truncate text-ink">{t.text}</span>
            {t.ok === true && <span className="text-bull">✓</span>}
            {t.ok === false && <span className="text-bear">✗</span>}
          </div>
        ))}
      </div>
    </section>
  );
}
