# Extending Joseph

How contributors add venues, brokers, indicators, strategy rule nodes and terminal features, and change contracts. The module map is in the [architecture index](architecture/INDEX.md).

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

Indicator algorithms live only in the library `joseph-smc-indicators` (`python_indicators/`, package `smc_indicators`) in the private repository `apoman123/PineScripts`; this path needs access to it.

1. **Library:** port the Pine script line by line into `src/smc_indicators/<module>.py` (subclass `base.Indicator`; outputs `types.Zone`, `types.Event` and `Result.values`), add `tests/test_<module>.py` (scenarios, batch equals streaming, no look-ahead) and `docs/<module>.md`. Its CI runs ruff, mypy and pytest.
2. **Pin:** bump `rev` of `joseph-smc-indicators` in `joseph-market-engine/pyproject.toml` and run `uv lock`.
3. **Adapter:** subclass `bridge.PackagedPlugin` in `joseph/indicators/packaged/plugins.py` (settings mirroring the Pine inputs, `_build`, `_advance`, value mapping) and list it in `default_plugins()`. The adapter holds no indicator rules.
4. **Publish** only `Aoi`, `StructureEvent` and documented values: kinds and events in `joseph/domain/structures.py` and the JSON Schema; values in `joseph/strategy/domain/catalog.py` (`PUBLISHED_VALUES`, `AOI_KIND_PLUGIN`, `EVENT_PLUGIN`).
5. **Test:** add a hand-derived golden scenario in `fixtures/indicators/<plugin_id>/`; `tests/unit/indicators/test_packaged_bridge.py` checks catalog consistency and library parity.

A changed algorithm needs a new `indicator_version`; `method: trade` outputs never replace `pine_parity` ones.

## Add a strategy rule node

1. Add it to `RuleNode` in `strategy.schema.json` (`op` discriminator) and regenerate the types.
2. Market engine: a Pydantic model in `joseph/strategy/domain/ast.py`, validation in `compiler.py`, tri-state semantics and a reason node in `evaluator.py`, and unit tests including UNKNOWN cases.
3. Terminal: an editor card and its sentence in `packages/strategy-builder`.

To remove a node, list it in the compiler's `unsupported_ops`: saved strategies that use it fail validation with an actionable message and are never evaluated without the condition.

## Extend the terminal

Drawing tools, features (panels, chart layers, commands) and rendering new indicator kinds: [Plugin authoring](../joseph-terminal/docs/PLUGIN_AUTHORING.md).

## Change a contract

Edit `joseph-terminal/packages/contracts/schemas/*.schema.json`, bump `schema_version` for breaking changes, update the examples in `packages/contracts/fixtures/` and run `make contracts openapi`. Record an ADR in [`adr/`](adr/) when the change affects time semantics, identity, risk or plugin APIs.
