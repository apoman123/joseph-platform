# TradingView-style use-case audit (Joseph Terminal)

Scope: every TradingView-like requirement in the V5 proposal — §1.2 use
cases, §3.1 workspace layout, §3.2 interaction table, "Core charts", §2.4
drawing milestones P0–P2, "Screening and alert workflows" and the §2.2
chart-layer invariants. Status was decided by reading `joseph-terminal/`
code and re-verified by tests after the changes (2026-11-22).

DONE = implemented and tested · PARTIAL = implemented with a named gap ·
MISSING = not implemented. Evidence: **U** Vitest file, **E** Playwright
DEMO spec in `apps/web/e2e/`, **S** screenshot in `apps/web/e2e/screenshots/`.

## Summary (94 requirements)

| | DONE | PARTIAL | MISSING |
|---|---:|---:|---:|
| Before | 54 | 16 | 24 |
| After | 94 | 0 | 0 |

## §1.2 Use cases

| # | Requirement | Before → after | Evidence |
|---|---|---|---|
| 1 | Cross-venue search; switch venue, timeframe, **feed mode**; freshness | PARTIAL → DONE (feed-mode select per pane) | E `charting.spec.ts`, S 12 |
| 2 | Watchlist **auto-shows** qualified perps; screener gates/reasons; missing ≠ pass | PARTIAL → DONE ("Qualified perpetuals" source) | U `watchlist/columns.test.tsx` |
| 3 | Drawings + 7 SMC indicators' AOIs/events, structure state | PARTIAL → DONE (market-structure layer, Delta % pane) | U `structure-and-study.test.ts`, E, S 12 |
| 4 | GUI rule builder → Alert/Paper strategy | DONE → DONE | E `trading-strategy.spec.ts` |
| 5 | Notification with exact instrument/version/AOI/levels, one-click locate | DONE → DONE | E `trading-navigation.spec.ts` |
| 6 | Chart orders: place, modify, cancel, partial/flatten/reverse | DONE → DONE (refused while replaying) | E `trading-orders.spec.ts`, E replay |
| 7 | Gated auto-live, disable, kill switch, audit | DONE → DONE | E `trading-risk.spec.ts` |
| 8 | Standalone with mock feed/broker | DONE → DONE (DEMO serves input schemas, structure, `at_ms`) | U `demo-indicators.test.ts` |

## §3.1 Workspace layout

| Requirement | Before → after | Evidence |
|---|---|---|
| Left toolbar (crosshair, lines/rays, trendline, rectangle, Fib R/E, long/short, ruler, text, channel, undo/redo, magnet, lock) | DONE → DONE (grouped flyouts, 18 tools) | U `charting.test.tsx`, E |
| Instrument selector venue/type/instrument | DONE → DONE | E `terminal.spec.ts` |
| Timeframe control | DONE → DONE (grouped 1s → 1M, venue-unpublished dimmed) | E `timeframes.spec.ts`, U |
| Chart type | DONE → DONE | E |
| **Compare** | MISSING → DONE (≤3 symbols, left % axis, chips) | E, S 14, U |
| **Indicator** control with parameters | PARTIAL → DONE (settings dialog from `input_schema`) | E, S 13, U |
| **Template** | MISSING → DONE (save/apply/delete) | E, U |
| Layout menu | DONE → DONE | E |
| Data-quality indicator | DONE → DONE | E |
| Candles canvas | DONE → DONE | E |
| **Volume pane** | PARTIAL → DONE (own LWC pane) | E (pane canvases) |
| **Indicator pane** | MISSING → DONE (Delta % columns, true vs faint estimate per bar, ±threshold, zero line) | E, S 12, S 24, U |
| AOI boxes/lines | DONE → DONE (+ per-kind colors/filters) | U |
| BOS/CHoCH | DONE → DONE (+ MSS, initial break, break lines) | U |
| Trading lines, entry/SL/TP | DONE → DONE | E |
| Crosshair, price scale | DONE → DONE | E |
| Right tabs, collapsible | DONE → DONE (+ Objects tab) | E |
| Bottom tabs (orders, positions, executions, history, alerts/strategy log, portfolio) | DONE → DONE | E |
| 1/2/4/8 charts; sync crosshair/symbol/timeframe | DONE → DONE | E |
| **Drag to resize** | MISSING → DONE (chart splitters per layout, resizable docks, kept) | E, S 19, U |
| **Chart linking** | PARTIAL → DONE (link-group chip A–D) | E (a11y), U |
| Layout save/restore | DONE → DONE | E |

## §3.2 Interaction table

