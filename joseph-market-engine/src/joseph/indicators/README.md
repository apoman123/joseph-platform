# Indicator adapters

This package adapts the market engine's indicator library to the plugin SDK. It is for contributors adding or changing an indicator; the terminal only renders the outputs.

**The algorithms are not here.** They live in the private library `smc_indicators` (`apoman123/PineScripts`, `python_indicators/`): line-by-line Python ports of Pine scripts, pinned in the market engine's `pyproject.toml` ([ADR 0009](../../../../docs/adr/0009-external-indicator-library.md)).

## Plugins

| Plugin | Publishes | Needs |
|---|---|---|
| `daily_bias` | BOS / CHoCH / initial-break events, `trend`, 1D/4H/1H `bias`, PDH/PDL, New York sessions | chart + `aux:1d/4h/1h` |
| `market_structure` | HH/HL/LH/LL/EQH/EQL, BOS / MSS (as CHoCH) / initial breaks, unbroken SWING_HIGH / SWING_LOW | chart |
| `fvg_ifvg` | FVG / IFVG zones with heat scores, first-test decay, inversion | chart |
| `order_breaker_blocks` | Order-block (OB) and breaker zones | chart |
| `liquidity_sweeps` | BSL / SSL / EQH / EQL pools, sweeps vs breaks | chart |
| `order_flow_pressure` | Delta: true from taker volume (`method: taker_volume`, snapshot `trade`), classified stock buy volume (`trade_classification`) or the Pine candle estimate; buyer/seller failure and breakout | chart + `intrabars:auto` (1s / 1m / 5m / 1h by chart) |
| `auction_value_areas` | VAH / POC / VAL (chart and 1H profiles), 1s–30m charts | chart + `aux:1h` |
| `premium_discount_ote` | Dealing range, premium / discount / EQ / OTE (derived from LuxAlgo, CC BY-NC-SA 4.0: no commercial use while enabled) | chart |
| `anchored_vwap` | VWAP + 3 standard-deviation bands from a session, week, month or anchor bar | chart |
| `regression_channel` | Least-squares channel, rolling or from an anchor bar, Pearson's R | chart |

- The first eight are Pine ports. `anchored_vwap` and `regression_channel` are Python-native studies in the same library that follow TradingView's built-in definitions.
- An anchored series (requested alone) runs from the anchor bar, at most `JOSEPH_MAX_ANCHORED_BARS` (default 50,000) bars back, else the request fails validation; it is kept incrementally between requests. On exchange calendars the `session` / `week` / `month` anchors are the calendar's `1d` / `1w` / `1M` bars.
- Every bar boundary (nesting, higher-timeframe (HTF) visibility, missing bars, anchors) follows the instrument's calendar (`joseph.domain.sessions.for_instrument`).
- Each plugin reports parity status `SPEC_ONLY` until compared with TradingView exports, and licensing status `PINE_PORT_PENDING_REVIEW`: [Pine source record](../../../../docs/reports/PINE_SOURCE_REVISION.md).

## Layout

- `sdk/`: `IndicatorPlugin` protocol, `BasePlugin`, settings, state codec, `IndicatorEngine` (multi-timeframe, watermark-gated, no look-ahead), registry. `catalog.py` builds the default registry.
- `packaged/bridge.py`: `PackagedPlugin` (checkpointing per ADR 0009, zone → `Aoi` identity, ended-state inference).
- `packaged/plugins.py`: one adapter per indicator (Pine inputs and bounds; values mapped onto `joseph/domain/structures.py`).
- `packaged/codec.py`: allowlisted unpickler for checkpoints. `packaged/convert.py`: candle → library bar, float → decimal.
- `ticks.py`: `TickSizeSource`; engines inject the instrument tick as `tick_size` (Pine `syminfo.mintick`).
- `service.py`: `IndicatorQueryService` (snapshot, AOIs, per-bar series) with per-request settings.
- `golden.py`: scenario fixtures and the Pine-export parity harness. `testing.py`: deterministic candle builders.

## Conventions

- Bars are closed `[start_ms, end_ms)` candles; events confirm at `end_ms`. Timeframes run `1s`…`1M` (calendar month): step with `Timeframe.shift` / `end_of`, never `duration_ms`.
- `required_streams(settings, timeframe=...)` receives the series timeframe, so a plugin can pick nesting intrabars.
- The engine supplies HTF bars that end at or before the chart bar's close; the library applies Pine's `lookahead_on` + `[1]`.
- Library floats become decimals rounded to the tick's precision plus four places.
- Area-of-interest (AOI) ids are `stable_id(plugin, instrument, timeframe, library zone id[:kind])`, so batch, streaming and replay agree. A kind change (FVG → IFVG, OB → breaker) ends the old AOI as `inverted`.

To add an indicator, see [Extending](../../../../docs/EXTENDING.md#add-or-change-an-indicator-python).
