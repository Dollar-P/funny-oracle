import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pro, type Metric, type Report, type Series } from "../api";
import { CalibrationPlot, DeltaBars, EquityChart, Histogram } from "../charts";
import { Badge, Btn, Empty, Insufficient, MetricCell, Panel, dt, num, pctf } from "../ui";

const NAMES: Record<string, string> = {
  buy_and_hold: "Buy & hold (equal weight)", cash: "Cash (always flat)", momentum_63: "Momentum (3-month)",
  sma_50_200: "SMA 50/200 cross", quant_only: "Quant-only (no LLM)",
};
const COLORS = ["#5b9cff", "#8a94a3", "#4b5360", "#c08cff", "#2fb874", "#d9a441"];

function Progress({ frac, label }: { frac: number; label: string }) {
  return (
    <div>
      <div className="flex justify-between text-[11.5px] text-desk-dim"><span>{label}</span><span className="num">{Math.round(frac * 100)} %</span></div>
      <div className="mt-1 h-1.5 rounded bg-desk-panel-2"><div className="h-1.5 rounded bg-desk-accent" style={{ width: `${frac * 100}%` }} /></div>
    </div>
  );
}

export default function Lab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["report"], queryFn: pro.latestReport });
  const st = useQuery({ queryKey: ["report", "status"], queryFn: pro.evaluationStatus, refetchInterval: (x) => (x.state.data?.busy ? 3000 : false) });
  const run = useMutation({ mutationFn: pro.runEvaluation, onSuccess: () => qc.invalidateQueries({ queryKey: ["report"] }) });
  const r: Report = q.data?.report;
  const bt = r?.backtests;
  const curves = useMemo(() => {
    if (!bt) return [];
    return Object.entries(bt.strategies as Record<string, { equity: Series }>).map(([k, v], i) => ({
      name: NAMES[k] ?? k, data: v.equity, color: COLORS[i % COLORS.length], width: k === "quant_only" ? 2 : 1, dashed: k === "buy_and_hold",
    }));
  }, [bt]);
  if (q.isLoading) return <Empty>Loading the latest report…</Empty>;
  const header = (
    <div className="flex flex-wrap items-center gap-2">
      <h1 className="text-[16px] font-semibold">Evaluation Lab</h1>
      {r && <span className="text-[12px] text-desk-dim">report #{q.data?.id} · {dt(r.created_at, true)} · experiment {r.experiment_id}</span>}
      <div className="flex-1" />
      {st.data?.busy && <Badge tone="accent">running…</Badge>}
      {st.data?.error && <Badge tone="down" title={st.data.error}>last run failed</Badge>}
      <Btn tone="accent" onClick={() => run.mutate()} disabled={run.isPending || st.data?.busy}>Run evaluation</Btn>
    </div>
  );
  if (!r) return <div className="space-y-4">{header}<Empty>No report yet — run an evaluation.</Empty></div>;
  const fq = r.forward.prediction_quality;
  const ft = r.forward.trading;
  const ab = r.forward.ablation as Record<string, { delta_brier_vs_full: number | null; brier: Metric; n: number }>;
  const rnd = bt?.random;
  return (
    <div className="space-y-4">
      {header}
      <Panel>
        <div className="grid min-w-0 gap-3 text-[12px] md:grid-cols-2 xl:grid-cols-4 [&>div]:min-w-0 [&>div]:break-words">
          <div><div className="text-desk-faint">Period (forward)</div><div className="num">{r.meta.period.from} → {r.meta.period.to}</div></div>
          <div><div className="text-desk-faint">Assets</div><div>{r.meta.assets.join(", ")}</div></div>
          <div><div className="text-desk-faint">Models · prompts</div><div>{r.meta.models.join(", ") || "—"} · {r.meta.prompt_versions.join(", ")}</div></div>
          <div><div className="text-desk-faint">Costs (bps per side)</div><div className="num">{Object.entries(r.meta.cost_assumptions_bps as Record<string, { fee: number; slippage: number }>).map(([k, v]) => `${k} ${v.fee}+${v.slippage}`).join(" · ")}</div></div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11.5px]">
          <Badge tone="up">forward: clean</Badge>
          <Badge>backtests: no LLM → no contamination</Badge>
          {(r.llm_backtests ?? []).length ? <Badge tone="warn">LLM backtests: dates on/before a model cutoff are "contaminated — not evidence"</Badge> : <Badge>no LLM backtests yet</Badge>}
        </div>
      </Panel>

      <Panel title="Verdicts">
        <ul className="space-y-1.5 text-[12.5px]">
          {r.verdicts.map((v: { topic: string; verdict: string; detail: string }, i: number) => (
            <li key={i}><span className="text-desk-dim">{v.topic}: </span>
              {v.verdict === "insufficient data" ? <Badge tone="warn">insufficient data</Badge> : <span className="font-medium">{v.verdict}</span>}
              <span className="ml-1 text-desk-dim">{v.detail}</span></li>
          ))}
        </ul>
        {r.multiple_comparisons && <p className="mt-2 text-[11.5px] text-desk-faint">{r.multiple_comparisons}</p>}
      </Panel>

      {bt && (
        <Panel title={`Strategy comparison — backtest ${bt.start} → ${String(bt.end).slice(0, 10)} (same costs, same Risk Engine)`} pad={false}>
          <div className="overflow-x-auto">
            <table className="dt">
              <thead><tr><th>Strategy</th><th>Sharpe [95 % CI] · n days</th><th>ΔSharpe vs B&H [95 % CI]</th><th className="r">Total return</th>
                <th className="r">Max DD</th><th className="r">Calmar</th><th className="r">Trades</th><th>Expectancy (R)</th><th className="r">Exposure</th><th className="r">Costs</th></tr></thead>
              <tbody>
                {Object.entries(bt.strategies as Record<string, Report>).map(([k, t]) => (
                  <tr key={k}>
                    <td className="font-medium">{NAMES[k] ?? k}</td>
                    <td><MetricCell m={t.sharpe} /></td>
                    <td>{k === "buy_and_hold" ? <span className="text-desk-faint">—</span> : <MetricCell m={t.sharpe_diff_vs_bh} showBadge={false} />}</td>
                    <td className="r num">{pctf(t.total_return, 1)}</td><td className="r num">{pctf(t.max_drawdown, 1)}</td>
                    <td className="r num">{num(t.calmar)}</td><td className="r num">{num(t.trades.value, 0)}</td>
                    <td>{k === "buy_and_hold" || k === "cash" ? <span className="text-desk-faint">—</span> : <MetricCell m={t.expectancy_r} />}</td>
                    <td className="r num">{pctf(t.exposure, 0, false)}</td><td className="r num">{num(t.total_costs, 0)}</td>
                  </tr>
                ))}
                <tr>
                  <td className="font-medium">LLM system (forward paper)</td>
                  <td><MetricCell m={ft.sharpe} /></td><td className="text-desk-faint">different period</td>
                  <td className="r num">{ft.total_return ? pctf(ft.total_return.value, 1) : "—"}</td>
                  <td className="r num">{ft.max_drawdown ? pctf(ft.max_drawdown.value, 1) : "—"}</td><td className="r num">—</td>
                  <td className="r num">{ft.n_trades}</td><td><MetricCell m={ft.expectancy_r} /></td><td className="r num">—</td>
                  <td className="r num">{num(ft.total_costs, 0)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="p-3"><EquityChart lines={curves} height={240} /></div>
        </Panel>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Forward prediction quality" className="xl:col-span-2">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2 text-[12.5px]">
              <div className="flex justify-between gap-2"><span className="text-desk-dim">Hit rate (Wilson)</span><MetricCell m={fq.hit_rate} fmt={(v) => pctf(v, 0, false)} /></div>
              <div className="flex justify-between gap-2"><span className="text-desk-dim">Brier (coin flip 0.250)</span><MetricCell m={fq.brier} fmt={(v) => num(v, 3)} /></div>
              <div className="flex justify-between gap-2"><span className="text-desk-dim">Band coverage P10–P90 (target 80 %)</span><MetricCell m={fq.band_coverage} fmt={(v) => pctf(v, 0, false)} /></div>
              <div className="flex justify-between gap-2"><span className="text-desk-dim">Log loss</span><MetricCell m={fq.log_loss} fmt={(v) => num(v, 3)} /></div>
              <Progress frac={fq.progress ?? 0} label={`towards n = ${r.meta.min_n} non-overlapping predictions (${fq.n_nonoverlap ?? 0})`} />
              <Progress frac={ft.progress ?? 0} label={`towards n = ${r.meta.min_n} closed trades (${ft.n_trades})`} />
            </div>
            <div>
              <div className="mb-1 text-[11.5px] text-desk-dim">Reliability (calibration curve)</div>
              {fq.reliability?.length ? <CalibrationPlot bins={fq.reliability} /> : <Empty>No evaluated predictions yet.</Empty>}
            </div>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="dt">
              <thead><tr><th>Bucket (mode/horizon/class)</th><th className="r">n</th><th className="r">non-overlap</th><th>Brier</th><th>Coverage</th><th></th></tr></thead>
              <tbody>
                {Object.entries(r.forward.buckets as Record<string, Report>).map(([k, b]) => (
                  <tr key={k}><td>{k}</td><td className="r num">{b.n}</td><td className="r num">{b.n_nonoverlap}</td>
                    <td><MetricCell m={b.brier} fmt={(v) => num(v, 3)} showBadge={false} /></td>
                    <td><MetricCell m={b.band_coverage} fmt={(v) => pctf(v, 0, false)} showBadge={false} /></td>
                    <td>{b.sufficient ? <Badge tone="up">ok</Badge> : <Insufficient n={b.n_nonoverlap} />}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Random-strategy distribution">
          {rnd?.n_runs ? (
            <>
              <Histogram counts={rnd.histogram} edges={rnd.bin_edges} marker={bt.strategies.quant_only.sharpe.value}
                markerLabel={rnd.quant_only_percentile !== null ? `quant-only: ${Math.round(rnd.quant_only_percentile * 100)}th pct` : ""} />
              <p className="mt-2 text-[11.5px] text-desk-dim num">{rnd.n_runs} seeded runs · Sharpe 5/50/95th pct {num(rnd.sharpe_p05)} / {num(rnd.sharpe_p50)} / {num(rnd.sharpe_p95)}</p>
              <p className="mt-1 text-[11px] text-desk-faint">The LLM system's percentile appears once its forward sample covers a comparable period.</p>
            </>
          ) : <Empty>Not computed in this report.</Empty>}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Ablation — Δ Brier vs full system (lower is better)">
          {Object.keys(ab).length ? (
            <DeltaBars fmt={(v) => (v > 0 ? "+" : "") + v.toFixed(3)} rows={Object.entries(ab).filter(([k]) => k !== "full").map(([k, v]) => ({ label: k, value: v.delta_brier_vs_full }))} />
          ) : <Empty>Needs evaluated predictions.</Empty>}
          <p className="mt-2 text-[11px] text-desk-faint">Variants are re-derived from the stored predictions (no extra LLM calls). n per variant = {ab.full?.n ?? 0}.</p>
        </Panel>
        <Panel title="Per agent — directional accuracy" pad={false}>
          <div className="overflow-x-auto"><table className="dt"><thead><tr><th>Agent</th><th>Hit rate [95 % CI] · n</th></tr></thead>
            <tbody>{Object.entries(r.forward.per_agent as Record<string, Metric>).map(([k, m]) => (
              <tr key={k}><td>{k}</td><td><MetricCell m={m} fmt={(v) => pctf(v, 0, false)} /></td></tr>))}</tbody></table></div>
          {!Object.keys(r.forward.per_agent).length && <Empty>—</Empty>}
        </Panel>
        <Panel title="Per model — does paying add value?" pad={false}>
          <div className="overflow-x-auto"><table className="dt"><thead><tr><th>Model</th><th className="r">n</th><th>Brier</th><th>Hit rate</th></tr></thead>
            <tbody>{Object.entries(r.forward.per_model as Record<string, Report>).map(([k, m]) => (
              <tr key={k}><td>{k}</td><td className="r num">{m.n}</td><td><MetricCell m={m.brier} fmt={(v) => num(v, 3)} showBadge={false} /></td>
                <td><MetricCell m={m.hit_rate} fmt={(v) => pctf(v, 0, false)} /></td></tr>))}</tbody></table></div>
          {!Object.keys(r.forward.per_model).length && <Empty>—</Empty>}
        </Panel>
      </div>

      <Panel title="LLM backtests (contamination-labelled)" pad={false}>
        <div className="overflow-x-auto"><table className="dt"><thead><tr><th>#</th><th>Model</th><th>Period</th><th>Clean / contaminated dates</th><th>Verdict</th><th>Note</th></tr></thead>
          <tbody>{(r.llm_backtests ?? []).map((b: Report) => (
            <tr key={b.id}><td>{b.id}</td><td>{b.model}</td><td className="num">{b.period}</td>
              <td className="num">{b.labels?.clean ?? 0} / {b.labels?.contaminated ?? 0}</td>
              <td>{b.verdict_eligible ? <Badge tone="up">eligible</Badge> : <Badge tone="warn">contaminated — not evidence</Badge>}</td>
              <td className="wrap text-desk-dim">{b.contamination_note}</td></tr>))}</tbody></table></div>
        {!(r.llm_backtests ?? []).length && <Empty>None yet. LLM backtests run only after a model's knowledge cutoff (config.MODEL_CUTOFFS); unknown cutoffs mean no clean dates.</Empty>}
      </Panel>
    </div>
  );
}
