import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pro } from "../api";
import { TradeChart } from "../charts";
import { Badge, Btn, Dir, Empty, Panel, Status, dt, num, pctf, px, rr } from "../ui";
import type { AssetType } from "../../lib/types";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-desk-line/60 py-1 text-[12.5px] last:border-0">
      <span className="text-desk-dim">{k}</span>
      <span className="num text-right">{v}</span>
    </div>
  );
}

export default function SignalDetail() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["signals", "detail", id], queryFn: () => pro.signal(id) });
  const s = q.data?.signal;
  const bars = useQuery({ queryKey: ["bars", s?.ticker], queryFn: () => pro.bars(s!.ticker, s!.asset_type), enabled: !!s });
  const act = useMutation({
    mutationFn: (ok: boolean) => (ok ? pro.approve(id) : pro.reject(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signals"] }),
  });
  if (q.isError) return <Empty>Signal not found.</Empty>;
  if (!q.data || !s) return <Empty>Loading…</Empty>;
  const p = q.data.prediction;
  const inp = s.inputs;
  const targets = s.targets ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/pro/signals" className="text-desk-dim hover:text-desk-ink">← Signals</Link>
        <h1 className="text-[16px] font-semibold">{s.ticker}</h1>
        <span className="text-desk-dim">{s.mode} · {s.horizon}</span>
        {s.direction === "flat" ? <Badge>FLAT · {s.flat_reason}</Badge> : <Dir side={s.direction} />}
        <Status s={s.status} reason={s.status_reason} />
        <span className="text-[11px] text-desk-faint">#{s.id} · {dt(s.created_at, true)}</span>
        <div className="flex-1" />
        {s.status === "new" && (
          <>
            <Btn tone="accent" onClick={() => act.mutate(true)} disabled={act.isPending}>Approve</Btn>
            <Btn tone="danger" onClick={() => act.mutate(false)} disabled={act.isPending}>Reject</Btn>
          </>
        )}
        {q.data.replay_url && <Link to={q.data.replay_url} className="text-[12px] text-desk-accent hover:underline">Arena replay →</Link>}
      </div>
      {act.isError && <div role="alert" className="panel px-3 py-2 text-[12px] text-desk-down">{(act.error as Error).message}</div>}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Panel title="Chart — entry, stop, targets, time exit, P10–P90" pad={false}>
          <div className="p-2">
            {bars.data?.candles.length ? (
              <TradeChart candles={bars.data.candles} entry={s.entry?.ref_price ?? null} stop={s.stop} targets={targets}
                side={s.side} createdAt={s.created_at} timeExit={s.time_exit} fan={p?.forecast.fan} asset={s.asset_type as AssetType}
                fills={q.data.fills.map((f) => ({ ts: f.ts, reason: f.reason, price: f.price, side: f.side }))}
                band={p ? { price: p.forecast.price_now, p10: p.forecast.p10, p50: p.forecast.p50, p90: p.forecast.p90 } : null} />
            ) : <Empty>No bars.</Empty>}
          </div>
        </Panel>
        <Panel title="Plan">
          <Row k="Entry (market, next open)" v={px(s.entry?.ref_price)} />
          <Row k="Stop" v={px(s.stop)} />
          <Row k="T1 (P50) · T2" v={`${px(targets[0])} · ${px(targets[1])}`} />
          <Row k="Time exit" v={dt(s.time_exit)} />
          <Row k="R:R (to T2)" v={num(s.reward_risk)} />
          <Row k="EV after costs" v={rr(s.ev_r)} />
          <Row k="P(win)" v={<>{pctf(s.p_win, 0, false)} {s.calibrated ? <Badge tone="up">calibrated</Badge> : <Badge>uncalibrated</Badge>}</>} />
          <Row k="Conviction |S|·conf" v={num(s.conviction)} />
          <Row k="Costs" v={rr(-s.cost_r, 3)} />
          {s.entry_deadline && <Row k="Approve before" v={dt(s.entry_deadline)} />}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Thesis" className="lg:col-span-2">
          <p className="text-[13px] leading-relaxed">{p?.thesis ?? s.thesis ?? "—"}</p>
          <div className="mt-2 text-[12.5px]"><span className="text-desk-dim">Invalidation: </span>{p?.invalidation ?? s.invalidation ?? "—"}</div>
          {p?.catalysts?.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {p.catalysts.map((c, i) => <Badge key={i} tone={c.impact === "high" ? "warn" : "neutral"}>{c.date} · {c.event} · {c.impact}</Badge>)}
            </div>
          ) : <div className="mt-2 text-[12px] text-desk-faint">No dated catalysts inside the horizon.</div>}
          {p?.no_trade_reason && <div className="mt-2 text-[12px] text-desk-warn">Judge: no trade — {p.no_trade_reason}</div>}
          {s.fails.length > 0 && <div className="mt-2 text-[12px] text-desk-dim">FLAT rules failed: {s.fails.join(", ")}{s.flat_detail ? ` — ${s.flat_detail}` : ""}</div>}
        </Panel>
        <Panel title="Risk calculation">
          <Row k="ATR × k" v={`${num(Number(inp.atr), 2)} × ${num(Number(inp.k_atr), 1)}`} />
          <Row k="ATR stop · band stop" v={`${px(Number(inp.atr_stop))} · ${inp.band_stop === null ? "—" : px(Number(inp.band_stop))}`} />
          <Row k="R1 · R2" v={`${num(Number(inp.r1))} · ${num(Number(inp.r2))}`} />
          <Row k="σ annual" v={pctf(Number(inp.sigma_annual), 1, false)} />
          {s.size ? (
            <>
              <Row k="Quantity" v={num(s.size.qty, s.size.qty < 1 ? 4 : 0)} />
              <Row k="Notional" v={`$${px(s.size.notional)}`} />
              <Row k="Risk" v={`$${px(s.size.risk_amount)} (${pctf(s.size.risk_pct, 2, false)})`} />
              <Row k="Capped by" v={s.size.capped_by.length ? s.size.capped_by.join(", ") : "risk per trade"} />
            </>
          ) : <Row k="Size" v="—" />}
          {q.data.risk_events.map((e, i) => <div key={i} className="mt-1 text-[11.5px] text-desk-warn">{e.rule}: {e.detail}</div>)}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Agents" pad={false} className="lg:col-span-2">
          <div className="overflow-x-auto">
            <table className="dt">
              <thead><tr><th>Agent</th><th className="r">Weight</th><th className="r">Score</th><th className="r">Conf.</th><th>View</th></tr></thead>
              <tbody>
                {(p?.agents ?? []).map((a) => (
                  <tr key={a.agent}>
                    <td>{a.agent}</td><td className="r num">{num(a.weight)}</td>
                    <td className="r num">{a.ok ? <span className={a.score > 0 ? "text-desk-up" : a.score < 0 ? "text-desk-down" : ""}>{a.score > 0 ? "▲" : a.score < 0 ? "▼" : ""} {num(a.score)}</span> : <Badge tone="warn">absent</Badge>}</td>
                    <td className="r num">{num(a.confidence)}</td>
                    <td className="wrap text-desk-dim">{a.ok ? a.view : a.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!p && <Empty>No stored prediction.</Empty>}
          </div>
          {p && <div className="border-t border-desk-line px-3 py-2 text-[11.5px] text-desk-dim num">S_raw {num(p.s_raw)} → S_final {num(p.s_final)} · confidence {num(p.confidence)} · P(up) {pctf(p.forecast.prob_up, 0, false)}</div>}
        </Panel>
        <Panel title="Provenance">
          {p ? (
            <>
              <Row k="Model" v={`${p.models.analyst ?? "—"}${p.think_preset ? ` · ${p.think_preset}` : ""}`} />
              <Row k="Prompt version" v={p.prompt_version} />
              <Row k="Run time" v={`${num(p.elapsed_s, 0)} s`} />
              <Row k="Cost (tracked / est. actual)" v={`฿${num(p.cost_thb)} / ฿${num(p.cost_actual_thb)}`} />
              <Row k="Data quality" v={p.degraded.length ? <Badge tone="warn">{p.degraded.join(", ")}</Badge> : "ok"} />
              <div className="mt-2 text-[11.5px] text-desk-dim">Data snapshots ({q.data.snapshots?.length ?? 0})</div>
              <ul className="mt-1 space-y-0.5 text-[11.5px]">
                {(q.data.snapshots ?? []).slice(0, 12).map((x) => (
                  <li key={x.id}><a className="text-desk-accent hover:underline" href={`/api/pro/snapshots/${x.id}`} target="_blank" rel="noreferrer">{x.tool}</a> <span className="text-desk-faint">{dt(x.as_of)}</span></li>
                ))}
              </ul>
            </>
          ) : <Empty>—</Empty>}
          {q.data.position?.review && (
            <div className="mt-3 border-t border-desk-line pt-2 text-[12px]">
              <div className="text-desk-dim">Reviewer post-mortem</div>
              <p className="mt-1">{q.data.position.review.post_mortem}</p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
