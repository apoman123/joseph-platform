# Plugin authoring (terminal)

Terminal extensions are vetted modules composed at build time — never fetched at runtime. CI fails on boundary violations (`pnpm arch:check`) and on missing JSDoc (`pnpm lint`).

## Add a drawing tool (TypeScript)

1. Create `packages/drawing-tools/src/<tool-id>/index.ts` exporting a `ToolPlugin` (manifest, anchor count, pointer state machine, `render`, `hitTest`, `handles`, `migrate`) plus a test that uses the recording 2D context in `src/testing`.
2. Add it to `FIRST_PARTY_TOOLS` in `src/index.ts`. Tools never import each other.
3. Removal: delete the directory and its list entry. Saved drawings of that tool stay stored and are shown as "unsupported".

Shared behaviour lives in `drawing-core`, never in another tool: geometry helpers (`distanceToPolyline`, `rayToEdge`, `sampleArc`, …), multi-point patterns (`createPatternTool` in `patterns.ts`) and long/short positions (`position.ts`). Tool-specific settings go in `props` and are declared as `inputs` (boolean or choice); the style bar edits them through the undoable `setDrawingInputs`. `hitTest` must return `handleIndex` equal to the anchor index the handle moves.

### Engine-backed drawing tools

A tool that shows engine values (anchored VWAP, regression channel) never computes them. It declares `seriesRequest(doc, {timeframe})`, a pure mapping from its anchors and props to an `/v1/indicators/series` request (`barStartAt` maps an anchor to its bar). Each chart's `DrawingSeriesSync` fetches it through `MarketDataClient.getIndicatorSeries`, debounces anchor drags, re-requests when a bar closes and drops superseded responses. The tool receives the result as `RenderState.series` (and as the last `hitTest` argument), draws only what the engine returned, and shows a `drawNotice` when values are missing (for example `ANCHOR_TOO_FAR_MESSAGE` for the engine's 2000-bar cap). `dataRows` and `describe(doc, {series})` feed the data window and the object list. The DEMO stand-in lives in `mock-provider/src/market/demo-drawing-series.ts`.

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

## Render a new backend indicator

Indicator values are computed by the backend. To display a new indicator kind, map its `AoiRecord`/`StructureEvent` records to overlay models in `packages/indicator-viz/src/mapping.ts` (boxes, price lines, markers) and add a test. Never compute trading signals in the terminal.

## Change a contract

Edit `packages/contracts/schemas/*.schema.json`, update the canonical examples in `packages/contracts/fixtures/`, run `pnpm contracts:gen`, and bump `schema_version` for breaking changes.
