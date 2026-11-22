# order_breaker_blocks

Order blocks from displacement breaks and breaker conversion (V5 §6.2-D).

**Settings:** pivots 3/3, `atr_length` 14, `min_body_atr` 0.50,
`ob_lookback` 12, `retest_decay` 0.70, `max_blocks` 20.

**Semantics**
- The first close beyond the latest confirmed pivot consumes that pivot
  (one block per pivot). If that bar is directional with body
  `>= 0.5 × ATR`, the most recent opposite-colored candle after the pivot
  bar (searching back at most 12 bars) becomes the block, spanning its full
  wick range. A candle never forms two blocks.
- OB score = 60·sat(breakout body/ATR, 2.0) + 40·sat(close beyond pivot /
  ATR, 1.0). First touch multiplies by 0.70.
- A close beyond the block's far edge converts it into an opposite-side
  BREAKER (score = 0.40 × remaining score + 60·sat(cross body/ATR));
  a later close back through the breaker removes it.

**ASSUMPTIONS**
- The breakout candle must close in the break direction.
- A weak break still consumes the pivot; a later strong break of the same
  pivot creates nothing.
- The opposing candle must start strictly after the pivot bar.
