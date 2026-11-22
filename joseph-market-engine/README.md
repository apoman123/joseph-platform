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
| `market_data/` | Venue adapters (httpx + websockets; klines and public trades), seconds bars built from trades, feed router (AUTO / NATIVE_WS / REST_POLL / REPLAY), candle store, order books |
| `screening/` | Five strict gates, funding risk, three-state qualification with reasons and policy version |
| `indicators/` | Plugin SDK, multi-timeframe engine (no look-ahead), adapters for the eight indicators of the external `smc_indicators` library (`apoman123/PineScripts`) |
| `strategy/` | Rule AST v2 models, compiler, tri-state evaluator with reason trees, setup state machine, immutable versions |
| `signals/`, `alerts/` | Signal records with dedupe keys; durable alert outbox → UI stream, Telegram, LINE Messaging API, e-mail, webhook, execution forwarder |
| `pipeline/` | Live loop: closed candles → indicator engine → runtime (history warms up without firing) |
| `backtest/` | Same evaluator over engine frames, conservative fills, metrics |
| `api/`, `bootstrap.py` | FastAPI routers; dependency-injection composition root |

## Venues (verified against live public APIs, 2026-11-22)

Capabilities (`GET /v1/venues`) list only what is implemented and verified; everything else is refused with `UNSUPPORTED`, never approximated.

| Venue | Klines (REST history + native WS) | `1M` | Taker buy volume | Trade stream (WS) | Trade history (REST, time range) | Seconds `1s 5s 15s 30s` |
|---|---|---|---|---|---|---|
| Binance USDⓈ-M | every timeframe `1m`…`1M` | native `1M` | yes: kline index 9 / `k.V` | `<sym>@aggTrade` on `/market/ws` | `aggTrades`, last 48 h | built from trades, 48 h history |
| Hyperliquid | `1m`…`1d` except `6h`; `1w` (Thursday-anchored) refused | no: the venue's `1M` is 30-day epoch buckets | no | `trades` | none | built from the live stream only |
| Bitget futures | UTC granularities (`6Hutc`…`1Mutc`); windows ≤ 90 days, older ranges via `history-candles` | native `1Mutc` | no | `trade` | `fills-history`, last 90 days, 7-day windows | built from trades, 90-day history |
| MEXC futures | `1m 5m 15m 30m 1h 4h 1d 1w 1M` | native `Month1` | no | `push.deal` | none (latest 100 only) | built from the live stream only |
| Lighter | `1m 5m 15m 30m 1h 4h 12h 1d`, REST only (AUTO polls; the venue's WS candle channel is not wired) | no (venue has no `1w`/`1M`) | no | `trade/<market_id>` | `trades`, about 53 min back | built from trades, about 53 min history |
| `mock` (DEMO) | every timeframe, synthetic | calendar months | synthetic, every bar | synthetic | synthetic | built from synthetic trades that sum exactly to the 1m bars |

Aggressor side (each checked live against the contemporaneous best bid/ask): Binance `m=true` (buyer is maker) is a **sell**; Bitget `side` is the taker side; MEXC `T` 1 = buy, 2 = sell (`v` is in contracts, converted with `contractSize`); Hyperliquid `side` "B" = buy, "A" = sell; Lighter `is_maker_ask=true` = **buy**.

Seconds bars (`market_data/domain/trade_bars.py`) are folded from public trades: OHLC, base volume, quote turnover, trade count (null for aggregated fills) and `taker_buy_base_volume` (null if any trade lacks a side). Empty seconds between two observed trades of one continuous coverage segment become flat zero-volume bars; nothing is filled before the first trade, after the last one or across a disconnect. After a reconnect missed trades are backfilled from the REST history where one exists (Hyperliquid's 30-trade replay can also prove continuity); otherwise a gap is recorded and `REST_POLL` of seconds bars is refused. `source` is `<venue>.trades` (stream) or `<venue>.trades.rest` (history).

Order-book streams are not wired for any venue yet; capabilities say so.

## Configuration

See `docs/OPERATIONS.md` for every variable. The key ones are `JOSEPH_DEV_TOKENS`, `JOSEPH_SERVICE_TOKEN`, `JOSEPH_ENABLED_VENUES` / `JOSEPH_DEMO_ONLY`, `JOSEPH_DATABASE_URL`, `JOSEPH_EXECUTION_URL`, `JOSEPH_PIPELINE_*` and the notification-channel credentials.

## API

OpenAPI: `../joseph-terminal/packages/contracts/openapi/market-engine.openapi.json` (regenerate with `uv run python scripts/export_openapi.py`). WebSocket protocol: `../joseph-terminal/packages/contracts/asyncapi/joseph-streams.asyncapi.yaml`.
