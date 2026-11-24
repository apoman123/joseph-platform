# Indicator adapters

This package adapts the market engine's indicator library to the plugin SDK. It is for contributors adding or changing an indicator; the terminal only renders the outputs.

**The algorithms are not here.** They live in the private indicator library (`apoman123/PineScripts`, `python_indicators/`): line-by-line Python ports of Pine scripts, pinned in the market engine's `pyproject.toml` ([ADR 0009](../../../../docs/adr/0009-external-indicator-library.md)).

## Plugins

`catalog.py` builds the default registry, one adapter per library indicator. `GET /v1/indicators/plugins` lists each plugin's id, version, description, input schema, required streams (`chart`, higher timeframes `aux:<tf>`, `intrabars:auto`), output kinds and events, and licensing and parity status.

- An anchored series (requested alone) runs from the anchor bar, at most `JOSEPH_MAX_ANCHORED_BARS` (default 50,000) bars back, else the request fails validation; it is kept incrementally between requests. On exchange calendars the `session` / `week` / `month` anchors are the calendar's `1d` / `1w` / `1M` bars.
- Every bar boundary (nesting, higher-timeframe (HTF) visibility, missing bars, anchors) follows the instrument's calendar (`joseph.domain.sessions.for_instrument`).
- Each plugin reports parity status `SPEC_ONLY` until compared with TradingView exports, and licensing status `PINE_PORT_PENDING_REVIEW` ([parity exports](../../../fixtures/indicators/pine_exports/README.md)).

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
- Area-of-interest (AOI) ids are `stable_id(plugin, instrument, timeframe, library zone id[:kind])`, so batch, streaming and replay agree. A kind change ends the old AOI as `inverted`.

To add an indicator, see [Extending](../../../../docs/EXTENDING.md#add-or-change-an-indicator-python).
