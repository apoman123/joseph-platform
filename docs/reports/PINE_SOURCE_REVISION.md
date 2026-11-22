# Pine source revision record

| Item | Value |
|---|---|
| Pine baseline named in V5 | `apoman123/PineScripts` @ `cf1ea56` |
| Pine HEAD ported (2026-11-22) | `4091278` ("new indicator": adds `smc_current_timeframe_structure.pine`, revises the daily-bias dashboard) |
| Python library | `apoman123/PineScripts/python_indicators` (`joseph-smc-indicators`, import `smc_indicators`) @ `89aa506` |
| Platform pin | `joseph-market-engine/pyproject.toml` → `[tool.uv.sources]` git `rev` (locked in `uv.lock`) |
| Method | Line-by-line port: result of closed bar k = Pine state on bar k with `barstate.isconfirmed`. Plugins report `licensing_status: PINE_PORT_PENDING_REVIEW`, `parity_status: SPEC_ONLY` until TradingView exports are compared |

The platform contains **no indicator logic** any more ("平台歸平台，指標歸指標").
`joseph/indicators/packaged/` holds only adapters: settings (same defaults and
bounds as the Pine inputs), data streams, decimal conversion, AOI identity and
the published-values contract. Removed: the seven clean-room plugins and
`sdk/ta.py`.

## Script → library → plugin

| Pine script | Library class | Plugin id | Streams |
|---|---|---|---|
| `smc_current_timeframe_structure.pine` | `market_structure.MarketStructure` | `market_structure` (new) | chart |
| `smc_daily_bias_dashboard.pine` | `daily_bias.StructureTracker` + `DailyBiasDashboard` | `daily_bias` | chart, aux 1D/4H/1H |
| `smc_fvg_ifvg.pine` | `fvg_ifvg.FvgIfvg` | `fvg_ifvg` | chart |
| `smc_order_breaker_blocks.pine` | `order_breaker_blocks.OrderBreakerBlocks` | `order_breaker_blocks` | chart |
| `smc_liquidity_sweeps.pine` | `liquidity_sweeps.LiquiditySweeps` | `liquidity_sweeps` | chart |
| `smc_order_flow_pressure.pine` | `order_flow_pressure.OrderFlowPressure` | `order_flow_pressure` | chart, intrabars (1m default) |
| `smc_auction_value_areas.pine` | `auction_value_areas.AuctionValueAreas` | `auction_value_areas` (1m–30m charts) | chart, aux 1H |
| `smc_premium_discount_ote_zones.pine` | `premium_discount_ote.PremiumDiscountOte` | `premium_discount_ote` | chart |
| `joseph_hot_perpetual_filter.pine` | `hot_perpetual_filter.HotPerpetualPrefilter` | — (screening implements the full 5-gate job) | — |

## Behavior changes versus the clean-room plugins

Golden scenarios were re-derived by hand from the Pine source and now match it:

| Area | Clean-room (old) | Pine (now) |
|---|---|---|
| Structure breaks | First close beyond a pivot | Needs **both** a pivot high and low known; each pivot breaks once; high side checked first |
| FVG closed through | `AOI_INVALIDATED` + IFVG | Inversion only (`IFVG_FORMED`; the FVG AOI ends `inverted`) |
| Dealing-range continuation | Low reset to the swing low | Low reset to the pullback extreme tracked **after** the direction exists |
| Value-area window | Any bars | Every profiled bar needs volume > 0; `chart_bars` ≥ 5; current bar excluded |
| PDH/PDL, sessions | UTC day | Previous 1D bar; sessions in America/New_York (Asia 20:00–00:00, London 02:00–05:00, NY 08:30–11:00) |
| HTF visibility | Bar end ≤ chart close | Engine supplies bars ending ≤ close; the library applies Pine `lookahead_on` + `[1]` (visible from the next chart bar) |

## Decided rules

- `ta.pivothigh/pivotlow`: an equal neighbour disqualifies a pivot (`pine.PIVOT_TIES_DISQUALIFY = True`). Confirmed by the owner on 2026-11-22: exactly equal neighbouring highs/lows are not pivots.

## Recorded assumptions (verify against TradingView exports)

- Values Pine computes only on `barstate.islast` (chart value area, OTE table) are published on every bar as if that bar were last.
- `na` comparisons in the daily-bias agreement follow Pine (`na != 0` true).
- Further per-script notes: `python_indicators/docs/<module>.md` in PineScripts.

## To reach `GOLDEN_VERIFIED`

1. Export per-bar values/events from TradingView for a pinned instrument and range into `joseph-market-engine/fixtures/indicators/pine_exports/{venue}/{symbol}/{tf}/{range}.jsonl` (format in `golden.py`).
2. Run `uv run pytest tests/replay -k parity`; the harness reports the first divergent bar.
3. Fix divergences in PineScripts, bump the platform `rev`, then set `parity_status` to `GOLDEN_VERIFIED`.

The OTE script and its port carry the LuxAlgo CC BY-NC-SA 4.0 notice; see `LICENSING_REPORT.md`.