| Use case | Before → after | Evidence |
|---|---|---|
| Cross-venue search never merges contracts | DONE → DONE | E |
| Live/historical candles, reconnect, duplicates, ordering | DONE → DONE | U `stream.test.ts` |
| Indicator overlay: toggle **+ parameters**, as-of | PARTIAL → DONE (inputs sent as `settings` JSON, decimals as strings) | U, E |
| Measure and draw: drag, snap, reset, save | DONE → DONE | E |
| Manual market order with preview/consent | DONE → DONE | E |
| Limit via right-click, drag to modify | DONE → DONE | E |
| Position management | DONE → DONE | E |
| Builder → backtest → paper → explicit live | DONE → DONE | E |
| Alert click locates; ack, filter, dedupe, audit | DONE → DONE | E |

## Core charts

| Requirement | Before → after | Evidence |
|---|---|---|
| Candles, bars, line, area, Heikin Ashi | DONE → DONE | E |
| Volume pane | PARTIAL → DONE | E |
| Linear/log/percent scales | DONE → DONE | E |
| Crosshair with OHLC**V** | PARTIAL → DONE (data window: OHLC, change, volume, Delta %, compared closes) | E, S 12, U |
| Session separators | MISSING → DONE (UTC day/week/month by timeframe) | U `panes.test.ts`, E |
| Wheel zoom, keyboard shortcuts | PARTIAL → DONE (Alt 0 fit, Alt⇧R replay, Alt⇧S screenshot) | U |
| Timeframes seconds → 1M | PARTIAL → DONE (`1s 5s 15s 30s` … `1M`; seconds axis, minute/hour/year session breaks, calendar-month ends for replay, data window and bar-close countdown; DEMO serves them) | U `contracts/tests/timeframes.test.ts`, `terminal-shell/timeframes.test.ts`, `chart-core/time-scale.test.ts`, `mock-provider/timeframes-and-delta.test.ts`, E `timeframes.spec.ts`, S 24 |
| Custom resolutions | MISSING → DONE (typed `15`/`4h`/`1D`/`5S`/`1M`; unsupported or venue-unpublished refused) | U, E |
| Timeframe favorites | DONE → DONE | E |
| Venue/instrument selector, registry search, health/latency | DONE → DONE | E |
| Up to 8 charts with sync options | DONE → DONE | E |
| Compare with separate price axis | MISSING → DONE | E, S 14 |
| Detachable panes | MISSING → DONE (chart, Market, Watchlist, Screener, Alerts in `/popout`; BroadcastChannel sync of link groups, crosshair, theme, workspace; re-dock on close/heartbeat loss; re-open after reload; same trading guards) | U `terminal-shell/detach.test.ts`, `web/composition/detach.test.ts`, E `popout.spec.ts`, S 21–23 |
| Tool persistence by instrument/timeframe/workspace | DONE → DONE | E |
| Chart templates | MISSING → DONE | E, U |
| Favorites, per-user workspaces | DONE → DONE | E |
| Import/export/share | PARTIAL → DONE (Import command) | U |
| Screenshot | PARTIAL → DONE (PNG with provenance header) | E, U |
| FVG box, polyline | MISSING → DONE (zone box, polyline) | E, S 16, U |
| Selection/editing, snapping, magnet, undo/redo, hotkeys | DONE → DONE | E |
| Per-object visibility and lock | PARTIAL → DONE (object list, style bar) | E, S 17 |
| Drawing colors/style | MISSING → DONE (color, width, dash, fill, extend, text, z-order) | E, U |
| Backend AOIs: BOS/CHoCH, FVG/IFVG, OB/Breaker, BSL/SSL/EQH/EQL, VAH/POC/VAL, P/D/OTE | DONE → DONE | U |
| Delta (estimate / true) | MISSING → DONE (per-bar *true* from taker volume vs faint *estimate* from `values.method`; data window source and buy/sell volume; `delta_source` input) | U `indicator-viz/delta-method.test.ts`, `mock-provider/timeframes-and-delta.test.ts`, E `timeframes.spec.ts`, S 24 |
| Per-indicator visibility, colors, parameters | PARTIAL → DONE (per chart) | U, E |

## §2.4 Drawing milestones

