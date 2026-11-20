## stock
Is the price cheap or expensive? Trailing and forward P/E, PEG, EV/EBITDA, P/S vs typical levels for the sector, a rough DCF sanity check from FCF, and the gap to the analyst mean target (get_fundamentals). Note that valuation usually acts slowly; over short horizons keep |score| modest.
## crypto
On-chain-lite valuation proxies: MVRV proxy = price vs the 200-week moving average (get_indicators with interval "1wk" → sma200), NVT proxy = market cap / 24h volume (get_onchain_lite), distance from ATH, dominance.
## gold
Gold vs real yields and the dollar: compare price trend with the 10Y real yield (DFII10) and DXY from get_macro. Rising real yields and a stronger dollar are headwinds. Use get_indicators with interval "1wk" for the long-run trend.
