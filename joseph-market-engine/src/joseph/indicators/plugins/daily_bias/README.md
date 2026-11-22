# daily_bias

Confirmed market structure per timeframe, prior-day levels and NY sessions
(V5 §6.2-A). Run one series per timeframe (e.g. 1d, 4h, 1h); the strategy
`BIAS` node combines their `trend` values with closed-bar visibility.

**Settings:** `pivot_left` = 3, `pivot_right` = 3.

**Semantics**
- A close above the latest confirmed, not-yet-broken pivot high is a
  bullish break: `INITIAL_BREAK_BULL` when the trend is neutral, `BOS_BULL`
  with a bullish trend, `CHOCH_BULL` against a bearish trend. Mirror for
  lows. Each pivot triggers at most once.
- PDH/PDL are the previous **UTC** calendar day's high/low (crypto perps
  trade 24/7; the instrument's daily candle is the UTC day). Published as
  line AOIs from the first bar of the new day.
- Sessions use America/New_York local time (DST aware via `zoneinfo`):
  Asia 20:00–24:00, London 02:00–05:00, New York 08:30–11:00, half-open, by
  bar start.

**Values:** `trend`, `last_event`, `last_event_direction`,
`last_pivot_high`, `last_pivot_low`, `pdh`, `pdl`, `session`.

**ASSUMPTIONS**
- If the previous calendar day has no bars, PDH/PDL are `None` (never a
  stale older day). Weekly series therefore never publish PDH/PDL.
- When one bar breaks both pivots, the bullish break is evaluated first.
- The Pine original targets ≤5m charts; here each timeframe is a separate
  series, removing that UI limitation.
