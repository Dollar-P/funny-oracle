import { motion } from "framer-motion";
import type { Prediction } from "../lib/types";
import { member } from "../lib/cast";
import { price, share, shortModel, signed, thb } from "../lib/format";

function dir(p: Prediction) {
  return p.forecast.prob_up > 0.5 ? "bull" : p.forecast.prob_up < 0.5 ? "bear" : "flat";
}

export default function HeadToHead({ a, b, usdThb, mock }: { a: Prediction; b: Prediction; usdThb: number; mock: boolean }) {
  const da = dir(a);
  const db = dir(b);
  const agree = da === db;
  const banner = agree
    ? da === "bull"
      ? "🤝 Both bullish"
      : da === "bear"
        ? "🤝 Both bearish"
        : "🤝 Both on the fence"
    : "⚔️ They disagree!";
  const rows: { label: string; a: string; b: string; better?: "a" | "b" | null }[] = [
    { label: "🎯 P50", a: price(a.forecast.p50), b: price(b.forecast.p50) },
    { label: "P(up)", a: share(a.forecast.prob_up), b: share(b.forecast.prob_up) },
    { label: "Confidence", a: a.confidence.toFixed(2), b: b.confidence.toFixed(2) },
    { label: "S_final", a: signed(a.s_final), b: signed(b.s_final) },
    {
      label: "🪙 Cost",
      a: mock ? "฿0 (mock)" : thb(a.cost_usd * usdThb),
      b: mock ? "฿0 (mock)" : thb(b.cost_usd * usdThb),
      better: mock ? null : a.cost_usd < b.cost_usd ? "a" : a.cost_usd > b.cost_usd ? "b" : null,
    },
    {
      label: "⏱ Time",
      a: `${a.elapsed_s.toFixed(0)}s`,
      b: `${b.elapsed_s.toFixed(0)}s`,
      better: a.elapsed_s < b.elapsed_s ? "a" : a.elapsed_s > b.elapsed_s ? "b" : null,
    },
  ];
  const agents = Array.from(new Set([...a.reports.map((r) => r.agent), ...b.reports.map((r) => r.agent)]));
  const score = (p: Prediction, ag: string) => {
    const r = p.reports.find((x) => x.agent === ag);
    return r && !r.error ? r.score : null;
  };
  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="card p-4 sm:p-6 space-y-6"
      data-testid="head-to-head"
      aria-label="Head-to-head"
    >
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 300, damping: 14 }}
        className={`text-center font-display text-3xl sm:text-5xl ${agree ? "text-bull" : "text-gold"}`}
        role="status"
      >
        {banner}
      </motion.div>
      <table className="w-full text-sm">
        <caption className="sr-only">Model A versus model B</caption>
        <thead>
          <tr className="text-ink-dim">
            <th className="text-left font-normal py-1"> </th>
            <th className="text-right font-semibold py-1 text-[#38bdf8]">A · {shortModel(a.models?.analyst)}</th>
            <th className="text-right font-semibold py-1 text-oracle">B · {shortModel(b.models?.analyst)}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-line">
              <th scope="row" className="text-left font-normal text-ink-dim py-2">
                {r.label}
              </th>
              <td className={`text-right num py-2 ${r.better === "a" ? "text-gold" : ""}`}>{r.a}{r.better === "a" && " 🏅"}</td>
              <td className={`text-right num py-2 ${r.better === "b" ? "text-gold" : ""}`}>{r.b}{r.better === "b" && " 🏅"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        <h3 className="text-xs uppercase tracking-wider text-ink-faint mb-3">Agent scores · A vs B</h3>
        <div className="space-y-2">
          {agents.map((ag) => {
            const sa = score(a, ag);
            const sb = score(b, ag);
            const m = member(ag);
            const bar = (v: number | null, color: string) =>
              v === null ? (
                <span className="text-[11px] text-ink-faint">absent</span>
              ) : (
                <div className="relative h-2.5 flex-1 bg-surface-2 rounded-full">
                  <div className="absolute left-1/2 top-0 bottom-0 w-px bg-ink-faint" />
                  <div
                    className="absolute top-0 bottom-0 rounded-full"
                    style={{
                      background: color,
                      left: v >= 0 ? "50%" : `${50 + v * 50}%`,
                      width: `${Math.abs(v) * 50}%`,
                    }}
                  />
                </div>
              );
            return (
              <div key={ag} className="grid grid-cols-[130px_1fr_44px_1fr_44px] items-center gap-2 text-xs">
                <span className="truncate">
                  {m.emoji} {m.name}
                </span>
                {bar(sa, "#38bdf8")}
                <span className="num text-right">{sa === null ? "—" : signed(sa)}</span>
                {bar(sb, "#a78bfa")}
                <span className="num text-right">{sb === null ? "—" : signed(sb)}</span>
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-ink-faint">bars: −1 ◀ 0 ▶ +1 · A blue, B violet</p>
      </div>
    </motion.section>
  );
}
