# auction_value_areas

Candle-volume profiles with VAH / POC / VAL (V5 §6.2-F, uniform
approximation; a trade-based profile must be a separate `method=trade`
plugin).

**Settings:** `htf_bars` 24 (completed 1H), `chart_bars` 20, `bins` 32,
`value_area_pct` 70.

**Semantics**
- Profile range = min low … max high of the window, split into 32 bins.
  Each bar's volume is spread over the bins its high–low overlaps in
  proportion to the overlap; a zero-range bar puts its volume in the
  containing bin.
- POC = bin with the most volume (published at the bin midpoint). The value
  area grows from the POC toward the larger adjacent bin until it holds
  ≥ 70 % of volume; VAH/VAL are the outer bin edges.
- The chart profile uses the last 20 completed chart bars (including the
  bar that just closed) and updates every bar. The 1H profile uses the last
  24 completed 1H bars visible at the bar close and recomputes only when a
  new 1H bar completes (`VALUE_AREA_UPDATED`).
- Missing bars, gaps or zero total volume → values `None`.

**ASSUMPTIONS**
- POC ties go to the bin closest to the profile center, then the lower bin.
- Equal adjacent volumes expand upward first.
- Prices are rounded to 8 decimals for publication.
