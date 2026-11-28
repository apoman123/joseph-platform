# Joseph Terminal

Open-source browser terminal for charting and trading crypto perpetuals, stocks, ETFs and Treasury yields, built on [Lightweight Charts™](https://www.tradingview.com/lightweight-charts/) (Apache-2.0); not affiliated with TradingView and free of its proprietary Advanced Charts code. This README is for anyone trying, deploying or developing it.

## Try it in DEMO mode

Needs Node.js 20.19+ (Corepack provides pnpm).

```bash
corepack enable && pnpm install
pnpm dev          # http://localhost:3000
```

With no backend configured, the terminal runs in **DEMO mode**: synthetic perpetuals, stocks, bond ETFs and a yield on the `mock` venue, generated in the browser, and a simulated paper broker. Every chart is labelled *Demo data*, and every panel works: drawing, built-in indicator studies, screener, strategies, alerts, backtests, paper trading. `?demo-live-rehearsal=1` adds a simulated account to rehearse LIVE arming (code `123456`).

## Connect to the engines

Two external services do the work over HTTP and WebSocket, following the versioned contracts in `packages/contracts`:

| Service | Provides |
|---|---|
| Market engine | Instruments, candles, indicators, screening, strategies, signals, backtests |
| Execution engine | Accounts, orders, risk gateway, positions, kill switch, audit |

Set their URLs at build time (Next.js inlines them; see `apps/web/.env.example`):

```bash
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000
NEXT_PUBLIC_MARKET_WS_URL=ws://localhost:8000      # defaults to the API URL
NEXT_PUBLIC_EXECUTION_API_URL=http://localhost:8100
NEXT_PUBLIC_EXECUTION_WS_URL=ws://localhost:8100
NEXT_PUBLIC_DEV_TOKEN=dev-local-token              # development only
```

- A configured market engine ends DEMO mode and never falls back to synthetic data: failures show as *Stale* or *Unavailable*. Without an execution engine, trading fails closed.
- The browser sends order intents only; venue credentials, signing and the LIVE switch stay in the execution engine, which risk-checks every order.
- Production needs a server-side session instead of `NEXT_PUBLIC_DEV_TOKEN`; never put real credentials in `NEXT_PUBLIC_*`.

The `Dockerfile` builds a standalone image on port 3000; pass the same variables as `--build-arg` (none gives a DEMO image).

## Packages

| Package | Purpose |
|---|---|
| `contracts` | JSON Schemas (source of truth), generated types, `Decimal`, HTTP/WS clients, OpenAPI/AsyncAPI |
| `feature-registry` | Feature manifests, lifecycle, dependencies, the `FeatureContext` |
| `chart-core` | `ChartPort` and the Lightweight Charts adapter (its only importer) |
| `drawing-core`, `drawing-tools` | Anchored drawings, undo, migrations, engine-series sync; 37 tools |
| `terminal-shell` | Workspace, sync, sessions, persistence, hotkeys |
| `chart-trading` | Order-ticket state machine |
| `mock-provider` | DEMO market, broker and strategy services |
| `apps/web` | Next.js app, composition root, React features |

## Develop

`pnpm lint` · `pnpm typecheck` · `pnpm test` · `pnpm test:e2e` (Playwright + axe, DEMO) · `pnpm contracts:gen|check` · `pnpm arch:check` · `pnpm secrets:scan` · `pnpm licenses:check` · `pnpm build` · `pnpm ci` (everything but the browser tests). `pnpm test:e2e:fullstack` starts both engines from sibling directories `../joseph-market-engine` and `../joseph-execution-engine`.

Code follows Google's TypeScript, JavaScript and HTML/CSS style guides (ESLint, stylelint, Prettier, JSDoc). Extensions: [plugin authoring](docs/PLUGIN_AUTHORING.md). Architecture decisions: [docs/adr](docs/adr/).

## License

AGPL-3.0-only ([LICENSE](LICENSE)). Third-party notices: [NOTICE](NOTICE) and [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md); the Lightweight Charts attribution also shows in the status bar and on `/notices`.
