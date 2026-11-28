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

Writing the script itself (context API, inputs, drawings, testing) is covered in the [script development guides](scripting/README.md); this section covers shipping it with the platform.

An indicator is a concrete `joseph_script.Indicator` subclass ([ADR 0011](adr/0011-indicators-are-joseph-script-classes.md)); there is no other indicator format. Generic indicators can become built-ins; anything else, strategy-specific indicators included, goes into a separate plugin package that the engine loads at startup. The platform never names a plugin package or its ids.

1. **Implement** `on_bar(ctx)`: read the current bar (`ctx.bar`, including optional fields such as `taker_buy_volume`), declared inputs and series, and keep state incrementally on the instance (one pass per run, O(n)). Publish through the context only: `ctx.plot(name, value, color=, style=, width=, offset=, display=)` (`display="data_window"` lists a value without drawing it), drawings (`line`, `label`, `box`, `fill`, `hline`, `linefill`, `table`, ...) and `ctx.alert` conditions on confirmed bars.
2. **Declare** `ID`, `TITLE`, `SHORT_TITLE`, `DESCRIPTION`, `CATEGORY`, `OVERLAY` (price pane or its own pane), every setting as a `joseph_script.Input` (bounds, options, step, group `Style` for colors, tooltip; the terminal's settings dialog is generated from them), additional series as `REQUESTS` (`joseph_script.Request`, including lower-timeframe `intrabars=True` requests with `tf="auto"`, `tf_input`, `unless` and `unless_input`) and calendar periods as `PERIODS` (read with `ctx.period(tf)`). See [Python scripts](PYTHON_SCRIPTS_AND_MCP.md#context-extensions-for-indicators).
3. **Ship** it:
   - **Built-in:** one module in `joseph/scripts/builtin/indicators/` registered in `MODULES["indicators"]` (`joseph/scripts/builtin/__init__.py`; the order is the picker order). It imports only `joseph_script` and the standard library (helpers such as price sources are duplicated per module), so its public source is valid tenant source (`tests/unit/scripts/test_catalog.py`, import-linter contract `builtin-scripts-pure`). DEMO mode has no Python: add the study to the DEMO study evaluator (`joseph-terminal/packages/mock-provider/src/market/demo-scripts.ts`), then run `make openapi`, which refreshes the built-in catalog (`contracts/openapi/builtin-scripts.json`) and the outputs of every built-in on fixed bars (`contracts/fixtures/builtin-indicator-outputs.json`); `demo-scripts.test.ts` requires the DEMO evaluator to reproduce them.
   - **Plugin package:** a distribution that depends on `joseph-market-engine` (a compatible minor version) and declares an entry point in the `joseph.scripts` group naming the class or a zero-argument factory returning classes:

     ```toml
     [project.entry-points."joseph.scripts"]
     my_scripts = "my_scripts.indicators:classes"
     ```

     Its catalog id is `plugin:<entry point>:<ID>`. Install it with `make plugins` ([operations](OPERATIONS.md#script-plugins)); `uv run python -m joseph.scripts.catalog --check` in `joseph-market-engine/` lists what loads.
4. **Test:** hand-derived golden scenarios in `fixtures/indicators/<id>/<scenario>.json` (bars, optional intrabars and period bars, expected final values), reproduced by `tests/replay/scripts/test_builtin_indicator_golden.py`; add a reader there that maps the script's plots and drawings to the scenario's value names. Plugin packages test their classes with `joseph.scripts.worker.run_class` on deterministic bars.

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
