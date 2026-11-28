# Extending Joseph

How contributors add venues, brokers, indicators, strategies and terminal features, and change contracts. The module map is in the [architecture index](architecture/INDEX.md).

Every extension is a local change: new files, one registration line, tests. CI fails on boundary violations (import-linter, dependency-cruiser). Removing an extension never deletes saved user data.

## Add a market-data venue (Python)

1. Add the venue id to the `Venue` enum and the instrument-id pattern in `joseph-terminal/packages/contracts` (`schemas/common.schema.json`, `src/identity.ts`) and the market engine's `joseph/domain/instruments.py` (with its session calendar, if any); run `make contracts`.
2. Implement `MarketDataPort` (`joseph/market_data/app/ports.py`) in `joseph/market_data/adapters/<venue>.py`. `capabilities()` reports only verified features; everything else raises `UnsupportedError`.
3. Register it in `joseph/market_data/adapters/factory.py` (`build_adapters`, plus a rate budget in `_RATES`).
4. Add small sanitized responses in `fixtures/venues/<venue>/` with respx contract tests, and an opt-in test in `tests/network`.

Never change indicators, strategies or the risk gateway for a venue.

## Add a trading venue or broker (Python)

- **Crypto venue** (execution engine, separate from market data): a `TradingAdapter` in `joseph_execution/adapters/venues/<venue>.py` subclassing `live.py`'s `LiveVenueAdapter` (LIVE gate, credential and permission guards), capabilities in `venues/catalog.py`, the id in `domain/enums.py`. It reports `TRADING_UNAVAILABLE` until a signed permission probe passes and cites the venue's docs for every endpoint.
- **Securities broker:** `joseph_execution/adapters/brokers/<broker>.py` modelled on `sinopac.py` (client protocol, fake client, pure order mappings; helpers in `base.py`), capabilities in `capabilities.py`, the id in `enums.Broker`, an enable flag in `catalog.build()`. Routing, risk and the OMS need no change. See [Brokers](BROKERS.md).

## Add or change an indicator (Python)

An indicator is an `IndicatorPlugin` ([ADR 0004](adr/0004-indicator-plugin-sdk.md)). Generic indicators can become built-ins in `joseph/indicators/builtin/`; anything else, strategy-specific indicators included, goes into a separate plugin package that the engine discovers at startup ([ADR 0009](adr/0009-external-indicator-library.md)). The platform never names a plugin package or its vocabulary.

1. **Implement** a `BasePlugin` (`joseph.indicators.sdk`): settings with bounds, required streams, warm-up, a frozen state and `on_bar_closed`. Publish only `Aoi`, `StructureEvent` (`joseph.domain.structures`) and declared values.
2. **Declare** what it publishes and how it is drawn, in `joseph.indicators.sdk.metadata` types:

   | Class variable | Holds |
   |---|---|
   | `OUTPUT_KINDS`, `OUTPUT_EVENTS` | AOI kinds and event names, `^[A-Z][A-Z0-9_]{0,47}$`. Kinds are unique across installed plugins; `AOI_INVALIDATED` and `AOI_EXPIRED` are reserved |
   | `OUTPUT_VALUES` | `ValueSpec`s: key, type, unit, description, choices (strings only). Consumers read only these |
   | `DISPLAY` | `Display`: layer, default visibility, z-order, event markers, and `KindStyle`, `EventStyle`, `PlotStyle` (price-pane lines or histograms), `PaneStyle` (sub-panes with levels) and `EndReasonStyle` entries |

   Registration validates the metadata. The terminal renders exactly what is declared; no terminal code is needed.
3. **Ship** it:
   - **Built-in:** add it to `builtin_plugins()` in `joseph/indicators/builtin/__init__.py` (the order is the registration and Layers order) and keep it pure (import-linter contract `builtin-indicators-pure`). Run `make openapi` to refresh `contracts/openapi/builtin-indicator-plugins.json`, which the terminal's DEMO mirror (`mock-provider`) is tested against.
   - **Plugin package:** a distribution that depends on `joseph-market-engine` (a compatible minor version, e.g. `>=0.6,<0.7`) and declares an entry point whose zero-argument callable returns its plugins in display order:

     ```toml
     [project.entry-points."joseph.indicator_plugins"]
     my_indicators = "my_indicators.plugins:default_plugins"
     ```

     Install it with `make plugins` ([operations](OPERATIONS.md#indicator-plugins)); `uv run python -m joseph.indicators.discovery --check` in `joseph-market-engine/` lists what loads.
4. **Test:** hand-derived golden scenarios in `fixtures/indicators/<plugin_id>/` with `joseph.indicators.golden`; batch, streaming and checkpoint-restore runs must agree. `joseph.indicators.testing` provides candle builders and the test plugins `EchoPlugin` and `ZoneEchoPlugin`; plugin packages use them from the installed engine.

A changed algorithm needs a new `indicator_version`; `method: trade` outputs never replace `pine_parity` ones.

## Add a strategy (Python)

A strategy is a concrete `joseph_script.Strategy` subclass ([ADR 0010](adr/0010-python-strategy-scripts.md)); there is no other strategy format.

1. **Implement** `on_bar(ctx)`: read closed bars, `INPUTS` and declared `REQUESTS`, plot what the rules use and queue orders with `ctx.entry(side, quantity, stop=..., target=..., comment=...)` or `ctx.close(comment)`. Keep state incremental on the instance (one pass per run, O(n)); manage simulated exits through `ctx.position_stop` and `ctx.position_target`.
2. **Declare** every parameter as a `joseph_script.Input` (bounds, options, groups), including stop and target settings, plus `ID`, `TITLE`, `DESCRIPTION` and `CATEGORY`.
3. **Ship** it:
   - **Built-in:** one module in `joseph/scripts/builtin/strategies/` registered in `MODULES["strategies"]` (`joseph/scripts/builtin/__init__.py`). It imports only `joseph_script` and the standard library (helpers such as moving averages are duplicated in the module), so its public source is valid tenant source; `tests/unit/scripts/test_catalog.py` checks this.
   - **Plugin package:** a distribution that declares an entry point in the `joseph.scripts` group naming the class or a zero-argument factory returning classes; its catalog id is `plugin:<entry point>:<ID>`. `uv run python -m joseph.scripts.catalog --check` lists what loads.
   - **Tenant script:** saved through `/v1/scripts`; it runs only in the isolated Docker runner.
4. **Test:** deterministic bars through `worker.run_class` with the declared input defaults; assert orders (bar, stop, target), fills at the next open and exits (see `tests/unit/scripts/test_builtin_strategies.py`).

Activations (`/v1/strategy-activations`) and backtests (`/v1/scripts/{id}/run`) need no change for a new strategy.

## Extend the terminal

Drawing tools and features (panels, chart layers, commands): [Plugin authoring](../joseph-terminal/docs/PLUGIN_AUTHORING.md). Indicators render from their plugin's display metadata.

## Change a contract

Edit `joseph-terminal/packages/contracts/schemas/*.schema.json`, bump `schema_version` for breaking changes, update the examples in `packages/contracts/fixtures/` and run `make contracts openapi`. Record an ADR in [`adr/`](adr/) when the change affects time semantics, identity, risk or plugin APIs.
