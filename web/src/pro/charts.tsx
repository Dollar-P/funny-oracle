import { useEffect, useRef } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { fanTimes, toLocal } from "../components/ForecastChart";
import type { AssetType } from "../lib/types";
import type { Series } from "./api";

const C = {
  bg: "#15181d", grid: "#1f242b", text: "#9aa3ae", line: "#262b33", accent: "#5b9cff", up: "#2fb874", down: "#e5535b",
  dim: "#6b7480", warn: "#d9a441",
};

function baseChart(el: HTMLDivElement, height: number): IChartApi {
  return createChart(el, {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: C.bg }, textColor: C.text, fontFamily: "Inter, system-ui", fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.line },
    timeScale: { borderColor: C.line, timeVisible: true },
    crosshair: { mode: 0 },
    localization: { priceFormatter: (p: number) => p.toLocaleString("en-US", { maximumFractionDigits: Math.abs(p) >= 1000 ? 0 : 2 }) },
  });
}

function toPoints(s: Series) {
  const seen = new Set<number>();
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (const [ts, v] of s) {
    const t = toLocal(Math.floor(Date.parse(ts) / 1000));
    if (seen.has(t)) continue;
    seen.add(t);
    out.push({ time: t, value: v });
  }
  return out.sort((a, b) => a.time - b.time);
}

