# Plugin authoring

How contributors add drawing tools, features and chart studies to Joseph Terminal. Plugins are vetted modules composed at build time, never fetched at runtime. CI fails on boundary violations (`pnpm arch:check`) and missing JSDoc (`pnpm lint`).

## Add a drawing tool

1. Create `packages/drawing-tools/src/<tool-id>/index.ts` exporting a `ToolPlugin` (manifest, `defaultStyle`, `render`, `hitTest`, optional `inputs`, `describe`, `migrate`) and a test using the recording 2D context in `src/testing`.
2. Add it to `FIRST_PARTY_TOOLS` in `src/index.ts`. Tools never import each other.
3. To remove it, delete the directory and the list entry; saved drawings stay and show as unsupported.

Shared behaviour lives in `drawing-core`: geometry helpers (`distanceToPolyline`, `rayToEdge`, `sampleArc`, …), multi-point patterns (`createPatternTool` in `patterns.ts`) and positions (`position.ts`). Tool settings go in `props`, declared as `inputs` (boolean or choice) and edited through the undoable `setDrawingInputs`. `hitTest` returns the `handleIndex` of the anchor a handle moves.

### Engine-backed tools

A tool that shows engine values (anchored VWAP, regression channel) never computes them. It declares `seriesRequest(doc, {timeframe, calendar})`, a pure mapping from anchors and props to a built-in script run: script id, first bar and inputs (`barStartAt` maps an anchor to its bar). Each chart's `DrawingSeriesSync` runs it through the study client (`/v1/scripts/run-batch`), debounces drags, re-runs on bar close and drops superseded responses. The script's plot values and lines become per-bar values (`seriesPointsFromResult`: plot names, and `<line id>.from_price` / `.to_price` / `.from_ms` on the bar a line ends). The tool gets them as `RenderState.series` (also the last `hitTest` argument), draws only what the engine returned, and shows a `drawNotice` with the engine's message when values are missing or refused. `dataRows` and `describe` feed the data window and object list. In DEMO the DEMO study evaluator (`mock-provider/src/market/demo-scripts.ts`) answers.

## Add a feature

1. Create `apps/web/src/features/<name>/index.ts` exporting a `WebFeature` whose manifest declares id, version, UI slots, capabilities, permissions, settings and contract versions and a removal plan.
2. In `activate(ctx)` use only the `FeatureContext`: `ctx.slots.contribute(...)` for panels, `ctx.chartLayers.register(...)` for overlays, `ctx.commands` and `ctx.hotkeys` for actions, `ctx.events` for chart right-clicks and focus requests, `ctx.clients` for backend calls.
3. Return a disposer; the registry also disposes everything the feature registered.
4. Add it to `extensionFeatures` in `apps/web/src/composition/features.ts`.

Feature flags change visibility only. No feature can grant trading permission or reach a venue: orders go through `ctx.clients.execution` to the execution engine's risk gateway.

## Chart studies

Indicators are `joseph_script.Indicator` classes run by the market engine: its built-ins, plugin packages installed through `joseph.scripts` entry points, and the user's own scripts. The terminal knows no indicator by name. It reads the catalog once per runtime (`GET /v1/scripts/catalog?kind=indicator`, `useStudyCatalog()` in `apps/web/src/shell/study-catalog.ts`) and keeps each chart's studies in `ChartState.studies` (script id, pinned version for owned scripts, inputs, plot colors, visibility).

- `useStudyRuns` (`apps/web/src/shell/python-studies.tsx`) runs the visible studies in batches (`POST /v1/scripts/run-batch`) once the chart has its candles, re-runs them when a bar closes and every few seconds while live, and draws each `ScriptResult` with the Python painter: overlay scripts on the price pane, the others in a study pane.
- The legend, the picker (`indicator-library.tsx`: Built-ins, Plugins, My scripts, My collection) and the settings dialog (`indicator-settings-dialog.tsx`, generated from the declared `INPUTS`) work the same for every origin.
- Plots declared with `display="data_window"` are only listed in the data window; drawings (`line`, `label`, `box`, `fill`, `hline`, `table`, ...) are rendered from their documented fields only.
- DEMO mode has no Python: `DemoStudyClient` serves the built-in catalog exported from the engine (`contracts/openapi/builtin-scripts.json`) and evaluates the built-ins with the DEMO study evaluator, checked against outputs exported from the Python built-ins (`contracts/fixtures/builtin-indicator-outputs.json`). Never compute trading signals in the terminal.

## Change a contract

Edit `packages/contracts/schemas/*.schema.json`, update the examples in `packages/contracts/fixtures/`, run `pnpm contracts:gen`, and bump `schema_version` for breaking changes. The engines serve these contracts; coordinate breaking changes with them and record an [ADR](adr/) when time semantics, identity, risk or plugin APIs change.
