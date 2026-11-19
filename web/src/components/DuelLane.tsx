import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRunStream } from "../hooks/useRunStream";
import type { ShowState } from "../state/show";
import ProgressRail from "./ProgressRail";
import Stage, { provisionalS } from "./Stage";
import TugOfWar from "./TugOfWar";
import TickerTape from "./TickerTape";
import PrizeCards from "./PrizeCards";
import VerdictCard from "./VerdictReveal";
import { shortModel } from "../lib/format";

/** Compact war room for one side of a duel. Reports its state upward when it changes. */
export default function DuelLane({
  runId,
  side,
  onState,
}: {
  runId: string;
  side: "A" | "B";
  onState: (s: ShowState) => void;
}) {
  const qc = useQueryClient();
  const s = useRunStream(runId, (ev) => {
    if (ev.type === "run_done" || ev.type === "cost_update") void qc.invalidateQueries({ queryKey: ["budget"] });
  });
  // report the *reduced* state once finished (the stream callback sees the state before the event)
  useEffect(() => {
    if (s.status === "done" || s.status === "error") onState(s);
  }, [s.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const accent = side === "A" ? "#38bdf8" : "#a78bfa";
  const last = s.debate[s.debate.length - 1];
  return (
    <section className="card p-3 sm:p-4 space-y-3 min-w-0" style={{ borderColor: `${accent}66` }} aria-label={`Model ${side}`}>
      <div className="flex items-center gap-2">
        <span className="w-7 h-7 rounded-full grid place-items-center text-sm font-black text-stage" style={{ background: accent }}>
          {side}
        </span>
        <span className="font-semibold">{shortModel(s.meta?.models.analyst)}</span>
        {s.meta?.mock && <span className="text-[10px] px-1.5 rounded bg-oracle/20 text-oracle">mock #{s.meta.replayOf}</span>}
        <span className="ml-auto text-xs text-ink-dim">{s.status}</span>
      </div>
      {s.meta && <ProgressRail mode={s.meta.mode} stage={s.stage} seen={s.stagesSeen} done={s.status === "done"} compact />}
      <Stage s={s} compact />
      <TugOfWar s={provisionalS(s)} provisional={s.sRaw === null} final={s.sFinal} compact />
      {last && s.status !== "done" && (
        <p className="text-xs text-ink-dim line-clamp-2">
          {last.side === "bull" ? "🐂" : "🐻"} {last.text}
        </p>
      )}
      {s.status === "done" && s.quant && (
        <>
          <VerdictCard verdict={s.verdict} sFinal={s.sFinal ?? 0} sRaw={s.sRaw} confidence={s.confidence ?? 0} animated={false} compact />
          <PrizeCards now={s.quant.price_now} p10={s.quant.p10} p50={s.quant.p50} p90={s.quant.p90} probUp={s.quant.prob_up} compact />
        </>
      )}
      {s.status === "error" && <p className="text-sm text-bear">Show cancelled: {s.error?.message}</p>}
      <TickerTape items={s.tape} compact />
    </section>
  );
}
