// REDESIGN §3.2 — original characters built from emoji + shapes.

export interface CastMember {
  id: string;
  name: string;
  role: string;
  emoji: string;
  color: string; // css var
}

export const CAST: Record<string, CastMember> = {
  business: { id: "business", name: "The CEO", role: "Business", emoji: "🏢", color: "var(--color-ceo)" },
  technical: { id: "technical", name: "Chart Wizard", role: "Technical", emoji: "📈", color: "var(--color-wizard)" },
  valuation: { id: "valuation", name: "The Accountant", role: "Valuation", emoji: "🧮", color: "var(--color-accountant)" },
  macro: { id: "macro", name: "The Economist", role: "Macro", emoji: "🌍", color: "var(--color-economist)" },
  psychology: { id: "psychology", name: "Mood Reader", role: "Psychology", emoji: "🧠", color: "var(--color-mood)" },
  news_researcher: { id: "news_researcher", name: "News Desk", role: "News", emoji: "📰", color: "var(--color-ink-dim)" },
  bull: { id: "bull", name: "Bull", role: "Debate", emoji: "🐂", color: "var(--color-bull)" },
  bear: { id: "bear", name: "Bear", role: "Debate", emoji: "🐻", color: "var(--color-bear)" },
  judge: { id: "judge", name: "The Oracle", role: "Judge (host)", emoji: "🔮", color: "var(--color-oracle)" },
};

export const ANALYSTS = ["business", "technical", "valuation", "macro", "psychology"];

export function member(id: string): CastMember {
  return CAST[id] ?? { id, name: id, role: id, emoji: "🤖", color: "var(--color-ink-dim)" };
}

export const TOOL_ICON: Record<string, string> = {
  get_price_history: "🕯️",
  get_indicators: "📡",
  get_fundamentals: "📑",
  get_macro: "🏦",
  get_news: "📰",
  get_sentiment: "🌡️",
  get_onchain_lite: "⛓️",
};

export const HORIZONS: Record<"swing" | "scalp", string[]> = {
  swing: ["1W", "1M", "3M"],
  scalp: ["15m", "1h", "4h"],
};

export const DISCLAIMER = "For fun — not investment advice";
