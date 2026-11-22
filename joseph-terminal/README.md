# Joseph Terminal

Open-source, multi-venue perpetual-futures charting and trading terminal built on [Lightweight Charts™](https://www.tradingview.com/lightweight-charts/) (Apache-2.0). It is not affiliated with TradingView and uses none of its proprietary Advanced Charts code or assets.

All indicator values, screening results and strategy decisions come from the Joseph backends over versioned contracts; the terminal renders them and sends order *intents* that the server risk-checks. No venue credentials or signing code ever reach the browser.

## Run

```bash
corepack enable && pnpm install
pnpm dev          # http://localhost:3000 — DEMO mode (synthetic data, clearly labelled)
```

To connect to running engines, set these at build time (see `.env.example`):

```bash
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000
NEXT_PUBLIC_MARKET_WS_URL=ws://localhost:8000
NEXT_PUBLIC_EXECUTION_API_URL=http://localhost:8100
NEXT_PUBLIC_EXECUTION_WS_URL=ws://localhost:8100
NEXT_PUBLIC_DEV_TOKEN=dev-local-token   # development only; production uses a server session
```

## Packages

| Package | Purpose |
|---|---|
| `contracts` | JSON Schemas (single source of truth), generated types, exact `Decimal`, typed HTTP/WS clients, OpenAPI/AsyncAPI |
| `feature-registry` | Feature manifests, lifecycle, dependency resolution and a leak-proof `FeatureContext` |
| `chart-core` | `ChartPort` and the Lightweight Charts adapter (the only importer of the library) |
| `drawing-core`, `drawing-tools` | Anchored drawing documents, commands with undo/redo, migrations; 12 tools |
| `indicator-viz` | Render-only mapping of backend zones and events to overlays |
| `terminal-shell` | Workspace model (1/2/4/8 charts, sync), persistence, hotkeys |
| `chart-trading`, `strategy-builder` | Order-ticket state machine and rule-AST editing models |
| `mock-provider` | DEMO market, broker and strategy services |
| `apps/web` | Next.js app, composition root and React features |

## Scripts

`pnpm lint` · `pnpm typecheck` · `pnpm test` · `pnpm test:e2e` (Playwright + axe, DEMO) · `pnpm test:e2e:fullstack` (needs the private engines) · `pnpm contracts:gen|check` · `pnpm arch:check` · `pnpm secrets:scan` · `pnpm licenses:check` · `pnpm build`

Code follows the Google TypeScript Style Guide, enforced by ESLint/Prettier. See `docs/PLUGIN_AUTHORING.md` to add drawing tools and features.

## License

MIT for this repository (`LICENSE`). Third-party notices: `NOTICE`, `docs/THIRD_PARTY_NOTICES.md`.
