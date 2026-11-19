import { useRunStream } from "../hooks/useRunStream";

// R4 placeholder — replaced by the full war room in R5.
export default function LiveShow({ runId, onNewShow }: { runId: string; onNewShow: () => void }) {
  const s = useRunStream(runId);
  return (
    <section className="card p-6 space-y-2" aria-live="polite">
      <div className="font-display text-2xl">
        🎬 {s.meta?.ticker ?? "…"} · {s.meta?.mode} · {s.meta?.horizon}
      </div>
      <div className="text-sm text-ink-dim" data-testid="show-status">
        status: {s.status} · stage: {s.stage ?? "—"} · events: {s.lastSeq}
      </div>
      <button onClick={onNewShow} className="text-sm underline">
        New show
      </button>
    </section>
  );
}
