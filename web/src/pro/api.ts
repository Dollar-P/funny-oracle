// Typed client for /api/pro (PRO.md §8). Paper trading only.
import { ApiError } from "../lib/api";

export interface Metric {
  value: number | null;
  n: number;
  lo: number | null;
  hi: number | null;
  sufficient: boolean;
  label: "ok" | "insufficient data";
  note?: string;
}

export type SignalStatus = "flat" | "rejected" | "new" | "approved" | "filled" | "closed" | "cancelled" | "expired";

export interface Signal {
  id: number;
  prediction_id: number | null;
  experiment_id: number;
  created_at: string;
  ticker: string;
  asset_type: string;
  mode: string;
  horizon: string;
  direction: "long" | "short" | "flat";
  side: "long" | "short" | null;
  conviction: number;
  entry: { type: string; ref_price: number } | null;
  stop: number;
  targets: number[] | null;
  time_exit: string;
  reward_risk: number;
  ev_r: number;
  size: { qty: number; notional: number; risk_amount: number; risk_pct: number; capped_by: string[] } | null;
  thesis: string;
  invalidation: string;
  flat_reason: string | null;
  flat_detail: string | null;
  fails: string[];
  status: SignalStatus;
  status_reason: string | null;
  p_win: number;
  calibrated: boolean;
  cost_r: number;
  entry_deadline: string | null;
  model: string | null;
  think_preset: string | null;
  prompt_version: string | null;
  inputs: Record<string, number | string | null>;
}

export interface AgentRow {
  agent: string;
  score: number;
  confidence: number;
  ok: boolean;
  error: string | null;
  view: string;
  invalidation: string | null;
  weight: number;
}

export interface SignalDetail {
  signal: Signal;
  risk_events: { ts: string; rule: string; detail: string }[];
  position: Record<string, unknown> & { review?: { post_mortem: string; thesis_held: boolean | null; lesson: string } | null } | null;
  fills: { id: number; reason: string; side: string; ts: string; price: number; qty: number; fee: number }[];
  prediction?: {
    id: number;
    created_at: string;
    target_time: string;
    s_raw: number;
    s_final: number;
    confidence: number;
    forecast: { price_now: number; p10: number; p50: number; p90: number; prob_up: number; fan: Record<string, number[]> };
    models: Record<string, string>;
    think_preset: string | null;
    prompt_version: string;
    elapsed_s: number;
    degraded: string[];
    cost_thb: number;
    cost_actual_thb: number;
    thesis: string | null;
    invalidation: string | null;
    catalysts: { date: string; event: string; impact: string }[];
    headline: string | null;
    key_risks: string[];
    no_trade_reason: string | null;
    agents: AgentRow[];
  };
  snapshots?: { id: number; tool: string; as_of: string }[];
}

export interface Position {
  id: number;
  signal_id: number;
  ticker: string;
  asset_type: string;
  side: "long" | "short";
  qty: number;
  qty_open: number;
  state: "pending" | "open";
  opened_at: string | null;
  entry: number | null;
  entry_ref: number | null;
  stop: number;
  stop_initial: number;
  targets: number[];
  t1_done: boolean;
  mark: number | null;
  time_exit: string;
  time_left_h: number;
  horizon: string | null;
  unrealized_pnl?: number;
  r_now?: number;
  to_stop_pct?: number;
  to_target_pct?: number;
  notional?: number;
}

export interface JournalRow {
  id: number;
  signal_id: number;
  ticker: string;
  side: string;
  qty: number;
  opened_at: string;
  closed_at: string;
  avg_entry: number;
  avg_exit: number;
  realized_pnl: number;
  r_multiple: number | null;
  mae_r: number | null;
  mfe_r: number | null;
  costs: number;
  exit_reason: string;
  holding_h: number;
  tags: string[];
  post_mortem: string | null;
  thesis_held: boolean | null;
  lesson: string | null;
  thesis: string | null;
}

