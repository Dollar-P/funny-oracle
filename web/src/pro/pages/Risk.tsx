import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { pro, type Report } from "../api";
import { UsageBar } from "../charts";
import { Badge, Empty, Panel, dt, num, pctf } from "../ui";

const LABEL: Record<string, string> = {
  gross_exposure: "Gross exposure", open_positions: "Open positions", open_risk: "Open risk (to stops)", drawdown: "Drawdown from peak",
  max_position: "Largest position",
};

function corrColor(v: number | null) {
  if (v === null) return "transparent";
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(91,156,255,${0.12 + 0.55 * a})` : `rgba(217,164,65,${0.12 + 0.55 * a})`;
}

export default function Risk() {
  const q = useQuery({ queryKey: ["risk"], queryFn: pro.risk, refetchInterval: 60_000 });
  const r: Report = q.data;
  if (!r) return <Empty>Loading…</Empty>;
  const tick = r.correlation.tickers as string[];
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Limit usage" actions={r.halted ? <Badge tone="down">halted — reset in Settings</Badge> : <Badge tone="up">trading allowed</Badge>}>
          <div className="space-y-3">
            {(r.limits as { rule: string; used: number; limit: number | null; unit?: string }[]).map((l) => (
              <UsageBar key={l.rule} label={LABEL[l.rule] ?? l.rule.replace("class:", "Asset class: ")} used={l.used} limit={l.limit}
                fmt={(v) => (l.unit === "count" ? num(v, 0) : pctf(v, 1, false))} />
            ))}
          </div>
          <p className="mt-3 text-[11px] text-desk-faint">
            Per trade: {pctf(r.params.risk_pct_swing, 2, false)} of equity (swing), {pctf(r.params.risk_pct_scalp, 2, false)} (scalp); vol target {pctf(r.params.vol_target, 0, false)};
            daily loss stop {pctf(r.params.daily_loss, 0)}; correlation guard ρ &gt; {r.params.corr_max} with combined risk &gt; {pctf(r.params.corr_combined_risk, 0, false)}.
          </p>
        </Panel>
        <Panel title="Exposure by asset class and direction" pad={false}>
          <table className="dt">
            <thead><tr><th>Class</th><th className="r">▲ Long</th><th className="r">▼ Short</th><th className="r">Net</th></tr></thead>
            <tbody>
              {Object.entries(r.exposure as Record<string, { long: number; short: number }>).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="r num">{pctf(v.long, 1, false)}</td><td className="r num">{pctf(v.short, 1, false)}</td>
                  <td className="r num">{pctf(v.long - v.short, 1)}</td></tr>
              ))}
            </tbody>
          </table>
          {!Object.keys(r.exposure).length && <Empty>Flat — no open positions.</Empty>}
        </Panel>
      </div>
      <Panel title="Correlation of open positions (60-day daily returns)">
        {tick.length > 1 ? (
          <div className="overflow-x-auto">
            <table className="text-[12px] num">
              <thead><tr><th></th>{tick.map((t) => <th key={t} className="px-2 py-1 font-normal text-desk-dim">{t}</th>)}</tr></thead>
              <tbody>
                {tick.map((a, i) => (
                  <tr key={a}>
                    <th className="px-2 py-1 text-left font-normal text-desk-dim">{a}</th>
                    {(r.correlation.matrix[i] as (number | null)[]).map((v, j) => (
                      <td key={j} className="px-2 py-1 text-center" style={{ background: corrColor(v) }}>{v === null ? "—" : num(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>Needs at least two open positions.</Empty>}
      </Panel>
      <Panel title="Rejected-signal log" pad={false}>
        <div className="max-h-[460px] overflow-auto">
          <table className="dt">
            <thead><tr><th>Time</th><th>Asset</th><th>Rule</th><th>Detail</th></tr></thead>
            <tbody>
              {(r.rejections as { ts: string; signal_id: number | null; rule: string; detail: string; ticker: string | null }[]).map((e, i) => (
                <tr key={i}>
                  <td className="num">{dt(e.ts)}</td>
                  <td>{e.signal_id ? <Link className="hover:text-desk-accent" to={`/pro/signals/${e.signal_id}`}>{e.ticker}</Link> : "—"}</td>
                  <td><Badge tone={e.rule === "max_drawdown" || e.rule === "daily_loss" ? "down" : "warn"}>{e.rule}</Badge></td>
                  <td className="wrap text-desk-dim">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!r.rejections.length && <Empty>Nothing rejected.</Empty>}
        </div>
      </Panel>
    </div>
  );
}
