import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pro, type Settings } from "../api";
import { Badge, Btn, Empty, Panel } from "../ui";

type Draft = Settings;

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="grid grid-cols-1 items-center gap-1 py-1.5 text-[12.5px] sm:grid-cols-[13rem_1fr]">
      <span className="text-desk-dim">{label}</span>
      <span className="min-w-0">{children}{hint && <span className="ml-2 text-[11px] text-desk-faint">{hint}</span>}</span>
    </label>
  );
}

const input = "w-full max-w-[18rem] rounded border border-desk-line bg-desk-panel-2 px-2 py-1 text-[12.5px] num";

function NumIn({ v, on, step = "any" }: { v: number; on: (n: number) => void; step?: string }) {
  return <input type="number" step={step} value={Number.isFinite(v) ? v : ""} onChange={(e) => on(parseFloat(e.target.value))} className={input} />;
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: pro.settings });
  const [d, setD] = useState<Draft | null>(null);
  const [wl, setWl] = useState("");
  const [msg, setMsg] = useState<{ tone: "up" | "warn" | "down"; text: string } | null>(null);
  useEffect(() => {
    if (q.data) {
      setD(structuredClone(q.data.settings));
      setWl(q.data.settings.watchlist.map((w) => `${w.ticker}:${w.asset_type}:${w.horizons.join("+")}`).join("\n"));
    }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => {
      const watchlist = wl.split(/\n|,/).map((l) => l.trim()).filter(Boolean).map((l) => {
        const [ticker, asset_type = "stock", h = "1M"] = l.split(":");
        return { ticker: ticker.toUpperCase(), asset_type, horizons: h.split("+") };
      });
      const { halted: _h, account_reset: _a, ...rest } = d!;
      void _h; void _a;
      return pro.putSettings({ ...rest, watchlist });
    },
    onSuccess: (r) => {
      setMsg(r.new_experiment ? { tone: "warn", text: `Saved. Results-affecting parameters changed → new experiment #${r.experiment_id} (a fresh paper account).` }
        : { tone: "up", text: "Saved (same experiment)." });
      ["settings", "overview", "signals", "scheduler"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    },
    onError: (e: Error) => setMsg({ tone: "down", text: e.message }),
  });
  const reset = useMutation({
    mutationFn: pro.reset,
    onSuccess: (r) => {
      setMsg({ tone: "warn", text: `Paper account reset → experiment #${r.experiment_id}.` });
      qc.invalidateQueries();
    },
  });
  if (!d || !q.data) return <Empty>Loading…</Empty>;
  const set = (patch: Partial<Draft>) => setD({ ...d, ...patch });
  const risk = d.risk as Record<string, number>;
  const cls = d.risk.max_class as Record<string, number>;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="space-y-4">
        <Panel title="Watchlist & schedule">
          <Field label="Watchlist" hint="one per line: TICKER:class:horizons">
            <textarea aria-label="Watchlist" value={wl} onChange={(e) => setWl(e.target.value)} rows={8} className={`${input} max-w-[22rem] font-mono`} />
          </Field>
          <Field label="Stocks run" hint="minutes after the NYSE close"><NumIn v={Number(d.schedule.stock_after_close_min)} on={(n) => set({ schedule: { ...d.schedule, stock_after_close_min: n } })} /></Field>
          <Field label="Crypto run (UTC)"><input className={input} value={String(d.schedule.crypto_utc)} onChange={(e) => set({ schedule: { ...d.schedule, crypto_utc: e.target.value } })} /></Field>
          <Field label="Gold run" hint="minutes after the CME close"><NumIn v={Number(d.schedule.gold_after_close_min)} on={(n) => set({ schedule: { ...d.schedule, gold_after_close_min: n } })} /></Field>
          <Field label="Scalp loop" hint="crypto, Gemini only — off by default">
            <input type="checkbox" checked={Boolean(d.schedule.scalp_enabled)} onChange={(e) => set({ schedule: { ...d.schedule, scalp_enabled: e.target.checked } })} />
          </Field>
          <Field label="Local window"><input className={input} value={d.local_window} onChange={(e) => set({ local_window: e.target.value })} /></Field>
          <Field label="Local time zone" hint="empty = this machine"><input className={input} value={d.local_tz} onChange={(e) => set({ local_tz: e.target.value })} /></Field>
        </Panel>
        <Panel title="Models per role">
          <Field label="Swing (primary)"><input className={input} value={d.models.swing} onChange={(e) => set({ models: { ...d.models, swing: e.target.value } })} /></Field>
          <Field label="Think preset (local)">
            <select className={input} value={d.models.swing_think_preset} onChange={(e) => set({ models: { ...d.models, swing_think_preset: e.target.value } })}>
              <option value="quick">quick</option><option value="deep">deep</option>
            </select>
          </Field>
          <Field label="Swing fallback"><input className={input} value={d.models.swing_fallback} onChange={(e) => set({ models: { ...d.models, swing_fallback: e.target.value } })} /></Field>
          <Field label="Scalp" hint="Gemini only"><input className={input} value={d.models.scalp} onChange={(e) => set({ models: { ...d.models, scalp: e.target.value } })} /></Field>
          <Field label="Reviewer"><input className={input} value={d.models.reviewer} onChange={(e) => set({ models: { ...d.models, reviewer: e.target.value } })} /></Field>
        </Panel>
      </div>
      <div className="space-y-4">
        <Panel title="Approval & budgets">
          <Field label="Approval mode">
            <select className={input} value={d.approval} onChange={(e) => set({ approval: e.target.value as "manual" | "auto" })}>
              <option value="manual">manual (default)</option><option value="auto">auto (paper)</option>
            </select>
          </Field>
          <Field label="Daily LLM budget (฿)"><NumIn v={d.budgets.daily_thb} on={(n) => set({ budgets: { daily_thb: n } })} /></Field>
        </Panel>
        <Panel title="Risk & cost parameters" actions={<Badge tone="warn">changes start a new experiment</Badge>}>
          <Field label="Risk per trade (swing)"><NumIn v={risk.risk_pct_swing} on={(n) => set({ risk: { ...d.risk, risk_pct_swing: n } })} /></Field>
          <Field label="Risk per trade (scalp)"><NumIn v={risk.risk_pct_scalp} on={(n) => set({ risk: { ...d.risk, risk_pct_scalp: n } })} /></Field>
          <Field label="Max position"><NumIn v={risk.max_position} on={(n) => set({ risk: { ...d.risk, max_position: n } })} /></Field>
          <Field label="Max gross exposure"><NumIn v={risk.max_gross} on={(n) => set({ risk: { ...d.risk, max_gross: n } })} /></Field>
          <Field label="Max per class (stock/crypto/gold)">
            <span className="flex gap-1">
              {(["stock", "crypto", "gold"] as const).map((k) => (
                <input key={k} aria-label={`max ${k}`} type="number" step="any" value={cls[k]} className={`${input} w-20`}
                  onChange={(e) => set({ risk: { ...d.risk, max_class: { ...cls, [k]: parseFloat(e.target.value) } } })} />
              ))}
            </span>
          </Field>
          <Field label="Max open positions"><NumIn step="1" v={risk.max_open} on={(n) => set({ risk: { ...d.risk, max_open: n } })} /></Field>
          <Field label="Daily loss limit"><NumIn v={risk.daily_loss} on={(n) => set({ risk: { ...d.risk, daily_loss: n } })} /></Field>
          <Field label="Max drawdown breaker"><NumIn v={risk.max_dd} on={(n) => set({ risk: { ...d.risk, max_dd: n } })} /></Field>
          <Field label="S min · conf min · R:R min">
            <span className="flex gap-1">
              {(["s_min", "conf_min", "rr_min"] as const).map((k) => (
                <input key={k} aria-label={k} type="number" step="any" value={Number(d.signal[k])} className={`${input} w-20`}
                  onChange={(e) => set({ signal: { ...d.signal, [k]: parseFloat(e.target.value) } })} />
              ))}
            </span>
          </Field>
          {(["stock", "crypto", "gold"] as const).map((k) => (
            <Field key={k} label={`Costs ${k} (fee · slippage bps)`}>
              <span className="flex gap-1">
                <input aria-label={`${k} fee`} type="number" step="any" value={d.costs[k].fee} className={`${input} w-20`}
                  onChange={(e) => set({ costs: { ...d.costs, [k]: { ...d.costs[k], fee: parseFloat(e.target.value) } } })} />
                <input aria-label={`${k} slippage`} type="number" step="any" value={d.costs[k].slippage} className={`${input} w-20`}
                  onChange={(e) => set({ costs: { ...d.costs, [k]: { ...d.costs[k], slippage: parseFloat(e.target.value) } } })} />
              </span>
            </Field>
          ))}
        </Panel>
        <Panel title="Save">
          <div className="flex flex-wrap items-center gap-2">
            <Btn tone="accent" onClick={() => save.mutate()} disabled={save.isPending}>Save settings</Btn>
            <Btn tone="danger" onClick={() => { if (window.confirm("Reset the paper account? This starts a new experiment with fresh cash; history stays in the old experiment.")) reset.mutate(); }}>
              Reset paper account
            </Btn>
            <span className="text-[11px] text-desk-faint">experiment #{q.data.experiment_id} · {q.data.experiment_hash}</span>
          </div>
          {msg && <div role="status" className={`mt-2 text-[12px] ${msg.tone === "up" ? "text-desk-up" : msg.tone === "warn" ? "text-desk-warn" : "text-desk-down"}`}>{msg.text}</div>}
          {d.halted && <div className="mt-2 text-[12px] text-desk-down">The max-drawdown breaker is tripped. Only a reset re-enables new trades.</div>}
        </Panel>
      </div>
    </div>
  );
}
