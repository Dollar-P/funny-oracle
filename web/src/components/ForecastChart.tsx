import { useEffect, useRef } from "react";
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

const STEP_SECONDS: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };
const HISTORY: Record<Mode, { period: string; limit: number }> = {
  swing: { period: "6mo", limit: 130 },
  scalp: { period: "5d", limit: 120 },
};

/** Future bar times for the fan, starting at `start`. Daily stock/gold bars skip weekends. */
export function fanTimes(start: number, steps: number[], stepLabel: string, asset: AssetType): number[] {
  const dt = STEP_SECONDS[stepLabel] ?? 86400;
  if (dt !== 86400 || asset === "crypto") return steps.map((k) => start + k * dt);
  // trading days only
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

export default function ForecastChart({
  ticker,
  mode,
  asset,
  fan,
  stepLabel,
  createdAt,
  height = 340,
  animate = true,
}: {
  ticker: string;
  mode: Mode;
  asset: AssetType;
  fan: Fan;
  stepLabel: string;
  createdAt: string;
  height?: number;
  animate?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const h = HISTORY[mode];
  const { data, isError } = useQuery({
    queryKey: ["history", ticker, stepLabel, h.period],
    queryFn: () => api.history(ticker, stepLabel, h.period, h.limit),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!box.current) return;
    const el = box.current;
    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#15152e" },
        textColor: "#a1a1b8",
        fontFamily: "JetBrains Mono, monospace",
        attributionLogo: true, // TradingView attribution required by the licence
      },
      grid: { vertLines: { color: "#1f1f3d" }, horzLines: { color: "#1f1f3d" } },
      rightPriceScale: { borderColor: "#2a2a4a" },
      timeScale: { borderColor: "#2a2a4a", timeVisible: mode === "scalp", secondsVisible: false },
      crosshair: { horzLine: { color: "#6c6c88" }, vertLine: { color: "#6c6c88" } },
      autoSize: true,
    });
    const candles = data?.candles ?? [];
    const created = Math.floor(Date.parse(createdAt) / 1000);
    const before = candles.filter((c) => c.time <= created + 60);
    const after = candles.filter((c) => c.time > created + 60);
    const cs = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
    });
    cs.setData(before.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
    if (after.length) {
      // what really happened after the prediction (replays of older shows)
      const real = chart.addSeries(CandlestickSeries, {
        upColor: "rgba(34,197,94,0.45)",
        downColor: "rgba(239,68,68,0.45)",
        borderVisible: false,
        wickUpColor: "rgba(34,197,94,0.45)",
        wickDownColor: "rgba(239,68,68,0.45)",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      real.setData(after.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
    }

    const lastCandle = before[before.length - 1]?.time ?? created;
    const start = Math.max(lastCandle, Math.min(created, lastCandle + 1));
    const times = fanTimes(start, fan.step, stepLabel, asset);
    const pts = (arr: number[]) =>
      arr.map((v, i) => ({ time: times[i] as UTCTimestamp, value: v })).filter((p, i, a) => i === 0 || p.time > a[i - 1].time);

    // shaded P10–P90: p90 area filled violet, p10 area "erases" below with the background colour
    const hi = chart.addSeries(AreaSeries, {
      lineColor: "rgba(167,139,250,0.7)",
      topColor: "rgba(167,139,250,0.30)",
      bottomColor: "rgba(167,139,250,0.30)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "P90",
    });
    const lo = chart.addSeries(AreaSeries, {
      lineColor: "rgba(167,139,250,0.7)",
      topColor: "#15152e",
      bottomColor: "#15152e",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "P10",
    });
    const mid = chart.addSeries(LineSeries, {
      color: "#facc15",
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      title: "P50",
    });
    const full = { hi: pts(fan.p90), lo: pts(fan.p10), mid: pts(fan.p50) };
    let raf = 0;
    const draw = (n: number) => {
      hi.setData(full.hi.slice(0, n));
      lo.setData(full.lo.slice(0, n));
      mid.setData(full.mid.slice(0, n));
    };
    if (animate && !prefersReducedMotion() && full.mid.length > 2) {
      let n = 1;
      const t0 = performance.now();
      const tick = (now: number) => {
        n = Math.max(1, Math.round(((now - t0) / 1200) * full.mid.length));
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
  }, [data, fan, stepLabel, asset, createdAt, height, mode, animate]);

  return (
    <figure className="relative" aria-label={`Price history and forecast fan for ${ticker}`}>
      <div ref={box} style={{ height }} className="rounded-xl overflow-hidden" />
      {isError && <p className="absolute inset-0 grid place-items-center text-sm text-ink-dim">No price history available — fan only.</p>}
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
      </figcaption>
    </figure>
  );
}
