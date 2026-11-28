# TradingView-style use-case audit (Joseph Terminal)

Checks every TradingView-like requirement of the original design plan against the code in `joseph-terminal/` and its tests. For users comparing Joseph with TradingView and contributors looking for test evidence.

The requirements cover the plan's use cases, layout, interactions, core charts, drawing-tool milestones P0–P2 (the plan ships drawing tools in batches P0–P3), screening and alerts, and chart-layer invariants. Tests re-verified them on 2026-11-22 and 2026-11-23 (session markets). Evidence: **U** Vitest file, **E** Playwright DEMO spec in `apps/web/e2e/`, **S** screenshot number in `apps/web/e2e/screenshots/`.

| 94 requirements | DONE | PARTIAL | MISSING |
|---|---:|---:|---:|
| Before | 54 | 16 | 24 |
| After | 94 | 0 | 0 |

## Closed by the audit (40; P = was partial, M = was missing)

- **Use cases:** feed-mode select per pane (P; E `charting.spec.ts`, S 12) · watchlist *Qualified perpetuals* source (P; U `watchlist/columns.test.tsx`) · indicator layers and Delta % pane (P; U `layers-and-study.test.ts`, S 12).
- **Layout and core charts:** compare, ≤3 symbols on a left % axis (M; E, S 14) · indicator settings dialog from `input_schema`, inputs sent as `settings` JSON (P; E, S 13) · templates (M; E, U) · volume pane (P; E) · Delta % indicator pane, true vs faint estimate (M; S 12, 24) · drag to resize (M; S 19) · link groups A–D (P; U) · data window with OHLCV, Delta % and compared closes (P; S 12) · session separators (M; U `panes.test.ts`) · Alt 0 / Alt⇧R / Alt⇧S shortcuts (P; U) · timeframes `1s`…`1M` with calendar months and countdown (P; U `timeframes.test.ts`, `time-scale.test.ts`, E `timeframes.spec.ts`, S 24) · typed custom resolutions (M; U, E) · detachable panes with BroadcastChannel sync, re-dock and reload survival (M; U `detach.test.ts`, E `popout.spec.ts`, S 21–23, 35–36) · import with drawings (P; E `workspace-export.spec.ts`, S 37) · screenshot with provenance (P; E) · zone box and polyline (M; S 16) · object list and style bar (P; S 17) · drawing colors and style (M; E, U) · true / classified / estimate delta with `delta_source` (M; U `delta-method.test.ts`, S 24) · per-chart indicator visibility, colors and parameters (P; U, E).
- **Drawings:** brush, ≤64 anchors (M; U `editing.test.ts`) · shape library incl. the P3 set (M; U `drawing-tools/*`, E `extended-drawings.spec.ts`, S 25) · anchored VWAP and regression channel from engine series (M ×2; U, E, full-stack E, S 26–27).
- **Screening and alerts:** filter presets (M) · sort by R multiple, confluence, age and bar volume (M ×4; U `alerts/volume.test.tsx`) · snooze and dismiss (M; S 18).
- **Chart-layer invariants:** workspace templates, import/export and migration (P) · bar replay with `at_ms` AOIs, REPLAY provenance and trading off (M; S 15).

Session markets (2026-11-23) added no-gap session axes, the status badge, the asset-class picker, rate lines without a ticket, 張 volumes, the equity ticket and LIVE arming (U `contracts/sessions`, `sessions-axis`, `shell/sessions`; E `sessions.spec.ts`, `trading-equity.spec.ts`; S 30–34, 38–41).

## Already done (54), re-verified

Mostly by E specs (`terminal`, `trading-*`) and U `stream.test.ts`: the use-case rule builder, notifications with one-click locate, chart orders, gated auto-live and kill switch, standalone DEMO; the layout's toolbar (now 18 grouped tools), selector, timeframe control, chart type, layout menu, data-quality badge, candles, AOI boxes, event markers, trading lines, crosshair, tabs, 1/2/4/8 sync, layout save; all interactions; core chart types, scales, favorites, health, persistence, workspaces, editing and hotkeys, backend AOIs; drawing milestones P0–P2 (lines, Fibonacci, ranges, long/short, channels); screener filters, alert locate, history, channels, builder and deployments; chart-layer boundaries (chart core holds no rules or credentials, indicator-viz renders backend records only, chart trading drafts intents only, venue switch clears overlays).

No requirement in that original design plan was open at the audit date.
This is not a claim of TradingView feature parity. See the broader
[2026-11-26 comparison and roadmap](TRADINGVIEW_GAPS_2026_11_26.md). Data-depth limits are in the [user manual](../USER_MANUAL.md#delta) and the [README](../../README.md#known-limitations).
