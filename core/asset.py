"""Classify a ticker into stock / crypto / gold."""
from __future__ import annotations

from typing import Literal

AssetType = Literal["stock", "crypto", "gold"]

GOLD_TICKERS = {"GC=F", "GLD", "IAU", "XAUUSD=X", "XAU-USD", "GOLD"}

# yfinance symbol prefix → CoinGecko id
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "TRX": "tron",
}

ALIASES = {"GOLD": "GC=F", "BTC": "BTC-USD", "ETH": "ETH-USD"}


def normalize(ticker: str) -> str:
    t = ticker.strip().upper()
    return ALIASES.get(t, t)


def classify(ticker: str) -> AssetType:
    t = normalize(ticker)
    if t in GOLD_TICKERS:
        return "gold"
    if t.endswith("-USD") or t.endswith("-USDT") or t in COINGECKO_IDS:
        return "crypto"
    return "stock"


def coingecko_id(coin: str) -> str:
    """Accepts 'BTC-USD', 'BTC', or a CoinGecko id like 'bitcoin'."""
    c = coin.strip()
    sym = c.upper().split("-")[0]
    return COINGECKO_IDS.get(sym, c.lower())
