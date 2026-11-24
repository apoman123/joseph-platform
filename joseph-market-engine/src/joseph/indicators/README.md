# Indicators

This package runs the market engine's indicators: the plugin SDK, the built-in indicators, run-time discovery of plugin packages, the multi-timeframe engine and the query service. It is for contributors adding or changing an indicator; the terminal only renders the outputs, from each plugin's metadata.

## Plugins

`catalog.py` composes the registry. The nine built-ins (`builtin/`) always register first, in this order: `sma`, `ema`, `anchored_vwap`, `regression_channel`, `bollinger_bands`, `rsi`, `atr`, `volume_delta`, `pivot_points`. Plugin packages installed in the environment then add theirs (`discovery.py`). `GET /v1/indicators/plugins` lists each usable plugin's id, version, description, input schema, required streams (`chart`, higher timeframes `aux:<tf>`, `intrabars:<tf>`), the AOI kinds, events and values it publishes, its display hints, and its licensing and parity status.

- An anchored series (requested alone) runs from the anchor bar, at most `JOSEPH_MAX_ANCHORED_BARS` (default 50,000) bars back, else the request fails validation; it is kept incrementally between requests. On exchange calendars the `session` / `week` / `month` anchors are the calendar's `1d` / `1w` / `1M` bars.
- Every bar boundary (nesting, higher-timeframe (HTF) visibility, missing bars, anchors) follows the instrument's calendar (`joseph.domain.sessions.for_instrument`).
- Built-ins report licensing status `CLEAN_ROOM_FROM_SPEC` and parity status `SPEC_ONLY` until compared with TradingView exports ([parity exports](../../../fixtures/indicators/pine_exports/README.md)).

## Plugin packages

A distribution contributes plugins through an entry point in the `joseph.indicator_plugins` group that names a zero-argument factory returning `IndicatorPlugin` objects:

```toml
[project.entry-points."joseph.indicator_plugins"]
example = "example_plugins.plugins:default_plugins"
```

- Entry points load after the built-ins, sorted by entry-point name and distribution name; each one is isolated, so a failure is logged and skipped. A plugin is rejected when its id is taken (a package never shadows a built-in), it declares an AOI kind another plugin declared, its `schema_version` is not 2, or its metadata breaks the rules of `sdk/metadata.py`.
- Settings: `JOSEPH_INDICATOR_PLUGIN_PACKAGES` (`*`, `none` or entry-point names), `JOSEPH_INDICATOR_PLUGINS_DISABLED` (registered but disabled, metadata kept) and `JOSEPH_INDICATOR_PLUGINS_REQUIRED` (startup fails unless these end up enabled).
- `python -m joseph.indicators.discovery --check` prints the loaded ids and exits 1 on any failure.
- `testing.py` ships `EchoPlugin` and `ZoneEchoPlugin` for package tests, and `golden.py` the fixture harness.

## Layout

- `sdk/`: `IndicatorPlugin` protocol, `BasePlugin`, `metadata.py` (published values and display hints, and their validation), settings, state codec, `IndicatorEngine` (multi-timeframe, watermark-gated, no look-ahead), registry.
- `builtin/`: one module per built-in family; `_series.py` holds the shared price sources, tick handling and publication rounding.
- `discovery.py`: entry-point discovery and the check command. `catalog.py`: the registry builders.
- `ticks.py`: `TickSizeSource`; engines inject the instrument tick as `tick_size` (TradingView `syminfo.mintick`).
- `service.py`: `IndicatorQueryService` (snapshot, AOIs, per-bar series) with per-request settings.
- `golden.py`: scenario fixtures and the TradingView parity harness. `testing.py`: deterministic candle builders and test plugins.

## Conventions

- Bars are closed `[start_ms, end_ms)` candles; events confirm at `end_ms`. Timeframes run `1s`…`1M` (calendar month): step with `Timeframe.shift` / `end_of`, never `duration_ms`.
- `required_streams(settings, timeframe=...)` receives the series timeframe, so a plugin can pick nesting intrabars.
- The engine supplies HTF bars that end at or before the chart bar's close.
- Built-ins compute in doubles in bar order; prices become decimals rounded half-up to the tick's precision plus four places, only when published.
- AOI kinds and event names are upper snake case and declared by their plugin; kinds are unique across plugins, event names may be shared, and `AOI_INVALIDATED` / `AOI_EXPIRED` are reserved for any plugin. An ended AOI may carry a display-only `end_reason`.
- Plugin states are frozen dataclasses checkpointed by the state codec, so batch, streaming and replay agree.

To add an indicator, see [Extending](../../../../docs/EXTENDING.md#add-or-change-an-indicator-python).
