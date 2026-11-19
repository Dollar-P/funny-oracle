import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { Mode, Prediction } from "../lib/types";
import type { ShowState } from "../state/show";
import { DEFAULT_HORIZON } from "../state/arena";
import { useToasts } from "../state/settings";
import TickerSearch from "../components/TickerSearch";
import PresetChips from "../components/PresetChips";
import ModeHorizon from "../components/ModeHorizon";
import { ModelCard } from "../components/ModelPicker";
import DuelLane from "../components/DuelLane";
import HeadToHead from "../components/HeadToHead";
import OutOfCoins, { type Refusal } from "../components/OutOfCoins";
import { useModels } from "../components/SetupPanel";
import { useBudget } from "../components/BudgetMeter";
import { HORIZONS } from "../lib/cast";
import { thb } from "../lib/format";
import { displayTicker } from "../lib/ticker";

interface DuelRun {
  duelId: string;
  runIds: [string, string];
  mock: boolean;
}
const KEY = "oracle.duel";

function loadRun(): DuelRun | null {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "null");
  } catch {
    return null;
  }
}

export default function Duel() {
  const [params] = useSearchParams();
  const push = useToasts((t) => t.push);
  const models = useModels();
  const budget = useBudget();
  const list = models.data?.models ?? [];
  const isMock = models.data?.mock ?? false;
  const initMode = (params.get("mode") as Mode) || "swing";
  const [ticker, setTicker] = useState(params.get("ticker") || "NVDA");
  const [mode, setMode] = useState<Mode>(initMode);
  const [horizon, setHorizon] = useState(
    params.get("horizon") && HORIZONS[initMode].includes(params.get("horizon")!) ? params.get("horizon")! : DEFAULT_HORIZON[initMode],
  );
  const [a, setA] = useState<string | null>(null);
  const [b, setB] = useState<string | null>(null);
  const [run, setRun] = useState<DuelRun | null>(params.get("ticker") ? null : loadRun());
  const [results, setResults] = useState<Record<string, ShowState>>({});
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (list.length && !a) setA(list[0].id);
    if (list.length && !b) setB(list[Math.min(list.length - 1, 2)]?.id ?? list[0].id);
  }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(run));
    } catch {
      /* ignore */
    }
  }, [run]);

  const ma = list.find((m) => m.id === a);
  const mb = list.find((m) => m.id === b);
  const est = (ma?.est_cost_thb[mode] ?? 0) + (mb?.est_cost_thb[mode] ?? 0);
  const remaining = budget.data?.remaining_thb ?? Infinity;
  const reason = !ma || !mb ? "Pick two models" : !isMock && est > remaining ? `Over budget: ~${thb(est)} for both, ${thb(Math.max(0, remaining))} left` : null;

  async function start() {
    if (reason || !a || !b) return;
    setBusy(true);
    try {
      const r = await api.startDuel({ ticker, mode, horizon, model_a: a, model_b: b });
      setResults({});
      setRun({ duelId: r.duel_id, runIds: r.run_ids, mock: r.mock });
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) setRefusal(e.body as Refusal);
      else if (e instanceof ApiError && e.status === 429) push("info", "🎭 Shows are already running — max two at once.");
      else push("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const pa = run ? (results[run.runIds[0]]?.prediction as Prediction | undefined) : undefined;
  const pb = run ? (results[run.runIds[1]]?.prediction as Prediction | undefined) : undefined;

  return (
    <div className="pt-6 space-y-5">
      <header className="text-center">
        <h1 className="font-display text-4xl sm:text-5xl text-gold">⚔️ MODEL vs MODEL</h1>
        <p className="text-ink-dim mt-1">Same asset, same data, two brains. Who calls it better?</p>
      </header>
      {!run ? (
        <section className="card p-4 sm:p-6 space-y-5">
          <div className="flex flex-col lg:flex-row lg:items-center gap-3">
            <TickerSearch value={ticker} onPick={setTicker} />
            <PresetChips value={ticker} onPick={setTicker} />
          </div>
          <ModeHorizon
            mode={mode}
            horizon={horizon}
            onMode={(m) => {
              setMode(m);
              setHorizon(DEFAULT_HORIZON[m]);
            }}
            onHorizon={setHorizon}
          />
          <div className="grid md:grid-cols-2 gap-4">
            {(["A", "B"] as const).map((side) => (
              <div key={side} className="min-w-0">
                <h2 className="font-display text-xl mb-2" style={{ color: side === "A" ? "#38bdf8" : "#a78bfa" }}>
                  {side === "A" ? "🔵" : "🟣"} Model {side}
                </h2>
                <div role="radiogroup" aria-label={`Model ${side}`} className="flex gap-2 overflow-x-auto pb-2 pt-2 pr-2">
                  {list.map((m) => (
                    <ModelCard
                      key={m.id}
                      m={m}
                      mode={mode}
                      compact
                      accent={side === "A" ? "#38bdf8" : "#a78bfa"}
                      selected={(side === "A" ? a : b) === m.id}
                      onPick={() => (side === "A" ? setA(m.id) : setB(m.id))}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="flex flex-col items-center gap-2">
            <button
              onClick={start}
              disabled={!!reason || busy}
              className="font-display text-xl sm:text-2xl px-10 h-14 rounded-full text-stage bg-gradient-to-b from-gold to-amber-500 shadow-glow-gold disabled:from-surface-2 disabled:to-surface-2 disabled:text-ink-faint disabled:shadow-none"
            >
              ⚔️ START THE DUEL · {isMock ? "mock ฿0" : `est. ${thb(est)}`}
            </button>
            {reason ? <p className="text-sm text-bear">{reason}</p> : <p className="text-xs text-ink-faint">Both shows run at the same time; the budget guard checks both together.</p>}
          </div>
        </section>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <h2 className="font-display text-2xl">
              <span className="text-gold">{displayTicker(results[run.runIds[0]]?.meta?.ticker ?? ticker)}</span>{" "}
              <span className="text-ink-dim text-lg">· {mode} · {horizon}</span>
            </h2>
            <div className="flex-1" />
            <button onClick={() => setRun(null)} className="px-4 h-9 rounded-full border border-line bg-surface-2 text-sm font-semibold">
              🔁 New duel
            </button>
          </div>
          <div className="grid lg:grid-cols-2 gap-4">
            {run.runIds.map((rid, i) => (
              <DuelLane key={rid} runId={rid} side={i === 0 ? "A" : "B"} onState={(st) => setResults((r) => ({ ...r, [rid]: st }))} />
            ))}
          </div>
          {pa && pb ? (
            <HeadToHead a={pa} b={pb} usdThb={budget.data?.usd_thb ?? 33} mock={run.mock} />
          ) : (
            <p className="text-center text-ink-dim text-sm">The head-to-head appears when both verdicts are in…</p>
          )}
        </>
      )}
      <OutOfCoins refusal={refusal} onClose={() => setRefusal(null)} />
    </div>
  );
}
