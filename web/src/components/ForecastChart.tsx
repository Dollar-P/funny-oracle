import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  LineStyle,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../lib/api";
import type { AssetType, Fan, Mode } from "../lib/types";
import { prefersReducedMotion } from "../state/settings";
import { dateTime, price } from "../lib/format";

const STEP_SECONDS: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };
// history shown before the prediction time, in bars of the forecast interval
const LOOKBACK_BARS: Record<Mode, number> = { swing: 130, scalp: 120 };

/** Future bar times (UTC seconds) for the fan, starting at `start`. Daily stock/gold bars skip weekends. */
export function fanTimes(start: number, steps: number[], stepLabel: string, asset: AssetType): number[] {
  const dt = STEP_SECONDS[stepLabel] ?? 86400;
  if (dt !== 86400 || asset === "crypto") return steps.map((k) => start + k * dt);
  const out: number[] = [];
  let t = start;
  let k = 0;
  for (const target of steps) {
    while (k < target) {
      t += 86400;
      const d = new Date(t * 1000).getUTCDay();
      if (d !== 0 && d !== 6) k++;
    }
    out.push(t);
  }
  return out;
}

/**
 * lightweight-charts formats every timestamp as UTC. Shifting each UTC timestamp by the browser's offset makes
 * the axis, the crosshair and the day boundaries read in local time (e.g. Asia/Bangkok), matching the tables.
 */
export function toLocal(t: number): UTCTimestamp {
  return (t - new Date(t * 1000).getTimezoneOffset() * 60) as UTCTimestamp;
}

/** The [start, end] history window around a prediction made at `createdAt` for a horizon ending at `targetTime`. */
export function historyWindow(mode: Mode, stepLabel: string, createdAt: string, targetTime: string) {
  const dt = STEP_SECONDS[stepLabel] ?? 86400;
  const created = Date.parse(createdAt) / 1000;
  const target = Date.parse(targetTime) / 1000;
  const lookback = LOOKBACK_BARS[mode] * dt * (dt === 86400 ? 1.45 : 1); // calendar days for ~130 trading days
  const start = created - lookback;
  const end = target + Math.max(0.25 * (target - created), 4 * dt);
  // round so a live show doesn't refetch every second
  const round = (x: number) => Math.floor(x / Math.min(dt, 3600)) * Math.min(dt, 3600);
  return { start: new Date(round(start) * 1000).toISOString(), end: new Date(round(end) * 1000).toISOString() };
}

