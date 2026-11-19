import type {
  AutoScalpStatus,
  Budget,
  Candle,
  DuelInfo,
  Mode,
  ModelInfo,
  Prediction,
  PredictionRow,
  Quote,
  Scoreboard,
  TickerHit,
} from "./types";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
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
    const b = body as { detail?: unknown; reason?: string } | null;
    const msg =
      (b && (b.reason || (typeof b.detail === "string" ? b.detail : null))) || `${r.status} ${r.statusText}`;
    throw new ApiError(r.status, msg, body);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  req<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export interface StartRun {
  ticker: string;
  mode: Mode;
  horizon: string;
  analyst_model?: string | null;
  judge_model?: string | null;
  mock?: boolean;
}

export const api = {
  health: () => req<{ ok: boolean; mock: boolean }>("/health"),
  models: () =>
    req<{ models: ModelInfo[]; default: Record<string, string>; default_scalp: Record<string, string>; mock: boolean }>(
      "/models",
    ),
  searchTickers: (q: string) => req<{ results: TickerHit[]; presets: string[] }>(`/tickers/search?q=${encodeURIComponent(q)}`),
  quote: (t: string) => req<Quote>(`/quote/${encodeURIComponent(t)}`),
  history: (t: string, interval: string, period?: string, limit = 300) =>
    req<{ symbol: string; interval: string; candles: Candle[] }>(
      `/history/${encodeURIComponent(t)}?interval=${interval}${period ? `&period=${period}` : ""}&limit=${limit}`,
    ),
  startPrediction: (b: StartRun) => post<{ run_id: string; mock: boolean }>("/predictions", b),
  replay: (id: number) => post<{ run_id: string; mock: boolean }>(`/predictions/${id}/replay`),
  predictions: (params: { limit?: number; ticker?: string; mode?: string; model?: string } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return req<{ predictions: PredictionRow[] }>(`/predictions?${q}`);
  },
  prediction: (id: number) =>
    req<{ prediction: Prediction; actual_price: number | null; evaluated_at: string | null; source: string }>(
      `/predictions/${id}`,
    ),
  run: (id: string) => req<{ run_id: string; done: boolean; prediction_id: number | null; error: string | null }>(`/runs/${id}`),
  startDuel: (b: { ticker: string; mode: Mode; horizon: string; model_a: string; model_b: string; mock?: boolean }) =>
    post<{ duel_id: string; run_ids: [string, string]; mock: boolean }>("/duels", b),
  duel: (id: string) => req<DuelInfo>(`/duels/${id}`),
  scoreboard: (evaluate = true) => req<Scoreboard>(`/scoreboard?evaluate=${evaluate}`),
  budget: () => req<Budget>("/budget"),
  autoscalpStatus: () => req<AutoScalpStatus>("/autoscalp/status"),
  autoscalpStart: () => post<AutoScalpStatus>("/autoscalp/start"),
  autoscalpStop: () => post<AutoScalpStatus>("/autoscalp/stop"),
};
