# Joseph market engine

The market engine serves market data, perpetual screening, indicators, strategies, signals, alerts and backtests over HTTP and WebSocket (FastAPI, port 8000). This page is for developers; settings are in the [operations runbook](../docs/OPERATIONS.md#market-engine).

> **Private dependency.** `uv sync` installs the indicator library `joseph-smc-indicators` from the private repository `apoman123/PineScripts` (pinned in `pyproject.toml`) and fails without read access to it ([access](../docs/OPERATIONS.md#start)).

## Run and test

```bash
uv sync                                              # add --extra sinopac for SinoPac data
JOSEPH_DEV_TOKENS=dev-local-token:tenant-01:user-01 \
  uv run uvicorn joseph.bootstrap:create_application --factory --port 8000
uv run pytest                                        # all offline tiers
JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network   # opt-in live venue checks
uv run python scripts/probe_venues.py                # connectivity evidence
```

## Modules (`src/joseph/`)

| Package | Responsibility |
|---|---|
| `domain/` | Standard-library kernel: ids, decimals, timeframes, instruments, session calendars, candles, areas of interest (AOIs), `BarFrame` |
| `market_data/` | Venue adapters, seconds bars, trade recorder and archives, aggregated timeframes, feed router (AUTO / NATIVE_WS / REST_POLL / REPLAY), candle store, order books |
| `screening/` | Five strict gates, funding risk, three-state qualification with reasons |
| `indicators/` | Plugin SDK, multi-timeframe engine, adapters for the indicator library ([README](src/joseph/indicators/README.md)) |
| `strategy/` | Rule AST v2, compiler, tri-state evaluator, setup state machine, immutable versions |
| `signals/`, `alerts/` | Deduplicated signals; durable outbox to the UI, Telegram, LINE, e-mail, webhooks and the execution engine |
| `pipeline/`, `backtest/` | Live loop over closed candles (history warms up without firing); backtests on the same evaluator |
| `api/`, `bootstrap.py` | FastAPI routers; composition root |

## Venues

`GET /v1/venues` lists only implemented, verified capabilities; any other request is refused with `UNSUPPORTED`. Every venue except Alpaca and Treasury has order-book snapshots; only SinoPac data streams books.

### Crypto perpetuals (verified live 2026-11-22/23)

| Venue | Klines | `1M` | Taker volume | Trade history (REST) | Public archive |
|---|---|---|---|---|---|
| Binance USDⓈ-M | all, REST + WS | native | yes | 48 h | `data.binance.vision`, sha256-checked; next day about 07:30 UTC |
| Bitget | UTC granularities, ≤90-day windows (older via `history-candles`) | native | no | 90 days | Download page; UTC+8 days, about a day late; checked against the `1D` kline |
| Hyperliquid | `1m`…`1d` except `6h`; Thursday `1w` refused | from `1d` | no | none | S3, requester-pays: not used |
| Lighter | `1m 5m 15m 30m 1h 4h 12h 1d`, REST only | from `1d` | no | about 53 min | AWS Parquet range reads (about 14–19 MB per market-day, nothing on disk) from 2025-01-17, about 7 h after the UTC day; checked against Lighter's `1d` candle |
| MEXC futures | `1m`…`1M` | native | no | latest 100 only | none |
| `mock` (DEMO) | all, synthetic | yes | yes | synthetic | — |

Trade streams and their aggressor side (checked against the best bid and ask):

| Venue | Channel | Aggressor side |
|---|---|---|
| Binance | `<sym>@aggTrade` | `m=true` is a sell |
| Bitget | `trade` | `side` is the taker's |
| Hyperliquid | `trades` | `B` buy, `A` sell |
| Lighter | `trade/<market_id>` | `is_maker_ask=true` is a buy |
| MEXC | `push.deal` | `T` 1 buy, 2 sell; size is `v` × `contractSize` |

- **Seconds bars** (`1s 5s 15s 30s`, `market_data/domain/trade_bars.py`) fold trades into OHLC, volume, turnover, trade count and taker buy volume. Empty seconds inside one coverage segment are flat zero-volume bars; nothing is filled across a disconnect. Reconnects backfill from REST where possible (Hyperliquid's 30-trade replay can prove continuity); otherwise a gap is recorded and `REST_POLL` of seconds is refused.
- **True delta** on venues without kline taker volume needs recorded trades that cover the whole bar and match its volume. Lighter liquidation, deleverage and settlement fills are excluded, as in Lighter's candles.
- **Aggregated timeframes** fold a shorter native timeframe that tiles exactly; a missing child bar is a gap.
- **MEXC** market data is Protobuf by default (`JOSEPH_MEXC_WS_FORMAT`; JSON ends 2026-12-01 10:00 UTC). The vendored `.proto` files are Apache-2.0 (`adapters/mexc_proto/NOTICE`).

### Securities and rates

| Venue | Source | Calendar | Intraday | Delta |
|---|---|---|---|---|
| `twse`, `tpex` | Official lists, daily reports and MIS snapshots; optional SinoPac | XTAI | Recorded MIS (coarse, no seconds) or Shioaji | Classified (quote rule; Shioaji `tick_type`) |
| `nasdaq`, `nyse`, `cboe` | Nasdaq Trader listings, Alpaca data | XNYS | Alpaca minutes on the XNYS grid; seconds from 30 min of trades | Classified (Lee-Ready) |
| `treasury` | Treasury.gov par and real yields | USBOND | Daily only; not tradable | — |

#### Taiwan

`adapters/twse.py` and `tpex.py` share one `TaiwanExchangeAdapter`. Ids: `twse:stock:2330`, `tpex:etf:00679B`. Bars are sparse with stated coverage; recorded bars persist across restarts.

- **Rate limits:** MIS responses are cached server-side (data advances every 4–120 s); TWSE blocks clients above about 3 requests per 5 s, shared by TWSE and TPEx. www.tpex.org.tw is paced at 1 request per 2 s.
- **TPEx listings** come from the OpenAPI datasets `tpex_mainboard_quotes`, `mopsfin_t187ap03_O`, `tpex_ipo_no_limit` and `tpex_cmode`: stocks and ETFs, including bond ETFs (sixth character `B`, or `D` for active; asset class `BOND`). Not served: 興櫃 (emerging board), foreign-currency counters (`K`/`C`), preferreds, ETNs, warrants. Suspended companies are `HALTED`.
- **TPEx history** goes back to 1994 with volume exact to 1,000 shares, including odd-lot and after-hours trades but not block trades; prices are unadjusted. TPEx uses the XTAI calendar (its daily index history has exactly the XTAI sessions in every month 2005–2026); new closures are added to XTAI at runtime. Screening uses TWD thresholds (`tpex_equity`, `tpex_bond` in `screening/domain/policy.py`).
- **SinoPac data** (`adapters/sinopac.py`, `JOSEPH_TW_DATA_SOURCE=sinopac`): 1m kbars back to 2020-03-02 (3m–12h aggregated), live 1m and seconds bars from ticks, seconds history from tick history, streamed 5-level books, classified buy volume. Board lots only (no odd-lot, after-hours or block trades); listings, limits and `1d`/`1w`/`1M` stay official. Quotas: 50 queries per 10 s; 10 tick and 270 kbar queries in trading hours; 500 MB–10 GB of data a day (reset 08:00 Taipei); 200 subscriptions; 5 connections per person, shared with trading. On failure it falls back to official data (`twse.mis`/`tpex.mis`, `DEGRADED`); a bar never mixes sources. Opt-in test, outside trading hours: `JOSEPH_SINOPAC_DATA_TESTS=1 uv run --extra sinopac pytest tests/network/test_live_sinopac.py -m sinopac_data`.

#### United States

`adapters/alpaca.py` needs an Alpaca market-data key. Exchange `Z` in Nasdaq Trader's `otherlisted` file maps to `cboe` (e.g. `cboe:etf:GOVT`); IEX, NYSE Texas, TXSE and OTC listings, preferreds, units, warrants and rights are skipped. Free plan: 200 requests per minute, one stream, 30 symbols. Ticks are 0.01, or 0.0001 below $1.

- **Closing print:** on `sip`/`delayed_sip`, each session's official closing print (condition `6`) is read once per symbol from `[close, close + 60 s)` and folded into the last minute bar, so 1m, 1h, 4h and 1d closes agree. At most 20 requests per fetch and 60 per minute; until read, that minute stays out of coverage (a gap). `iex` reads nothing.

## API

- OpenAPI: [`market-engine.openapi.json`](../joseph-terminal/packages/contracts/openapi/market-engine.openapi.json) (`uv run python scripts/export_openapi.py`).
- WebSocket (AsyncAPI): [`joseph-streams.asyncapi.yaml`](../joseph-terminal/packages/contracts/asyncapi/joseph-streams.asyncapi.yaml).
