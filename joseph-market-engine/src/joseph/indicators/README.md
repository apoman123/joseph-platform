# Indicators (market engine, private)

Backend-authoritative SMC indicators. The terminal only renders these
outputs; it never recomputes a tradable signal.

**The algorithms are not here.** They live in the private library
`apoman123/PineScripts/python_indicators` (`joseph-smc-indicators`, import
`smc_indicators`), a line-by-line port of the Pine scripts, pinned by commit in
`pyproject.toml`. This package adapts it to the plugin SDK (ADR 0009).

| Plugin | Publishes | Needs |
|---|---|---|
| `daily_bias` | BOS / CHoCH / initial-break events on this timeframe, `trend`, 1D/4H/1H `bias`, PDH/PDL, NY sessions | chart + `aux:1d/4h/1h` |
| `market_structure` | HH/HL/LH/LL/EQH/EQL labels, BOS / MSS (as CHoCH) / initial breaks, unbroken SWING_HIGH / SWING_LOW | chart |
| `fvg_ifvg` | FVG / IFVG zones with heat scores, first-test decay, inversion | chart |
| `order_breaker_blocks` | OB / breaker zones | chart |
| `liquidity_sweeps` | BSL / SSL / EQH / EQL pools, sweeps vs breaks | chart |
| `order_flow_pressure` | true delta from venue taker volume (`method: taker_volume`, snapshot method `trade`), stock-exchange buy volume classified against the quote (`method: trade_classification`, snapshot method `trade_classification`) or the Pine candle-direction estimate; buyer/seller failure & breakout | chart + `intrabars:auto` (1s / 1m / 5m / 1h by chart) |
| `auction_value_areas` | VAH / POC / VAL (chart and 1H profiles), 1s–30m charts | chart + `aux:1h` |
| `premium_discount_ote` | dealing range, premium / discount / EQ / OTE (LuxAlgo, CC BY-NC-SA 4.0) | chart |
| `anchored_vwap` | VWAP + 3 stdev bands from a session/week/month or an anchor bar (Python-native study) | chart |
| `regression_channel` | least-squares channel, rolling or from an anchor bar, Pearson's R (Python-native study) | chart |

The two Python-native studies follow TradingView's built-in VWAP and
regression-channel definitions; they are not Pine ports. A series of an
anchored study (requested alone) is computed from the anchor bar onward, up
to `QueryConfig.max_anchored_bars` (50,000; `JOSEPH_MAX_ANCHORED_BARS`)
closed bars back; a more distant anchor is refused with a validation error.
Anchored series are kept incrementally between requests, so a re-request
after a bar closes only feeds the new bar. On exchange calendars the VWAP's
`session` / `week` / `month` anchors are the calendar's `1d` / `1w` / `1M`
bars.

Every bar boundary (nesting, higher-timeframe visibility, missing bars,
anchor bars) follows the instrument's session calendar
(`joseph.domain.sessions.for_instrument`).

`licensing_status = PINE_PORT_PENDING_REVIEW`, `parity_status = SPEC_ONLY`
until TradingView exports are compared (`golden.py`,
`docs/reports/PINE_SOURCE_REVISION.md`).

## Layout

- `sdk/` – `IndicatorPlugin` protocol, `BasePlugin`, settings, state codec,
  `IndicatorEngine` (multi-timeframe, watermark-gated, no look-ahead), registry.
- `packaged/bridge.py` – `PackagedPlugin`: shared live objects for the newest
  state, pickled base every 32 bars + bar-input log so any state can be rebuilt
  (pure transitions, checkpoints), zone → `Aoi` identity, ended-state inference.
- `packaged/plugins.py` – one adapter per indicator (settings = Pine inputs and
  bounds; value mapping onto the contract in `joseph/domain/structures.py`).
- `packaged/codec.py` – allowlisted unpickler for checkpoints.
- `packaged/convert.py` – candle → library bar, float → decimal at the boundary.
- `ticks.py` – `TickSizeSource` port; engines inject the instrument tick as the
  `tick_size` setting (Pine `syminfo.mintick`).
- `service.py` – `IndicatorQueryService` (snapshot, AOIs, per-bar series) with
  per-request settings.
- `golden.py` – scenario fixtures and the Pine-export parity harness.
- `testing.py` – deterministic candle builders for every context's tests.

## Conventions

- Bars are closed `[start_ms, end_ms)` candles; events confirm at `end_ms`.
  Timeframes run from `1s` (built from venue trades) to `1M` (calendar
  month); step between bars with `Timeframe.shift`/`end_of`, never
  `duration_ms` (an upper bound for `1M`).
- `required_streams(settings, timeframe=...)` receives the series timeframe so
  a plugin can choose intrabars that nest (`order_flow_pressure` `auto`).
- The engine supplies higher-timeframe bars ending at or before the chart bar's
  close; the library then applies Pine `lookahead_on` + `[1]` visibility.
- Prices leave the library as floats and become decimals rounded to the tick's
  precision plus four places.
- AOI ids are `stable_id(plugin, instrument, timeframe, library zone id[:kind])`,
  so batch, streaming and replay agree; a kind change (FVG → IFVG, OB →
  breaker) ends the old AOI as `inverted`.

## Adding an indicator

See `docs/EXTENDING.md` → "Add or change an indicator".
