import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { useArena } from "../state/arena";
import { useQueryClient } from "@tanstack/react-query";
import { useRunStream } from "../hooks/useRunStream";
import type { ShowState } from "../state/show";
import type { RunEvent } from "../lib/types";
import ProgressRail from "./ProgressRail";
import Stage, { provisionalS } from "./Stage";
import TugOfWar from "./TugOfWar";
import DebateThread from "./DebateThread";
import TickerTape from "./TickerTape";
import VerdictSection from "./VerdictSection";
import ShareButton from "./ShareButton";
import { shortModel, thb } from "../lib/format";
import { displayTicker } from "../lib/ticker";
import { sfx } from "../lib/sound";
import { useToasts } from "../state/settings";

function Elapsed({ s }: { s: ShowState }) {
  const [now, setNow] = useState(Date.now());
  const running = s.status === "live" || s.status === "connecting";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [running]);
  if (!s.meta) return null;
  if (!running && s.prediction && !s.meta.mock && s.doneTs === null) return <span className="num">{s.prediction.elapsed_s.toFixed(0)}s</span>;
  const end = s.doneTs ?? (running ? now : s.tape[s.tape.length - 1]?.ts ?? now);
  return <span className="num">{Math.max(0, (end - s.meta.startedTs) / 1000).toFixed(0)}s</span>;
}

export function ShowHeader({ s, compact = false, right }: { s: ShowState; compact?: boolean; right?: React.ReactNode }) {
  const m = s.meta;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <h2 className={`font-display ${compact ? "text-xl" : "text-3xl sm:text-4xl"} tracking-wide`}>
        {m ? (
          <>
            <span className="text-gold">{displayTicker(m.ticker)}</span>{" "}
            <span className="text-ink-dim text-[0.7em]">
              · {m.mode === "swing" ? "🐢 Swing" : "⚡ Scalp"} · {m.horizon}
            </span>
          </>
        ) : (
          "Connecting to the stage…"
        )}
      </h2>
      {m && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-dim">
          <span className="px-2 py-0.5 rounded-full bg-surface-2 border border-line" title={`judge: ${m.models.judge ?? "—"}`}>
            🧠 {shortModel(m.models.analyst)}
            {m.models.judge && m.models.judge !== m.models.analyst && ` · 🔮 ${shortModel(m.models.judge)}`}
          </span>
          {m.mock && (
            <span className="px-2 py-0.5 rounded-full bg-oracle/20 border border-oracle/40 text-oracle font-semibold">
              mock replay{m.replayOf ? ` of #${m.replayOf}` : ""}
            </span>
          )}
          <span className="px-2 py-0.5 rounded-full bg-surface-2 border border-line">
            ⏱ <Elapsed s={s} />
          </span>
          <span className="px-2 py-0.5 rounded-full bg-surface-2 border border-line num" title="cost of this show (tracked)">
            🪙 {m.mock ? "฿0" : thb(s.cost?.run ?? 0)}
          </span>
        </div>
      )}
      <div className="flex-1" />
      {right}
    </div>
  );
}

export function ShowActions({ s, onRunAgain, extra }: { s: ShowState; onRunAgain?: () => void; extra?: React.ReactNode }) {
  const nav = useNavigate();
  const pid = s.predictionId;
  const m = s.meta;
  const btn = "px-4 h-10 rounded-full border border-line bg-surface-2 hover:bg-surface text-sm font-semibold inline-flex items-center gap-1.5";
  return (
    <>
      {extra}
      {pid !== null && (
        <Link to={`/p/${pid}`} className={btn}>
          🎞️ Replay page
        </Link>
      )}
      {onRunAgain && (
        <button onClick={onRunAgain} className={btn}>
          🔁 Run again
        </button>
      )}
      {m && (
        <button onClick={() => nav(`/duel?ticker=${encodeURIComponent(m.ticker)}&mode=${m.mode}&horizon=${m.horizon}`)} className={btn}>
          ⚔️ Duel this ticker
        </button>
      )}
    </>
  );
}

