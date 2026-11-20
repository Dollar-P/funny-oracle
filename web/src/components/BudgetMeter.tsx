import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { thb } from "../lib/format";

export function useBudget() {
  return useQuery({ queryKey: ["budget"], queryFn: api.budget, refetchInterval: 15_000 });
}

export default function BudgetMeter() {
  const { data } = useBudget();
  if (!data) return <div className="w-28 h-8 rounded-full bg-surface animate-pulse" aria-hidden="true" />;
  const used = data.basis === "actual" ? data.estimated_actual_thb : data.spent_thb;
  const frac = Math.min(1, used / Math.max(1e-9, data.budget_thb));
  const tone = frac > 0.9 ? "var(--color-bear)" : frac > 0.7 ? "#f59e0b" : "var(--color-bull)";
  const title =
    `Tracked ${thb(data.spent_thb)} (search at list price) · est. actual ${thb(data.estimated_actual_thb)}` +
    ` (first 5,000 searches/month free) · remaining ${thb(data.remaining_thb)}`;
  return (
    <div className="flex items-center gap-2" title={title}>
      <div className="hidden sm:block w-24">
        <div
          className="h-2 rounded-full bg-surface-2 overflow-hidden border border-line"
          role="meter"
          aria-label="Budget used"
          aria-valuemin={0}
          aria-valuemax={data.budget_thb}
          aria-valuenow={used}
        >
          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${frac * 100}%`, background: tone }} />
        </div>
      </div>
      {/* nowrap: on phones the bar also carries the Desk icon; the cap is hidden there (still in the tooltip) */}
      <span className="num text-sm whitespace-nowrap" style={{ color: frac > 0.7 ? tone : undefined }}>
        <span aria-hidden="true">🪙 </span>
        {thb(used, 0)}<span className="hidden sm:inline"> / {thb(data.budget_thb, 0)}</span>
      </span>
      {data.mock && (
        <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-oracle/20 text-oracle border border-oracle/40">
          mock
        </span>
      )}
    </div>
  );
}
