# Joseph trading platform

Joseph is a self-hosted platform for charting, researching and trading crypto perpetuals, Taiwan and US stocks and ETFs, with US Treasury yields as reference data. It has three parts: a browser charting terminal, a market engine (data, screening, indicator plugins, strategies, alerts, backtests) and an execution engine (risk gateway, order management, paper, shadow and live trading, audit, kill switch).

> **Risk disclaimer.** Joseph is research software, provided as is under the MIT licence with no warranty. Live trading can lose money. The LIVE adapters are tested only against mocked venues and fake broker clients, never with real orders. Nothing in this repository is financial advice.

## Markets and venues

| Market | Venues | Market data | Trading |
|---|---|---|---|
| Crypto perpetuals | Binance USDⓈ-M, Bitget USDT-M, Hyperliquid, Lighter, MEXC futures | Each venue's public API; no key | Paper and shadow; LIVE through each venue's own adapter |
| Taiwan stocks and ETFs | TWSE, TPEx (mainboard) | Official exchange data, free, no key; optional SinoPac Shioaji data (SinoPac API key) | Paper and shadow; LIVE through SinoPac Shioaji. Cathay is wired in but refuses every order (no public API) |
| US stocks and ETFs | Nasdaq, NYSE, Cboe BZX | Alpaca market data; needs a free Alpaca key (IEX real time by default) | Paper and shadow; LIVE through Interactive Brokers (IB Gateway). Alpaca is data only |
| US Treasury yields | Par and real yield curves | Treasury.gov; free, no key | Data only |
| DEMO | `mock` | Synthetic, clearly labelled | Paper |

Futures and options are not supported.

