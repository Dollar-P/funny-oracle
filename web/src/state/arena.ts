import { create } from "zustand";
import type { Mode } from "../lib/types";

// Setup choices + the current run. Kept in sessionStorage so a reload resumes the show
// (the server replays every buffered event).
interface ArenaState {
  ticker: string;
  mode: Mode;
  horizon: string;
  analystModel: string | null;
  judgeModel: string | null; // null = same as analyst
  runId: string | null;
  set: (p: Partial<Omit<ArenaState, "set">>) => void;
}

const KEY = "oracle.arena";

function load(): Partial<ArenaState> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

export const DEFAULT_HORIZON: Record<Mode, string> = { swing: "1M", scalp: "15m" };

export const useArena = create<ArenaState>((set, get) => ({
  ticker: "NVDA",
  mode: "swing",
  horizon: "1M",
  analystModel: null,
  judgeModel: null,
  runId: null,
  ...load(),
  set: (p) => {
    set(p);
    const { ticker, mode, horizon, analystModel, judgeModel, runId } = { ...get(), ...p };
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ ticker, mode, horizon, analystModel, judgeModel, runId }));
    } catch {
      /* ignore */
    }
  },
}));
