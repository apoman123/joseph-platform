# order_flow_pressure

Estimated order-flow pressure from lower-timeframe candle direction
(V5 §6.2-E). Values are **estimates** (`method = "candle_direction"`),
never venue-measured taker delta; a trade-aggressor model must be a
separate plugin.

**Settings:** `intrabar_tf` 1m, `min_intrabars` 5, `min_coverage_pct` 80,
`baseline_bars` 20, `rel_volume_min` 1.5, `delta_min_pct` 20,
`wick_reversal_pct` 35, `breakout_body_pct` 50, `breakout_close_pct` 25.

**Semantics**
- Each intrabar's volume is up if close > open, down if close < open;
  `delta_pct = (up − down) / classified × 100`.
- Coverage = intrabar volume / bar volume. Fewer than 5 intrabars or
  coverage < 80 % → `delta_pct = None` and no events.
- Relative volume = bar volume / mean of the **previous** 20 bars.
- With RelVol ≥ 1.5 (inclusive):
  - `BUYER_FAILURE`: delta ≥ +20 and upper wick ≥ 35 % of range.
  - `SELLER_FAILURE`: delta ≤ −20 and lower wick ≥ 35 %.
  - `BUYER_BREAKOUT`: delta ≥ +20, bullish body ≥ 50 % of range, close
    within 25 % of the high, close above the prior 20-bar high.
  - `SELLER_BREAKOUT`: mirror.

**ASSUMPTIONS**
- A doji intrabar carries the previous intrabar's direction (also across
  bars); a doji with no prior direction stays unclassified.
- Breakouts additionally require a close beyond the prior 20-bar extreme.
