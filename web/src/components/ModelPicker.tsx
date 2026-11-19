import { useState } from "react";
import type { Mode, ModelInfo } from "../lib/types";
import { thb } from "../lib/format";

const SPEED_LABEL = { 3: "fast", 2: "steady", 1: "slower" } as const;

export function ModelCard({
  m,
  mode,
  selected,
  onPick,
  accent = "var(--color-gold)",
  compact = false,
}: {
  m: ModelInfo;
  mode: Mode;
  selected: boolean;
  onPick: () => void;
  accent?: string;
  compact?: boolean;
}) {
  const est = m.est_cost_thb[mode];
  const obs = m.avg_elapsed_s[mode];
  return (
    <button
      role="radio"
      aria-checked={selected}
      onClick={onPick}
      className={`card text-left p-3 sm:p-4 transition-all relative ${compact ? "min-w-[150px]" : "min-w-[170px]"} ${
        selected ? "bg-surface-2" : "hover:bg-surface-2 opacity-90"
      }`}
      style={selected ? { borderColor: accent, boxShadow: `0 0 0 1px ${accent}, 0 0 24px color-mix(in srgb, ${accent} 30%, transparent)` } : undefined}
    >
      {selected && (
        <span className="absolute -top-2 -right-2 w-6 h-6 rounded-full grid place-items-center text-xs text-stage font-bold" style={{ background: accent }}>
          ✓
        </span>
      )}
      <div className="font-semibold text-sm leading-tight">{m.label}</div>
      <div className="mt-2 text-sm" title={obs ? `observed avg ${obs}s per ${mode} run` : "no runs observed yet — guessed from the model family"}>
        <span aria-hidden="true">{"⚡".repeat(m.speed_tier)}</span>
        <span className="opacity-20 grayscale" aria-hidden="true">{"⚡".repeat(3 - m.speed_tier)}</span>
        <span className="ml-1.5 text-ink-dim">{SPEED_LABEL[m.speed_tier]}{obs ? ` · ~${Math.round(obs)}s` : ""}</span>
      </div>
      <div className="mt-1 num text-sm" title="estimate: this model's recent average × 1.25, or a default scaled by price">
        ~{thb(est, est < 1 ? 2 : 1)} <span className="text-ink-faint font-sans">/ run</span>
      </div>
      {!compact && (
        <div className="mt-1 text-xs text-ink-dim">
          {m.supports_search === true ? "🔎 search ✓" : m.supports_search === false ? "🔎 news tool only" : "🔎 search: probed on 1st run"}
        </div>
      )}
    </button>
  );
}

export default function ModelPicker({
  models,
  mode,
  analyst,
  judge,
  onAnalyst,
  onJudge,
}: {
  models: ModelInfo[];
  mode: Mode;
  analyst: string | null;
  judge: string | null;
  onAnalyst: (id: string) => void;
  onJudge: (id: string | null) => void;
}) {
  const [adv, setAdv] = useState(judge !== null);
  const provider = models.find((m) => m.id === analyst)?.provider;
  const judges = models.filter((m) => m.provider === provider);
  return (
    <div>
      <div role="radiogroup" aria-label="Oracle brain (analyst model)" className="flex gap-3 overflow-x-auto pb-2 pt-2 pr-2">
        {models.map((m) => (
          <ModelCard
            key={m.id}
            m={m}
            mode={mode}
            selected={analyst === m.id}
            onPick={() => {
              onAnalyst(m.id);
              const jm = models.find((x) => x.id === judge);
              if (jm && jm.provider !== m.provider) onJudge(null);
            }}
          />
        ))}
      </div>
      <button
        className="mt-2 text-sm text-ink-dim hover:text-ink"
        aria-expanded={adv}
        onClick={() => {
          if (adv) onJudge(null);
          setAdv(!adv);
        }}
      >
        {adv ? "▾" : "▸"} Advanced: separate Judge model
      </button>
      {adv && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <label htmlFor="judge-model" className="text-ink-dim">
            🔮 Judge / Bull / Bear:
          </label>
          <select
            id="judge-model"
            className="card px-3 h-9 bg-surface text-sm"
            value={judge ?? ""}
            onChange={(e) => onJudge(e.target.value || null)}
          >
            <option value="">Same as analysts</option>
            {judges.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
