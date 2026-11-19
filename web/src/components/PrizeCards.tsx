import { motion } from "framer-motion";
import { pct, price, share } from "../lib/format";

export default function PrizeCards({
  now,
  p10,
  p50,
  p90,
  probUp,
  compact = false,
}: {
  now: number;
  p10: number;
  p50: number;
  p90: number;
  probUp: number;
  compact?: boolean;
}) {
  const cards = [
    { icon: "🐻", label: "Bear", sub: "P10", v: p10, color: "var(--color-bear)" },
    { icon: "🎯", label: "Base", sub: "P50", v: p50, color: "var(--color-gold)" },
    { icon: "🐂", label: "Bull", sub: "P90", v: p90, color: "var(--color-bull)" },
  ];
  const upColor = probUp > 0.52 ? "var(--color-bull)" : probUp < 0.48 ? "var(--color-bear)" : "var(--color-neutral)";
  return (
    <div className={`grid ${compact ? "grid-cols-2 gap-2" : "grid-cols-2 lg:grid-cols-4 gap-3"}`}>
      {cards.map((c, i) => (
        <motion.div
          key={c.sub}
          initial={{ opacity: 0, y: 20, rotateX: -60 }}
          animate={{ opacity: 1, y: 0, rotateX: 0 }}
          transition={{ delay: 0.15 * i, type: "spring", stiffness: 260, damping: 20 }}
          className={`card ${compact ? "p-2" : "p-4"} text-center`}
          style={{ borderColor: `color-mix(in srgb, ${c.color} 45%, transparent)` }}
        >
          <div className={compact ? "text-xs" : "text-sm"}>
            <span aria-hidden="true">{c.icon} </span>
            <b>{c.label}</b> <span className="text-ink-faint">{c.sub}</span>
          </div>
          <div className={`num font-semibold ${compact ? "text-base" : "text-2xl"} mt-1`}>{price(c.v)}</div>
          <div className="num text-xs mt-0.5" style={{ color: c.v >= now ? "var(--color-bull)" : "var(--color-bear)" }}>
            {c.v >= now ? "▲" : "▼"} {pct((c.v / now - 1) * 100, 1)} vs now
          </div>
        </motion.div>
      ))}
      <motion.div
        initial={{ opacity: 0, scale: 0.6 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 0.5, type: "spring", stiffness: 300, damping: 14 }}
        className={`card ${compact ? "p-2" : "p-4"} text-center`}
      >
        <div className={compact ? "text-xs" : "text-sm"}>
          <b>P(up)</b> <span className="text-ink-faint">vs now {price(now)}</span>
        </div>
        <div className={`num font-bold ${compact ? "text-2xl" : "text-4xl"}`} style={{ color: upColor }}>
          {share(probUp)}
        </div>
        <div className="text-[11px] text-ink-faint">coin flip = 50%</div>
      </motion.div>
    </div>
  );
}
