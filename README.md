# Joseph trading platform

Joseph is a self-hosted platform for charting, researching and trading crypto perpetuals, Taiwan and US stocks and ETFs, with US Treasury yields as reference data. It has three parts: a browser charting terminal, a market engine (data, screening, indicator plugins, strategies, alerts, backtests) and an execution engine (risk gateway, order management, paper, shadow and live trading, audit, kill switch).

> **Risk disclaimer.** Joseph is research software, provided as is under the AGPL-3.0-only licence with no warranty. Live trading can lose money. The LIVE adapters are tested only against mocked venues and fake broker clients, never with real orders. Nothing in this repository is financial advice.

## Markets and venues

| Market | Venues | Market data | Trading |
|---|---|---|---|
| Crypto and reference markets | Binance, Bybit, OKX, Coinbase, Kraken, Deriv | Native public APIs; Binance/Bybit/Deriv WebSockets and explicit REST polling for the other three; no key | Existing Binance, Bitget, Hyperliquid, Lighter and MEXC trading adapters remain separate; new data sources do not enable trading |
| Taiwan stocks and ETFs | TWSE, TPEx (mainboard) | Fugle free-tier per-symbol data with a key | Paper and shadow; LIVE through SinoPac Shioaji. Cathay is wired in but refuses every order (no public API) |
| US stocks and ETFs | Nasdaq, NYSE, Cboe BZX | Alpaca market data; needs a free Alpaca key (IEX real time by default) | Paper and shadow; LIVE through Interactive Brokers (IB Gateway). Alpaca is data only |
| US Treasury yields | Par and real yield curves; separate FRED rate series | Treasury.gov without a key; FRED with a free key | Data only |
| DEMO | `mock` | Synthetic, clearly labelled | Paper |

Futures and options are not supported.

