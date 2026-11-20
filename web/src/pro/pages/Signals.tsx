import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pro, type Signal } from "../api";
import { Badge, Btn, Dir, Empty, Panel, Status, dt, num, px, rr } from "../ui";

const STATUSES = ["", "new", "approved", "filled", "closed", "flat", "rejected", "cancelled", "expired"];

export default function Signals({ flash }: { flash: Set<number> }) {
  const [sp, setSp] = useSearchParams();
  const status = sp.get("status") ?? "";
  const ticker = sp.get("ticker") ?? "";
  const mode = sp.get("mode") ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["signals", status, ticker, mode], queryFn: () => pro.signals({ status, ticker, mode }) });
  const ov = useQuery({ queryKey: ["overview"], queryFn: pro.overview });
  const [err, setErr] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: ({ id, ok }: { id: number; ok: boolean }) => (ok ? pro.approve(id) : pro.reject(id)),
    onSuccess: () => {
      setErr(null);
      ["signals", "overview", "positions", "orders"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    },
    onError: (e: Error) => setErr(e.message),
  });
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const tickers = Array.from(new Set((q.data?.signals ?? []).map((s) => s.ticker))).sort();
  const manual = ov.data?.approval !== "auto";
  return (
    <Panel
      title="Signal blotter"
      pad={false}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Status" value={status} onChange={(e) => set("status", e.target.value)} className="rounded border border-desk-line bg-desk-panel-2 px-1.5 py-1 text-[12px]">
            {STATUSES.map((s) => <option key={s} value={s}>{s ? `${s} (${q.data?.counts[s] ?? 0})` : "all statuses"}</option>)}
          </select>
          <select aria-label="Asset" value={ticker} onChange={(e) => set("ticker", e.target.value)} className="rounded border border-desk-line bg-desk-panel-2 px-1.5 py-1 text-[12px]">
            <option value="">all assets</option>
            {Array.from(new Set([ticker, ...tickers].filter(Boolean))).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select aria-label="Mode" value={mode} onChange={(e) => set("mode", e.target.value)} className="rounded border border-desk-line bg-desk-panel-2 px-1.5 py-1 text-[12px]">
            <option value="">swing + scalp</option><option value="swing">swing</option><option value="scalp">scalp</option>
          </select>
          <Badge tone={manual ? "accent" : "neutral"}>{manual ? "manual approval" : "auto (paper)"}</Badge>
        </div>
      }
    >
      {err && <div role="alert" className="border-b border-desk-line bg-desk-down/10 px-3 py-2 text-[12px] text-desk-down">{err}</div>}
      <div className="overflow-x-auto">
        <table className="dt">
          <thead>
            <tr>
              <th>Time</th><th>Asset</th><th>Mode</th><th>Direction</th><th className="r">Conviction</th><th className="r">R:R</th>
              <th className="r">EV (R)</th><th className="r">Size</th><th>Status</th><th>Reason</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.signals ?? []).map((s: Signal) => (
              <tr key={s.id} className={flash.has(s.id) ? "flash" : ""} data-signal={s.id}>
                <td className="num"><Link className="hover:text-desk-accent" to={`/pro/signals/${s.id}`}>{dt(s.created_at)}</Link></td>
                <td><Link className="font-medium hover:text-desk-accent" to={`/pro/signals/${s.id}`}>{s.ticker}</Link></td>
                <td className="text-desk-dim">{s.mode} · {s.horizon}</td>
                <td>{s.direction === "flat" ? <span className="text-desk-dim">FLAT</span> : <Dir side={s.direction} />}</td>
                <td className="r num">{num(s.conviction, 2)}</td>
                <td className="r num">{num(s.reward_risk, 2)}</td>
                <td className="r num">{rr(s.ev_r)}{!s.calibrated && <span className="ml-1 text-[10px] text-desk-faint" title="P(win) from raw P(up), not calibrated yet">u</span>}</td>
                <td className="r num">{s.size ? `${num(s.size.qty, s.size.qty < 1 ? 4 : 0)} · $${px(s.size.notional)}` : "—"}</td>
                <td><Status s={s.status} reason={s.status_reason} /></td>
                <td className="wrap max-w-[22rem] text-desk-dim">{s.flat_reason ? `${s.flat_reason}${s.flat_detail ? ` — ${s.flat_detail}` : ""}` : s.status_reason ?? ""}</td>
                <td>
                  {s.status === "new" && manual && (
                    <span className="flex gap-1">
                      <Btn tone="accent" onClick={() => act.mutate({ id: s.id, ok: true })} disabled={act.isPending} title="Approve (paper order at the next bar's open)">Approve</Btn>
                      <Btn tone="danger" onClick={() => act.mutate({ id: s.id, ok: false })} disabled={act.isPending}>Reject</Btn>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.data && !q.data.signals.length && <Empty>No signals match.</Empty>}
      </div>
      <p className="border-t border-desk-line px-3 py-2 text-[11px] text-desk-faint">
        Most predictions should be FLAT: a trade needs |S| ≥ 0.15, confidence ≥ 0.5, R:R ≥ 1.5 and positive EV after costs.
        "u" = uncalibrated P(win). Approve before the entry bar opens, otherwise the signal expires.
      </p>
    </Panel>
  );
}
