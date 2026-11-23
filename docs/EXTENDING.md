# Extending Joseph

Every extension is a local change: new files, one registration line, tests. CI fails if an extension reaches across boundaries (import-linter, dependency-cruiser). Removing an extension never deletes saved user data.

## Add a market-data venue (Python)

1. Add the venue id to `Venue` in `common.schema.json`, `joseph/domain/instruments.py` and the canonical id regex; run `make contracts`.
2. Create `joseph/market_data/adapters/<venue>.py` implementing `MarketDataPort` (`joseph/market_data/app/ports.py`). Report only verified features in `capabilities()`; raise `UnsupportedError` for the rest.
3. Register it in `market_data/adapters/factory.py` (`build_adapters` + a rate budget in `_RATES`).
4. Capture small sanitized API responses in `fixtures/venues/<venue>/` and add respx contract tests; add an opt-in `tests/network` test.
5. Trading support is separate: add a `TradingAdapter` in `joseph_execution/adapters/venues/` and its capabilities in `venues/catalog.py`. Subclass `venues/live.py`'s `LiveVenueAdapter` so the gate, credential and permission guards apply; it must report `TRADING_UNAVAILABLE` until a signed permission probe passes and cite the venue's docs for every endpoint.

Forbidden: changing indicators, strategies or the risk gateway for a venue.

## Add or change an indicator (Python)

Indicator logic lives only in `apoman123/PineScripts` (`python_indicators/`, package `smc_indicators`); the platform holds adapters.

1. **In PineScripts:** port the Pine script line by line into `src/smc_indicators/<module>.py` (subclass `base.Indicator`; outputs are `types.Zone` / `types.Event` / `Result.values`), add `tests/test_<module>.py` (scenarios + batch == streaming + no look-ahead) and `docs/<module>.md`. Push; its CI runs ruff, mypy and pytest.
2. **In the platform:** bump `rev` in `joseph-market-engine/pyproject.toml` and run `uv lock`.
3. Add an adapter class in `joseph/indicators/packaged/plugins.py` (subclass `bridge.PackagedPlugin`: settings mirroring the Pine inputs and bounds, `_build`, `_advance`, value mapping) and list it in `default_plugins()`. Never put indicator rules in the adapter.
4. Publish only `Aoi`, `StructureEvent` and documented `values` keys: add new kinds/events to `joseph/domain/structures.py` and the JSON Schema, new values to `strategy/domain/catalog.py` (`PUBLISHED_VALUES`, `AOI_KIND_PLUGIN`, `EVENT_PLUGIN`).
5. Add a hand-derived golden scenario (`fixtures/indicators/<plugin_id>/`); `tests/unit/indicators/test_packaged_bridge.py` checks catalog consistency and library parity.
6. Optional terminal rendering: map its kinds in `packages/indicator-viz/src/mapping.ts`.

Changing an algorithm means a new `indicator_version`; `method: trade` outputs must never replace `pine_parity` ones.

## Add a strategy rule node

1. Add the node to `RuleNode` in `strategy.schema.json` (`op` discriminator) and regenerate types.
2. Python: Pydantic model in `strategy/domain/ast.py`, validation in `compiler.py`, tri-state semantics plus a reason node in `evaluator.py`, and unit tests including the UNKNOWN cases.
3. Terminal: an editor card and natural-language rendering in `packages/strategy-builder`.

Removing a node: mark it unsupported in the compiler (`unsupported_ops`). Saved strategies that use it then fail validation with an actionable message. They are never silently evaluated without the condition.

## Add a drawing tool (TypeScript)

1. Create `packages/drawing-tools/src/<tool-id>/index.ts` exporting a `ToolPlugin` (manifest, anchor count, pointer state machine, `render`, `hitTest`, `handles`, `migrate`) plus a test that uses the recording 2D context in `src/testing`.
2. Add it to `FIRST_PARTY_TOOLS` in `src/index.ts`. Tools never import each other.
3. Removal: delete the directory and its list entry. Saved drawings of that tool stay stored and are shown as "unsupported".

## Add a terminal feature (TypeScript)

1. Create `apps/web/src/features/<name>/index.ts` exporting a `WebFeature`. Its manifest needs an id, version, slots, required capabilities, permissions, settings schema version, contract version and a removal plan.
2. In `activate(ctx)`, use only the `FeatureContext`:
   - `ctx.slots.contribute(...)` for panels;
   - `ctx.chartLayers.register(...)` for chart overlays;
   - `ctx.commands` / `ctx.hotkeys` for actions;
   - `ctx.events` for chart right-clicks and focus requests;
   - `ctx.clients` for backend calls.
3. Return a disposer. The registry also disposes everything the feature registered.
4. Add one line to `apps/web/src/composition/features.ts`.

Feature flags change visibility only. No feature can grant trading permission or reach a venue: all orders go through `ctx.clients.execution` to the server-side risk gateway.

## Change a contract

Edit `joseph-terminal/packages/contracts/schemas/*.schema.json`, then:
- bump `schema_version` for breaking changes;
- update the shared example payloads in `packages/contracts/fixtures/`;
- run `make contracts openapi`;
- record an ADR in `docs/adr/` if the change affects time semantics, identity, risk or the plugin APIs.
