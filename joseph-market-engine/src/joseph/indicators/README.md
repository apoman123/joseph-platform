# Indicators (market engine, private)

Backend-authoritative SMC indicators; the terminal only renders their outputs. **The algorithms are not here:** they live in the private library `apoman123/PineScripts/python_indicators` (`smc_indicators`), a line-by-line Pine port pinned in `pyproject.toml`. This package adapts it to the plugin SDK (ADR 0009).

| Plugin | Publishes | Needs |
|---|---|---|
| `daily_bias` | BOS / CHoCH / initial-break events, `trend`, 1D/4H/1H `bias`, PDH/PDL, NY sessions | chart + `aux:1d/4h/1h` |
| `market_structure` | HH/HL/LH/LL/EQH/EQL, BOS / MSS (as CHoCH) / initial breaks, unbroken SWING_HIGH / SWING_LOW | chart |
| `fvg_ifvg` | FVG / IFVG zones with heat scores, first-test decay, inversion | chart |
| `order_breaker_blocks` | OB / breaker zones | chart |
| `liquidity_sweeps` | BSL / SSL / EQH / EQL pools, sweeps vs breaks | chart |
| `order_flow_pressure` | Delta: true from taker volume (`method: taker_volume`, snapshot `trade`), classified stock buy volume (`trade_classification`) or the Pine candle estimate; buyer/seller failure and breakout | chart + `intrabars:auto` (1s / 1m / 5m / 1h by chart) |
| `auction_value_areas` | VAH / POC / VAL (chart and 1H profiles), 1s–30m charts | chart + `aux:1h` |
| `premium_discount_ote` | Dealing range, premium / discount / EQ / OTE (LuxAlgo, CC BY-NC-SA 4.0) | chart |
| `anchored_vwap` | VWAP + 3 stdev bands from a session, week, month or anchor bar | chart |
| `regression_channel` | Least-squares channel, rolling or from an anchor bar, Pearson's R | chart |

- The last two are Python-native studies following TradingView's built-in definitions, not Pine ports. An anchored series (requested alone) runs from the anchor bar, at most `JOSEPH_MAX_ANCHORED_BARS` (50,000) bars back, else a validation error; it is kept incrementally between requests. On exchange calendars the `session` / `week` / `month` anchors are the calendar's `1d` / `1w` / `1M` bars.
- Every bar boundary (nesting, HTF visibility, missing bars, anchors) follows the instrument's calendar (`joseph.domain.sessions.for_instrument`).
- Parity and licensing status: [Pine source record](../../../../docs/reports/PINE_SOURCE_REVISION.md).

## Layout

- `sdk/`: `IndicatorPlugin` protocol, `BasePlugin`, settings, state codec, `IndicatorEngine` (multi-timeframe, watermark-gated, no look-ahead), registry.
- `packaged/bridge.py`: `PackagedPlugin` (checkpointing per ADR 0009, zone → `Aoi` identity, ended-state inference).
- `packaged/plugins.py`: one adapter per indicator (Pine inputs and bounds; values mapped onto `joseph/domain/structures.py`).
- `packaged/codec.py`: allowlisted unpickler for checkpoints. `packaged/convert.py`: candle → library bar, float → decimal.
- `ticks.py`: `TickSizeSource`; engines inject the instrument tick as `tick_size` (Pine `syminfo.mintick`).
- `service.py`: `IndicatorQueryService` (snapshot, AOIs, per-bar series) with per-request settings.
- `golden.py`: scenario fixtures and the Pine-export parity harness. `testing.py`: deterministic candle builders.

## Conventions

- Bars are closed `[start_ms, end_ms)` candles; events confirm at `end_ms`. Timeframes run `1s`…`1M` (calendar month): step with `Timeframe.shift` / `end_of`, never `duration_ms`.
- `required_streams(settings, timeframe=...)` gets the series timeframe so a plugin can pick nesting intrabars.
- The engine supplies HTF bars ending at or before the chart bar's close; the library applies Pine `lookahead_on` + `[1]`.
- Library floats become decimals rounded to the tick's precision plus four places.
- AOI ids are `stable_id(plugin, instrument, timeframe, library zone id[:kind])`, so batch, streaming and replay agree; a kind change (FVG → IFVG, OB → breaker) ends the old AOI as `inverted`.

Adding an indicator: [`docs/EXTENDING.md`](../../../../docs/EXTENDING.md#add-or-change-an-indicator-python).
