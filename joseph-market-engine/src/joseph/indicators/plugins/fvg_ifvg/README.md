# fvg_ifvg

Three-candle fair value gaps and inversions (V5 §6.2-B).

**Settings:** `atr_length` 14, `min_gap_atr` 0.10, `tick_size` 0,
`retest_decay` 0.70, `max_zones` 20, `expire_bars` 0 (never).

**Semantics**
- Bullish FVG when `low[0] > high[2]` (zone `[high[2], low[0]]`), bearish
  when `high[0] < low[2]`; strict, so touching wicks form no gap. Confirmed
  at the third bar's close (`created_at` = first bar start).
- Gap height must be `>= tick_size` and `>= min_gap_atr × ATR` (ATR
  includes the confirming bar). No gap before ATR is seeded.
- Midline = 50 %. The first wick to the midline marks the zone `tested`
  and multiplies the score by 0.70 (once).
- Only a **close** beyond the far edge inverts an FVG into an IFVG of the
  opposite side (wicks never do). An IFVG closing back through its far
  edge is invalidated and removed.
- Formation score = 55·sat(gap/ATR) + 30·sat(middle body/ATR) +
  15·close location of the middle candle in the gap direction.
  IFVG score = 40·sat(original gap/ATR) + 60·sat(crossing body/ATR).

**ASSUMPTIONS**
- Saturation points: gap/ATR at 1.0, middle body/ATR at 1.5, crossing
  body/ATR at 1.0.
- The midline-retest decay also applies to IFVGs.
- When inversion and midline retest happen on one bar, only the inversion
  is applied.
- Beyond `max_zones` the oldest zone expires (`AOI_EXPIRED`).
