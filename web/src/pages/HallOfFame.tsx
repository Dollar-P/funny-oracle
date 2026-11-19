import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { AgentPodium, AutoScalpCard, CoinFlip, CoverageGauge, History, ModelLeaderboard, Pending } from "../components/HallParts";

export default function HallOfFame() {
  // evaluate=true: due predictions get their actual price on open (like the Streamlit Scoreboard tab)
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["scoreboard"],
    queryFn: () => api.scoreboard(true),
    refetchInterval: 60_000,
  });
  return (
    <div className="pt-6 space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="font-display text-4xl sm:text-5xl text-gold">🏆 HALL OF FAME</h1>
          <p className="text-ink-dim mt-1">Real results only. Graded against what the market actually did.</p>
        </div>
        <div className="flex-1" />
        <button onClick={() => refetch()} disabled={isFetching} className="px-4 h-9 rounded-full border border-line bg-surface-2 text-sm">
          {isFetching ? "Grading…" : "↻ Re-grade"}
        </button>
      </header>
      {isLoading && <p className="py-12 text-center text-ink-dim">Fetching actual prices and grading the Oracle…</p>}
      {isError && <p className="py-12 text-center text-bear">Could not load the scoreboard — is the API running?</p>}
      {data && (
        <>
          {data.evaluated_now > 0 && <p className="text-sm text-bull">✓ Graded {data.evaluated_now} prediction(s) just now.</p>}
          <CoinFlip metrics={data.metrics} />
          <div className="grid lg:grid-cols-2 gap-5">
            <CoverageGauge metrics={data.metrics} />
            <AgentPodium metrics={data.metrics} />
          </div>
          <ModelLeaderboard rows={data.models} />
          <div className="grid lg:grid-cols-2 gap-5">
            <Pending rows={data.pending} />
            <AutoScalpCard />
          </div>
          <History />
        </>
      )}
    </div>
  );
}
