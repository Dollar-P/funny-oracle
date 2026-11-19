import type { AssetType } from "./types";

// Same rules as core/asset.py (display only; the server is authoritative)
const GOLD = new Set(["GC=F", "GLD", "IAU", "XAUUSD=X", "XAU-USD", "GOLD"]);
const COINS = new Set(["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "LTC", "TRX"]);

export function classifyTicker(t: string): AssetType {
  const s = t.trim().toUpperCase();
  if (GOLD.has(s)) return "gold";
  if (s.endsWith("-USD") || s.endsWith("-USDT") || COINS.has(s)) return "crypto";
  return "stock";
}

export function displayTicker(t: string): string {
  if (t === "GC=F") return "GOLD";
  return t.replace(/-USD$/, "");
}
