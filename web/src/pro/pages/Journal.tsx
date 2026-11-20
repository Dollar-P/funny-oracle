import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pro, type JournalRow } from "../api";
import { Badge, Btn, Dir, Empty, MetricCell, Panel, Pnl, dt, num, px, rr } from "../ui";

function Tags({ row }: { row: JournalRow }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(row.tags.join(", "));
  const save = useMutation({
    mutationFn: () => pro.setTags(row.id, text.split(",").map((t) => t.trim()).filter(Boolean)),
    onSuccess: () => {
      setEdit(false);
      qc.invalidateQueries({ queryKey: ["journal"] });
    },
  });
  if (edit)
    return (
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="flex gap-1">
        <input aria-label="Tags" value={text} onChange={(e) => setText(e.target.value)} className="w-28 rounded border border-desk-line bg-desk-panel-2 px-1 text-[12px]" />
        <Btn type="submit">Save</Btn>
      </form>
    );
  return (
    <button onClick={() => setEdit(true)} className="flex flex-wrap gap-1 text-left" title="Edit tags">
      {row.tags.length ? row.tags.map((t) => <Badge key={t}>{t}</Badge>) : <span className="text-[11px] text-desk-faint">+ tag</span>}
    </button>
  );
}

export default function Journal() {
  const q = useQuery({ queryKey: ["journal"], queryFn: pro.journal });
  const rows = q.data?.journal ?? [];
  const rs = rows.map((r) => r.r_multiple).filter((x): x is number => x !== null);
  const wins = rows.filter((r) => r.realized_pnl > 0).length;
  const mean = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null;
  const n = rows.length;
  return (
    <Panel
      title="Journal — closed paper trades"
      pad={false}
      actions={
        <div className="flex items-center gap-3 text-[12px]">
          <span className="text-desk-dim">expectancy <MetricCell m={{ value: mean, n: rs.length, lo: null, hi: null, sufficient: rs.length >= 30, label: rs.length >= 30 ? "ok" : "insufficient data" }} fmt={(v) => rr(v)} /></span>
          <span className="text-desk-dim">win rate <span className="num text-desk-ink">{n ? `${Math.round((wins / n) * 100)} %` : "—"}</span></span>
          <a className="text-desk-accent hover:underline" href="/api/pro/journal?format=csv" download>Export CSV</a>
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="dt">
          <thead>
            <tr><th>Closed</th><th>Asset</th><th>Side</th><th className="r">Entry</th><th className="r">Exit</th><th className="r">P&L</th><th className="r">R</th>
              <th className="r">MAE / MFE (R)</th><th className="r">Costs</th><th>Exit</th><th className="r">Held</th><th>Post-mortem (Reviewer)</th><th>Tags</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="num">{dt(r.closed_at)}</td>
                <td><Link className="font-medium hover:text-desk-accent" to={`/pro/signals/${r.signal_id}`}>{r.ticker}</Link></td>
                <td><Dir side={r.side} /></td>
                <td className="r num">{px(r.avg_entry)}</td><td className="r num">{px(r.avg_exit)}</td>
                <td className="r"><Pnl v={r.realized_pnl} /></td>
                <td className="r num">{rr(r.r_multiple)}</td>
                <td className="r num">{num(r.mae_r)} / {num(r.mfe_r)}</td>
                <td className="r num">{num(r.costs)}</td>
                <td>{r.exit_reason}</td>
                <td className="r num">{r.holding_h > 48 ? `${num(r.holding_h / 24, 1)} d` : `${num(r.holding_h, 0)} h`}</td>
                <td className="wrap max-w-[26rem] text-desk-dim">
                  {r.thesis_held !== null && <Badge tone={r.thesis_held ? "up" : "down"}>{r.thesis_held ? "thesis held" : "thesis failed"}</Badge>}{" "}
                  {r.post_mortem}
                  {r.lesson && r.lesson !== "none" && <div className="mt-0.5 text-[11.5px] text-desk-faint">Lesson: {r.lesson}</div>}
                </td>
                <td><Tags row={r} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.data && !rows.length && <Empty>No closed trades yet.</Empty>}
      </div>
    </Panel>
  );
}
