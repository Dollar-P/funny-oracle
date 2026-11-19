import { useQueries } from "@tanstack/react-query";
import { api } from "../lib/api";
import { pct, price } from "../lib/format";
import Sparkline from "./Sparkline";

export const PRESETS = [
  { symbol: "NVDA", label: "NVDA" },
  { symbol: "AAPL", label: "AAPL" },
  { symbol: "BTC-USD", label: "BTC" },
  { symbol: "ETH-USD", label: "ETH" },
  { symbol: "GC=F", label: "GOLD" },
];

export default function PresetChips({ value, onPick }: { value: string; onPick: (s: string) => void }) {
  const quotes = useQueries({
    queries: PRESETS.map((p) => ({
      queryKey: ["quote", p.symbol],
      queryFn: () => api.quote(p.symbol),
      staleTime: 60_000,
      refetchInterval: 60_000,
    })),
  });
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Preset tickers">
      {PRESETS.map((p, i) => {
        const q = quotes[i].data;
        const up = (q?.change_pct ?? 0) >= 0;
        const selected = value === p.symbol;
        return (
          <button
            key={p.symbol}
            onClick={() => onPick(p.symbol)}
            aria-pressed={selected}
            className={`card flex items-center gap-2 pl-3 pr-2 h-11 text-sm transition-all ${
              selected ? "shadow-glow-gold !border-gold/70 bg-surface-2" : "hover:bg-surface-2"
            }`}
          >
            <span className="font-bold">{p.label}</span>
            {q ? (
              <>
                <span className={`num text-xs ${up ? "text-bull" : "text-bear"}`}>
                  {up ? "▲" : "▼"}
                  {pct(Math.abs(q.change_pct), 1, false)}
                </span>
                <Sparkline data={q.sparkline} width={48} height={18} label={`${p.label} recent price, ${price(q.price)}`} />
              </>
            ) : quotes[i].isError ? (
              <span className="text-xs text-ink-faint">no data</span>
            ) : (
              <span className="w-[86px] h-3 rounded bg-surface-2 animate-pulse" aria-hidden="true" />
            )}
          </button>
        );
      })}
    </div>
  );
}
