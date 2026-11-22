# liquidity_sweeps

Resting liquidity at confirmed swings and sweeps (V5 §6.2-C).

**Settings:** `pivot_left`/`pivot_right` 3, `atr_length` 14,
`equal_tolerance_atr` 0.10, `tick_size` 0, `max_pools` 30 per side.

**Semantics**
- Confirmed swing highs create BSL pools, swing lows SSL pools (line AOIs).
  A new swing within `tolerance = equal_tolerance_atr × ATR` of a live
  same-side pool merges into it as EQH/EQL (level = the more extreme
  price, `pivot_count` increments).
- A bar whose wick penetrates a pool by at least `max(tick, tolerance)` and
  closes back inside is a **sweep** (`BSL_SWEEP` / `SSL_SWEEP`, pool
  `swept`). Closing beyond is a **break** (`*_BREAK`, pool invalidated),
  never a sweep. Smaller penetrations leave the pool untouched.
- One bar may sweep several pools; each gets its own event. Pools exist
  only after confirmation, so a pivot bar cannot sweep its own pool.
- Sweep score = 50·sat(penetration/ATR) + 50·sat(reclaim/ATR).

**ASSUMPTIONS**
- Swing length 3/3 (the spec does not state one).
- A close exactly at the level is neither a sweep nor a break; the pool
  stays live.
- BSL/EQH AOIs carry `side = bearish` (resting above price; a sweep is a
  bearish reversal cue), SSL/EQL `side = bullish`.
- Without ATR (warmup) the tolerance is 0, so only exact equals merge.
