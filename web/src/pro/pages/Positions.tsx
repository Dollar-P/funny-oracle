import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { pro } from "../api";
import { Badge, Dir, Empty, Panel, Pnl, dt, num, px, rr } from "../ui";

export default function Positions() {
  const pos = useQuery({ queryKey: ["positions"], queryFn: pro.positions, refetchInterval: 60_000 });
  const ord = useQuery({ queryKey: ["orders"], queryFn: pro.orders });
  const fil = useQuery({ queryKey: ["fills"], queryFn: pro.fills });
  return (
    <div className="space-y-4">
      <Panel title="Open positions" pad={false}>
        <div className="overflow-x-auto">
          <table className="dt">
            <thead>
              <tr><th>Asset</th><th>Side</th><th>State</th><th className="r">Qty</th><th className="r">Entry</th><th className="r">Mark</th>
                <th className="r">Unrealised</th><th className="r">R now</th><th className="r">To stop</th><th className="r">To target</th>
                <th className="r">Time left</th><th>Opened</th></tr>
            </thead>
            <tbody>
              {(pos.data?.positions ?? []).map((p) => (
                <tr key={p.id}>
                  <td><Link className="font-medium hover:text-desk-accent" to={`/pro/signals/${p.signal_id}`}>{p.ticker}</Link></td>
                  <td><Dir side={p.side} /></td>
                  <td>{p.state === "open" ? <Badge tone="up">open{p.t1_done ? " · T1 hit, stop at BE" : ""}</Badge> : <Badge tone="accent">entry pending</Badge>}</td>
                  <td className="r num">{num(p.state === "open" ? p.qty_open : p.qty, p.qty < 1 ? 4 : 0)}</td>
                  <td className="r num">{px(p.entry ?? p.entry_ref)}</td>
                  <td className="r num">{px(p.mark)}</td>
                  <td className="r"><Pnl v={p.unrealized_pnl} /></td>
                  <td className="r num">{rr(p.r_now)}</td>
                  <td className="r num">{p.to_stop_pct === undefined ? "—" : `${num(p.to_stop_pct)} %`}</td>
                  <td className="r num">{p.to_target_pct === undefined ? "—" : `${num(p.to_target_pct)} %`}</td>
                  <td className="r num">{p.time_left_h > 48 ? `${num(p.time_left_h / 24, 1)} d` : `${num(p.time_left_h, 1)} h`}</td>
                  <td className="num">{dt(p.opened_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {pos.data && !pos.data.positions.length && <Empty>No open positions.</Empty>}
        </div>
      </Panel>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Working orders (OCO brackets)" pad={false}>
          <div className="max-h-[420px] overflow-auto">
            <table className="dt">
              <thead><tr><th>Asset</th><th>Role</th><th>Type</th><th>Side</th><th className="r">Qty</th><th className="r">Limit</th><th className="r">Stop</th><th>Status</th><th>Group</th></tr></thead>
              <tbody>
                {(ord.data?.orders ?? []).map((o) => (
                  <tr key={String(o.id)}>
                    <td>{o.ticker}</td><td>{o.role}</td><td className="text-desk-dim">{o.type}</td><td>{o.side}</td>
                    <td className="r num">{num(Number(o.qty), Number(o.qty) < 1 ? 4 : 0)}</td>
                    <td className="r num">{o.limit_price === null || !Number.isFinite(Number(o.limit_price)) ? "—" : px(Number(o.limit_price))}</td>
                    <td className="r num">{o.stop_price === null ? "—" : px(Number(o.stop_price))}</td>
                    <td><Badge>{String(o.status)}</Badge></td><td className="text-desk-faint">{o.oco_group}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ord.data && !ord.data.orders.length && <Empty>No working orders.</Empty>}
          </div>
        </Panel>
        <Panel title="Fills" pad={false}>
          <div className="max-h-[420px] overflow-auto">
            <table className="dt">
              <thead><tr><th>Time</th><th>Asset</th><th>Leg</th><th>Side</th><th className="r">Qty</th><th className="r">Price</th><th className="r">Fee</th><th className="r">Slippage</th></tr></thead>
              <tbody>
                {(fil.data?.fills ?? []).slice(0, 200).map((f) => (
                  <tr key={String(f.id)}>
                    <td className="num">{dt(String(f.ts))}</td><td>{f.ticker}</td><td>{f.reason}</td><td>{f.side}</td>
                    <td className="r num">{num(Number(f.qty), Number(f.qty) < 1 ? 4 : 0)}</td><td className="r num">{px(Number(f.price))}</td>
                    <td className="r num">{num(Number(f.fee))}</td><td className="r num">{num(Number(f.slippage))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {fil.data && !fil.data.fills.length && <Empty>No fills yet.</Empty>}
          </div>
          <p className="border-t border-desk-line px-3 py-2 text-[11px] text-desk-faint">
            Paper fills: market orders at the next bar's open + slippage; targets at the limit; stops gapped through at the open;
            stop and target in one bar → stop. Default costs: stocks 0 commission + 2 bps, gold 3 bps, crypto 10 bps fee + 5 bps per side.
          </p>
        </Panel>
      </div>
    </div>
  );
}
