import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { TickerHit } from "../lib/types";

const TYPE_ICON = { stock: "🏛️", crypto: "🪙", gold: "🥇" } as const;

function useDebounced<T>(v: T, ms: number): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

export default function TickerSearch({ value, onPick }: { value: string; onPick: (symbol: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const dq = useDebounced(q.trim(), 200);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const { data, isFetching } = useQuery({
    queryKey: ["tickers", dq],
    queryFn: () => api.searchTickers(dq),
    enabled: open && dq.length > 0,
    staleTime: 5 * 60_000,
  });
  const hits: TickerHit[] = data?.results ?? [];

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function pick(sym: string) {
    onPick(sym.toUpperCase());
    setQ("");
    setOpen(false);
  }

  return (
    <div ref={box} className="relative w-full sm:w-80">
      <label htmlFor={`${listId}-input`} className="sr-only">
        Search ticker
      </label>
      <div className="flex items-center gap-2 card px-3 h-11 focus-within:shadow-glow">
        <span aria-hidden="true">🔍</span>
        <input
          id={`${listId}-input`}
          role="combobox"
          aria-expanded={open && hits.length > 0}
          aria-controls={listId}
          aria-activedescendant={open && hits[active] ? `${listId}-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder={`Search ticker… (now: ${value})`}
          className="flex-1 bg-transparent outline-none placeholder:text-ink-faint text-sm"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, hits.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (hits[active]) pick(hits[active].symbol);
              else if (q.trim()) pick(q.trim());
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        {isFetching && <span className="text-xs text-ink-faint">…</span>}
      </div>
      {open && dq && (
        <ul id={listId} role="listbox" className="absolute z-20 mt-1 w-full card py-1 max-h-72 overflow-auto shadow-2xl">
          {hits.length === 0 && !isFetching && (
            <li className="px-3 py-2 text-sm text-ink-dim">
              No match — press Enter to use <b className="num">{q.trim().toUpperCase()}</b>
            </li>
          )}
          {hits.map((h, i) => (
            <li
              key={h.symbol}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(h.symbol);
              }}
              className={`px-3 py-2 flex items-center gap-2 text-sm cursor-pointer ${i === active ? "bg-surface-2" : ""}`}
            >
              <span aria-hidden="true">{TYPE_ICON[h.asset_type]}</span>
              <span className="num font-semibold w-20">{h.symbol}</span>
              <span className="text-ink-dim truncate flex-1">{h.name}</span>
              <span className="text-[10px] uppercase tracking-wider text-ink-faint">{h.asset_type}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
