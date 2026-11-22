# Architecture index

```text
Browser: joseph-terminal (PUBLIC, Next.js)            Private: Python services
┌──────────────────────────────────────────┐         ┌────────────────────────────────────────────┐
│ apps/web  composition root + React views │  HTTP   │ joseph-market-engine  :8000                │
│   features/* (chart, drawing, watchlist, │  + WS   │   market_data → screening                  │
│   screener, strategy, trading, alerts,   │ ──────▶ │   market_data → indicators → strategy      │
│   portfolio) via FeatureContext only     │         │                → signals → alerts ──┐      │
│ packages/                                │         │   backtest (same evaluator)         │      │
│   contracts       schemas, types, clients│         └─────────────────────────────────────┼──────┘
│   feature-registry manifests, lifecycle  │         ┌─────────────────────────────────────▼──────┐
│   chart-core      ChartPort (+LWC adapter)│  HTTP  │ joseph-execution-engine  :8100  (secrets)  │
│   drawing-core/-tools, indicator-viz     │  + WS   │   intent → risk gateway → OMS → adapters   │
│   terminal-shell, chart-trading,         │ ──────▶ │   accounts/vault, reconciliation, audit    │
│   strategy-builder, mock-provider (DEMO) │         │   adapters: paper, shadow, venues (closed) │
└──────────────────────────────────────────┘         └────────────────────────────────────────────┘
```

## Where things live

| Concern | Location |
|---|---|
| Canonical contracts (JSON Schema 2020-12) | `joseph-terminal/packages/contracts/schemas/` |
| Shared example payloads (both stacks validate) | `joseph-terminal/packages/contracts/fixtures/` |
| Market-engine domain kernel (ids, decimals, timeframes, candles, AOIs, BarFrame) | `joseph-market-engine/src/joseph/domain/` |
| Venue adapters, feed router, order books | `joseph-market-engine/src/joseph/market_data/` |
| Qualification filter | `joseph-market-engine/src/joseph/screening/` |
| Indicator SDK, engine and adapters | `joseph-market-engine/src/joseph/indicators/` |
| Indicator algorithms (Pine ports) | `apoman123/PineScripts` → `python_indicators/` (pinned dependency, ADR 0009) |
| Rule AST, compiler, evaluator, setup FSM | `joseph-market-engine/src/joseph/strategy/` |
| Signals, alerts, backtests | `joseph-market-engine/src/joseph/{signals,alerts,backtest}/` |
| Live signal pipeline (candles → engine → runtime) | `joseph-market-engine/src/joseph/pipeline/` |
| Cross-service system tests | `system-tests/` |
| Risk gateway, OMS, vault, audit | `joseph-execution-engine/src/joseph_execution/` |
| Composition roots | `*/bootstrap.py`, `joseph-terminal/apps/web/src/composition/` |
| ADRs | `docs/adr/` |
| Proposal revisions | `docs/PROPOSAL_REVISIONS.md` |

## Dependency rules (enforced in CI)

- Python: `domain` imports only the standard library; context `domain` layers import no framework/infrastructure; indicator plugins are mutually independent; market data never imports downstream contexts; only `bootstrap.py` imports the API layer (`.importlinter`, `tests/architecture`).
- Execution engine: venue adapters cannot import the OMS or risk modules, so no adapter can bypass the gateway.
- TypeScript: only `chart-core/src/adapters/lwc` imports `lightweight-charts`; packages never import `apps/*` or React; drawing tools never import each other; indicator-viz never imports trading or strategy packages; browser code never imports Node built-ins or private engine paths; no cycles (`.dependency-cruiser.cjs`).

## Data flow of a signal

Closed candle (pipeline poll) → indicator engine (per timeframe, strict HTF visibility) → `BarFrame` → strategy evaluator (tri-state + reason tree) → setup FSM (dedupe/cooldown) → `Signal` → alert outbox (UI, Telegram, LINE, email, webhook) and, for PAPER/SHADOW/AUTO_LIVE, the execution forwarder → `/internal/v1/signals` → deployment sizing → `OrderIntent` → risk gateway → OMS → paper/shadow adapter (live adapters fail closed).
