// SSE events → show state. Pure reducer so Arena, Duel (compact) and Replay share it.
import type {
  AgentReport,
  AssetType,
  EventOf,
  Fan,
  Mode,
  Prediction,
  RunEvent,
  Side,
  StageName,
  Verdict,
} from "../lib/types";
import { args as fmtArgs } from "../lib/format";

export type PodiumStatus = "idle" | "thinking" | "tool" | "done" | "absent";

export interface Bubble {
  kind: "tool" | "search";
  text: string;
  tool?: string;
  ok: boolean | null; // null = in flight
  ms?: number;
}

export interface TraceItem {
  kind: "tool" | "search";
  tool: string;
  text: string;
  ok: boolean | null;
  ms?: number;
  bytes?: number;
  ts: number;
}

export interface AgentState {
  id: string;
  status: PodiumStatus;
  bubble: Bubble | null;
  trace: TraceItem[];
  report: AgentReport | null;
  failReason: string | null;
  failDetail: string | null;
  startedAt: number | null;
  doneAt: number | null;
}

export interface TapeItem {
  seq: number;
  ts: number;
  agent: string | null;
  icon: string;
  text: string;
  ok: boolean | null;
}

export interface DebateTurnState {
  side: Side;
  turn: number;
  text: string;
  respondingTo: string | null;
  ts: number;
}

export interface ShowState {
  runId: string | null;
  status: "idle" | "connecting" | "live" | "done" | "error";
  lastSeq: number;
  meta: {
    ticker: string;
    assetType: AssetType;
    mode: Mode;
    horizon: string;
    models: Record<string, string>;
    agents: string[];
    weights: Record<string, number>;
    estCostThb: number;
    mock: boolean;
    replayOf: number | null;
    duelId: string | null;
    startedTs: number;
    createdAt: string | null; // prediction time the chart is anchored to
    shifted: boolean; // mock show moved to "now"
    rescaled: boolean | null;
  } | null;
  stage: StageName | null;
  stagesSeen: StageName[];
  agents: Record<string, AgentState>;
  tape: TapeItem[];
  news: { bullets: string[]; links: string[] } | null;
  sRaw: number | null;
  present: string[];
  debate: DebateTurnState[];
  verdict: Verdict | null;
  sFinal: number | null;
  confidence: number | null;
  judgeTs: number | null;
  quant: { price_now: number; p10: number; p50: number; p90: number; prob_up: number; fan: Fan; steps: number; step_label: string; target_time: string; created_at?: string } | null;
  cost: { run: number; runActual: number | null; total: number; totalActual: number | null; budget: number } | null;
  prediction: Prediction | null;
  predictionId: number | null;
  error: { message: string; fatal: boolean; kind?: string } | null;
  doneTs: number | null;
}

export const initialShow = (runId: string | null = null): ShowState => ({
  runId,
  status: runId ? "connecting" : "idle",
  lastSeq: 0,
  meta: null,
  stage: null,
  stagesSeen: [],
  agents: {},
  tape: [],
  news: null,
  sRaw: null,
  present: [],
  debate: [],
  verdict: null,
  sFinal: null,
  confidence: null,
  judgeTs: null,
  quant: null,
  cost: null,
  prediction: null,
  predictionId: null,
  error: null,
  doneTs: null,
});

const TAPE_MAX = 200;

