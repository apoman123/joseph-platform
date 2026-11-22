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
| `order_flow_pressure` | estimated delta, buyer/seller failure & breakout | chart + `intrabars:1m` |
| `auction_value_areas` | VAH / POC / VAL (chart and 1H profiles), 1m–30m charts | chart + `aux:1h` |
| `premium_discount_ote` | dealing range, premium / discount / EQ / OTE (LuxAlgo, CC BY-NC-SA 4.0) | chart |

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
- The engine supplies higher-timeframe bars ending at or before the chart bar's
  close; the library then applies Pine `lookahead_on` + `[1]` visibility.
- Prices leave the library as floats and become decimals rounded to the tick's
  precision plus four places.
- AOI ids are `stable_id(plugin, instrument, timeframe, library zone id[:kind])`,
  so batch, streaming and replay agree; a kind change (FVG → IFVG, OB →
  breaker) ends the old AOI as `inverted`.

## Adding an indicator

See `docs/EXTENDING.md` → "Add or change an indicator".
