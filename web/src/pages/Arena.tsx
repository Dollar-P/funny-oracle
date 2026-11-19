import SetupPanel from "../components/SetupPanel";
import LiveShow from "../components/LiveShow";
import { useArena } from "../state/arena";

export default function Arena() {
  const runId = useArena((s) => s.runId);
  const set = useArena((s) => s.set);
  return (
    <div className="pt-6 sm:pt-8 space-y-6">
      {!runId && (
        <header className="text-center">
          <h1 className="font-display text-4xl sm:text-6xl text-gold drop-shadow-[0_0_24px_rgba(250,204,21,0.35)]">
            THE ORACLE ARENA
          </h1>
          <p className="mt-2 text-ink-dim">Five AI analysts argue. A bull and a bear debate. The Oracle reveals the verdict.</p>
        </header>
      )}
      {runId ? <LiveShow runId={runId} onNewShow={() => set({ runId: null })} /> : <SetupPanel />}
    </div>
  );
}
