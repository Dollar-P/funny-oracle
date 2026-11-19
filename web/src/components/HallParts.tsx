import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Metrics, ModelRow, Scoreboard } from "../lib/types";
import { member } from "../lib/cast";
import { countdown, dateTime, price, share, shortModel, thb } from "../lib/format";
import { displayTicker } from "../lib/ticker";
import { Link } from "react-router-dom";
import { useToasts } from "../state/settings";

export function CoinFlip({ metrics }: { metrics: Metrics }) {
  const hit = metrics.hit_rate;
  const n = metrics.n_directional ?? 0;
  const diff = hit !== undefined ? Math.round((hit - 0.5) * 100) : null;
  const caption =
    diff === null
      ? "No evaluated predictions yet — the coin is undefeated."
      : diff > 0
        ? `The AI beats a coin by ${diff} points (n = ${n}${n < 30 ? ", so… maybe luck" : ""}).`
        : diff < 0
          ? `A coin beats the AI by ${-diff} points (n = ${n}). Humbling.`
          : `Dead heat with a coin (n = ${n}). Exactly as efficient-markets people predicted.`;
  return (
    <section className="card p-5 sm:p-8" aria-labelledby="coin-title">
      <h2 id="coin-title" className="font-display text-2xl sm:text-3xl text-center">
        🤖 AI vs 🪙 Coin Flip
      </h2>
      <div className="mt-6 grid grid-cols-[1fr_auto_1fr] items-center gap-4 sm:gap-10">
        <div className="text-center">
          <div className="text-sm text-ink-dim">AI direction hit rate</div>
          <div className="num text-5xl sm:text-7xl font-bold" style={{ color: hit === undefined ? undefined : hit > 0.5 ? "var(--color-bull)" : hit < 0.5 ? "var(--color-bear)" : undefined }}>
            {hit === undefined ? "—" : share(hit)}
          </div>
          <div className="text-xs text-ink-faint">{n} directional calls</div>
        </div>
        <div className="font-display text-3xl text-ink-faint">VS</div>
        <div className="text-center">
          <div className="text-sm text-ink-dim">Coin flip</div>
          <div className="flex justify-center my-1" style={{ perspective: 600 }}>
            <motion.div
              className="w-20 h-20 sm:w-28 sm:h-28 rounded-full grid place-items-center text-3xl sm:text-5xl font-display text-stage"
              style={{ background: "radial-gradient(circle at 35% 30%, #fde68a, #facc15 45%, #b45309)", boxShadow: "0 0 30px rgb(250 204 21 / 0.4)" }}
              initial={{ rotateY: 0 }}
              animate={{ rotateY: 1800 + 180 * 0 }}
              transition={{ duration: 1.6, ease: [0.2, 0.8, 0.2, 1] }}
              aria-hidden="true"
            >
              50
            </motion.div>
          </div>
          <div className="num text-2xl text-ink-dim">50%</div>
        </div>
      </div>
      <p className="mt-6 text-center text-ink-dim" aria-live="polite">
        {caption}
      </p>
    </section>
  );
}

export function CoverageGauge({ metrics }: { metrics: Metrics }) {
  const cov = metrics.band_coverage;
  return (
    <section className="card p-5" aria-labelledby="cov-title">
      <h3 id="cov-title" className="font-display text-xl">📏 Band coverage</h3>
      <p className="text-xs text-ink-dim mt-1">Share of actual prices that landed inside P10–P90. Well-calibrated ≈ 80%.</p>
      <div className="mt-5 relative h-4 rounded-full bg-surface-2 border border-line" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={cov !== undefined ? Math.round(cov * 100) : undefined} aria-label="Band coverage">
        {cov !== undefined && (
          <motion.div
            className="h-full rounded-full"
            style={{ background: Math.abs(cov - 0.8) < 0.1 ? "var(--color-bull)" : "#f59e0b" }}
            initial={{ width: 0 }}
            animate={{ width: `${cov * 100}%` }}
            transition={{ duration: 1 }}
          />
        )}
        <div className="absolute top-[-6px] bottom-[-6px] w-0.5 bg-gold" style={{ left: "80%" }} title="80% — well calibrated" />
        <div className="absolute -top-5 text-[10px] text-gold -translate-x-1/2" style={{ left: "80%" }}>
          80% target
        </div>
      </div>
      <div className="mt-3 flex justify-between text-sm">
        <span className="num text-2xl">{cov === undefined ? "—" : share(cov)}</span>
        <span className="text-ink-dim">
          MAPE of P50 <span className="num text-ink">{metrics.mape === undefined ? "—" : `${(metrics.mape * 100).toFixed(2)}%`}</span>
        </span>
      </div>
      <p className="text-xs text-ink-faint mt-2">
        {metrics.n} evaluated · {metrics.pending} pending
        {cov !== undefined && cov < 0.65 && " · bands look too narrow (overconfident)"}
        {cov !== undefined && cov > 0.95 && " · bands look too wide (underconfident)"}
      </p>
    </section>
  );
}

