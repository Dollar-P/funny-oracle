import type { Mode } from "../lib/types";
import { HORIZONS } from "../lib/cast";

function Seg<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex p-1 rounded-full bg-surface border border-line">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={`px-4 h-9 rounded-full text-sm font-semibold transition-colors ${
              on ? "bg-oracle/25 text-ink shadow-glow" : "text-ink-dim hover:text-ink"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export default function ModeHorizon({
  mode,
  horizon,
  onMode,
  onHorizon,
}: {
  mode: Mode;
  horizon: string;
  onMode: (m: Mode) => void;
  onHorizon: (h: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
      <div className="flex items-center gap-3">
        <span className="text-sm text-ink-dim">Mode</span>
        <Seg
          label="Mode"
          value={mode}
          onChange={onMode}
          options={[
            { value: "swing", label: "🐢 Swing" },
            { value: "scalp", label: "⚡ Scalp" },
          ]}
        />
      </div>
      <div className="flex items-center gap-3">
        <span className="text-sm text-ink-dim">Horizon</span>
        <Seg label="Horizon" value={horizon} onChange={onHorizon} options={HORIZONS[mode].map((h) => ({ value: h, label: h }))} />
      </div>
      {mode === "scalp" && (
        <span className="text-xs text-ink-faint">Scalp: Chart Wizard, Economist & Mood Reader only · no debate · US stocks need market hours</span>
      )}
    </div>
  );
}
