# Plugin authoring

How contributors add drawing tools, features and indicator renderings to Joseph Terminal. Plugins are vetted modules composed at build time, never fetched at runtime. CI fails on boundary violations (`pnpm arch:check`) and missing JSDoc (`pnpm lint`).

## Add a drawing tool

1. Create `packages/drawing-tools/src/<tool-id>/index.ts` exporting a `ToolPlugin` (manifest, `defaultStyle`, `render`, `hitTest`, optional `inputs`, `describe`, `migrate`) and a test using the recording 2D context in `src/testing`.
2. Add it to `FIRST_PARTY_TOOLS` in `src/index.ts`. Tools never import each other.
3. To remove it, delete the directory and the list entry; saved drawings stay and show as unsupported.

Shared behaviour lives in `drawing-core`: geometry helpers (`distanceToPolyline`, `rayToEdge`, `sampleArc`, …), multi-point patterns (`createPatternTool` in `patterns.ts`) and positions (`position.ts`). Tool settings go in `props`, declared as `inputs` (boolean or choice) and edited through the undoable `setDrawingInputs`. `hitTest` returns the `handleIndex` of the anchor a handle moves.

### Engine-backed tools

A tool that shows engine values (anchored VWAP, regression channel) never computes them. It declares `seriesRequest(doc, {timeframe, calendar})`, a pure mapping from anchors and props to a `/v1/indicators/series` request (`barStartAt` maps an anchor to its bar). Each chart's `DrawingSeriesSync` fetches it via `MarketDataClient.getIndicatorSeries`, debounces drags, re-requests on bar close and drops superseded responses. The tool gets the result as `RenderState.series` (also the last `hitTest` argument), draws only what the engine returned, and shows a `drawNotice` with the engine's message when values are missing or refused. `dataRows` and `describe` feed the data window and object list. The DEMO stand-in is `mock-provider/src/market/demo-drawing-series.ts`.

## Add a feature

1. Create `apps/web/src/features/<name>/index.ts` exporting a `WebFeature` whose manifest declares id, version, UI slots, capabilities, permissions, settings and contract versions and a removal plan.
2. In `activate(ctx)` use only the `FeatureContext`: `ctx.slots.contribute(...)` for panels, `ctx.chartLayers.register(...)` for overlays, `ctx.commands` and `ctx.hotkeys` for actions, `ctx.events` for chart right-clicks and focus requests, `ctx.clients` for backend calls.
3. Return a disposer; the registry also disposes everything the feature registered.
4. Add it to `extensionFeatures` in `apps/web/src/composition/features.ts`.

Feature flags change visibility only. No feature can grant trading permission or reach a venue: orders go through `ctx.clients.execution` to the execution engine's risk gateway.

## Render a new indicator kind

Indicators are computed by the market engine. Map a new kind's `AoiRecord` or `StructureEvent` records to overlays (boxes, price lines, markers) in `packages/indicator-viz/src/mapping.ts`, give the layer a name in `PLUGIN_NAMES`, and add a test. Never compute trading signals in the terminal.

## Change a contract

Edit `packages/contracts/schemas/*.schema.json`, update the examples in `packages/contracts/fixtures/`, run `pnpm contracts:gen`, and bump `schema_version` for breaking changes. The engines serve these contracts; coordinate breaking changes with them and record an [ADR](adr/) when time semantics, identity, risk or plugin APIs change.