export function AgentPodium({ metrics }: { metrics: Metrics }) {
  const lb = metrics.leaderboard ?? [];
  const top = lb.slice(0, 3);
  const rest = lb.slice(3);
  const order = [1, 0, 2].filter((i) => top[i]);
  const medal = ["🥇", "🥈", "🥉"];
  const heights = ["h-28", "h-20", "h-14"];
  return (
    <section className="card p-5" aria-labelledby="agents-title">
      <h3 id="agents-title" className="font-display text-xl">🏅 Agent podium</h3>
      <p className="text-xs text-ink-dim mt-1">Directional accuracy of each analyst's own score.</p>
      {lb.length === 0 ? (
        <p className="mt-6 text-sm text-ink-faint">No evaluated predictions yet.</p>
      ) : (
        <>
          <div className="mt-6 flex items-end justify-center gap-3">
            {order.map((i) => {
              const r = top[i];
              const m = member(r.agent);
              return (
                <motion.div
                  key={r.agent}
                  initial={{ y: 40, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  transition={{ delay: 0.2 * (2 - i) }}
                  className="flex flex-col items-center w-24"
                >
                  <div className="text-3xl" aria-hidden="true">{m.emoji}</div>
                  <div className="text-xs font-semibold text-center leading-tight mt-1">{m.name}</div>
                  <div className="num text-sm">{share(r.hit_rate)}</div>
                  <div className="text-[10px] text-ink-faint">n = {r.n}</div>
                  <div
                    className={`mt-1 w-full ${heights[i]} rounded-t-lg grid place-items-start justify-center pt-1 text-2xl border border-b-0`}
                    style={{ background: `color-mix(in srgb, ${m.color} 22%, var(--color-surface-2))`, borderColor: m.color }}
                  >
                    <span aria-label={`place ${i + 1}`}>{medal[i]}</span>
                  </div>
                </motion.div>
              );
            })}
          </div>
          {rest.length > 0 && (
            <ul className="mt-4 text-sm divide-y divide-line">
              {rest.map((r, i) => (
                <li key={r.agent} className="flex justify-between py-1.5">
                  <span>
                    {i + 4}. {member(r.agent).emoji} {member(r.agent).name}
                  </span>
                  <span className="num">
                    {share(r.hit_rate)} <span className="text-ink-faint">n={r.n}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

export function ModelLeaderboard({ rows }: { rows: ModelRow[] }) {
  return (
    <section className="card p-5 overflow-x-auto" aria-labelledby="models-title">
      <h3 id="models-title" className="font-display text-xl">🧠 Model leaderboard</h3>
      <table className="w-full text-sm mt-3 min-w-[560px]">
        <thead className="text-ink-dim text-xs">
          <tr>
            <th className="text-left font-normal py-1">Model</th>
            <th className="text-right font-normal">Runs</th>
            <th className="text-right font-normal">Hit rate</th>
            <th className="text-right font-normal">Coverage</th>
            <th className="text-right font-normal">Avg cost</th>
            <th className="text-right font-normal">Avg time</th>
            <th className="text-right font-normal">Duel wins</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.model} className="border-t border-line">
              <td className="py-2">{shortModel(r.model)}</td>
              <td className="text-right num">
                {r.n} <span className="text-ink-faint">({r.n_evaluated} eval)</span>
              </td>
              <td className="text-right num">{r.hit_rate === null ? "—" : share(r.hit_rate)}</td>
              <td className="text-right num">{r.band_coverage === null ? "—" : share(r.band_coverage)}</td>
              <td className="text-right num">{thb(r.avg_cost_thb)}</td>
              <td className="text-right num">{r.avg_time_s.toFixed(0)}s</td>
              <td className="text-right num">{r.duels ? `${r.duel_wins}/${r.duels}` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-ink-faint mt-2">Duel win = P50 closer to the actual price than the rival model.</p>
    </section>
  );
}

export function Pending({ rows }: { rows: Scoreboard["pending"] }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <section className="card p-5" aria-labelledby="pending-title">
      <h3 id="pending-title" className="font-display text-xl">⏳ Waiting for the market</h3>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-ink-faint">Nothing pending.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line text-sm max-h-72 overflow-y-auto">
          {rows.map((r) => {
            const left = Date.parse(r.target_time) - now;
            return (
              <li key={r.id} className="flex items-center gap-3 py-2">
                <Link to={`/p/${r.id}`} className="num text-ink-dim hover:text-ink">
                  #{r.id}
                </Link>
                <span className="font-semibold w-16">{displayTicker(r.ticker)}</span>
                <span className="text-ink-dim">
                  {r.mode} {r.horizon}
                </span>
                <span className="num text-xs text-ink-faint hidden sm:inline">P50 {price(r.p50)}</span>
                <span className="flex-1" />
                <span className={`num ${left <= 0 ? "text-gold" : ""}`} role="timer">
                  {left <= 0 ? "due — evaluating" : countdown(left)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function History() {
  const [ticker, setTicker] = useState("");
  const [mode, setMode] = useState("");
  const [model, setModel] = useState("");
  const { data } = useQuery({
    queryKey: ["history-list", ticker, mode, model],
    queryFn: () => api.predictions({ limit: 200, ticker: ticker || undefined, mode: mode || undefined, model: model || undefined }),
  });
  const all = useQuery({ queryKey: ["history-all"], queryFn: () => api.predictions({ limit: 500 }) });
  const tickers = Array.from(new Set((all.data?.predictions ?? []).map((r) => r.ticker))).sort();
  const models = Array.from(new Set((all.data?.predictions ?? []).map((r) => r.analyst_model).filter(Boolean) as string[])).sort();
  const rows = data?.predictions ?? [];
  const sel = "card h-9 px-2 bg-surface text-sm";
  return (
    <section className="card p-5 overflow-x-auto" aria-labelledby="hist-title">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="hist-title" className="font-display text-xl mr-auto">📼 History</h3>
        <label className="sr-only" htmlFor="f-ticker">Ticker</label>
        <select id="f-ticker" className={sel} value={ticker} onChange={(e) => setTicker(e.target.value)}>
          <option value="">All tickers</option>
          {tickers.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <label className="sr-only" htmlFor="f-mode">Mode</label>
        <select id="f-mode" className={sel} value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="">All modes</option>
          <option value="swing">swing</option>
          <option value="scalp">scalp</option>
        </select>
        <label className="sr-only" htmlFor="f-model">Model</label>
        <select id="f-model" className={sel} value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">All models</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {shortModel(m)}
            </option>
          ))}
        </select>
      </div>
      <table className="w-full text-sm mt-3 min-w-[760px]">
        <thead className="text-xs text-ink-dim">
          <tr>
            <th className="text-left font-normal py-1">#</th>
            <th className="text-left font-normal">When</th>
            <th className="text-left font-normal">Ticker</th>
            <th className="text-left font-normal">Mode</th>
            <th className="text-left font-normal">Model</th>
            <th className="text-right font-normal">Now</th>
            <th className="text-right font-normal">P50</th>
            <th className="text-right font-normal">P(up)</th>
            <th className="text-right font-normal">Actual</th>
            <th className="text-center font-normal">Result</th>
            <th className="text-right font-normal">Cost</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const evaluated = r.actual_price !== null;
            const hit = evaluated && r.prob_up !== 0.5 ? (r.prob_up > 0.5) === (r.actual_price! > r.price_now) : null;
            return (
              <tr key={r.id} className="border-t border-line">
                <td className="py-2 num text-ink-dim">{r.id}</td>
                <td className="text-ink-dim">{dateTime(r.created_at)}</td>
                <td className="font-semibold">{displayTicker(r.ticker)}</td>
                <td className="text-ink-dim">
                  {r.mode} {r.horizon}
                  {r.duel_id && <span title={`duel ${r.duel_id}`}> ⚔️</span>}
                </td>
                <td className="text-ink-dim">{shortModel(r.analyst_model)}</td>
                <td className="text-right num">{price(r.price_now)}</td>
                <td className="text-right num">{price(r.p50)}</td>
                <td className="text-right num">{share(r.prob_up)}</td>
                <td className="text-right num">{evaluated ? price(r.actual_price) : "—"}</td>
                <td className="text-center">{hit === null ? (evaluated ? "–" : "⏳") : hit ? "✅" : "❌"}</td>
                <td className="text-right num text-ink-dim">${r.cost_usd.toFixed(3)}</td>
                <td className="text-right">
                  <Link to={`/p/${r.id}`} className="text-wizard hover:underline">
                    replay ▶
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && <p className="text-sm text-ink-faint mt-3">No predictions match.</p>}
    </section>
  );
}

export function AutoScalpCard() {
  const qc = useQueryClient();
  const push = useToasts((t) => t.push);
  const { data } = useQuery({ queryKey: ["autoscalp"], queryFn: api.autoscalpStatus, refetchInterval: 5000 });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  void tick;
  const mut = useMutation({
    mutationFn: (start: boolean) => (start ? api.autoscalpStart() : api.autoscalpStop()),
    onSuccess: (d) => qc.setQueryData(["autoscalp"], d),
    onError: (e) => push("error", e instanceof ApiError ? e.message : String(e)),
  });
  if (!data) return null;
  return (
    <section className="card p-5" aria-labelledby="as-title">
      <div className="flex items-center gap-3">
        <h3 id="as-title" className="font-display text-xl">🤖 Auto-Scalp</h3>
        <span className={`text-xs px-2 py-0.5 rounded-full ${data.running ? "bg-bull/20 text-bull" : "bg-surface-2 text-ink-dim"}`}>
          {data.running ? "● running" : "○ stopped"}
        </span>
        <span className="flex-1" />
        <button
          onClick={() => mut.mutate(!data.running)}
          disabled={mut.isPending || (data.mock_blocked && !data.running)}
          className={`px-4 h-9 rounded-full text-sm font-semibold ${data.running ? "bg-bear/80 text-white" : "bg-bull text-stage"} disabled:opacity-40`}
        >
          {data.running ? "■ Stop" : "▶ Start"}
        </button>
      </div>
      <p className="text-sm text-ink-dim mt-2">
        Predicts {displayTicker(data.ticker)} {data.horizon} every {Math.round(data.interval_s / 60)} min so the scoreboard fills with real results.
      </p>
      <div className="mt-3 grid grid-cols-3 gap-3 text-center">
        <div className="card p-2">
          <div className="num text-xl">{data.runs}</div>
          <div className="text-[11px] text-ink-faint">runs</div>
        </div>
        <div className="card p-2">
          <div className="num text-xl">{data.runs_left}</div>
          <div className="text-[11px] text-ink-faint">left under cap</div>
        </div>
        <div className="card p-2">
          <div className="num text-xl">{data.running && data.seconds_to_next !== null ? countdown(data.seconds_to_next * 1000) : "—"}</div>
          <div className="text-[11px] text-ink-faint">next run</div>
        </div>
      </div>
      {data.mock_blocked && <p className="text-xs text-gold mt-2">Disabled in mock mode — it makes real (paid) predictions.</p>}
      {data.stopped_reason && <p className="text-xs text-bear mt-2">Stopped: {data.stopped_reason}</p>}
      {data.log.length > 0 && (
        <ul className="mt-3 num text-[11px] text-ink-dim space-y-0.5 max-h-28 overflow-y-auto">
          {data.log.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