/** Equity curve vs buy & hold basket and cash (and optional extra curves for the Lab overlay). */
export function EquityChart({ lines, height = 260 }: { lines: { name: string; data: Series; color?: string; dashed?: boolean; width?: number }[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = baseChart(ref.current, height);
    lines.forEach((l, i) => {
      if (!l.data.length) return;
      const s = chart.addSeries(LineSeries, {
        color: l.color ?? (i === 0 ? C.accent : C.dim), lineWidth: (l.width ?? (i === 0 ? 2 : 1)) as 1 | 2 | 3 | 4,
        lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, priceLineVisible: false, lastValueVisible: i === 0,
        title: l.name,
      });
      s.setData(toPoints(l.data));
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [lines, height]);
  return <div ref={ref} className="w-full" style={{ height }} />;
}

interface TradeChartProps {
  candles: { t: number; o: number; h: number; l: number; c: number }[];
  entry: number | null;
  stop: number;
  targets: number[];
  side: "long" | "short" | null;
  createdAt: string;
  timeExit: string;
  fan?: Record<string, number[]> | null;
  asset: AssetType;
  fills?: { ts: string; reason: string; price: number; side: string }[];
  band?: { price: number; p10: number; p50: number; p90: number } | null;
  height?: number;
}

/** Candles + entry / stop / targets as price lines + the P10–P90 fan + time-exit and fill markers. */
export function TradeChart({ candles, entry, stop, targets, side, createdAt, timeExit, fan, asset, fills = [], band = null, height = 320 }: TradeChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const chart = baseChart(ref.current, height);
    const cs = chart.addSeries(CandlestickSeries, {
      upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false,
    });
    cs.setData(candles.map((k) => ({ time: toLocal(k.t), open: k.o, high: k.h, low: k.l, close: k.c })));
    const line = (price: number, title: string, color: string, style = LineStyle.Solid) =>
      cs.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    if (entry) line(entry, "entry", C.accent);
    line(stop, "stop", C.down, LineStyle.Dashed);
    targets.forEach((t, i) => Number.isFinite(t) && line(t, `T${i + 1}`, C.up, LineStyle.Dashed));
    const start = Math.floor(Date.parse(createdAt) / 1000);
    const end = Math.floor(Date.parse(timeExit) / 1000);
    if (fan && fan.step?.length) {
      const times = fanTimes(start, fan.step, "1d", asset).map(toLocal);
      const pts = (k: string) => {
        const seen = new Set<number>();
        return times.map((t, i) => ({ time: t, value: fan[k][i] })).filter((p) => !seen.has(p.time) && seen.add(p.time));
      };
      const hi = chart.addSeries(AreaSeries, { lineColor: "rgba(91,156,255,0.5)", topColor: "rgba(91,156,255,0.12)",
        bottomColor: "rgba(91,156,255,0.12)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      hi.setData(pts("p90"));
      const lo = chart.addSeries(AreaSeries, { lineColor: "rgba(91,156,255,0.5)", topColor: C.bg, bottomColor: C.bg,
        lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      lo.setData(pts("p10"));
      const mid = chart.addSeries(LineSeries, { color: "rgba(91,156,255,0.8)", lineWidth: 1, lineStyle: LineStyle.Dotted,
        priceLineVisible: false, lastValueVisible: false });
      mid.setData(pts("p50"));
    } else if (band && end > start) {
      // no stored fan path (e.g. a closed-form forecast): straight lines from the decision price to P10/P50/P90
      for (const [k, color, style] of [["p90", "rgba(91,156,255,0.6)", LineStyle.Solid], ["p50", "rgba(91,156,255,0.8)", LineStyle.Dotted],
        ["p10", "rgba(91,156,255,0.6)", LineStyle.Solid]] as const) {
        const s = chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false });
        s.setData([{ time: toLocal(start), value: band.price }, { time: toLocal(end), value: band[k] }]);
      }
    }
    const lastT = candles[candles.length - 1].t;
    const markers: { time: UTCTimestamp; position: "aboveBar" | "belowBar" | "inBar"; color: string; shape: "arrowUp" | "arrowDown" | "circle" | "square"; text: string }[] = [];
    const exitT = Math.floor(Date.parse(timeExit) / 1000);
    const nearest = (t: number) => {
      let best = candles[0].t;
      for (const k of candles) if (Math.abs(k.t - t) < Math.abs(best - t)) best = k.t;
      return best;
    };
    if (exitT <= lastT) markers.push({ time: toLocal(nearest(exitT)), position: "aboveBar", color: C.warn, shape: "square", text: "time exit" });
    for (const f of fills) {
      const t = nearest(Math.floor(Date.parse(f.ts) / 1000));
      const buy = f.side === "buy";
      markers.push({ time: toLocal(t), position: buy ? "belowBar" : "aboveBar", color: f.reason === "stop" ? C.down : C.accent,
        shape: buy ? "arrowUp" : "arrowDown", text: f.reason });
    }
    if (side) markers.sort((a, b) => a.time - b.time);
    if (markers.length) createSeriesMarkers(cs, markers);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, entry, stop, targets, side, createdAt, timeExit, fan, asset, fills, band, height]);
  return <div ref={ref} className="w-full" style={{ height }} />;
}

// --- small SVG charts (calibration, histogram, bars) ------------------------------------------------------------

export function CalibrationPlot({ bins, size = 220 }: { bins: { mean_p: number; freq: number; n: number }[]; size?: number }) {
  const pad = 26;
  const w = size;
  const s = (v: number) => pad + v * (w - pad * 1.5);
  const y = (v: number) => w - pad - v * (w - pad * 1.5);
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  return (
    <svg viewBox={`0 0 ${w} ${w + 12}`} className="w-full max-w-[260px]" role="img" aria-label="Calibration plot: predicted vs observed frequency">
      <rect x={pad} y={pad / 2} width={w - pad * 1.5} height={w - pad * 1.5} fill="none" stroke={C.line} />
      <line x1={s(0)} y1={y(0)} x2={s(1)} y2={y(1)} stroke={C.dim} strokeDasharray="3 3" />
      {[0, 0.5, 1].map((v) => (
        <g key={v}>
          <text x={s(v)} y={w - 8} fill={C.text} fontSize="9" textAnchor="middle">{v}</text>
          <text x={8} y={y(v) + 3} fill={C.text} fontSize="9">{v}</text>
        </g>
      ))}
      {bins.map((b, i) => (
        <circle key={i} cx={s(b.mean_p)} cy={y(b.freq)} r={2 + 5 * Math.sqrt(b.n / maxN)} fill="rgba(91,156,255,0.55)" stroke={C.accent}>
          <title>{`predicted ${(b.mean_p * 100).toFixed(0)} % → observed ${(b.freq * 100).toFixed(0)} % (n=${b.n})`}</title>
        </circle>
      ))}
      <text x={w / 2} y={w + 10} fill={C.dim} fontSize="8" textAnchor="middle">predicted P(up)</text>
    </svg>
  );
}

export function Histogram({ counts, edges, marker, markerLabel }: { counts: number[]; edges: number[]; marker?: number | null; markerLabel?: string }) {
  const w = 320;
  const h = 120;
  const max = Math.max(1, ...counts);
  const lo = edges[0];
  const hi = edges[edges.length - 1];
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * w;
  return (
    <svg viewBox={`0 0 ${w} ${h + 18}`} className="w-full" role="img" aria-label="Random-strategy Sharpe distribution">
      {counts.map((c, i) => (
        <rect key={i} x={x(edges[i]) + 0.5} y={h - (c / max) * h} width={Math.max(1, x(edges[i + 1]) - x(edges[i]) - 1)}
          height={(c / max) * h} fill="#2b3340" />
      ))}
      {marker !== null && marker !== undefined && marker >= lo && marker <= hi && (
        <g>
          <line x1={x(marker)} x2={x(marker)} y1={0} y2={h} stroke={C.accent} strokeWidth="2" />
          <text x={Math.min(w - 4, x(marker) + 4)} y={10} fill={C.accent} fontSize="10" textAnchor={x(marker) > w - 80 ? "end" : "start"}>
            {markerLabel}
          </text>
        </g>
      )}
      <text x={0} y={h + 14} fill={C.text} fontSize="9">{lo.toFixed(2)}</text>
      <text x={w} y={h + 14} fill={C.text} fontSize="9" textAnchor="end">{hi.toFixed(2)}</text>
      <text x={w / 2} y={h + 14} fill={C.dim} fontSize="9" textAnchor="middle">Sharpe</text>
    </svg>
  );
}

export function UsageBar({ used, limit, label, fmt }: { used: number; limit: number | null; label: string; fmt: (v: number) => string }) {
  const frac = limit ? Math.min(1, Math.abs(used) / Math.abs(limit)) : 0;
  const tone = frac >= 0.9 ? "bg-desk-down" : frac >= 0.7 ? "bg-desk-warn" : "bg-desk-accent";
  return (
    <div className="min-w-0">
      <div className="flex justify-between gap-2 text-[12px]">
        <span className="text-desk-dim truncate">{label}</span>
        <span className="num text-desk-ink whitespace-nowrap">
          {fmt(used)}
          {limit !== null && <span className="text-desk-faint"> / {fmt(limit)}</span>}
        </span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-desk-panel-2">
        <div className={`h-1.5 rounded ${tone}`} style={{ width: `${(limit ? frac : 0) * 100}%` }} />
      </div>
    </div>
  );
}

/** Horizontal bars around zero (ablation ΔBrier etc.). Negative = better when `lowerIsBetter`. */
export function DeltaBars({ rows, fmt }: { rows: { label: string; value: number | null; note?: string }[]; fmt: (v: number) => string }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.value ?? 0)));
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[8.5rem_1fr_4.5rem] items-center gap-2 text-[12px]">
          <span className="truncate text-desk-dim" title={r.label}>{r.label}</span>
          <div className="relative h-3 rounded bg-desk-panel-2">
            <div className="absolute left-1/2 top-0 h-3 w-px bg-desk-line" />
            {r.value !== null && (
              <div className={`absolute top-0 h-3 rounded ${r.value > 0 ? "bg-desk-down/70" : "bg-desk-up/70"}`}
                style={r.value > 0 ? { left: "50%", width: `${(r.value / max) * 50}%` } : { right: "50%", width: `${(-r.value / max) * 50}%` }} />
            )}
          </div>
          <span className="num text-right">{r.value === null ? "—" : fmt(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
