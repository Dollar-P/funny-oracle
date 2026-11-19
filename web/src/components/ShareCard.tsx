import { forwardRef } from "react";
import type { Prediction } from "../lib/types";
import { member, DISCLAIMER } from "../lib/cast";
import { dateTime, pct, price, share, shortModel, signed } from "../lib/format";
import { displayTicker } from "../lib/ticker";

const W = 1080;
const H = 1350;

function MiniFan({ p }: { p: Prediction }) {
  const f = p.forecast.fan;
  const w = 900;
  const h = 200;
  const lo = Math.min(...f.p10);
  const hi = Math.max(...f.p90);
  const span = hi - lo || 1;
  const n = f.step.length;
  const x = (i: number) => (i / Math.max(1, n - 1)) * w;
  const y = (v: number) => h - 20 - ((v - lo) / span) * (h - 40);
  const band = [...f.p90.map((v, i) => `${x(i)},${y(v)}`), ...f.p10.map((_v, i) => `${x(n - 1 - i)},${y(f.p10[n - 1 - i])}`)].join(" ");
  const mid = f.p50.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <line x1={0} x2={w} y1={y(p.forecast.price_now)} y2={y(p.forecast.price_now)} stroke="#6c6c88" strokeDasharray="6 6" />
      <polygon points={band} fill="rgba(167,139,250,0.28)" stroke="rgba(167,139,250,0.8)" strokeWidth="2" />
      <polyline points={mid} fill="none" stroke="#facc15" strokeWidth="4" strokeDasharray="12 8" />
      <circle cx={0} cy={y(p.forecast.price_now)} r="8" fill="#ececf4" />
    </svg>
  );
}

/** 1080×1350 card rendered offscreen and captured with html-to-image. Inline styles only (robust capture). */
const ShareCard = forwardRef<HTMLDivElement, { p: Prediction }>(function ShareCard({ p }, ref) {
  const f = p.forecast;
  const dirColor = p.s_final > 0.1 ? "#22c55e" : p.s_final < -0.1 ? "#ef4444" : "#a1a1aa";
  const prize = [
    { icon: "🐻", label: "Bear P10", v: f.p10, c: "#ef4444" },
    { icon: "🎯", label: "Base P50", v: f.p50, c: "#facc15" },
    { icon: "🐂", label: "Bull P90", v: f.p90, c: "#22c55e" },
  ];
  return (
    <div
      ref={ref}
      style={{
        width: W,
        height: H,
        padding: 64,
        boxSizing: "border-box",
        color: "#ececf4",
        fontFamily: "Inter, system-ui, sans-serif",
        background:
          "radial-gradient(900px 600px at 10% -10%, rgba(109,40,217,0.45), transparent 60%), radial-gradient(800px 500px at 100% 0%, rgba(219,39,119,0.3), transparent 60%), #0b0b1e",
        display: "flex",
        flexDirection: "column",
        gap: 24,
        position: "relative",
        overflow: "hidden",
        paddingBottom: 190, // room for the pinned footer (model line + disclaimer)
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontSize: 56 }}>🔮</span>
        <span style={{ fontFamily: "'Lilita One', sans-serif", fontSize: 52, color: "#facc15", letterSpacing: 2 }}>FUNNY ORACLE</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 24, color: "#a1a1b8" }}>{dateTime(p.created_at)}</span>
      </div>
      <div>
        <div style={{ fontFamily: "'Lilita One', sans-serif", fontSize: 88, lineHeight: 1, color: "#ffffff" }}>
          {displayTicker(p.ticker)} <span style={{ fontSize: 40, color: "#a1a1b8" }}>{p.asset_type}</span>
        </div>
        <div style={{ fontSize: 30, color: "#a1a1b8", marginTop: 12 }}>
          {p.mode === "swing" ? "🐢 Swing" : "⚡ Scalp"} · horizon {p.horizon} · now {price(f.price_now)}
        </div>
      </div>
      <div
        style={{
          fontFamily: "'Lilita One', sans-serif",
          fontSize: 42,
          lineHeight: 1.15,
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          color: "#facc15",
          borderLeft: "8px solid #a78bfa",
          paddingLeft: 28,
        }}
      >
        “{p.verdict?.headline ?? "The Oracle abstains."}”
      </div>
      <div style={{ display: "flex", gap: 20 }}>
        {prize.map((x) => (
          <div key={x.label} style={{ flex: 1, background: "#15152e", border: `2px solid ${x.c}66`, borderRadius: 24, padding: "22px 20px", textAlign: "center" }}>
            <div style={{ fontSize: 26, color: "#a1a1b8" }}>
              {x.icon} {x.label}
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 44, fontWeight: 600, marginTop: 6 }}>{price(x.v)}</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 24, color: x.v >= f.price_now ? "#22c55e" : "#ef4444" }}>
              {pct((x.v / f.price_now - 1) * 100)}
            </div>
          </div>
        ))}
        <div style={{ flex: 1, background: "#15152e", border: "2px solid #2a2a4a", borderRadius: 24, padding: "22px 20px", textAlign: "center" }}>
          <div style={{ fontSize: 26, color: "#a1a1b8" }}>P(up)</div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 64, fontWeight: 700, color: dirColor }}>{share(f.prob_up)}</div>
        </div>
      </div>
      <div style={{ background: "#15152e", border: "2px solid #2a2a4a", borderRadius: 24, padding: "16px 36px", display: "flex", justifyContent: "center" }}>
        <MiniFan p={p} />
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {p.reports.map((r) => {
          const m = member(r.agent);
          const c = r.error ? "#6c6c88" : r.score > 0.1 ? "#22c55e" : r.score < -0.1 ? "#ef4444" : "#a1a1aa";
          return (
            <div key={r.agent} style={{ display: "flex", alignItems: "center", gap: 10, background: "#1c1c3a", border: `2px solid ${c}`, borderRadius: 999, padding: "8px 16px", fontSize: 21 }}>
              <span>{m.emoji}</span>
              <span>{m.name}</span>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", color: c, fontWeight: 700 }}>{r.error ? "absent" : signed(r.score)}</span>
            </div>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 64, right: 64, bottom: 56, display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", alignItems: "center", fontSize: 24, color: "#a1a1b8" }}>
        <span>🧠 {shortModel(p.models?.analyst)}{p.models?.judge && p.models.judge !== p.models.analyst ? ` · 🔮 ${shortModel(p.models.judge)}` : ""}</span>
        <span style={{ flex: 1 }} />
        <span>S_final {signed(p.s_final)} · conf {p.confidence.toFixed(2)}</span>
      </div>
      <div style={{ textAlign: "center", fontSize: 30, fontWeight: 700, color: "#facc15", border: "2px solid #facc1588", borderRadius: 999, padding: "14px 0" }}>
        ⚠ {DISCLAIMER}
      </div>
      </div>
    </div>
  );
});

export default ShareCard;
export { W as SHARE_W, H as SHARE_H };
