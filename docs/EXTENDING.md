# Extending Joseph

Every extension is a local change: new files, one registration line, tests. CI fails if an extension crosses a boundary (import-linter, dependency-cruiser). Removing an extension never deletes saved user data. Terminal extensions (drawing tools, features, rendering a new indicator kind): [`joseph-terminal/docs/PLUGIN_AUTHORING.md`](../joseph-terminal/docs/PLUGIN_AUTHORING.md).

## Add a market-data venue (Python)

1. Add the venue id to `Venue` in `common.schema.json`, `joseph/domain/instruments.py` (with its calendar, if any) and the id regex; run `make contracts`.
2. Implement `MarketDataPort` (`joseph/market_data/app/ports.py`) in `joseph/market_data/adapters/<venue>.py`. `capabilities()` reports only verified features; raise `UnsupportedError` for the rest.
3. Register it in `market_data/adapters/factory.py` (`build_adapters` and a rate budget in `_RATES`).
4. Add small sanitized responses in `fixtures/venues/<venue>/` with respx contract tests, and an opt-in `tests/network` test.
5. Trading is separate: a `TradingAdapter` in `joseph_execution/adapters/venues/` subclassing `live.py`'s `LiveVenueAdapter` (gate, credential and permission guards), with capabilities in `venues/catalog.py`. It reports `TRADING_UNAVAILABLE` until a signed permission probe passes and cites the venue's docs for every endpoint. Brokers: see [BROKERS](BROKERS.md#cathay-securities).

Never change indicators, strategies or the risk gateway for a venue.

## Add or change an indicator (Python)

Indicator logic lives only in `apoman123/PineScripts` (`python_indicators/`, package `smc_indicators`).

1. **PineScripts:** port the script line by line into `src/smc_indicators/<module>.py` (subclass `base.Indicator`; outputs `types.Zone` / `types.Event` / `Result.values`), add `tests/test_<module>.py` (scenarios, batch == streaming, no look-ahead) and `docs/<module>.md`. Its CI runs ruff, mypy and pytest.
2. **Platform:** bump `rev` in `joseph-market-engine/pyproject.toml`, run `uv lock`.
3. Add an adapter in `joseph/indicators/packaged/plugins.py` (subclass `bridge.PackagedPlugin`: settings mirroring the Pine inputs, `_build`, `_advance`, value mapping) and list it in `default_plugins()`. No indicator rules in the adapter.
4. Publish only `Aoi`, `StructureEvent` and documented `values`: new kinds and events in `joseph/domain/structures.py` and the JSON Schema; new values in `strategy/domain/catalog.py` (`PUBLISHED_VALUES`, `AOI_KIND_PLUGIN`, `EVENT_PLUGIN`).
5. Add a hand-derived golden scenario in `fixtures/indicators/<plugin_id>/`; `tests/unit/indicators/test_packaged_bridge.py` checks catalog consistency and library parity.

A changed algorithm needs a new `indicator_version`; `method: trade` outputs never replace `pine_parity` ones.

## Add a strategy rule node

1. Add it to `RuleNode` in `strategy.schema.json` (`op` discriminator) and regenerate types.
2. Python: Pydantic model in `strategy/domain/ast.py`, validation in `compiler.py`, tri-state semantics and a reason node in `evaluator.py`, unit tests including UNKNOWN cases.
3. Terminal: an editor card and sentence rendering in `packages/strategy-builder`.

To remove a node, mark it unsupported in the compiler (`unsupported_ops`): saved strategies using it fail validation with an actionable message and are never evaluated without the condition.

## Change a contract

Edit `joseph-terminal/packages/contracts/schemas/*.schema.json`, then bump `schema_version` for breaking changes, update the examples in `packages/contracts/fixtures/`, run `make contracts openapi`, and record an ADR in `docs/adr/` if the change affects time semantics, identity, risk or plugin APIs.
