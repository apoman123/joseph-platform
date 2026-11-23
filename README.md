# Joseph trading platform (V5)

Multi-market research and trading: crypto perpetuals (Binance, Bitget, Hyperliquid, Lighter, MEXC), Taiwan stocks and ETFs (TWSE, TPEx), US stocks and ETFs (Nasdaq, NYSE, Cboe BZX) and US Treasury yields. A browser charting terminal, a market engine (data, screening, SMC indicators, strategies, alerts, backtests) and an execution engine behind its own trust boundary (risk gateway, OMS, paper/shadow/live trading, audit, kill switch). MIT licensed ([LICENSE](LICENSE)).

> **Status:** paper and shadow trading work end to end. Live orders are implemented for the five crypto venues, Interactive Brokers and SinoPac (Cathay awaits its SDK) but verified only against mocks and fakes. The global gate is on in `deploy/`, off in code; every live order also needs a verified account, step-up arming or strategy consent, preview, the risk gateway and no kill switch ([gates](docs/OPERATIONS.md#live-enablement)). Indicators are Python ports of the owner's Pine scripts (private `apoman123/PineScripts`, pinned), `SPEC_ONLY` until compared with TradingView exports.

## What you need to do

**GitHub**

1. Verify `PINESCRIPTS_TOKEN` (already created): fine-grained, Contents read-only on `apoman123/PineScripts` only, stored as an Actions and a Dependabot secret. Rotate it before expiry ([CI/CD](docs/CI_CD.md#indicator-library-access)).
2. After the first green CI run, delete (if still there) the `PINESCRIPTS_DEPLOY_KEY` secret (Actions and Dependabot) and the `joseph-platform-ci` deploy key on PineScripts ([commands](docs/CI_CD.md#indicator-library-access)).
3. Delete the mistaken repository `HuJK/joseph-platform`: `gh auth refresh -h github.com -s delete_repo`, then `gh repo delete HuJK/joseph-platform` (or its Settings).
4. Before making the repository public: Settings → Environments → `staging` and `production`: you as required reviewer, deployment branches `main` (plus `v*` tags for `production`); Settings → Actions → General: require approval for fork pull requests from all outside contributors ([CI/CD](docs/CI_CD.md#one-time-repository-setup)).
5. Then Settings → General → Change visibility → Public. Keep the `joseph-market-engine` and `joseph-execution-engine` packages private (they contain PineScripts; CI refuses to publish into a public one). CodeQL starts on the next push: review Security → Code scanning.

**Market data**

Set these in `deploy/.env` (the compose file passes them to the market engine; every venue is enabled by default).

6. US data: a free Alpaca account's `JOSEPH_ALPACA_KEY_ID` / `JOSEPH_ALPACA_SECRET_KEY`, and a feed `JOSEPH_ALPACA_FEED`: `iex` (default, real time, IEX volume only), `delayed_sip` (free, all volume, ≥15 min late) or `sip` (paid real time).
7. Optional SinoPac data for Taiwan: `JOSEPH_TW_DATA_SOURCE=sinopac`, `JOSEPH_SINOPAC_API_KEY_FILE` / `JOSEPH_SINOPAC_SECRET_KEY_FILE` (no CA; the same files as trading) and `MARKET_UV_EXTRAS=--extra sinopac`. Test it outside trading hours: `JOSEPH_SINOPAC_DATA_TESTS=1 uv run --extra sinopac pytest tests/network/test_live_sinopac.py -m sinopac_data` (in `joseph-market-engine/`).

**Live trading**

8. Interactive Brokers: IB Gateway via the compose profile `ibkr`, the paper account (`DU…`) first, and an IBKR Mobile 2FA approval each week ([steps](docs/BROKERS.md#interactive-brokers)).
9. SinoPac trading: API key with trading permission, signed API agreement, CA certificate, and SinoPac's mandatory simulation test ([steps](docs/BROKERS.md#sinopac-shioaji)).
10. Cathay: ask your Cathay representative for the API SDK/spec and send it to the developer; until then Cathay refuses every order ([details](docs/BROKERS.md#cathay-securities)).
11. Crypto venues: run `make test-sandbox` on each testnet first; use dedicated accounts or subaccounts (positions Joseph did not open block new orders); one-way position mode, not hedge; Bitget needs a Unified Trading Account; MEXC needs KYC and the "Order Placing" key permission ([gates](docs/OPERATIONS.md#live-enablement)).
12. Production secrets: `JOSEPH_ENV=production`, `JOSEPH_VAULT_MASTER_KEY`, `JOSEPH_PREVIEW_SECRET`, `JOSEPH_STEP_UP_TOTP_SECRET` (also in your authenticator app) on the execution engine; the same `JOSEPH_SERVICE_TOKEN` on both engines ([checklist](docs/OPERATIONS.md#start)).

**Decisions**

13. TWD risk limits: choose `JOSEPH_TWD_LIMIT_FACTOR` (default 30), the multiple of USD-sized limits and hard caps for TWD accounts (a sizing convention, not an FX rate).

**Recurring**

14. After 2026-12-01 10:00 UTC, run `make test-network` to confirm MEXC's JSON shutdown; if JSON is gone, remove any `JOSEPH_MEXC_WS_FORMAT=json`.
15. When Taiwan's bond-ETF tax-suspension extension (to 2036) becomes law, update the end date of `BOND_ETF_TAX_SUSPENSION` in `joseph-execution-engine/src/joseph_execution/domain/equities.py`.
16. Yearly or after an ad hoc closure, regenerate the calendars: `uv run python scripts/generate_xtai_calendar.py` and `scripts/generate_usbond_calendar.py` (in `joseph-market-engine/`).
17. Honour LuxAlgo's CC BY-NC-SA 4.0 licence of the ported OTE indicator: no commercial use while it is enabled ([licensing](docs/reports/LICENSING_REPORT.md)).

## Known limitations

These come from venues, data plans or law; code cannot remove them.

| Limitation | Why | Workaround |
|---|---|---|
| Stock delta is *classified* (Lee-Ready / quote rule), never *true* | Stock exchanges publish no aggressor side | Labelled `classified` everywhere |
| Alpaca free plan: real time is IEX only, a few % of US volume | Consolidated real-time data (SIP) is a paid plan, about $99/month | `delayed_sip`: all volume, ≥15 min late |
| Alpaca free plan: 30 streamed symbols | Plan limit | Paid plan |
| US intraday bars include the official closing print only on `sip` / `delayed_sip` | IEX carries no closing cross | Use a consolidated feed |
| TWSE/TPEx official data: 1m bars are coarse; no seconds bars; intraday history only since recording began; daily prices not adjusted | Free intraday data is MIS snapshots that the server caches 4–120 s | `JOSEPH_TW_DATA_SOURCE=sinopac` (Shioaji quotas; board lots only) |
| MEXC and Hyperliquid: seconds bars and true delta only since recording began | Neither publishes trade history; Hyperliquid's S3 archive is requester-pays | An AWS account could read Hyperliquid's archive (not implemented) |
| The newest archive day arrives late | Binance publishes at about 07:30 UTC the next day; Lighter about 7 h after the UTC day; Bitget about a day later, in UTC+8 days | Recorded trades fill the gap while the engine runs |
| Live trading is unproven against real venues and brokers | Tests use mocked venues and fake broker clients; real orders need your keys and accounts | Testnet, IBKR paper and SinoPac simulation first |
| MEXC's private-stream format after 2026-12-01 is unknown | MEXC announced only the market-data change | The private stream reads JSON and Protobuf |
| IBKR's `BATS` code for Cboe BZX listings is unchecked live | Needs a logged-in gateway | Check with the IBKR paper account |
| No Cathay or Firstrade trading | Cathay has no public API (SDK pending); Firstrade has no official API (out of scope) | SinoPac for Taiwan, IBKR for US |
| Not served: TXSE, IEX, NYSE Texas and OTC listings; the 興櫃 emerging board; futures and options | Outside the supported listings (IEX and NYSE Texas listed only test issues on 2026-11-23); futures and options deferred by the owner | None |
| Taiwan fee estimates charge 0.3% on stock day trades | The 0.15% day-trade rate depends on the broker agreement and the whole day's trades | The broker statement decides |
| Bond-ETF sale tax returns on 2027-01-01 | The 2036 extension is not law yet | Update the date when it passes (item 15) |
| The US half-penny tick tier is not modelled | Prices snap to $0.01 (≥ $1) and $0.0001 (< $1) | None |

## Repository layout

| Path | What it is |
|---|---|
| `joseph-terminal/` | Next.js terminal, framework-free packages, JSON Schema contracts (own `LICENSE`/`NOTICE`, splittable) |
| `joseph-market-engine/` | FastAPI service on :8000 |
| `joseph-execution-engine/` | FastAPI service on :8100; the only holder of trading credentials |
| `system-tests/` | Cross-service tests that boot both engines |
| `docs/` | Manual, runbook, ADRs, reports |

## Quick start

```bash
make setup   # uv + pnpm, locked versions (Python 3.12+, Node 20+); needs PineScripts access
make dev     # market :8000 · execution :8100 · terminal http://localhost:3000
```

The terminal alone runs in labelled DEMO mode: `cd joseph-terminal && pnpm install && pnpm dev`. Containers: `cp deploy/.env.example deploy/.env`, fill the secrets, then `make compose-up`. Every push runs `.github/workflows/ci-cd.yml`; `main` and `v*` tags also publish images and deploy ([CI/CD](docs/CI_CD.md)).

## Tests

- `make ci`: lint, types, OpenAPI drift and every offline tier (`make test-python`, `test-terminal`, `test-system`, `test-e2e`).
- `cd joseph-terminal && pnpm test:e2e:fullstack`: browser → production build → both engines.
- Opt-in: `make test-network` (live public venue APIs), `make test-sandbox` (testnet orders; needs your testnet keys).

Tiers and latest results: [test report](docs/reports/TEST_REPORT.md).

## Documentation

- [User manual](docs/USER_MANUAL.md) · [Operations runbook](docs/OPERATIONS.md) · [Brokers](docs/BROKERS.md) · [CI/CD](docs/CI_CD.md) · [Extending](docs/EXTENDING.md)
- [Architecture](docs/architecture/INDEX.md) · [ADRs](docs/adr/) · [Proposal revisions](docs/PROPOSAL_REVISIONS.md) · [Spec gap matrix](docs/SPEC_GAP_MATRIX.md) · [Coding standards](docs/standards/CODING_STANDARDS.md)
- Reports: [threat model](docs/reports/THREAT_MODEL.md) · [licensing](docs/reports/LICENSING_REPORT.md) · [Pine source](docs/reports/PINE_SOURCE_REVISION.md) · [TradingView audit](docs/reports/TRADINGVIEW_USE_CASE_AUDIT.md) · [tests](docs/reports/TEST_REPORT.md)
- Engines: [market](joseph-market-engine/README.md) · [execution](joseph-execution-engine/README.md) · [terminal](joseph-terminal/README.md)
- API: `joseph-terminal/packages/contracts/{schemas,openapi,asyncapi}`