export interface Overview {
  experiment_id: number;
  equity: number;
  cash: number;
  starting_equity: number;
  day_pnl: number;
  day_pnl_pct: number;
  total_return: number;
  drawdown: number;
  gross_exposure_pct: number;
  open_risk_pct: number;
  open_positions: number;
  pending_entries: number;
  sharpe: Metric;
  halted: boolean;
  daily_loss_tripped_today: boolean;
  breaker_events: { rule: string; ts: string; detail: string }[];
  spent_today_thb: number;
  daily_budget_thb: number;
  pro_spent_total_thb: number;
  combined_spent_thb: number;
  combined_budget_thb: number;
  approval: "manual" | "auto";
  signal_counts: Record<string, number>;
  scheduler: { running: boolean; last_tick: string | null };
  risk_limits: { max_dd: number; daily_loss: number };
  as_of: string;
}

export interface SchedulerStatus {
  running: boolean;
  last_tick: string | null;
  local_available: boolean;
  local_window: string;
  in_local_window: boolean;
  window_end: string | null;
  approval: string;
  spent_today_thb: number;
  daily_budget_thb: number;
  halted: boolean;
  last_error: string | null;
  recent: { job_key: string; status: string; model: string | null; think_preset: string | null; detail: string; created_at: string }[];
}

export type Series = [string, number][];

export interface Settings {
  watchlist: { ticker: string; asset_type: string; horizons: string[] }[];
  schedule: Record<string, unknown>;
  models: { swing: string; swing_think_preset: string; swing_fallback: string; scalp: string; reviewer: string };
  local_window: string;
  local_tz: string;
  budgets: { daily_thb: number };
  approval: "manual" | "auto";
  starting_equity: number;
  account_reset: number;
  halted: boolean;
  prompt_version: string;
  temperature: number;
  risk: Record<string, number | Record<string, number>>;
  costs: Record<string, { fee: number; slippage: number }>;
  signal: Record<string, number | boolean>;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Report = any;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/pro${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await r.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!r.ok) {
    const b = body as { detail?: unknown } | null;
    throw new ApiError(r.status, typeof b?.detail === "string" ? b.detail : `${r.status} ${r.statusText}`, body);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  req<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export const pro = {
  overview: () => req<Overview>("/overview"),
  equity: () => req<{ equity: Series; benchmarks: { buy_and_hold: Series; cash: Series }; starting_equity: number }>("/equity"),
  signals: (q: { status?: string; ticker?: string; mode?: string } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v && p.set(k, v));
    p.set("limit", "100");
    return req<{ signals: Signal[]; counts: Record<string, number> }>(`/signals?${p}`);
  },
  signal: (id: number) => req<SignalDetail>(`/signals/${id}`),
  approve: (id: number) => post<{ signal: Signal }>(`/signals/${id}/approve`),
  reject: (id: number, reason = "manual") => post<{ signal: Signal }>(`/signals/${id}/reject`, { reason }),
  positions: () => req<{ positions: Position[] }>("/positions"),
  orders: () => req<{ orders: Record<string, string | number | null>[] }>("/orders"),
  fills: () => req<{ fills: Record<string, string | number | null>[] }>("/fills"),
  journal: () => req<{ journal: JournalRow[] }>("/journal"),
  setTags: (id: number, tags: string[]) => post<{ tags: string[] }>(`/journal/${id}/tags`, { tags }),
  risk: () => req<Report>("/risk"),
  scheduler: () => req<SchedulerStatus>("/scheduler/status"),
  schedulerStart: () => post<SchedulerStatus>("/scheduler/start"),
  schedulerStop: () => post<SchedulerStatus>("/scheduler/stop"),
  settings: () => req<{ settings: Settings; experiment_id: number; experiment_hash: string; experiment_keys: string[] }>("/settings"),
  putSettings: (patch: Partial<Settings>) =>
    req<{ settings: Settings; experiment_id: number; new_experiment: boolean }>("/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  reset: () => post<{ experiment_id: number }>("/account/reset"),
  latestReport: () => req<{ id?: number; created_at?: string; report: Report | null; markdown?: string }>("/evaluation/latest"),
  runEvaluation: () => post<{ status: string }>("/evaluation/run", { n_random: 1000 }),
  evaluationStatus: () => req<{ busy: boolean; error: string | null }>("/evaluation/status"),
  backtests: () => req<{ backtests: { id: number; kind: string; status: string; period: string; created_at: string }[] }>("/backtests"),
  bars: (ticker: string, assetType: string) =>
    req<{ candles: { t: number; o: number; h: number; l: number; c: number }[] }>(
      `/bars/${encodeURIComponent(ticker)}?asset_type=${assetType}&limit=120`,
    ),
};