export default function ForecastChart({
  ticker,
  mode,
  asset,
  fan,
  stepLabel,
  createdAt,
  targetTime,
  actual = null,
  height = 340,
  animate = true,
  compact = false,
}: {
  ticker: string;
  mode: Mode;
  asset: AssetType;
  fan: Fan;
  stepLabel: string;
  createdAt: string;
  targetTime: string;
  actual?: number | null;
  height?: number;
  animate?: boolean;
  compact?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const win = useMemo(() => historyWindow(mode, stepLabel, createdAt, targetTime), [mode, stepLabel, createdAt, targetTime]);
  const { data, isError } = useQuery({
    queryKey: ["history-window", ticker, stepLabel, win.start, win.end],
    queryFn: () => api.historyWindow(ticker, stepLabel, win.start, win.end),
    staleTime: 60_000,
  });
  const created = Math.floor(Date.parse(createdAt) / 1000);
  const target = Math.floor(Date.parse(targetTime) / 1000);

  useEffect(() => {
    if (!box.current) return;
    const el = box.current;
    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#15152e" },
        textColor: "#a1a1b8",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: compact ? 10 : 12,
        attributionLogo: !compact, // TradingView attribution (the main chart always shows it)
      },
      grid: { vertLines: { color: "#1f1f3d" }, horzLines: { color: "#1f1f3d" } },
      rightPriceScale: { borderColor: "#2a2a4a" },
      timeScale: { borderColor: "#2a2a4a", timeVisible: mode === "scalp", secondsVisible: false },
      crosshair: { horzLine: { color: "#6c6c88" }, vertLine: { color: "#6c6c88" } },
      handleScroll: !compact,
      handleScale: !compact,
      autoSize: true,
    });
    const candles = data?.candles ?? [];
    const before = candles.filter((c) => c.time <= created);
    const after = candles.filter((c) => c.time > created);
    const bar = (c: (typeof candles)[number]) => ({ time: toLocal(c.time), open: c.open, high: c.high, low: c.low, close: c.close });
    const cs = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    cs.setData(before.map(bar));

    // the fan starts at the prediction time and price (fan[0] = price_now)
    const times = fanTimes(created, fan.step, stepLabel, asset).map(toLocal);
    const pts = (arr: number[]) =>
      arr.map((v, i) => ({ time: times[i], value: v })).filter((p, i, a) => i === 0 || p.time > a[i - 1].time);
    const hi = chart.addSeries(AreaSeries, {
      lineColor: "rgba(167,139,250,0.7)",
      topColor: "rgba(167,139,250,0.30)",
      bottomColor: "rgba(167,139,250,0.30)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: !compact,
      title: compact ? "" : "P90",
    });
    const lo = chart.addSeries(AreaSeries, {
      lineColor: "rgba(167,139,250,0.7)",
      topColor: "#15152e",
      bottomColor: "#15152e",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: !compact,
      title: compact ? "" : "P10",
    });
    // faded candles go above the fan: the P10 "eraser" area would otherwise hide reality below P10
    if (after.length) {
      // what really happened after the prediction
      const real = chart.addSeries(CandlestickSeries, {
        upColor: "rgba(34,197,94,0.4)",
        downColor: "rgba(239,68,68,0.4)",
        borderVisible: false,
        wickUpColor: "rgba(34,197,94,0.4)",
        wickDownColor: "rgba(239,68,68,0.4)",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      real.setData(after.map(bar));
    }

    const mid = chart.addSeries(LineSeries, {
      color: "#facc15",
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: !compact,
      title: compact ? "" : "P50",
    });
    if (actual !== null && Number.isFinite(actual)) {
      // the real price at target_time
      const act = chart.addSeries(LineSeries, {
        color: "#ffffff",
        lineVisible: false,
        pointMarkersVisible: true,
        pointMarkersRadius: compact ? 3 : 5,
        priceLineVisible: true,
        priceLineColor: "rgba(255,255,255,0.5)",
        priceLineStyle: LineStyle.Dotted,
        lastValueVisible: !compact,
        title: compact ? "" : "actual",
      });
      act.setData([{ time: toLocal(target), value: actual }]);
    }
    const full = { hi: pts(fan.p90), lo: pts(fan.p10), mid: pts(fan.p50) };
    let raf = 0;
    const draw = (n: number) => {
      hi.setData(full.hi.slice(0, n));
      lo.setData(full.lo.slice(0, n));
      mid.setData(full.mid.slice(0, n));
    };
    if (animate && !prefersReducedMotion() && full.mid.length > 2) {
      const t0 = performance.now();
      const tick = (now: number) => {
        const n = Math.max(1, Math.round(((now - t0) / 1200) * full.mid.length));
        draw(Math.min(n, full.mid.length));
        if (n < full.mid.length) raf = requestAnimationFrame(tick);
      };
      draw(1);
      raf = requestAnimationFrame(tick);
    } else {
      draw(full.mid.length);
    }
    chart.timeScale().fitContent();
    return () => {
      cancelAnimationFrame(raf);
      chart.remove();
    };
  }, [data, fan, stepLabel, asset, created, target, actual, height, mode, animate, compact]);

  const candles = data?.candles ?? [];
  const fallback = data && data.interval !== stepLabel;
  return (
    <figure
      className="relative"
      aria-label={`Price history and forecast fan for ${ticker}, predicted ${dateTime(createdAt)}`}
      data-created={dateTime(createdAt)}
      data-first-candle={candles.length ? dateTime(new Date(candles[0].time * 1000).toISOString()) : ""}
      data-last-candle={candles.length ? dateTime(new Date(candles[candles.length - 1].time * 1000).toISOString()) : ""}
      data-candles-before={candles.filter((c) => c.time <= created).length}
      data-candles-after={candles.filter((c) => c.time > created).length}
      data-fan-start={fan.p50[0]?.toFixed(2)}
      data-interval={data?.interval ?? ""}
      data-actual={actual ?? ""}
    >
      <div ref={box} style={{ height }} className="rounded-xl overflow-hidden" />
      {isError && <p className="absolute inset-0 grid place-items-center text-sm text-ink-dim">No price history available — fan only.</p>}
      {!compact && (
        <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-dim">
          <span>
            <span className="inline-block w-3 h-3 rounded-sm align-middle mr-1 bg-oracle/40 border border-oracle/70" />
            P10–P90 band (80% of 10,000 simulated paths)
          </span>
          <span>
            <span className="inline-block w-4 border-t-2 border-dashed border-gold align-middle mr-1" />
            P50 (median)
          </span>
          <span>faded candles = what happened after the prediction</span>
          {actual !== null && <span>⚪ white dot + dotted line = actual price {price(actual)} at the target time</span>}
          <span>times in {Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
          {fallback && <span className="text-gold">older than yfinance keeps {stepLabel} bars — showing {data!.interval} candles</span>}
        </figcaption>
      )}
    </figure>
  );
}
