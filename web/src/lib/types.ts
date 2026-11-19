// Mirrors core/schemas.py and api/events.py (REDESIGN §2.2)

export type Mode = "swing" | "scalp";
export type AssetType = "stock" | "crypto" | "gold";
export type Side = "bull" | "bear";
export type StageName = "news" | "analysts" | "aggregate" | "debate" | "judge" | "quant" | "done";

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  latency_ms: number;
  result_bytes: number;
  error: string | null;
}

export interface AgentReport {
  agent: string;
  score: number;
  confidence: number;
  key_points: string[];
  reasoning: string;
  sources: string[];
  trace: ToolCall[];
  cost_usd: number;
  error: string | null;
}

export interface DebateArgument {
  side: Side;
  turn: "opening" | "rebuttal" | "reply" | null;
  argument: string;
  strongest_opponent_point: string;
}

export interface Verdict {
  adjustment: number;
  final_confidence: number;
  headline: string;
  summary: string;
  key_risks: string[];
}

export interface Fan {
  step: number[];
  p10: number[];
  p50: number[];
  p90: number[];
}

export interface Forecast {
  price_now: number;
  p10: number;
  p50: number;
  p90: number;
  prob_up: number;
  sigma_per_step: number;
  steps: number;
  step_label: string;
  fan: Fan;
}

export interface Prediction {
  id: number | null;
  created_at: string;
  ticker: string;
  asset_type: AssetType;
  mode: Mode;
  horizon: string;
  target_time: string;
  s_raw: number;
  s_final: number;
  confidence: number;
  weights: Record<string, number>;
  reports: AgentReport[];
  news: { bullets: string[]; links: string[] } | null;
  debate: DebateArgument[];
  verdict: Verdict | null;
  forecast: Forecast;
  cost_usd: number;
  cost_actual_usd: number | null;
  elapsed_s: number;
  search_mode: string;
  models: Record<string, string>;
  run_id: string | null;
  duel_id: string | null;
}

interface Envelope {
  run_id: string;
  ts: number;
  seq: number;
  duel_id?: string | null;
}

export type RunEvent = Envelope &
  (
    | {
        type: "run_started";
        ticker: string;
        asset_type: AssetType;
        mode: Mode;
        horizon: string;
        models: Record<string, string>;
        agents: string[];
        weights: Record<string, number>;
        est_cost_thb: number;
        mock: boolean;
        replay_of?: number | null;
        budget_thb?: number;
      }
    | { type: "stage"; name: StageName }
    | { type: "agent_started"; agent: string }
    | { type: "tool_call"; agent: string; tool: string; args: Record<string, unknown> }
    | { type: "tool_result"; agent: string; tool: string; ms: number; bytes: number; ok: boolean; error?: string | null }
    | { type: "search"; agent: string; query: string }
    | { type: "news_brief"; bullets: string[]; links: string[] }
    | { type: "agent_done"; agent: string; report: AgentReport }
    | { type: "agent_failed"; agent: string; reason: "timeout" | "error"; detail?: string | null }
    | { type: "aggregate"; s_raw: number; weights: Record<string, number>; present: string[] }
    | { type: "debate_turn"; side: Side; turn: number; turn_name?: string; text: string; responding_to: string | null }
    | { type: "judge"; verdict: Verdict | null; s_raw: number; s_final: number; confidence: number }
    | {
        type: "quant";
        price_now: number;
        p10: number;
        p50: number;
        p90: number;
        prob_up: number;
        fan: Fan;
        steps: number;
        step_label: string;
        target_time: string;
      }
    | {
        type: "cost_update";
        run_cost_thb: number;
        run_cost_actual_thb?: number;
        total_spent_thb: number;
        total_actual_thb?: number;
        budget_thb: number;
      }
    | { type: "run_done"; prediction_id: number | null; prediction: Prediction; mock?: boolean }
    | { type: "error"; message: string; fatal: boolean; kind?: string }
  );

export type EventOf<T extends RunEvent["type"]> = Extract<RunEvent, { type: T }>;

export interface ModelInfo {
  id: string;
  provider: "gemini" | "claude";
  label: string;
  price_in: number;
  price_out: number;
  price_search: number;
  speed_tier: 1 | 2 | 3;
  avg_elapsed_s: Partial<Record<Mode, number>>;
  supports_search: boolean | null;
  est_cost_thb: Record<Mode, number>;
  is_default: boolean;
}

export interface TickerHit {
  symbol: string;
  name: string;
  asset_type: AssetType;
  source: string;
}

export interface Quote {
  symbol: string;
  asset_type: AssetType;
  price: number;
  prev_close: number;
  change_pct: number;
  sparkline: number[];
  as_of: string;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Budget {
  budget_thb: number;
  usd_thb: number;
  spent_thb: number;
  estimated_actual_thb: number;
  remaining_thb: number;
  basis: "tracked" | "actual";
  gemini_searches_this_month: number;
  gemini_free_searches_left: number;
  mock: boolean;
}

export interface PredictionRow {
  id: number;
  run_id: string | null;
  created_at: string;
  ticker: string;
  asset_type: AssetType;
  mode: Mode;
  horizon: string;
  target_time: string;
  price_now: number;
  p10: number;
  p50: number;
  p90: number;
  prob_up: number;
  s_final: number;
  confidence: number;
  headline: string | null;
  cost_usd: number;
  cost_actual_usd: number | null;
  source: string;
  actual_price: number | null;
  evaluated_at: string | null;
  analyst_model: string | null;
  judge_model: string | null;
  duel_id: string | null;
}

export interface Metrics {
  n: number;
  pending: number;
  hit_rate?: number;
  n_directional?: number;
  band_coverage?: number;
  mape?: number;
  baseline_hit_rate?: number;
  actual_up_share?: number;
  leaderboard?: { agent: string; hit_rate: number; n: number }[];
}

export interface ModelRow {
  model: string;
  n: number;
  n_evaluated: number;
  hit_rate: number | null;
  band_coverage: number | null;
  avg_cost_thb: number;
  avg_time_s: number;
  duels: number;
  duel_wins: number;
  duel_win_rate: number | null;
}

export interface Scoreboard {
  metrics: Metrics;
  models: ModelRow[];
  pending: {
    id: number;
    ticker: string;
    mode: Mode;
    horizon: string;
    created_at: string;
    target_time: string;
    price_now: number;
    p50: number;
    prob_up: number;
    analyst_model: string | null;
  }[];
  evaluated_now: number;
  now: string;
}

export interface AutoScalpStatus {
  running: boolean;
  ticker: string;
  mode: Mode;
  horizon: string;
  runs: number;
  max_runs: number;
  runs_left: number;
  interval_s: number;
  seconds_to_next: number | null;
  stopped_reason: string | null;
  log: string[];
  mock_blocked: boolean;
}

export interface DuelSide {
  p50: number;
  p10: number;
  p90: number;
  prob_up: number;
  price_now: number;
  confidence: number;
  s_final: number;
  cost_usd: number;
  elapsed_s: number;
  headline: string | null;
  direction: "bull" | "bear" | "flat";
}

export interface DuelInfo {
  duel_id: string;
  meta: {
    run_ids: string[];
    models: Record<string, string>[];
    mock: boolean;
    ticker: string;
    mode: Mode;
    horizon: string;
  } | null;
  status: string[];
  predictions: (Prediction | null)[];
  comparison: {
    a: DuelSide;
    b: DuelSide;
    agents: { agent: string; a: number | null; b: number | null }[];
    agree: boolean;
    banner: string;
    p50_gap_pct: number;
  } | null;
}
