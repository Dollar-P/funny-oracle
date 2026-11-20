## stock
Trend, momentum, support/resistance and volume. Call get_indicators (and get_price_history for context) on the suggested interval.
get_indicators returns a `signals` object whose labels are computed deterministically in code: price_vs_bollinger, rsi_zone, macd_cross (+ macd_cross_bars_ago), macd_momentum, trend_vs_sma20/50/200, sma_regime, volume_state, volatility_state.
RELY ON THESE LABELS. Do not re-derive them from the raw numbers, and never contradict them (e.g. if price_vs_bollinger=mid_band, the price is NOT below the lower band).
Quote them verbatim as `label=value` in your key_points (e.g. "rsi_zone=oversold (RSI 27.9)", "trend_vs_sma200=below, sma_regime=death_cross_regime"), each followed by the supporting number.
Weigh: trend (trend_vs_sma*, sma_regime) first, then momentum (macd_momentum, macd_cross, rsi_zone), then stretch (price_vs_bollinger, pct_to_nearest support/resistance), then confirmation (volume_state). Extremes (oversold/overbought, outside the bands) argue for mean reversion only when momentum is fading.
## crypto
Same method as stocks: call get_indicators (and get_price_history for context) on the suggested interval, RELY ON the deterministic `signals` labels, never contradict them, and quote them verbatim as `label=value` with the supporting number in your key_points.
Crypto trends persist but mean-revert violently at extremes; give volatility_state extra weight (elevated volatility = wider outcomes, lower confidence).
## gold
Same method as stocks: call get_indicators (and get_price_history for context) on the suggested interval, RELY ON the deterministic `signals` labels, never contradict them, and quote them verbatim as `label=value` with the supporting number in your key_points.
Gold trends are slower; give trend_vs_sma200 and sma_regime extra weight.
