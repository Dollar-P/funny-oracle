// SSE → reducer → show state. EventSource reconnects by itself and sends Last-Event-ID;
// the server replays buffered events, and the reducer drops duplicates by seq.
import { useEffect, useReducer, useRef } from "react";
import { initialShow, reduce, type ShowState } from "../state/show";
import type { RunEvent } from "../lib/types";

const TYPES: RunEvent["type"][] = [
  "run_started", "stage", "agent_started", "tool_call", "tool_result", "search", "news_brief", "agent_done",
  "agent_failed", "aggregate", "debate_turn", "judge", "quant", "cost_update", "run_done", "error",
];

type Action = { kind: "reset"; runId: string | null } | { kind: "event"; ev: RunEvent } | { kind: "lost" };

function reducer(s: ShowState, a: Action): ShowState {
  if (a.kind === "reset") return initialShow(a.runId);
  if (a.kind === "lost")
    return s.status === "done" ? s : { ...s, status: "error", error: { message: "Lost the show — the server may have restarted.", fatal: true } };
  if (a.ev.run_id !== s.runId) return s;
  return reduce(s, a.ev);
}

export function useRunStream(runId: string | null, onEvent?: (ev: RunEvent, state: ShowState) => void): ShowState {
  const [state, dispatch] = useReducer(reducer, runId, initialShow);
  const cb = useRef(onEvent);
  cb.current = onEvent;
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    dispatch({ kind: "reset", runId });
    if (!runId) return;
    const es = new EventSource(`/api/runs/${runId}/events`);
    let finished = false;
    let failures = 0;
    const handler = (e: MessageEvent) => {
      failures = 0;
      const ev = JSON.parse(e.data) as RunEvent;
      dispatch({ kind: "event", ev });
      cb.current?.(ev, stateRef.current);
      if (ev.type === "run_done" || (ev.type === "error" && ev.fatal)) {
        finished = true;
        es.close(); // otherwise EventSource would reconnect after the server ends the stream
      }
    };
    TYPES.forEach((t) => es.addEventListener(t, handler as EventListener));
    es.onerror = () => {
      if (finished) return;
      failures += 1;
      // EventSource retries on its own; a 404 (run gone after a server restart) closes it for good.
      if (es.readyState === EventSource.CLOSED || failures > 8) {
        es.close();
        dispatch({ kind: "lost" });
      }
    };
    return () => es.close();
  }, [runId]);

  return state;
}
