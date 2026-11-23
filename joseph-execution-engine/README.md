# Joseph Execution Engine (private)

The trust boundary that owns accounts, credentials, the Risk Gateway, the OMS, reconciliation, the audit log and kill switches. Every manual, chart, emergency and strategy order follows one path:

```text
OrderIntent -> Risk Gateway (deny by default) -> OMS (transactional outbox) -> TradingAdapter
```

**LIVE trading** runs on Binance USDⓈ-M, Bitget USDT-M (Unified Trading Account), Hyperliquid perps, Lighter perps and MEXC futures through signed REST requests and private WebSocket streams (see [Live venues](#live-venues)). The code default keeps the global gate off (`JOSEPH_LIVE_TRADING_ENABLED=false`); the deploy compose and `deploy/.env.example` turn it on. An account without stored credentials reports `TRADING_UNAVAILABLE` and refuses before anything is sent. PAPER runs on a simulated venue. SHADOW records the orders Joseph would send but never sends them.

**Securities brokers** (`adapters/brokers/`): Interactive Brokers (`ib_async` to IB Gateway/TWS; Nasdaq and NYSE), SinoPac Shioaji (TWSE) and a fail-closed Cathay Securities entry awaiting its SDK. They stay gated (no client is even created) unless `JOSEPH_LIVE_TRADING_ENABLED=true` and the broker is enabled, and every order still passes every LIVE gate. Setup, 2FA, SinoPac's simulation test and the securities risk rules are in [`docs/BROKERS.md`](../docs/BROKERS.md). Optional extras: `uv sync --extra ibkr --extra sinopac`.

## Run

```bash
uv sync
uv run uvicorn joseph_execution.bootstrap:create_application --factory --port 8100
```

The defaults are for development: dev token `dev-local-token` maps to `tenant-01:user-01`, the market gateway is the static DEMO gateway, and the database is SQLite at `./var/execution.db`. To paper trade on real market data, run the market engine on :8000 and set `JOSEPH_MARKET_GATEWAY=http`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `JOSEPH_ENV` | `development` | `production` refuses dev tokens and an ephemeral vault key |
| `JOSEPH_DATABASE_URL` | `sqlite:///./var/execution.db` | SQLAlchemy URL (PostgreSQL supported) |
| `JOSEPH_MARKET_GATEWAY` / `JOSEPH_MARKET_URL` | `static` / `http://localhost:8000` | Market data source for risk checks and paper fills |
| `JOSEPH_DEV_TOKENS` | dev only | `token:tenant:user,...` bearer tokens |
| `JOSEPH_SERVICE_TOKEN` | dev only | Token required by `/internal/v1/signals` and `/metrics` |
| `JOSEPH_LIVE_TRADING_ENABLED` | `false` (`true` in `deploy/`) | Global LIVE gate; one of five gates that must all pass |
| `JOSEPH_EMERGENCY_HALT` | `false` | Platform-wide block on new exposure |
| `JOSEPH_VAULT_MASTER_KEY` | ephemeral in dev | Base64 32-byte key that wraps per-credential AES-GCM data keys |
| `JOSEPH_PREVIEW_SECRET` | random | HMAC key for preview tokens |
| `JOSEPH_STEP_UP_TOTP_SECRET` | unset (step-up always fails) | Base32 TOTP secret for live consent |
| `JOSEPH_CORS_ORIGINS` | `http://localhost:3000,...` | Browser origins |
| `JOSEPH_PAPER_STARTING_EQUITY` | `100000` | Paper account equity |
| `JOSEPH_BACKGROUND_TASKS` | `true` | Outbox worker, paper ticker, reconciler and broker event loops |
| `JOSEPH_PAPER_TWD_STARTING_CASH` | `3000000` | TWD paper account for TWSE securities (`off` disables it) |
| `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS` | `reject` | `queue` lets DAY market orders wait for the next open |
| `JOSEPH_IBKR_*`, `JOSEPH_SINOPAC_*` | disabled | Broker connections; see `docs/BROKERS.md` |
| `JOSEPH_VENUE_SANDBOX` | empty | `all` or venues (`binance,bitget,…`) whose accounts default to the test environment |
| `JOSEPH_VENUE_URLS` | empty | Endpoint overrides `venue.key=url,…` (`rest_url`, `testnet_rest_url`, `ws_url`, `testnet_ws_url`, `demo_ws_url`) |
| `JOSEPH_VENUE_TIMEOUT_S` / `JOSEPH_VENUE_RECV_WINDOW_MS` | `10` / `5000` | Venue HTTP timeout and signed-request validity window |
| `JOSEPH_VENUE_MARKET_SLIPPAGE_BPS` | `50` | Worst-price band of market and stop-market orders on Hyperliquid and Lighter |
| `JOSEPH_LIVE_STREAMS` | `true` | Private account streams for live accounts (only while the gate is on) |

## Live venues

Every order still follows `OrderIntent -> Risk Gateway -> OMS -> adapter`: the global gate, account LIVE mode, preview tokens for manual orders, AUTO_LIVE consent with step-up and venue-native stop-loss for strategies, the kill switch, idempotent client order ids and the audit log all run first. Adapters add a last line of defense: with the gate off they negotiate nothing and refuse submits and amends; without credentials they refuse every call before sending.

Capabilities are negotiated per account at runtime: `AVAILABLE` only when credentials are stored, the venue is healthy and a signed permission probe passes; otherwise `TRADING_UNAVAILABLE` with the reason in `notes` (for example `AUTH_SCOPE_MISSING: permission missing: …`). Joseph trades **one-way position mode** only.

| Venue | Credential fields (`POST /v1/accounts/connect/start`) | Probe | Test environment |
|---|---|---|---|
| `binance` | `api_key`, `api_secret` | `accountConfig` (canTrade, one-way) and, on mainnet, `apiRestrictions` (futures on, withdrawals off) | USDⓈ-M testnet (`demo-fapi.binance.com`) |
| `bitget` | `api_key`, `api_secret`, `passphrase` | `account/settings`: Unified Trading Account in one-way mode (Classic accounts must upgrade) | demo trading (`paptrading: 1`, demo key) |
| `hyperliquid` | `agent_private_key` (approved API wallet), `account_address` (master) | `userRole` of the agent is `agent` for that account | testnet |
| `lighter` | `api_private_key`, `account_index`, `api_key_index` (4-254) | native `check_client` against the registered key | testnet (`api.testnet.lighter.xyz`, chain 300) |
| `mexc` | `api_key`, `api_secret` | position mode one-way and a no-op cancel proving "Order Placing" (KYC required) | none |

Every venue also accepts `environment` = `mainnet` (default) or `testnet`. Errors map to distinct, non-retryable domain errors (`InsufficientMarginError`, `OrderRejectedError`, `VenuePermissionError`); a venue rejection of a submit becomes a `REJECTED` order, never a resend. Rate limits follow each venue's documented weights; Binance syncs its clock with `serverTime` and resyncs on `-1021`. The Hyperliquid and Lighter signers are optional extras (`uv sync --extra venues`; the dev environment and the image include them).

Testnet round trips (place and cancel a far post-only limit) are opt-in:

```bash
JOSEPH_SANDBOX_TESTS=1 \
JOSEPH_SANDBOX_BINANCE_API_KEY=... JOSEPH_SANDBOX_BINANCE_API_SECRET=... \
uv run pytest -m sandbox tests/sandbox -k binance -s
```

Variables per venue: `JOSEPH_SANDBOX_BITGET_{API_KEY,API_SECRET,PASSPHRASE}`, `JOSEPH_SANDBOX_HYPERLIQUID_{AGENT_PRIVATE_KEY,ACCOUNT_ADDRESS}`, `JOSEPH_SANDBOX_LIGHTER_{API_PRIVATE_KEY,ACCOUNT_INDEX,API_KEY_INDEX}`; optional `JOSEPH_SANDBOX_<VENUE>_{SYMBOL,QTY}`.

## API

All routes except `/health` require `Authorization: Bearer <token>`. Errors use the contract `ErrorResponse`.

| Area | Endpoints |
|---|---|
| Accounts | `GET /v1/accounts`, `POST /v1/accounts/connect/start`, `POST /v1/accounts/{id}/verify`, `POST /v1/accounts/{id}/revoke`, `GET /v1/accounts/{id}/summary`, `GET /v1/accounts/{id}/capabilities` |
| Orders | `POST /v1/orders/preview`, `POST /v1/orders/intents` (`Idempotency-Key` required), `GET /v1/orders?account_id=&state=a,b`, `POST /v1/orders/{id}/cancel`, `POST /v1/orders/{id}/amend` |
| Positions | `GET /v1/positions`, `POST /v1/positions/{id}/close-preview`, `POST /v1/positions/{id}/close` (`Idempotency-Key`), `GET /v1/fills` |
| Risk | `GET`/`POST /v1/risk/kill-switch`, `GET`/`PUT /v1/risk/limits?account_id=`, `GET /v1/audit/events` (includes `chain_valid`) |
| Strategies | `GET`/`POST /v1/deployments`, `POST /v1/deployments/{id}/disable`, `POST /v1/deployments/{id}/live-consent` |
| Internal | `POST /internal/v1/signals` (`X-Joseph-Service-Token`), `GET /metrics` (service token) |
| Stream | `WS /v1/stream/user?token=`: send `{"action":"subscribe","channels":[{"channel":"orders"}],"last_sequence":N}`. Unknown gaps return `snapshot_required` |

## Safety guarantees and the tests that prove them

| Guarantee | Tests |
|---|---|
| LIVE needs the global flag, account LIVE mode, an AVAILABLE adapter, a preview token for manual orders and, for strategies, unexpired scoped consent with venue-native stop-loss | `unit/test_risk.py`, `security/test_security.py::test_live_gate_positive_control...`, `integration/test_live_venues.py` |
| Gate off or no credentials: no venue request at all; kill switch or missing consent: no order request | `integration/test_live_venues.py`, `unit/venues/` |
| Signatures match official vectors (Binance docs, Hyperliquid SDK, Lighter native signer) | `unit/venues/test_signing_vectors.py` |
| A lost ACK never produces a second order; resubmission only after the venue confirms absence; no query capability means manual intervention | `chaos/test_fault_injection.py` |
| Duplicate, reordered or dropped venue events apply once; reconciliation repairs missed fills and blocks on position mismatch | `chaos/`, `property/test_invariants.py` |
| Idempotency keys replay the original result, and a different body under the same key is rejected (DB unique constraints back this up) | `integration/test_api.py` |
| A kill switch blocks opening orders but still allows reduce-only closes. Cancel and flatten run only when explicitly requested | `integration/test_oms_flows.py` |
| Closes are reduce-only and never flip; reverse is two steps | `integration/test_oms_flows.py` |
| Tenants never see or mutate each other's data (HTTP and WebSocket) | `integration/test_api.py` |
| Secrets never appear in responses, logs or the audit log; the audit log is a hash chain and append-only in the database | `security/`, `unit/test_preview_platform.py` |

## Test

```bash
uv run pytest                 # unit, integration, chaos, security, property, contract, architecture, system
# opt-in broker checks (skipped by default): -m ibkr_paper / -m sinopac_sim, see docs/BROKERS.md
uv run ruff check src tests && uv run mypy src && uv run lint-imports
```

The system tests start a real uvicorn process and talk to it over HTTP and WebSocket.

## Known gaps

- Testnet round trips need real keys and are not run in CI (`tests/sandbox`); MEXC has no sandbox.
- Bitget Classic accounts are not supported (UTA only); MEXC stop orders are plan orders that expire after 7 days.
- Production MFA, KMS/HSM master-key custody and key rotation tooling. The vault uses a process-held master key.
- Multi-instance deployment: outbox in-flight guards are per process, so multiple workers need row leases.
- Funding accrual and liquidation prices are not modelled for paper positions.
- LIVE position reconciliation is skipped (broker positions bought outside Joseph are not managed); see `docs/BROKERS.md` for the broker-specific limitations.
