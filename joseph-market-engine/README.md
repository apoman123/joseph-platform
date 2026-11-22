# Joseph Market Engine (private)

Python-authoritative market data, perpetual qualification, the seven SMC indicator plugins, the strategy rule AST evaluator, signals, alerts and backtests. FastAPI on port 8000.

```bash
uv sync
JOSEPH_DEV_TOKENS=dev-local-token:tenant-01:user-01 \
  uv run uvicorn joseph.bootstrap:create_application --factory --port 8000
uv run pytest                 # all offline tiers
JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network   # live venue checks
uv run python scripts/probe_venues.py                # connectivity evidence
```

## Modules (`src/joseph/`)

| Package | Responsibility |
|---|---|
| `domain/` | Standard-library kernel: ids (UUIDv7), decimals, timeframes, instruments, candles, AOIs, `BarFrame` |
| `market_data/` | Venue adapters (httpx + websockets), feed router (AUTO / NATIVE_WS / REST_POLL / REPLAY), candle store, order books |
| `screening/` | Five strict gates, funding risk, three-state qualification with reasons and policy version |
| `indicators/` | Plugin SDK, multi-timeframe engine (no look-ahead), adapters for the eight indicators of the external `smc_indicators` library (`apoman123/PineScripts`) |
| `strategy/` | Rule AST v2 models, compiler, tri-state evaluator with reason trees, setup state machine, immutable versions |
| `signals/`, `alerts/` | Signal records with dedupe keys; durable alert outbox → UI stream, Telegram, LINE Messaging API, e-mail, webhook, execution forwarder |
| `pipeline/` | Live loop: closed candles → indicator engine → runtime (history warms up without firing) |
| `backtest/` | Same evaluator over engine frames, conservative fills, metrics |
| `api/`, `bootstrap.py` | FastAPI routers; dependency-injection composition root |

## Venues (verified against live public APIs)

| Venue | REST history / snapshot / OI / funding / book | Native WS candles |
|---|---|---|
| Binance USDⓈ-M | yes | yes |
| Hyperliquid | yes | yes (6h bars refused; weekly bars are Thursday-anchored at the venue and refused) |
| Bitget futures | yes | yes |
| MEXC futures | yes | yes |
| Lighter | yes | no: reported unsupported; AUTO polls and labels it `POLL` |
| `mock` | synthetic DEMO data | simulated |

WebSocket trades and order-book streams are not wired for any venue yet; capabilities say so.

## Configuration

See `docs/OPERATIONS.md` for every variable. The key ones are `JOSEPH_DEV_TOKENS`, `JOSEPH_SERVICE_TOKEN`, `JOSEPH_ENABLED_VENUES` / `JOSEPH_DEMO_ONLY`, `JOSEPH_DATABASE_URL`, `JOSEPH_EXECUTION_URL`, `JOSEPH_PIPELINE_*` and the notification-channel credentials.

## API

OpenAPI: `../joseph-terminal/packages/contracts/openapi/market-engine.openapi.json` (regenerate with `uv run python scripts/export_openapi.py`). WebSocket protocol: `../joseph-terminal/packages/contracts/asyncapi/joseph-streams.asyncapi.yaml`.