export default function LiveShow({ runId, onNewShow }: { runId: string; onNewShow: () => void }) {
  const qc = useQueryClient();
  const arena = useArena();
  const push = useToasts((t) => t.push);
  const [debateShown, setDebateShown] = useState(false);
  const s = useRunStream(runId, (ev: RunEvent) => {
    if (ev.type === "cost_update" || ev.type === "run_done") void qc.invalidateQueries({ queryKey: ["budget"] });
    if (ev.type === "agent_done") sfx.tick();
    if (ev.type === "agent_failed") sfx.buzzer();
    if (ev.type === "error") push("error", ev.fatal ? `Show cancelled: ${ev.message}` : ev.message);
  });
  useEffect(() => setDebateShown(false), [runId]);

  async function runAgain() {
    const m = s.meta;
    if (!m) return;
    try {
      const r = await api.startPrediction({
        ticker: m.ticker,
        mode: m.mode,
        horizon: m.horizon,
        analyst_model: m.models.analyst ?? arena.analystModel,
        judge_model: m.models.judge ?? arena.judgeModel,
      });
      arena.set({ runId: r.run_id, ticker: m.ticker, mode: m.mode, horizon: m.horizon });
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      push("error", e instanceof ApiError && e.status === 402 ? "🪙 The Oracle is out of coins." : e instanceof Error ? e.message : String(e));
    }
  }

  const mode = s.meta?.mode ?? "swing";
  const sNow = provisionalS(s);
  const reachedDebate = s.stagesSeen.includes("debate");
  // verdict waits until the debate has been typed out (swing), so the reveal doesn't spoil it
  const verdictReady = s.verdict !== null || s.sFinal !== null ? mode === "scalp" || debateShown || s.debate.length === 0 : false;

  return (
    <div className="space-y-5">
      <div className="card bg-surface/70 backdrop-blur px-4 sm:px-6 py-4 space-y-3">
        <ShowHeader
          s={s}
          right={
            <button onClick={onNewShow} className="px-4 h-9 rounded-full border border-line bg-surface-2 hover:bg-surface text-sm font-semibold">
              {s.status === "done" || s.status === "error" ? "🔁 New show" : "✕ Leave show"}
            </button>
          }
        />
        {s.meta && <ProgressRail mode={mode} stage={s.stage} seen={s.stagesSeen} done={s.status === "done"} />}
        {s.status === "live" && !s.stage && <p className="text-sm text-ink-dim">Fetching volatility history… the cast is walking on.</p>}
      </div>

      {s.status === "error" && (
        <div role="alert" className="card border-bear/60 p-6 text-center">
          <div className="text-5xl" aria-hidden="true">🎭</div>
          <h3 className="font-display text-3xl text-bear mt-2">Show cancelled</h3>
          <p className="text-ink-dim mt-2 text-sm">{s.error?.message}</p>
        </div>
      )}

      <Stage s={s} />
      <TugOfWar s={sNow} provisional={s.sRaw === null} final={verdictReady ? s.sFinal : null} />
      {s.news && (
        <details className="card px-4 py-3 text-sm">
          <summary className="cursor-pointer text-ink-dim">📰 News desk brief ({s.news.bullets.length} headlines)</summary>
          <ul className="mt-2 list-disc pl-5 space-y-1">
            {s.news.bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        </details>
      )}
      {mode === "swing" && (reachedDebate || s.debate.length > 0) && (
        <DebateThread
          key={runId}
          turns={s.debate}
          waiting={s.stage === "debate"}
          onAllShown={() => setDebateShown(true)}
        />
      )}
      {verdictReady && <VerdictSection s={s} runId={runId} actions={s.status === "done" ? <ShowActions s={s} onRunAgain={runAgain} extra={s.prediction ? <ShareButton prediction={s.prediction} /> : null} /> : undefined} />}
      <TickerTape items={s.tape} />
    </div>
  );
}