export function quoteOnce(q: string): string {
  const t = q.trim();
  return /^["“]/.test(t) ? t : `"${t}"`;
}

function agentOf(s: ShowState, id: string): AgentState {
  return (
    s.agents[id] ?? {
      id,
      status: "idle",
      bubble: null,
      trace: [],
      report: null,
      failReason: null,
      failDetail: null,
      startedAt: null,
      doneAt: null,
    }
  );
}

function withAgent(s: ShowState, id: string, f: (a: AgentState) => AgentState): ShowState {
  return { ...s, agents: { ...s.agents, [id]: f(agentOf(s, id)) } };
}

function tape(s: ShowState, ev: RunEvent, agent: string | null, icon: string, text: string, ok: boolean | null = null): ShowState {
  const item: TapeItem = { seq: ev.seq, ts: ev.ts, agent, icon, text, ok };
  return { ...s, tape: [...s.tape, item].slice(-TAPE_MAX) };
}

export function reduce(s: ShowState, ev: RunEvent): ShowState {
  if (ev.seq <= s.lastSeq) return s; // replayed duplicate (reconnect)
  s = { ...s, lastSeq: ev.seq, status: s.status === "connecting" || s.status === "idle" ? "live" : s.status };
  switch (ev.type) {
    case "run_started": {
      const agents: Record<string, AgentState> = {};
      ev.agents.forEach((a) => (agents[a] = agentOf(s, a)));
      s = {
        ...s,
        agents,
        meta: {
          ticker: ev.ticker,
          assetType: ev.asset_type,
          mode: ev.mode,
          horizon: ev.horizon,
          models: ev.models,
          agents: ev.agents,
          weights: ev.weights,
          estCostThb: ev.est_cost_thb,
          mock: ev.mock,
          replayOf: ev.replay_of ?? null,
          duelId: ev.duel_id ?? null,
          startedTs: ev.ts,
          createdAt: ev.created_at ?? null,
          shifted: ev.mock ? ev.shifted !== false : false,
          rescaled: ev.rescaled ?? null,
        },
      };
      return tape(s, ev, null, "🎬", `Show starts: ${ev.ticker} · ${ev.mode} · ${ev.horizon}${ev.mock ? " (mock replay)" : ""}`);
    }
    case "stage": {
      const seen = s.stagesSeen.includes(ev.name) ? s.stagesSeen : [...s.stagesSeen, ev.name];
      return { ...s, stage: ev.name, stagesSeen: seen };
    }
    case "agent_started":
      s = withAgent(s, ev.agent, (a) => ({ ...a, status: "thinking", startedAt: ev.ts }));
      return tape(s, ev, ev.agent, "🎤", "takes the podium");
    case "tool_call": {
      const text = `${ev.tool}(${fmtArgs(ev.args)})`;
      s = withAgent(s, ev.agent, (a) => ({
        ...a,
        status: a.status === "done" ? a.status : "tool",
        bubble: { kind: "tool", text, tool: ev.tool, ok: null },
        trace: [...a.trace, { kind: "tool", tool: ev.tool, text, ok: null, ts: ev.ts }],
      }));
      return tape(s, ev, ev.agent, "📡", text);
    }
    case "tool_result": {
      s = withAgent(s, ev.agent, (a) => {
        const trace = [...a.trace];
        for (let i = trace.length - 1; i >= 0; i--) {
          if (trace[i].kind === "tool" && trace[i].tool === ev.tool && trace[i].ok === null) {
            trace[i] = { ...trace[i], ok: ev.ok, ms: ev.ms, bytes: ev.bytes };
            break;
          }
        }
        const bubble = a.bubble && a.bubble.tool === ev.tool ? { ...a.bubble, ok: ev.ok, ms: ev.ms } : a.bubble;
        return { ...a, trace, bubble, status: a.status === "tool" ? "thinking" : a.status };
      });
      // update the matching tape line in place: "… 412 ms ✓"
      const t = [...s.tape];
      for (let i = t.length - 1; i >= 0; i--) {
        if (t[i].agent === ev.agent && t[i].ok === null && t[i].text.startsWith(ev.tool + "(")) {
          t[i] = { ...t[i], ok: ev.ok, text: `${t[i].text} ${Math.round(ev.ms)} ms` };
          break;
        }
      }
      return { ...s, tape: t };
    }
    case "search": {
      const q = quoteOnce(ev.query);
      s = withAgent(s, ev.agent, (a) => ({
        ...a,
        bubble: { kind: "search", text: q, ok: true },
        trace: [...a.trace, { kind: "search", tool: "search", text: ev.query, ok: true, ts: ev.ts }],
      }));
      return tape(s, ev, ev.agent, "🔎", q, true);
    }
    case "news_brief":
      s = { ...s, news: { bullets: ev.bullets, links: ev.links } };
      return tape(s, ev, "news_researcher", "📰", `News brief: ${ev.bullets.length} headlines`, true);
    case "agent_done":
      s = withAgent(s, ev.agent, (a) => ({ ...a, status: "done", report: ev.report, bubble: null, doneAt: ev.ts }));
      return tape(s, ev, ev.agent, "✅", `verdict ${ev.report.score >= 0 ? "+" : ""}${ev.report.score.toFixed(2)} (conf ${ev.report.confidence.toFixed(2)})`, true);
    case "agent_failed":
      s = withAgent(s, ev.agent, (a) => ({
        ...a,
        status: "absent",
        bubble: null,
        failReason: ev.reason,
        failDetail: ev.detail ?? null,
        doneAt: ev.ts,
      }));
      return tape(s, ev, ev.agent, "⚠️", `absent (${ev.reason})`, false);
    case "aggregate":
      s = { ...s, sRaw: ev.s_raw, present: ev.present };
      return tape(s, ev, null, "🪢", `Tug-of-war: S_raw ${ev.s_raw >= 0 ? "+" : ""}${ev.s_raw.toFixed(2)}`);
    case "debate_turn":
      s = {
        ...s,
        debate: [...s.debate, { side: ev.side, turn: ev.turn, text: ev.text, respondingTo: ev.responding_to, ts: ev.ts }],
      };
      return tape(s, ev, ev.side, ev.side === "bull" ? "🐂" : "🐻", `turn ${ev.turn}: ${ev.text.slice(0, 80)}${ev.text.length > 80 ? "…" : ""}`);
    case "judge":
      s = { ...s, verdict: ev.verdict, sFinal: ev.s_final, confidence: ev.confidence, judgeTs: ev.ts };
      return tape(s, ev, "judge", "🔮", ev.verdict ? `"${ev.verdict.headline}"` : "The Oracle abstains — team score stands");
    case "quant":
      s = {
        ...s,
        quant: {
          price_now: ev.price_now,
          p10: ev.p10,
          p50: ev.p50,
          p90: ev.p90,
          prob_up: ev.prob_up,
          fan: ev.fan,
          steps: ev.steps,
          step_label: ev.step_label,
          target_time: ev.target_time,
          created_at: ev.created_at,
        },
      };
      return tape(s, ev, null, "🎲", `Monte Carlo: P50 ${ev.p50.toFixed(2)} · P(up) ${(ev.prob_up * 100).toFixed(0)}%`);
    case "cost_update":
      return {
        ...s,
        cost: {
          run: ev.run_cost_thb,
          runActual: ev.run_cost_actual_thb ?? null,
          total: ev.total_spent_thb,
          totalActual: ev.total_actual_thb ?? null,
          budget: ev.budget_thb,
        },
      };
    case "run_done":
      s = { ...s, status: "done", prediction: ev.prediction, predictionId: ev.prediction_id, doneTs: ev.ts };
      return tape(s, ev, null, "🏁", ev.prediction_id ? `Saved as #${ev.prediction_id}` : "Show over");
    case "error":
      s = { ...s, error: { message: ev.message, fatal: ev.fatal, kind: ev.kind }, status: ev.fatal ? "error" : s.status };
      return tape(s, ev, null, "🛑", ev.message, false);
  }
  return s;
}

/** Build a finished show directly from a stored prediction (Replay page, no streaming). */
export function showFromPrediction(p: Prediction): ShowState {
  const agents: Record<string, AgentState> = {};
  p.reports.forEach((r) => {
    agents[r.agent] = {
      id: r.agent,
      status: r.error ? "absent" : "done",
      bubble: null,
      trace: r.trace.map((t) => ({
        kind: t.tool === "google_search" || t.tool === "web_search" ? "search" : "tool",
        tool: t.tool,
        text: `${t.tool}(${fmtArgs(t.args)})`,
        ok: t.error === null,
        ms: t.latency_ms,
        bytes: t.result_bytes,
        ts: 0,
      })),
      report: r.error ? null : r,
      failReason: r.error ? (r.error.startsWith("timeout") ? "timeout" : "error") : null,
      failDetail: r.error,
      startedAt: null,
      doneAt: null,
    };
  });
  const f = p.forecast;
  return {
    ...initialShow(null),
    status: "done",
    meta: {
      ticker: p.ticker,
      assetType: p.asset_type,
      mode: p.mode,
      horizon: p.horizon,
      models: p.models ?? {},
      agents: p.reports.map((r) => r.agent),
      weights: p.weights,
      estCostThb: 0,
      mock: false,
      replayOf: p.id,
      duelId: p.duel_id ?? null,
      startedTs: Date.parse(p.created_at),
      createdAt: p.created_at,
      shifted: false,
      rescaled: null,
    },
    stage: "done",
    stagesSeen: ["news", "analysts", "aggregate", "debate", "judge", "quant", "done"],
    agents,
    news: p.news,
    sRaw: p.s_raw,
    present: p.reports.filter((r) => !r.error).map((r) => r.agent),
    debate: p.debate.map((d, i) => ({
      side: d.side,
      turn: i + 1,
      text: d.argument,
      respondingTo: i > 0 ? d.strongest_opponent_point : null,
      ts: 0,
    })),
    verdict: p.verdict,
    sFinal: p.s_final,
    confidence: p.confidence,
    judgeTs: 0,
    quant: { ...f, target_time: p.target_time, created_at: p.created_at },
    prediction: p,
    predictionId: p.id,
  };
}

export type { EventOf };
