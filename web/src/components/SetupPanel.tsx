import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api, ApiError } from "../lib/api";
import { useArena, DEFAULT_HORIZON } from "../state/arena";
import { useToasts } from "../state/settings";
import { classifyTicker } from "../lib/ticker";
import { thb } from "../lib/format";
import TickerSearch from "./TickerSearch";
import PresetChips from "./PresetChips";
import ModeHorizon from "./ModeHorizon";
import ModelPicker, { ThinkToggle, blockedReason } from "./ModelPicker";
import OutOfCoins, { type Refusal } from "./OutOfCoins";
import { useBudget } from "./BudgetMeter";

const TYPE_LABEL = { stock: "🏛️ US stock", crypto: "🪙 crypto", gold: "🥇 gold" } as const;

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 60_000 });
}

export default function SetupPanel() {
  const a = useArena();
  const push = useToasts((s) => s.push);
  const models = useModels();
  const budget = useBudget();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  const list = models.data?.models ?? [];
  const isMock = models.data?.mock ?? budget.data?.mock ?? false;
  // default model: .env default for the mode, else the first listed
  useEffect(() => {
    if (!models.data || list.length === 0) return;
    if (!a.analystModel || !list.some((m) => m.id === a.analystModel)) {
      const def = (a.mode === "scalp" ? models.data.default_scalp : models.data.default).analyst;
      a.set({ analystModel: list.some((m) => m.id === def) ? def : list[0].id });
    }
  }, [models.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const analyst = list.find((m) => m.id === a.analystModel);
  const est = analyst?.est_cost_thb[a.mode] ?? 0;
  const remaining = budget.data?.remaining_thb ?? Infinity;
  const overBudget = !isMock && est > remaining;

  const disabledReason = useMemo(() => {
    if (!analyst) return models.isLoading ? "Loading models…" : "No models configured — set an API key in .env";
    const blocked = blockedReason(analyst, a.mode);
    if (blocked) return blocked;
    if (overBudget) return `Over budget: ~${thb(est)} needed, ${thb(Math.max(0, remaining))} left`;
    return null;
  }, [analyst, overBudget, est, remaining, models.isLoading, a.mode]);

  async function start() {
    if (disabledReason || busy) return;
    setBusy(true);
    try {
      const r = await api.startPrediction({
        ticker: a.ticker,
        mode: a.mode,
        horizon: a.horizon,
        analyst_model: a.analystModel,
        judge_model: a.judgeModel,
        think_preset: analyst?.provider === "local" ? a.thinkPreset : null,
      });
      a.set({ runId: r.run_id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) setRefusal(e.body as Refusal);
      else if (e instanceof ApiError && e.status === 429) push("info", "🎭 A show is already in progress — wait for the curtain call.");
      else push("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="card bg-surface/80 backdrop-blur p-4 sm:p-6 space-y-6"
      aria-labelledby="setup-title"
    >
      <h2 id="setup-title" className="sr-only">
        Set up the show
      </h2>
      <div className="flex flex-col lg:flex-row lg:items-center gap-3">
        <TickerSearch value={a.ticker} onPick={(t) => a.set({ ticker: t })} />
        <PresetChips value={a.ticker} onPick={(t) => a.set({ ticker: t })} />
      </div>
      <div className="text-sm text-ink-dim -mt-2">
        On stage: <b className="num text-ink">{a.ticker}</b> · {TYPE_LABEL[classifyTicker(a.ticker)]}
      </div>
      <div className="border-t border-line" />
      <ModeHorizon
        mode={a.mode}
        horizon={a.horizon}
        onMode={(m) => a.set({ mode: m, horizon: DEFAULT_HORIZON[m] })}
        onHorizon={(h) => a.set({ horizon: h })}
      />
      <div className="border-t border-line" />
      <div>
        <h3 className="font-display text-xl tracking-wide">Choose your Oracle brain</h3>
        {models.isError && <p className="text-bear text-sm mt-2">Could not load models — is the API running?</p>}
        <ModelPicker
          models={list}
          mode={a.mode}
          analyst={a.analystModel}
          judge={a.judgeModel}
          onAnalyst={(id) => a.set({ analystModel: id })}
          onJudge={(id) => a.set({ judgeModel: id })}
        />
        {analyst?.provider === "local" && (
          <div className="mt-3">
            <ThinkToggle value={a.thinkPreset} onChange={(p) => a.set({ thinkPreset: p })} />
          </div>
        )}
      </div>
      <div className="flex flex-col items-center gap-2 pt-2">
        <motion.button
          whileHover={disabledReason ? undefined : { scale: 1.03 }}
          whileTap={disabledReason ? undefined : { scale: 0.97 }}
          onClick={start}
          disabled={!!disabledReason || busy}
          aria-describedby={disabledReason ? "start-reason" : undefined}
          className="font-display text-xl sm:text-2xl tracking-wide px-8 sm:px-12 h-16 rounded-full text-stage bg-gradient-to-b from-gold to-amber-500 shadow-glow-gold disabled:from-surface-2 disabled:to-surface-2 disabled:text-ink-faint disabled:shadow-none"
        >
          🔔 START THE SHOW · {isMock ? "mock replay ฿0" : `est. ${thb(est)}`}
        </motion.button>
        {disabledReason ? (
          <p id="start-reason" className="text-sm text-bear">
            {disabledReason}
          </p>
        ) : (
          <p className="text-xs text-ink-faint">
            {isMock
              ? "Mock mode: replays a stored show with fake live timing — no LLM calls, no cost."
              : `Budget left ${thb(remaining)} · the estimate is this model's recent average × 1.25`}
          </p>
        )}
      </div>
      <OutOfCoins refusal={refusal} onClose={() => setRefusal(null)} />
    </motion.section>
  );
}
