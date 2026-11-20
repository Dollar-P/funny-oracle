import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Metric } from "./api";

// --- formatting (tabular numbers; UTC stored, local displayed) ---------------------------------------------

const nf = (d: number) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export function money(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return nf(d).format(v);
}

export function px(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  return nf(a >= 1000 ? 2 : a >= 1 ? 2 : 4).format(v);
}

export function num(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return nf(d).format(v);
}

/** fraction → "+1.23 %" */
export function pctf(v: number | null | undefined, d = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = (v * 100).toFixed(d);
  return `${signed && v > 0 ? "+" : ""}${s} %`;
}

export function rr(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(d)} R`;
}

export function dt(iso: string | null | undefined, withYear = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", ...(withYear ? { year: "numeric" } : {}), hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

export function tzName(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

// --- atoms -------------------------------------------------------------------------------------------------

export function Dir({ side }: { side: string | null | undefined }) {
  if (side === "long") return <span className="text-desk-up">▲ long</span>;
  if (side === "short") return <span className="text-desk-down">▼ short</span>;
  return <span className="text-desk-dim">— flat</span>;
}

/** P&L: green/red always paired with ▲/▼ (never colour alone). */
export function Pnl({ v, d = 2, suffix = "" }: { v: number | null | undefined; d?: number; suffix?: string }) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span className="text-desk-faint">—</span>;
  if (v === 0) return <span className="num">0.00{suffix}</span>;
  const up = v > 0;
  return (
    <span className={`num ${up ? "text-desk-up" : "text-desk-down"}`}>
      {up ? "▲" : "▼"} {nf(d).format(Math.abs(v))}
      {suffix}
    </span>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "accent" | "warn" | "up" | "down"; title?: string }) {
  const cls = {
    neutral: "border-desk-line text-desk-dim",
    accent: "border-desk-accent/50 text-desk-accent",
    warn: "border-desk-warn/50 text-desk-warn",
    up: "border-desk-up/50 text-desk-up",
    down: "border-desk-down/50 text-desk-down",
  }[tone];
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded border px-1.5 py-px text-[11px] leading-4 ${cls}`}>
      {children}
    </span>
  );
}

export function Insufficient({ n, min = 30 }: { n: number; min?: number }) {
  return (
    <Badge tone="warn" title={`n = ${n}; a verdict needs at least ${min}`}>
      insufficient data · n={n}
    </Badge>
  );
}

/** value [lo, hi] · n, or "insufficient data" — every metric shown with n and a CI (PRO.md §2.5). */
export function MetricCell({ m, fmt = (v: number) => num(v), showBadge = true }: { m: Metric | null | undefined; fmt?: (v: number) => string; showBadge?: boolean }) {
  if (!m || m.value === null || m.value === undefined) {
    return <span className="text-desk-faint num">— {m ? <span className="text-[11px]">n={m.n}</span> : null}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5 num">
      <span className={m.sufficient ? "" : "text-desk-dim"}>{fmt(m.value)}</span>
      {m.lo !== null && m.hi !== null && (
        <span className="text-[11px] text-desk-faint">
          [{fmt(m.lo)}, {fmt(m.hi)}]
        </span>
      )}
      <span className="text-[11px] text-desk-faint">n={m.n}</span>
      {showBadge && !m.sufficient && <Insufficient n={m.n} />}
    </span>
  );
}

export function Panel({ title, actions, children, className = "", pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`panel min-w-0 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-desk-line px-3 py-2">
          <h2 className="text-[13px] font-medium text-desk-ink">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={pad ? "p-3" : ""}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="panel px-3 py-2.5 min-w-0">
      <div className="text-[11px] uppercase tracking-wide text-desk-faint">{label}</div>
      <div className="num mt-0.5 text-lg text-desk-ink truncate">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-desk-dim">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-[13px] text-desk-faint">{children}</div>;
}

export function Btn({ children, onClick, tone = "neutral", disabled, title, type = "button" }: { children: ReactNode; onClick?: () => void; tone?: "neutral" | "accent" | "danger"; disabled?: boolean; title?: string; type?: "button" | "submit" }) {
  const cls = {
    neutral: "border-desk-line bg-desk-panel-2 text-desk-ink hover:border-desk-faint",
    accent: "border-desk-accent/60 bg-desk-accent/15 text-desk-accent hover:bg-desk-accent/25",
    danger: "border-desk-down/50 bg-desk-down/10 text-desk-down hover:bg-desk-down/20",
  }[tone];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={`rounded border px-2 py-1 text-[12px] disabled:opacity-40 ${cls}`}>
      {children}
    </button>
  );
}

export const STATUS_TONE: Record<string, "neutral" | "accent" | "warn" | "up" | "down"> = {
  new: "accent", approved: "accent", filled: "up", closed: "neutral", flat: "neutral", rejected: "warn",
  cancelled: "neutral", expired: "neutral",
};

export function Status({ s, reason }: { s: string; reason?: string | null }) {
  return (
    <Badge tone={STATUS_TONE[s] ?? "neutral"} title={reason ?? undefined}>
      {s.replace("_", " ")}
    </Badge>
  );
}

// --- live updates -----------------------------------------------------------------------------------------

const INVALIDATE: Record<string, string[]> = {
  signal_created: ["signals", "overview"],
  signal_flat: ["signals"],
  risk_rejected: ["signals", "risk"],
  order_placed: ["signals", "positions", "orders", "overview"],
  order_filled: ["signals", "positions", "orders", "fills", "overview"],
  position_closed: ["signals", "positions", "journal", "overview", "equity"],
  entry_cancelled: ["signals", "positions"],
  breaker_tripped: ["overview", "risk"],
  equity_tick: ["overview", "equity"],
  job_started: ["scheduler"],
  job_done: ["scheduler", "signals"],
  job_skipped: ["scheduler"],
  job_failed: ["scheduler"],
  scheduler_started: ["scheduler", "overview"],
  scheduler_stopped: ["scheduler", "overview"],
  settings_changed: ["settings", "overview", "signals"],
  account_reset: ["settings", "overview", "signals", "positions", "journal", "equity", "risk"],
  report_ready: ["report"],
};

/** Subscribes to /api/pro/events; refreshes the affected queries and returns ids of rows to highlight. */
export function useDeskEvents(): Set<number> {
  const qc = useQueryClient();
  const [flash, setFlash] = useState<Set<number>>(new Set());
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/pro/events");
    const onEvent = (ev: MessageEvent) => {
      try {
        const e = JSON.parse(ev.data) as { type: string; signal_id?: number };
        (INVALIDATE[e.type] ?? []).forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
        if (e.signal_id) {
          setFlash((s) => new Set(s).add(e.signal_id as number));
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setFlash(new Set()), 2600);
        }
      } catch {
        /* ignore malformed events */
      }
    };
    Object.keys(INVALIDATE).forEach((t) => es.addEventListener(t, onEvent as EventListener));
    return () => {
      es.close();
      window.clearTimeout(timer.current);
    };
  }, [qc]);
  return flash;
}
