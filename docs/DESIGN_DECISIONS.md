# Design decisions

How the built platform differs from its original, unpublished design plan (V5): what the plan said, what was built instead and why. For contributors; the core architecture decisions are in the [ADRs](adr/).

## Contracts and API

| # | Original plan | Built | Why |
|---|---|---|---|
| D1 | No single instrument string (`BINANCE:PERP:BTCUSDT`, `hyperliquid:perp:BTC:USDC` and `BTC-USDT-PERP@BINANCE` all appear) | `{venue}:{market_type}:{venue_market_id}` with a lowercase venue, e.g. `binance:perp:BTCUSDT`. Quote, settlement and version live in `InstrumentKey` | One regex-validated id in both stacks; a settlement change bumps `instrument_version`, not the id |
| D2 | Three rule-AST shapes (`op`, `all`/`any`, `ref`) | No rule AST: a strategy is a Python `joseph_script.Strategy` subclass (built-in, plugin or tenant version) activated through `/v1/strategy-activations`; backtests are script runs ([ADR 0010](adr/0010-python-strategy-scripts.md), superseding the Rule AST v2 of ADR 0005) | One strategy format, one simulator for backtests and live signals, and no strategy vocabulary in platform contracts |
| D12 | No defined response for a submission the risk checks block | `POST /v1/orders/intents` records and audits the blocked intent and returns `201` with the `RiskDecision` and `orders: []` | The UI shows every failed check; the audit captures every attempt |

## Terminal

| # | Original plan | Built | Why |
|---|---|---|---|
| D3 | `packages/drawing-tools/*/` as separate packages | One `@joseph/drawing-tools` package, one directory per tool; cross-tool imports forbidden | Same removability with 1 package instead of 13 |
| D4 | `chart-indicators`, `indicator-viz-core` + `indicator-viz/*` | One `@joseph/indicator-viz` that maps backend records to declarative `OverlayLayer`s | Data-driven rendering; per-plugin modules stay separable |
| D5 | React UI inside `strategy-builder` / `chart-trading` | Framework-free packages; React views in `apps/web/src/features/*` | Logic is reusable and testable without a DOM |
| D6 | Features draw through `ChartEngineAdapter` methods | Features publish declarative layers (`price-line`, `box`, `marker`) through `FeatureContext.chartLayers`; only `chart-core` touches Lightweight Charts | Features cannot change chart state, a rule the plan's architecture tests check |

## Storage, fixtures and integrations

| # | Original plan | Built | Why |
|---|---|---|---|
| D7 | Parquet golden fixtures | JSON/JSONL fixtures with a loader ready for Pine exports | Diffable; no binary dependency; Parquet remains possible for bulk data |
| D8 | PostgreSQL from day one | SQLAlchemy Core; SQLite for local runs, development and tests, PostgreSQL through `JOSEPH_DATABASE_URL` | Same SQL and constraints with zero-setup local runs; PostgreSQL stays the production target |
| D9 | LINE notifications | LINE Messaging API push (LINE Notify ended on 2025-03-31) | Same channel on a supported API |
| D10 | Venue access through CCXT | Direct `httpx`/`websockets` adapters | Exact control of timestamps, finality, sequences and capabilities; no hidden fallbacks |

## Markets, timeframes and history

| # | Original plan | Built | Why |
|---|---|---|---|
| D11 | Seconds timeframes | First release: 1m minimum. Superseded by D14 | That release ingested no trades |
| D14 | Seconds through 1M, estimated and true delta, anchored VWAP and regression channel | `1s/5s/15s/30s` built from public trades and declared in `trade_built_timeframes`; `1M` is a calendar month, and all boundary math goes through `Timeframe.shift`/`end_of`. True delta from taker buy volume (`method: trade`), the Pine estimate as fallback. VWAP and regression are built-in engine series | No perpetual venue publishes seconds klines. Values stay engine-computed and labelled; nothing is approximated in the browser or passed off as Pine parity |
| D15 | Crypto perpetuals on five venues | Adds TWSE and TPEx (official free sources), Nasdaq, NYSE and Cboe BZX (Alpaca market data, free key; Nasdaq Trader listings), Treasury par and real yields (`treasury:rate:*`, not tradable) and bond ETFs. Venues `twse`, `tpex`, `nasdaq`, `nyse`, `cboe`, `treasury`; market types `stock`/`etf`/`rate`; asset class `BOND`. Every bar boundary follows a session calendar (`domain/sessions.py`: XTAI from TWSE's observed trading days, XNYS by NYSE rules, USBOND from Treasury's publication days, crypto `24x7`). Stock delta is classified (Lee-Ready / quote rule) and labelled `classified`, never `taker` | Scope extends to Taiwan and US stocks and US bonds, on free data where possible. Session bars match TradingView regular-hours charts; delta provenance stays honest |
| D17 | Seconds and true-delta history limited to what venues return (D14) | Public trades are recorded and stored; the Binance, Bitget and Lighter public trade archives are imported; missing timeframes are aggregated from shorter native ones (e.g. `1M` from `1d` on Hyperliquid and Lighter); bars of 1m and longer get true delta only when recorded trades cover and match them | History grows beyond venue API limits without invented data |

## Indicators

| # | Original plan | Built | Why |
|---|---|---|---|
| D13 | Indicators inside the market engine (`plugins/<id>`) | Nine generic built-ins in `joseph/indicators/builtin/` (TradingView built-in semantics); every other indicator is a plugin package discovered through the entry-point group `joseph.indicator_plugins` and described by its own metadata ([ADR 0009](adr/0009-external-indicator-library.md)) | "平台歸平台，指標歸指標" (the platform is a platform, indicators are indicators): the public platform builds and runs without private code or strategy-specific vocabulary, and indicator packages evolve and are licensed on their own |
| D18 | CI fetches private indicator code over SSH | No private access in CI: tests use the built-ins. Images take plugin packages from the build arg `PLUGIN_REQUIREMENTS` (CI: variable `MARKET_PLUGIN_REQUIREMENTS`) with the optional BuildKit secret `plugin_git_token` (CI: secret `PLUGIN_GIT_TOKEN`, a read-only token) | Forks run every job; operators choose plugins by configuration; credentials reach only the one build step |

## Live trading

| # | Original plan | Built | Why |
|---|---|---|---|
| D16 | LIVE adapters fail closed | LIVE orders on Binance, Bitget (Unified Trading Account), Hyperliquid, Lighter and MEXC (KYC-verified keys); securities through Interactive Brokers and SinoPac; Cathay refuses every order (no public API; its SDK is pending); Firstrade is out of scope (no official API). The deploy configuration enables the global gate; manual LIVE orders also need server-side, step-up, time-limited account arming (else `LIVE_NOT_ARMED`); strategy orders (AUTO_LIVE) keep deployment consent; risk checks, kill switch, idempotency and audit stay mandatory | Live trading on real accounts is in scope. Server-side arming replaces a browser-only acknowledgement |