**Trading modes.** PAPER fills on a simulated venue; SHADOW records what it would send and sends nothing; LIVE sends real orders and needs all of: the global gate `JOSEPH_LIVE_TRADING_ENABLED` turned on (off in code, on in `deploy/`), a verified account, step-up arming of the account (manual orders) or step-up consent for an AUTO_LIVE deployment (strategies), an order preview, a pass from the risk gateway and no engaged kill switch ([LIVE enablement](docs/OPERATIONS.md#live-enablement)).

## Architecture at a glance

| Path | Port | What it is |
|---|---|---|
| `joseph-terminal/` | 3000 | Next.js terminal: charts, drawing tools, screener, strategy builder, order tickets. Renders engine outputs; holds only a session token. Own licence; can become a separate repository |
| `joseph-market-engine/` | 8000 | FastAPI service: market data, screening, indicator plugins, strategy evaluation, signals, alerts, backtests. Holds no trading credentials |
| `joseph-execution-engine/` | 8100 | FastAPI service: accounts and an encrypted credential vault, risk gateway, order management system (OMS), reconciliation, audit log, kill switch. The only holder of trading credentials |
| `system-tests/` | | Cross-service tests that boot both engines |
| `deploy/` | | Docker Compose stack for the three services (plus optional IB Gateway) |
| `docs/` | | Manual, runbook, architecture, ADRs, reports |

**Trust boundary.** The browser never sees venue credentials or signing code; it sends order *intents*. Every order (manual, chart, emergency or strategy) takes one path inside the execution engine: intent → risk gateway (deny by default) → OMS → venue or broker adapter. The market engine forwards strategy signals with a separate service token. Details: [architecture index](docs/architecture/INDEX.md).

## Getting started

### Try the terminal (DEMO)

Needs Node 20+ with corepack (pnpm 10). No keys and no engines:

```bash
cd joseph-terminal
corepack enable && pnpm install
pnpm dev   # http://localhost:3000, DEMO mode: synthetic data, clearly labelled
```

### Full stack

> **Private dependency.** The market engine installs its indicator library from the private repository `apoman123/PineScripts` (`python_indicators/`, pinned to a commit in `joseph-market-engine/pyproject.toml`). Without read access to that repository the market engine cannot be built or run. The terminal (DEMO) and the execution engine do not need it.

Needs uv with Python 3.12+, Node 20+ with corepack, and read access to the library ([how](docs/OPERATIONS.md#start)).

```bash
make setup   # locked installs for both engines, system tests and the terminal
make dev     # market :8000, execution :8100 (paper; LIVE off), terminal http://localhost:3000
```

`make dev` uses development tokens; `make help` lists every target.

**Containers.** Building the market image needs the same library access.

```bash
cp deploy/.env.example deploy/.env   # replace every change-me value
make compose-up                      # build and start the stack
```

`deploy/docker-compose.yml` enables every venue and turns the global LIVE gate on; accounts still trade live only after verification and arming. Optional SDKs are build args in `deploy/.env`: `MARKET_UV_EXTRAS=--extra sinopac` (SinoPac data) and `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"` (brokers).

## Configuration and operations

- [Operations runbook](docs/OPERATIONS.md): start-up and production secrets, [every setting](docs/OPERATIONS.md#settings) of both engines, monitoring, incidents, [LIVE enablement](docs/OPERATIONS.md#live-enablement) and [maintenance](docs/OPERATIONS.md#maintenance) (dated tasks such as MEXC retiring its JSON stream on 2026-12-01).
- [Brokers and venues](docs/BROKERS.md): Interactive Brokers, SinoPac and Cathay setup, securities order rules, and live prerequisites per broker and venue.
- [CI/CD](docs/CI_CD.md): pipelines, container images, environments, releases and access to the indicator library.

## Testing

| Command | Runs |
|---|---|
| `make ci` | Lint, types, OpenAPI drift and every offline tier: `make test-python` (both engines), `test-terminal` (Vitest plus boundary, secret and licence scans), `test-system` (both engines as real processes), `test-e2e` (DEMO browser tests with accessibility checks) |
| `cd joseph-terminal && pnpm test:e2e:fullstack` | Browser tests against a production build and both engines |
| `make test-network` | Opt-in: live public venue APIs |
| `make test-sandbox` | Opt-in: testnet order round trips; needs `JOSEPH_SANDBOX_<VENUE>_*` keys |

CI runs every offline tier on each push and pull request. Counts, coverage and evidence: [test report](docs/reports/TEST_REPORT.md).

## Known limitations

These come from venues, data plans or law; code cannot remove them.

| Limitation | Why | Workaround |
|---|---|---|
| Stock delta is *classified* (Lee-Ready or quote rule), never *true* | Stock exchanges publish no aggressor side | Labelled `classified` everywhere |
| Alpaca free plan: real time is IEX only (a few % of US volume); 30 streamed symbols | Consolidated real-time data (SIP) is a paid plan, about $99/month | `delayed_sip` (all volume, ≥15 min late) or a paid plan |
| US intraday bars include the official closing print only on `sip` / `delayed_sip` | IEX carries no closing cross | Use a consolidated feed |
| TWSE/TPEx official data: coarse 1m bars, no seconds bars, intraday history only since recording began, unadjusted daily prices | Free intraday data is MIS snapshots that the exchange caches for 4–120 s | `JOSEPH_TW_DATA_SOURCE=sinopac` (Shioaji quotas; board lots only) |
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

- **Users:** [User manual](docs/USER_MANUAL.md)
- **Operators:** [Operations runbook](docs/OPERATIONS.md) · [Brokers](docs/BROKERS.md) · [CI/CD](docs/CI_CD.md)
- **Contributors:** [Architecture](docs/architecture/INDEX.md) · [ADRs](docs/adr/) · [Extending](docs/EXTENDING.md) · [Coding standards](docs/standards/CODING_STANDARDS.md) · [Design decisions](docs/DESIGN_DECISIONS.md) · [Feature status](docs/FEATURE_STATUS.md)
- **Components:** [Market engine](joseph-market-engine/README.md) · [Indicators](joseph-market-engine/src/joseph/indicators/README.md) · [Execution engine](joseph-execution-engine/README.md) · [Terminal](joseph-terminal/README.md)
- **Reports:** [Threat model](docs/reports/THREAT_MODEL.md) · [Licensing](docs/reports/LICENSING_REPORT.md) · [TradingView use-case audit](docs/reports/TRADINGVIEW_USE_CASE_AUDIT.md) · [Tests](docs/reports/TEST_REPORT.md)
- **API contracts:** `joseph-terminal/packages/contracts/` (`schemas/`, `openapi/`, `asyncapi/`)

## Licence

- **MIT** ([LICENSE](LICENSE)), copied into both engines and their images.
- **Terminal:** `joseph-terminal/` keeps its own MIT [LICENSE](joseph-terminal/LICENSE) and [NOTICE](joseph-terminal/NOTICE) so it can become a separate repository. Charts use TradingView's Lightweight Charts (Apache-2.0); Joseph is not affiliated with TradingView.
- **Indicator library:** the private library is not part of this repository or its licence. One optional indicator in it derives from a LuxAlgo script under CC BY-NC-SA 4.0: do not use the market engine commercially while that plugin is enabled ([which one](docs/reports/LICENSING_REPORT.md#luxalgo-derived-indicator-cc-by-nc-sa-40)).

Third-party components and obligations: [licensing report](docs/reports/LICENSING_REPORT.md).
