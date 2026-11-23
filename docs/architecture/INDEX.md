# Architecture index

How Joseph fits together, for contributors: the three deployables, where each concern lives, the dependency rules CI enforces and how a candle becomes an order.

```text
Browser: joseph-terminal (Next.js)                    Server side: Python services
┌──────────────────────────────────────────┐         ┌────────────────────────────────────────────┐
│ apps/web  composition root + React views │  HTTP   │ joseph-market-engine  :8000                │
│   features/* (chart, drawing, watchlist, │  + WS   │   market_data → screening                  │
│   screener, strategy, trading, alerts,   │ ──────▶ │   market_data → indicators → strategy      │
│   portfolio) via FeatureContext only     │         │                → signals → alerts ──┐      │
│ packages/                                │         │   backtest (same evaluator)         │      │
│   contracts       schemas, types, clients│         └─────────────────────────────────────┼──────┘
│   feature-registry manifests, lifecycle  │         ┌─────────────────────────────────────▼──────┐
│   chart-core      ChartPort + LWC adapter│  HTTP   │ joseph-execution-engine  :8100  (secrets)  │
│   drawing-core/-tools, indicator-viz     │  + WS   │   intent → risk gateway → OMS → adapters   │
│   terminal-shell, chart-trading,         │ ──────▶ │   accounts/vault, reconciliation, audit    │
│   strategy-builder, mock-provider (DEMO) │         │   adapters: paper, shadow, venues, brokers │
└──────────────────────────────────────────┘         └────────────────────────────────────────────┘
```

LWC is the Lightweight Charts library. The browser holds only a session token; venue credentials never leave the execution engine.

## Where things live

| Concern | Location |
|---|---|
| Contracts (JSON Schema 2020-12) and shared examples | `joseph-terminal/packages/contracts/{schemas,fixtures}/` |
| Market data, screening, indicator SDK, strategies, signals, alerts, backtests | `joseph-market-engine/src/joseph/` ([modules](../../joseph-market-engine/README.md#modules-srcjoseph)) |
| Indicator algorithms (Pine ports) | Private library `apoman123/PineScripts`, `python_indicators/` (pinned; ADR 0009) |
| Risk gateway, OMS, vault, audit, venue and broker adapters | `joseph-execution-engine/src/joseph_execution/` |
| Cross-service system tests | `system-tests/` |
| Composition roots | `*/bootstrap.py`, `joseph-terminal/apps/web/src/composition/` |

## Decision records

The [ADRs](../adr/) record each decision and its reasons. `joseph-terminal/docs/adr/` holds identical copies of 0001–0003, 0007 and 0008 so the terminal can stand alone; change both copies together. "Private" in ADRs 0001–0002 means server side, not repository visibility.

## Dependency rules (enforced in CI)

- **Python** (`.importlinter`, `tests/architecture`): `domain` imports only the standard library; context domains import no framework or infrastructure; indicator plugins are independent; market data never imports downstream contexts; only `bootstrap.py` imports the API layer. Execution venue adapters cannot import the OMS or risk modules, so no adapter can bypass the gateway.
- **TypeScript** (`.dependency-cruiser.cjs`): only `chart-core/src/adapters/lwc` imports `lightweight-charts`; packages never import `apps/*` or React; drawing tools never import each other; indicator-viz never imports trading or strategy packages; browser code never imports Node built-ins or private engine paths; no cycles.

## From candle to order

1. A closed candle feeds the indicator engine (per timeframe, strict higher-timeframe visibility), which builds a `BarFrame`.
2. The strategy evaluator (tri-state, reason tree) and the setup state machine (dedupe, cooldown) emit a `Signal`.
3. The alert outbox delivers it (UI, Telegram, LINE, e-mail, webhook); for PAPER, SHADOW and AUTO_LIVE deployments the forwarder posts it to `/internal/v1/signals`.
4. Deployment sizing creates an `OrderIntent` → risk gateway → OMS → paper or shadow adapter, or a live adapter for a verified account (fail-closed without credentials or with the gate off).
