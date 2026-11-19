import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { showFromPrediction } from "../state/show";
import { useToasts } from "../state/settings";
import Stage from "../components/Stage";
import TugOfWar from "../components/TugOfWar";
import DebateThread from "../components/DebateThread";
import VerdictSection from "../components/VerdictSection";
import LiveShow, { ShowActions, ShowHeader } from "../components/LiveShow";
import ShareButton from "../components/ShareButton";
import { dateTime, price } from "../lib/format";

export default function Replay() {
  const { id } = useParams();
  const pid = Number(id);
  const push = useToasts((t) => t.push);
  const [runId, setRunId] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["prediction", pid], queryFn: () => api.prediction(pid), enabled: Number.isFinite(pid) });
  const s = useMemo(() => (q.data ? showFromPrediction(q.data.prediction) : null), [q.data]);

  async function replayShow() {
    try {
      setRunId((await api.replay(pid)).run_id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      push("error", e instanceof ApiError && e.status === 429 ? "🎭 A show is already in progress." : String(e));
    }
  }

  if (runId) return <div className="pt-6"><LiveShow runId={runId} onNewShow={() => setRunId(null)} /></div>;
  if (q.isLoading) return <div className="py-24 text-center text-ink-dim">🎞️ Loading the tape…</div>;
  if (q.isError || !s || !q.data)
    return <div className="py-24 text-center text-ink-dim">No prediction #{id} in the archive. 🗄️</div>;

  const p = q.data.prediction;
  const actual = q.data.actual_price;
  const btn = "px-4 h-10 rounded-full text-sm font-semibold inline-flex items-center gap-1.5";
  return (
    <div className="pt-6 space-y-5">
      <div className="card bg-surface/70 backdrop-blur px-4 sm:px-6 py-4 space-y-2">
        <ShowHeader
          s={s}
          right={
            <button onClick={replayShow} className={`${btn} bg-gradient-to-b from-gold to-amber-500 text-stage shadow-glow-gold`}>
              ▶ Replay the show
            </button>
          }
        />
        <div className="text-xs text-ink-dim">
          Prediction #{p.id} · {dateTime(p.created_at)} · source {q.data.source} · target {dateTime(p.target_time)}
          {actual !== null && (
            <>
              {" "}
              · actual <span className="num text-ink">{price(actual)}</span>{" "}
              {p.forecast.prob_up !== 0.5 && (p.forecast.prob_up > 0.5) === (actual > p.forecast.price_now) ? "✅ direction right" : "❌ direction wrong"}
            </>
          )}
        </div>
      </div>
      <Stage s={s} />
      <TugOfWar s={s.sRaw} provisional={false} final={s.sFinal} />
      {s.debate.length > 0 && <DebateThread turns={s.debate} instant waiting={false} />}
      <VerdictSection
        s={s}
        reveal={false}
        actual={actual}
        actions={<ShowActions s={s} extra={<ShareButton prediction={p} />} />}
      />
    </div>
  );
}
