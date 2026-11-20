import { useState } from "react";
import type { Mode, ModelInfo, ThinkPreset } from "../lib/types";
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
  const reason = blockedReason(m, mode);
  const local = m.provider === "local";
  return (
    <button
      role="radio"
      aria-checked={selected}
      aria-disabled={!!reason}
      onClick={reason ? undefined : onPick}
      title={reason ?? undefined}
      className={`card text-left p-3 sm:p-4 transition-all relative ${compact ? "min-w-[150px]" : "min-w-[170px]"} ${
        reason ? "opacity-45 cursor-not-allowed" : selected ? "bg-surface-2" : "hover:bg-surface-2 opacity-90"
      }`}
      style={selected ? { borderColor: accent, boxShadow: `0 0 0 1px ${accent}, 0 0 24px color-mix(in srgb, ${accent} 30%, transparent)` } : undefined}
    >
      {selected && (
        <span className="absolute -top-2 -right-2 w-6 h-6 rounded-full grid place-items-center text-xs text-stage font-bold" style={{ background: accent }}>
          ✓
        </span>
      )}
      <div className="font-semibold text-sm leading-tight">{m.label}</div>
      {local && (
        <span className="inline-block mt-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-bull/15 text-bull border border-bull/40">
          free · local
        </span>
      )}
      <div className="mt-2 text-sm" title={obs ? `observed avg ${obs}s per ${mode} run` : "no runs observed yet — guessed from the model family"}>
        <span aria-hidden="true">{"⚡".repeat(m.speed_tier)}</span>
        <span className="opacity-20 grayscale" aria-hidden="true">{"⚡".repeat(3 - m.speed_tier)}</span>
        <span className="ml-1.5 text-ink-dim">
          {local ? "slow" : SPEED_LABEL[m.speed_tier]}
          {obs ? ` · ~${obs >= 120 ? `${Math.round(obs / 60)} min` : `${Math.round(obs)}s`}` : local ? " · ~8–10 min (Quick)" : ""}
        </span>
      </div>
      <div className="mt-1 num text-sm" title="estimate: this model's recent average × 1.25, or a default scaled by price">
        {local ? <span className="text-bull">฿0</span> : <>~{thb(est, est < 1 ? 2 : 1)}</>} <span className="text-ink-faint font-sans">/ run</span>
      </div>
      {!compact && (
        <div className="mt-1 text-xs text-ink-dim">
          {local ? "🔎 own web_search tool" : m.supports_search === true ? "🔎 search ✓" : m.supports_search === false ? "🔎 news tool only" : "🔎 search: probed on 1st run"}
        </div>
      )}
      {reason && <div className="mt-1 text-[11px] text-gold leading-snug">{reason.split(" (")[0].split(". ")[0]}</div>}
    </button>
  );
}

/** Why a model can't be picked right now (or null). */
export function blockedReason(m: ModelInfo, mode: Mode): string | null {
  if (m.available === false) return m.unavailable_reason ?? "Not available";
  if (mode === "scalp" && m.scalp_ok === false) return m.scalp_reason ?? "Not available for Scalp";
  return null;
}

/** Quick / Deep thinking preset for local models (LOCAL_LLM.md). */
export function ThinkToggle({ value, onChange }: { value: ThinkPreset; onChange: (p: ThinkPreset) => void }) {
  const opts: { v: ThinkPreset; label: string; hint: string }[] = [
    { v: "quick", label: "⚡ Quick", hint: "thinking off everywhere · ~8–10 min" },
    { v: "deep", label: "🧠 Deep", hint: "thinking on for synthesis, debate and verdict · ~20–30 min" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="think-preset">
      <span className="text-ink-dim">Local thinking</span>
      <div role="radiogroup" aria-label="Local thinking preset" className="inline-flex p-1 rounded-full bg-surface border border-line">
        {opts.map((o) => (
          <button
            key={o.v}
            role="radio"
            aria-checked={value === o.v}
            onClick={() => onChange(o.v)}
            title={o.hint}
            className={`px-4 h-8 rounded-full font-semibold transition-colors ${value === o.v ? "bg-oracle/25 text-ink shadow-glow" : "text-ink-dim hover:text-ink"}`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <span className="text-xs text-ink-faint">{opts.find((o) => o.v === value)!.hint}</span>
    </div>
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
