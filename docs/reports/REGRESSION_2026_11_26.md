# Regression verification — 2026-11-26

Environment: WSL Linux, Python 3.12.14 with uv, Node 24.19.0, pnpm
10.34.9, and Playwright 1.65.1 with Chromium. Results below come from
executed checks, including the complete root `make ci` target.

## Offline and browser suites

| Suite | Result |
|---|---|
| Market engine: unit, integration, contracts, architecture, property, replay, security, chaos, and process tests | 1,963 passed; 51 skipped |
| Execution engine: unit, integration, contracts, architecture, property, security, chaos, and process tests | 976 passed; 9 skipped |
| Terminal: unit, component, contract, and architecture tests | 1,020 passed across 113 files |
| Cross-service system tests using real HTTP and WebSocket processes | 118 passed |
| Production terminal browser tests, including accessibility | 50 passed |
| Full-stack browser tests against both running engines | 15 passed |

The six suites contain 4,142 passing tests. The default engine suites skip
opt-in public-network checks, broker/testnet checks requiring credentials,
and Pine parity checks awaiting external exports. Public-network checks
were also run separately, as described below. Real-money orders were not
placed.

Coverage runs reported 96.37% total line coverage for the market engine,
96.30% for the execution engine, and 83.84% for the terminal. The Python
coverage totals include test and script modules, matching the CI command.

## Recent changes covered

- More than eight chart tabs, independent symbols, timeframes and indicators,
  tab switching, closing, and persistence across reloads.
- Indicator library search, default indicators, custom-script category,
  saved collection, keyboard focus, desktop/mobile layout, and accessibility.
- History deadlines while waiting for headers or response bodies, cancellation
  after chart changes, Retry recovery, and preservation of streamed prices
  when history fails.
- WebSocket opening deadlines, reconnection, feed-mode isolation, resubscription,
  and actual server events after switching between polling and native streams.
- Older-history paging and suppression of premature paging before the initial
  snapshot completes; price-axis wheel zoom and drawing-toolbar interaction.
- Monthly stock history and indicator windows at the exchange calendar's
  earliest supported session; bounded lower-timeframe indicator history and
  explicit missing coverage instead of invented delta values.
- CCXT history, RWA discovery, tickers, order books with additional level
  metadata, public WebSocket subscriptions, and subscription cleanup.
- Bitget public-history queries at whole-second and fractional-second
  boundaries, including clipping extra rows from widened requests.

## Live providers

The five CCXT production adapters passed the new live contract test for
history, RWA discovery, current snapshots, order books, and WebSocket
candles. The connectivity probe passed for Binance, Hyperliquid, Lighter,
Bitget, and MEXC. Unsupported optional open-interest or funding metrics
are reported separately; genuine venue outages still fail the probe.

The full market network suite initially found a Bitget final-second
boundary error. That error was reproduced directly against the public API,
fixed, and the archive-to-REST comparison passed on rerun.

A later full network run had 47 passes, one credential skip, and two
Binance failures caused by temporary DNS resolution errors. These failures
both passed on a targeted rerun (2 passed). They are recorded separately
from deterministic CI and the production CCXT checks.

The Alpaca AAPL daily/5-minute history and snapshot test passed with the
configured keys. A live browser also loaded Nasdaq AAPL on monthly, daily,
hourly, and 5-minute charts without HTTP errors or JavaScript exceptions.
The execution engine's two public MEXC stream checks passed.

## Static and supply-chain checks

- Root `make ci`: passed, including Python format, lint, documentation checks,
  strict mypy, terminal format, ESLint, stylelint, TypeScript, every offline
  test tier, browser tests, production builds, and OpenAPI drift checks.
- Contract generation, dependency boundaries, and import-layer contracts:
  passed.
- Production npm and Python vulnerability audits: no known vulnerabilities.
- Production dependency license checks: passed under the existing policy.
- Terminal secret scan, full-history gitleaks scan, and staged gitleaks scan:
  passed.

The generated OpenAPI document was refreshed. Next.js's generated declaration
file is excluded from formatting checks, and generated Python coverage XML
is ignored by Git.

## Reproduce

```sh
make ci
cd joseph-terminal
pnpm test:coverage
pnpm test:e2e:fullstack
cd ../joseph-market-engine
uv run pytest --cov --cov-report=xml
JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network
uv run python scripts/probe_venues.py --json
cd ../joseph-execution-engine
uv run pytest --cov --cov-report=xml
JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network
```

Browser runs require Playwright Chromium and its system libraries. Live
Alpaca checks also require the two market-data keys documented in the root
README. The GitHub `ci-cd` and `security` workflows run on the pushed commit;
their run links are reported separately from these local results.