| Level · tool | Before → after | Evidence |
|---|---|---|
| P0 horizontal line/ray, trendline, rectangle, text, crosshair, selection | DONE → DONE (+ ray, extended line) | U `drawing-tools/*` |
| P1 Fib retracement/extension | DONE → DONE | U |
| P1 price range / measured move | DONE → DONE | U |
| P1 long/short, R/R overlay (no auto order) | DONE → DONE | U, E |
| P2 parallel channel, vertical line | DONE → DONE | U |
| P2 brush | MISSING → DONE (freehand, ≤64 time/price anchors) | U `editing.test.ts`, E |
| P2 shape library | MISSING → DONE (arrow, zone box, polyline plus the P3 set: ellipse, circle, triangle, rotated rectangle, arc, curve, pitchfork, Fib time zones/circles, Gann fan/box, XABCD, ABCD, head and shoulders, Elliott impulse/correction, triangle pattern) | U `drawing-tools/*/index.test.ts`, E `extended-drawings.spec.ts`, S 25 |
| P2 anchored VWAP | MISSING → DONE (tool anchors a bar; the engine's `anchored_vwap` series supplies VWAP and bands) | U, E `extended-drawings.spec.ts`, full-stack E, S 26–27 |
| P2 regression channel | MISSING → DONE (two anchors; the engine's `regression_channel` series supplies line, bands, Pearson's R) | U, E `extended-drawings.spec.ts`, S 26 |
| P2 keyboard/touch, snapping, selection, locking | DONE → DONE (+ per-object lock, z-order) | U |

## Screening and alerts

| Requirement | Before → after | Evidence |
|---|---|---|
| Venue filters, statuses, volume, OI, Vol/OI, funding, spread, slippage | DONE → DONE | E |
| Saved filter presets | MISSING → DONE | U, E |
| Dashboard sort: newest | DONE → DONE | E |
| … R multiple | MISSING → DONE | U, E |
| … confluence | MISSING → DONE (satisfied reason-tree conditions) | U |
| … age | MISSING → DONE | U |
| … distance to AOI | MISSING → DONE (engine price vs signal AOIs) | U |
| … volume | MISSING → DONE (*Bar volume* sort and *Bar vol.* column on `bar_quote_volume`, nulls last) | U `alerts/volume.test.tsx`, E `timeframes.spec.ts` |
| Click opens exact venue/symbol/timeframe/AOI | DONE → DONE | E |
| Alert history, acknowledged | DONE → DONE | E |
| Snooze / dismiss | MISSING → DONE (15 min/1 h/4 h, dismiss/restore, show hidden) | U, E, S 18 |
| Channels, quiet hours, subscriptions | DONE → DONE | E `trading-screens.spec.ts` |
| Builder: cards + AST, validation, backtest, versions, deployment | DONE → DONE | E |

## §2.2 Invariants and replay

| Invariant | Before → after | Evidence |
|---|---|---|
| Chart core: no strategy rules/credentials | DONE → DONE | dependency-cruiser |
| Drawing core: anchors, commands, z-index, lock, migration, unknown tools kept | DONE → DONE (z-order/style as commands) | U |
| Indicator viz renders backend records only | DONE → DONE (Delta from `/v1/indicators/series`) | U |
| Chart trading only drafts intents | DONE → DONE | E |
| Workspace: multi-pane, templates, import/export, migration | PARTIAL → DONE | U, E |
| Venue switch clears old overlays | DONE → DONE (also studies and comparisons) | U |
| Bar replay | MISSING → DONE (start bar, step, play/pause, speed, truncation, `at_ms` AOIs, REPLAY provenance, trading off) | U, E, S 15 |

## Residual limits (no requirement open)

| Area | Limit |
|---|---|
| Seconds timeframes | Built by the engine from venue trades. History depth is the venue's trade history: Binance 48 h, Bitget 90 days, Lighter about 53 min; MEXC and Hyperliquid only from when the stream started. |
| `1M` timeframe | Native calendar-month klines on Binance, Bitget and MEXC (and DEMO). Hyperliquid's `1M` is 30-day buckets and Lighter has none, so those venues do not offer it. |
| True delta | From klines' taker volume on Binance and DEMO, and on every venue's seconds bars; other venues' minute-and-above bars stay *estimate* (drawn faint, `method: pine_parity`). |
| Signal ranking by volume | Ranks by venue quote turnover of the signal bar; venues that publish none show *—* and sort last (never estimated). |
| Detachable panes | Each window runs its own provider connection (in DEMO its own simulated broker); browsers allow one window per click, so re-opening several needs several clicks. |
| Anchored VWAP / regression channel | Anchors more than 2000 bars back are refused with a message rather than computed from the wrong bar. |
| Workspace export | Carries layouts, not drawings; drawings persist through the drawing store (as before). |

## Tests added

Vitest (+14 files): `chart-core/panes`, `terminal-shell/charting`,
`indicator-viz/structure-and-study`, `mock-provider/demo-indicators`,
`drawing-core/editing`, drawing-tools `ray`, `extended-line`, `arrow`,
`polyline`, `brush`, `zone-box`; web `shell/charting.test.tsx`,
`alerts/triage.test.tsx`, `watchlist/columns.test.tsx`.
Playwright DEMO: `e2e/charting.spec.ts` (8 tests), screenshots 12–19.

Second increment (2026-11-22): Vitest `contracts/timeframes`,
`terminal-shell/{timeframes,detach}`, `chart-core/time-scale`,
`drawing-core/{series,series-sync,patterns}`, one test file per new drawing
tool, `alerts/ranking`; Playwright `popout.spec.ts`, `timeframes.spec.ts`,
`extended-drawings.spec.ts`; full-stack `fullstack.spec.ts` (seconds bars,
true delta, anchored VWAP from the engine).
