import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { pro } from "../api";
import { EquityChart } from "../charts";
import { Badge, Btn, Empty, Kpi, MetricCell, Panel, Pnl, dt, money, num, pctf } from "../ui";

export default function Overview() {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["overview"], queryFn: pro.overview });
  const eq = useQuery({ queryKey: ["equity"], queryFn: pro.equity });
  const sch = useQuery({ queryKey: ["scheduler"], queryFn: pro.scheduler, refetchInterval: 30_000 });
  const toggle = useMutation({
    mutationFn: () => (sch.data?.running ? pro.schedulerStop() : pro.schedulerStart()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scheduler"] }),
  });
  const lines = useMemo(() => {
    const d = eq.data;
    if (!d) return [];
    return [
      { name: "Desk", data: d.equity },
      { name: "Buy & hold", data: d.benchmarks.buy_and_hold, color: "#8a94a3", dashed: true },
      { name: "Cash", data: d.benchmarks.cash, color: "#4b5360" },
    ];
  }, [eq.data]);
  const o = ov.data;
  if (!o) return <Empty>Loading the paper account…</Empty>;
  const ddLimit = o.risk_limits.max_dd;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Equity (USD, paper)" value={money(o.equity)} sub={<>start {money(o.starting_equity, 0)} · <span className="num">{pctf(o.total_return)}</span></>} />
        <Kpi label="Day P&L" value={<Pnl v={o.day_pnl} />} sub={<span className="num">{pctf(o.day_pnl_pct)}</span>} />
        <Kpi label="Open risk" value={<span className="num">{pctf(o.open_risk_pct, 2, false)}</span>} sub={`${o.open_positions} open · ${o.pending_entries} pending`} />
        <Kpi label="Drawdown" value={<span className="num">{pctf(o.drawdown)}</span>} sub={`halt at ${pctf(ddLimit, 0)}`} />
        <Kpi label="Sharpe (daily, annualised)" value={<MetricCell m={o.sharpe} showBadge={false} />}
          sub={o.sharpe.sufficient ? "block-bootstrap 95 % CI" : <Badge tone="warn">insufficient data</Badge>} />
        <Kpi label="Pro LLM spend today" value={<span className="num">฿{num(o.spent_today_thb)}</span>}
          sub={<>
            <div>of ฿{num(o.daily_budget_thb, 0)} daily · Pro total ฿{num(o.pro_spent_total_thb)}</div>
            <div title="The real budget cap counts Arena and Pro together; Arena stats never include Pro spend">
              shared cap (Arena + Pro) ฿{num(o.combined_spent_thb)} / ฿{num(o.combined_budget_thb, 0)}</div>
          </>} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Panel title="Equity vs buy & hold basket and cash" actions={<span className="text-[11px] text-desk-faint">hourly marks, local time</span>}>
          {lines.length && lines[0].data.length ? <EquityChart lines={lines} /> : <Empty>No equity history yet.</Empty>}
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-desk-dim">
            <span><span className="mr-1 inline-block h-0.5 w-4 align-middle bg-desk-accent" />Desk</span>
            <span><span className="mr-1 inline-block h-0.5 w-4 align-middle border-t border-dashed border-[#8a94a3]" />Buy & hold (equal weight, watchlist)</span>
            <span><span className="mr-1 inline-block h-0.5 w-4 align-middle bg-[#4b5360]" />Cash</span>
          </div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Circuit breakers">
            <ul className="space-y-1.5 text-[12.5px]">
              <li className="flex justify-between"><span className="text-desk-dim">Max drawdown ({pctf(ddLimit, 0)})</span>
                {o.halted ? <Badge tone="down">tripped — halted</Badge> : <Badge tone="up">armed</Badge>}</li>
              <li className="flex justify-between"><span className="text-desk-dim">Daily loss ({pctf(o.risk_limits.daily_loss, 0)})</span>
                {o.daily_loss_tripped_today ? <Badge tone="down">tripped today</Badge> : <Badge tone="up">armed</Badge>}</li>
            </ul>
            {o.breaker_events.length > 0 && (
              <div className="mt-2 border-t border-desk-line pt-2 text-[11px] text-desk-faint">
                Last: {o.breaker_events[0].rule} · {dt(o.breaker_events[0].ts)}
              </div>
            )}
          </Panel>
          <Panel title="Scheduler" actions={<Btn onClick={() => toggle.mutate()} tone={sch.data?.running ? "danger" : "accent"} disabled={toggle.isPending}>{sch.data?.running ? "Stop" : "Start"}</Btn>}>
            {sch.data ? (
              <ul className="space-y-1 text-[12.5px]">
                <li className="flex justify-between"><span className="text-desk-dim">Status</span>{sch.data.running ? <Badge tone="up">running</Badge> : <Badge>stopped</Badge>}</li>
                <li className="flex justify-between"><span className="text-desk-dim">Last tick</span><span className="num">{dt(sch.data.last_tick)}</span></li>
                <li className="flex justify-between"><span className="text-desk-dim">Local window</span>
                  <span>{sch.data.local_window} {sch.data.in_local_window ? <Badge tone="up">open</Badge> : <Badge>closed</Badge>}</span></li>
                <li className="flex justify-between"><span className="text-desk-dim">LM Studio</span>
                  {sch.data.local_available ? <Badge tone="up">reachable</Badge> : <Badge tone="warn">unreachable → Gemini fallback</Badge>}</li>
                <li className="flex justify-between"><span className="text-desk-dim">Approval</span><span>{sch.data.approval}</span></li>
                {sch.data.last_error && <li className="text-desk-down text-[11px]">{sch.data.last_error}</li>}
              </ul>
            ) : <Empty>…</Empty>}
          </Panel>
          <Panel title="Signals">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(o.signal_counts).sort().map(([k, v]) => (
                <Link key={k} to={`/pro/signals?status=${k}`}><Badge tone={k === "new" ? "accent" : "neutral"}>{k} <span className="num">{v}</span></Badge></Link>
              ))}
            </div>
            {(o.signal_counts.new ?? 0) > 0 && (
              <Link to="/pro/signals?status=new" className="mt-2 block text-[12px] text-desk-accent hover:underline">
                {o.signal_counts.new} signal(s) waiting for approval →
              </Link>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="Recent scheduler jobs" pad={false}>
        <div className="overflow-x-auto">
          <table className="dt">
            <thead><tr><th>Time</th><th>Job</th><th>Status</th><th>Model</th><th>Routing / reason</th></tr></thead>
            <tbody>
              {(sch.data?.recent ?? []).slice(0, 12).map((r) => (
                <tr key={r.job_key}>
                  <td className="num">{dt(r.created_at)}</td>
                  <td className="num">{r.job_key}</td>
                  <td><Badge tone={r.status === "done" ? "up" : r.status === "failed" ? "down" : r.status === "skipped" ? "warn" : "neutral"}>{r.status}</Badge></td>
                  <td>{r.model ?? "—"}{r.think_preset ? ` · ${r.think_preset}` : ""}</td>
                  <td className="wrap text-desk-dim">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!sch.data?.recent?.length && <Empty>No jobs yet.</Empty>}
        </div>
      </Panel>
    </div>
  );
}