**Trading modes.** PAPER fills on a simulated venue; SHADOW records what it would send and sends nothing; LIVE sends real orders and needs all of: the global gate `JOSEPH_LIVE_TRADING_ENABLED` turned on (off in code, on in `deploy/`), a verified account, step-up arming of the account (manual orders) or step-up consent for an AUTO_LIVE deployment (strategies), an order preview, a pass from the risk gateway and no engaged kill switch ([LIVE enablement](docs/OPERATIONS.md#live-enablement)).

## Architecture at a glance

| Path | Port | What it is |
|---|---|---|
| `joseph-terminal/` | 3000 | Next.js terminal: charts, drawing tools, screener, Python strategy panel, order tickets. Renders engine outputs; holds only a session token. Own licence; can become a separate repository |
| `joseph-market-engine/` | 8000 | FastAPI service: market data, screening, indicators (built-ins and plugin packages), Python scripts and strategy activations, signals, alerts, backtests. Holds no trading credentials |
| `joseph-execution-engine/` | 8100 | FastAPI service: accounts and an encrypted credential vault, risk gateway, order management system (OMS), reconciliation, audit log, kill switch. The only holder of trading credentials |
| `system-tests/` | | Cross-service tests that boot both engines |
| `deploy/` | | Docker Compose stack for the three services (plus optional IB Gateway) |
| `docs/` | | Manual, runbook, architecture, ADRs, reports |

**Trust boundary.** The browser never sees venue credentials or signing code; it sends order *intents*. Every order (manual, chart, emergency or strategy) takes one path inside the execution engine: intent → risk gateway (deny by default) → OMS → venue or broker adapter. The market engine forwards strategy signals with a separate service token. Details: [architecture index](docs/architecture/INDEX.md).

## Getting started

### Try the terminal (DEMO)

Needs Node 20.19+ with corepack (pnpm 10). No keys and no engines:

```bash
cd joseph-terminal
corepack enable && pnpm install
pnpm dev   # http://localhost:3000, DEMO mode: synthetic data, clearly labelled
```

### Full stack

Needs uv with Python 3.12+ and Node 20.19+ with corepack.

```bash
make setup   # locked installs for both engines, system tests and the terminal
make dev     # market :8000, execution :8100 (paper; LIVE off), terminal http://localhost:3000
```

`make dev` uses development tokens; `make help` lists every target.

### Market-data keys

Set these on the **market engine**, not in the browser. Both `make dev` and
Docker Compose read data-feed settings from `deploy/.env`, copied from
`deploy/.env.example`. Exported environment variables take precedence.
Restart the market engine after changing keys. For another file, run
`make dev-market MARKET_ENV_FILE=/absolute/path/to/market.env`. Direct uvicorn
launches can set `JOSEPH_MARKET_ENV_FILE` to that path. Only allowlisted market-data and alert-delivery
settings are imported from this file by the market engine. Trading credentials
remain in the execution engine.
A missing optional key leaves only that source
unavailable. Trading API keys and broker credentials belong to the separate
execution engine ([broker setup](docs/BROKERS.md)).

| Data feed | Required settings | What is available without a key | Free-plan scope in this implementation |
|---|---|---|---|
| Binance and Bybit native APIs | None for public data | Native exchange candles and real-time candle WebSockets | Binance USDⓈ-M; Bybit spot, linear/inverse perpetuals and dated futures. Exact source and quote identities remain separate |
| OKX, Coinbase Exchange and Kraken | None for public data | Native spot history and tickers | REST polling, as in the upstream plugins. Kraken limits native OHLC to its newest 720 candles. Coinbase 30m/4h and additional supported resolutions aggregate in Python |
| Deriv public reference feed | None | FX, commodity, index and crypto reference quotes | Reusable bounded WebSocket requests for history and native OHLC subscriptions. No exchange volume is invented; PAXG and gold spot are distinct instruments |
| Alpaca IEX, for Nasdaq/NYSE/Cboe listings | `JOSEPH_ALPACA_KEY_ID` and `JOSEPH_ALPACA_SECRET_KEY` from [Alpaca](https://docs.alpaca.markets/us/docs/about-market-data-api); `JOSEPH_ALPACA_FEED=iex` | US data feed remains disabled | IEX only; consolidated SIP is not enabled |
| Fugle Taiwan stocks/ETFs | `JOSEPH_TW_DATA_SOURCE=fugle` and `JOSEPH_FUGLE_API_KEY` from [Fugle](https://developer.fugle.tw/pricing) | Taiwan data feed is disabled until the key is set | Per-symbol REST quotes and candles; 1m history since 2023-05-23, daily since 2010; 60 REST calls/minute paced by the engine. Free futures/options and Fugle bulk snapshots are unavailable |
| U.S. Treasury official | No key | Daily par and real yield curves | No intraday bond or yield quote |
| Heatmap sources: Nasdaq.com screener, Wikipedia S&P 500 list, TWSE/TPEx open data, CoinGecko | None | Stock, ETF and crypto heatmaps (sector, market cap, delayed or end-of-day quotes) | Unofficial or keyless public endpoints, cached 60 s to 24 h; Alpaca keys add live US quotes and history-based metrics ([details](docs/HEATMAPS.md)) |
| FRED | `JOSEPH_FRED_API_KEY` from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) | Official Treasury daily series remain available | Separate daily `treasury:rate:FRED_*` yield and spread IDs; weekly/monthly bars aggregate daily observations |

For example, to use Fugle and FRED in Compose, edit `deploy/.env`:

```dotenv
JOSEPH_TW_DATA_SOURCE=fugle
JOSEPH_FUGLE_API_KEY=your-fugle-market-data-key
JOSEPH_FRED_API_KEY=your-fred-api-key
JOSEPH_ALPACA_KEY_ID=your-alpaca-key-id
JOSEPH_ALPACA_SECRET_KEY=your-alpaca-secret-key
```

Fugle's free plan allows five WebSocket subscriptions, but this adapter uses
paced REST requests and does not claim a native Fugle stream. FRED and the
official Treasury feed are daily sources, so minute/hour yield candles remain
unavailable. The Dashboard lists the actual native data providers separately;
CCXT is no longer required or selected by the running market engine. An idle on-demand REST source shows `IDLE`; it becomes `POLL` only
after REST data is received, or `LIVE` after WebSocket events arrive. See [market-data scope](docs/MARKET_DATA_SCOPE.md) for
timeframe and provenance details.

**Indicators.** Every indicator is a Python `joseph_script.Indicator` class ([ADR 0011](docs/adr/0011-indicators-are-joseph-script-classes.md)). The market engine ships nine built-ins (moving averages, VWAP, regression channel, Bollinger Bands, RSI, ATR, volume delta, pivot points); plugin packages add more through the `joseph.scripts` entry points, installed with `make plugins PLUGIN_REQUIREMENTS="..."` and loaded at startup ([script plugins](docs/OPERATIONS.md#script-plugins)); users write their own in the Python editor. The terminal runs and draws all of them the same way.

**Containers.** Build and start the whole stack:

```bash
cp deploy/.env.example deploy/.env   # replace every change-me value
make compose-up                      # build and start the stack
```

`deploy/docker-compose.yml` enables the selected real market-data venues and turns the global LIVE gate on; accounts still trade live only after verification and arming. The synthetic `mock` venue is used only with `JOSEPH_DEMO_ONLY=true`. Broker SDKs and indicator plugins are build args in `deploy/.env`: `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"` (brokers) and `MARKET_PLUGIN_REQUIREMENTS` (indicator plugin packages; empty: built-ins only).

## Configuration and operations

- [Operations runbook](docs/OPERATIONS.md): start-up and production secrets, [every setting](docs/OPERATIONS.md#settings) of both engines, monitoring, incidents, [LIVE enablement](docs/OPERATIONS.md#live-enablement) and [maintenance](docs/OPERATIONS.md#maintenance) (dated tasks such as MEXC retiring its JSON stream on 2026-12-01).
- [Brokers and venues](docs/BROKERS.md): Interactive Brokers, SinoPac and Cathay setup, securities order rules, and live prerequisites per broker and venue.
- [Market data scope](docs/MARKET_DATA_SCOPE.md): implemented free-first feeds, their limits, and the separation from trading destinations.
- [CI/CD](docs/CI_CD.md): pipelines, container images (with optional indicator plugins), environments and releases.

## Testing

| Command | Runs |
|---|---|
| `make ci` | Lint, types, OpenAPI drift and every offline tier: `make test-python` (both engines), `test-terminal` (Vitest plus boundary, secret and licence scans), `test-system` (both engines as real processes), `test-e2e` (DEMO browser tests with accessibility checks) |
| `cd joseph-terminal && pnpm test:e2e:fullstack` | Browser tests against a production build and both engines |
| `make test-network` | Opt-in: live public venue APIs |
| `make test-sandbox` | Opt-in: testnet order round trips; needs `JOSEPH_SANDBOX_<VENUE>_*` keys |

CI runs every offline tier on each push and pull request. Counts, coverage
and evidence: [latest regression verification](docs/reports/REGRESSION_2026_11_26.md)
and [baseline test report](docs/reports/TEST_REPORT.md).

## Known limitations

These come from venues, data plans or law; code cannot remove them.

| Limitation | Why | Workaround |
|---|---|---|
| Stock delta is *classified* (Lee-Ready or quote rule), never *true* | Stock exchanges publish no aggressor side | Labelled `classified` everywhere |
| Alpaca free plan: real time is IEX only; 30 streamed symbols | IEX is one exchange, so volume and closing prints differ from the consolidated market | Keep IEX provenance visible and treat missing data as unavailable |
| TWSE/TPEx official data: coarse 1m bars, no seconds bars, intraday history only since recording began, unadjusted daily prices | Free intraday data is MIS snapshots that the exchange caches for 4–120 s | Use official daily history for older dates |
| MEXC and Hyperliquid: seconds bars and true delta only since recording began | Neither publishes trade history; Hyperliquid's S3 archive is requester-pays | An AWS account could read Hyperliquid's archive (not implemented) |
| The newest archive day arrives late | Binance publishes about 07:30 UTC the next day, Lighter about 7 h after the UTC day, Bitget about a day later in UTC+8 days | Recorded trades fill the gap while the engine runs |
| Live trading is unproven against real venues and brokers | Tests use mocked venues and fake broker clients; real orders need real keys and accounts | Testnets, IBKR paper and SinoPac simulation first |
| MEXC's private-stream format after 2026-12-01 is unknown | MEXC announced only the market-data change | The private stream reads JSON and Protobuf |
| IBKR's `BATS` code for Cboe BZX listings is unchecked live | Needs a logged-in gateway | Check with an IBKR paper account |
| No Cathay or Firstrade trading | Cathay has no public API; Firstrade has no official API | SinoPac for Taiwan, IBKR for US |
| Not served: TXSE, IEX, NYSE Texas and OTC listings; the 興櫃 emerging board; futures and options | Outside the supported listings (IEX and NYSE Texas listed only test issues on 2026-11-23); futures and options are out of scope | None |
| Taiwan fee estimates charge 0.3% tax on stock day trades | The 0.15% day-trade rate depends on the broker agreement and the whole day's trades | The broker statement decides |
| Bond-ETF sale tax returns on 2027-01-01 | The extension to 2036 is not law yet | Update `BOND_ETF_TAX_SUSPENSION` when it passes ([maintenance](docs/OPERATIONS.md#maintenance)) |
| The US half-penny tick tier is not modelled | Prices snap to $0.01 (≥ $1) and $0.0001 (< $1) | None |

## Documentation

- **Users:** [User manual](docs/USER_MANUAL.md) · [Heatmaps](docs/HEATMAPS.md)
- **Operators:** [Operations runbook](docs/OPERATIONS.md) · [Brokers](docs/BROKERS.md) · [CI/CD](docs/CI_CD.md)
- **Contributors:** [Architecture](docs/architecture/INDEX.md) · [ADRs](docs/adr/) · [Extending](docs/EXTENDING.md) · [Coding standards](docs/standards/CODING_STANDARDS.md) · [Design decisions](docs/DESIGN_DECISIONS.md) · [Feature status](docs/FEATURE_STATUS.md)
- **Components:** [Market engine](joseph-market-engine/README.md) · [Execution engine](joseph-execution-engine/README.md) · [Terminal](joseph-terminal/README.md)
- **Reports:** [Threat model](docs/reports/THREAT_MODEL.md) · [Licensing](docs/reports/LICENSING_REPORT.md) · [TradingView use-case audit](docs/reports/TRADINGVIEW_USE_CASE_AUDIT.md) · [Tests](docs/reports/TEST_REPORT.md)
- **API contracts:** `joseph-terminal/packages/contracts/` (`schemas/`, `openapi/`, `asyncapi/`)

## Licence

- **Integrated application: AGPL-3.0-only** ([LICENSE](LICENSE)); original MIT file notices are retained.
- **Terminal:** `joseph-terminal/` keeps its own AGPL [LICENSE](joseph-terminal/LICENSE) and [NOTICE](joseph-terminal/NOTICE) so it can become a separate repository. Charts use TradingView's Lightweight Charts (Apache-2.0); Joseph is not affiliated with TradingView.
- **Plugin packages:** indicator plugin packages you install are not covered by this licence; check their terms before using or redistributing an image that contains one.

Third-party components and obligations: [licensing report](docs/reports/LICENSING_REPORT.md).


### Alert delivery

Open **Notifications** to add Telegram or WhatsApp destinations, select signal
kinds/severity/quiet hours, or **Enable push on this device**. Save a destination
and use **Send test** to check delivery. The test queues a notification through
the durable outbox; it never creates an order or executable trading signal.
`DELIVERED` means the provider accepted the message, not that the person read it.
Failures retry up to six attempts; permanent failures are reported in the panel.
Quiet hours and strategy filters apply to strategy signals; explicit tests bypass
them. Deleting a destination stops future routing; a message already queued for
Telegram/WhatsApp may still arrive. Push destinations can be edited, disabled,
and deleted like other channels. Expired push endpoints are disabled automatically.
If a device expires, delete its destination and enable push on that device again
to obtain a fresh browser registration.

Set the following **on the market engine** in `deploy/.env` (or an explicit
`JOSEPH_MARKET_ENV_FILE`), then restart it. Never put these secrets into
`NEXT_PUBLIC_*` variables. Secret settings also accept `_FILE` paths. Compose
passes these settings to the market container; direct launches must export them
or point to the environment file. Partial configuration leaves that channel off.

| Channel | Server configuration | Recipient setup |
|---|---|---|
| Telegram | `JOSEPH_TELEGRAM_BOT_TOKEN` from [BotFather](https://core.telegram.org/bots/features#botfather) | Start a private chat with your bot first. Obtain your numeric chat id from the official [getUpdates API](https://core.telegram.org/bots/api#getupdates), or use a group/channel id where the bot can post. Enter that id in Notifications. |
| WhatsApp | `JOSEPH_WHATSAPP_ACCESS_TOKEN`, `JOSEPH_WHATSAPP_PHONE_NUMBER_ID`, `JOSEPH_WHATSAPP_TEMPLATE`; optional `JOSEPH_WHATSAPP_LANGUAGE=en_US`, `JOSEPH_WHATSAPP_API_VERSION=v27.0` | Use the official Meta WhatsApp Business Cloud API. Create an approved template with exactly **one BODY text parameter**, for example `Joseph alert: {{1}}`. Configure the matching template language. Add an opted-in recipient as `+` plus country code and number. Meta test senders only message approved test recipients. |
| Browser/mobile Web Push | `JOSEPH_WEB_PUSH_PRIVATE_KEY`, `JOSEPH_WEB_PUSH_PUBLIC_KEY`, `JOSEPH_WEB_PUSH_CONTACT=mailto:you@example.com` | Serve the terminal over HTTPS (localhost works for development). Press Enable push on this device and allow permission. On iOS/iPadOS 16.4+, first use Add to Home Screen, then open the installed app. Repeat on each device. |

Generate one VAPID key pair, copy the output into your protected environment file,
and keep that pair stable across restarts and replicas:

```bash
cd joseph-market-engine
uv run python scripts/generate_push_keys.py
```

The private key stays on the server; only the public application key is returned
to browsers. Browser endpoint and encryption keys are stored privately in the
market database and omitted from subscription responses. Back up that database
and restrict access to it. Rotate VAPID keys only with a plan to delete and
re-enable affected device registrations. This service worker displays alerts; it
does not cache prices or provide offline trading. Background push depends on OS,
browser, installation and permission settings; it is not a native mobile app.

WhatsApp template approval, recipient consent, token permissions and any Meta
messaging charges are required by [Meta's Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages/).
An approved template lets this implementation send outside the 24-hour customer
service window. No unofficial WhatsApp Web session or personal-account automation
is used. See [WebKit's Web Push requirements](https://webkit.org/blog/13878/) for
Apple mobile devices.

**Strategy routing:** destination registration does not override a strategy
activation's notification policy. Include `TELEGRAM`, `WHATSAPP` and/or
`WEB_PUSH` in the activation's `channels` when creating it
(`POST /v1/strategy-activations`). Existing activations retain their channels. Push follows the same
quiet hours, severity, signal-kind and strategy filters as other channels. Python price/indicator conditions are also available through the five-kind
script editor described below. Native drawing-attached alert controls remain
a separate roadmap item.

### Python scripts, heatmaps, and MCP

The terminal now has five Python script kinds: **indicator, strategy, library,
filter, and alert**, each requiring its own abstract base class. The Python
editor supports immutable versions, library pins, visual studies, simulations,
market scans, and future-bar alerts. Build `make script-runner` and set
`JOSEPH_SCRIPT_RUNTIME=docker` to execute sources in isolated containers.

New tabs can contain candlestick charts or heatmaps. Active indicators
have name/settings/source/hide/remove controls in the chart's upper-left corner.
Heatmaps cover stocks (S&P 500, Nasdaq 100, Nasdaq Composite, NYSE, all US
stocks, TWSE, TPEx), ETFs (US, Taiwan) and crypto coins, sized by market cap,
volume or turnover and coloured by change or performance; see
[Heatmaps](docs/HEATMAPS.md).

The market server automatically starts MCP at `/mcp`. Open the **MCP** terminal
panel to create a scoped bearer grant and optionally enable browser control.
Set `JOSEPH_MCP_PUBLIC_URL` for deployments. See
[Python scripts and MCP](docs/PYTHON_SCRIPTS_AND_MCP.md) for setup, all context
methods, notification routing, permissions, and test commands.

See [TradeX integration](docs/TRADEX_INTEGRATION.md) for the pinned upstream
revision, Python-only script policy, drawing bridge and provider migration.
