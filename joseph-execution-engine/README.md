# Joseph Execution Engine (private)

The trust boundary that owns accounts, credentials, the Risk Gateway, the OMS, reconciliation, the audit log and kill switches. Every manual, chart, emergency and strategy order takes one path:

```text
OrderIntent -> Risk Gateway (deny by default) -> OMS (transactional outbox) -> TradingAdapter
```

- **PAPER** runs on a simulated venue; **SHADOW** records what would be sent and sends nothing.
- **LIVE** runs on Binance USDⓈ-M, Bitget USDT-M (UTA), Hyperliquid, Lighter and MEXC futures (signed REST, private WebSocket streams) and on [brokers](../docs/BROKERS.md). [Gates](../docs/OPERATIONS.md#live-enablement) · [settings](../docs/OPERATIONS.md#execution-engine); the global gate is off by default in code.

## Run

```bash
uv sync                      # optional extras: --extra venues (signers) --extra ibkr --extra sinopac
uv run uvicorn joseph_execution.bootstrap:create_application --factory --port 8100
```

Development defaults: dev token `dev-local-token` → `tenant-01:user-01`, the static DEMO market gateway, SQLite at `./var/execution.db`. To paper trade on real data, run the market engine on :8000 and set `JOSEPH_MARKET_GATEWAY=http`.

## Live venues

Adapters are the last line of defense: with the gate off they negotiate nothing and refuse submits and amends; without credentials they send nothing. An account is `AVAILABLE` only when credentials are stored, the venue is healthy and a signed permission probe passes; otherwise `TRADING_UNAVAILABLE` with the reason in `notes`. **One-way position mode** only.

| Venue | Credential fields (`POST /v1/accounts/connect/start`) | Probe | Test environment |
|---|---|---|---|
| `binance` | `api_key`, `api_secret` | `accountConfig` (canTrade, one-way); on mainnet `apiRestrictions` (futures on, withdrawals off) | USDⓈ-M testnet (`demo-fapi.binance.com`) |
| `bitget` | `api_key`, `api_secret`, `passphrase` | `account/settings`: UTA in one-way mode (Classic accounts must upgrade) | demo trading (`paptrading: 1`, demo key) |
| `hyperliquid` | `agent_private_key` (approved API wallet), `account_address` (master) | the agent's `userRole` is `agent` for that account | testnet |
| `lighter` | `api_private_key`, `account_index`, `api_key_index` (4–254) | native `check_client` | testnet (`api.testnet.lighter.xyz`, chain 300) |
| `mexc` | `api_key`, `api_secret` | one-way mode and a no-op cancel proving "Order Placing" (KYC required) | none |

- Credentials also take `environment`: `mainnet` (default) or `testnet`.
- Errors map to non-retryable domain errors; a rejected submit becomes `REJECTED`, never a resend. Rate limits follow documented weights; Binance resyncs its clock on `-1021`.
- MEXC's private stream stays on the JSON endpoint but parses JSON and Protobuf; `JOSEPH_VENUE_URLS=mexc.ws_url=wss://contract.mexc.com/edge?Trans-Protocol=Protobuf` switches endpoints. MEXC has not said whether private channels change on 2026-12-01. Vendored `.proto` files: Apache-2.0 (`adapters/venues/mexc_proto/NOTICE`).
- The Hyperliquid and Lighter signers are the `venues` extra (in the dev environment and image).

Testnet round trips (place and cancel a far post-only limit) are opt-in:

```bash
JOSEPH_SANDBOX_TESTS=1 JOSEPH_SANDBOX_BINANCE_API_KEY=... JOSEPH_SANDBOX_BINANCE_API_SECRET=... \
uv run pytest -m sandbox tests/sandbox -k binance -s
```

Other venues: `JOSEPH_SANDBOX_BITGET_{API_KEY,API_SECRET,PASSPHRASE}`, `JOSEPH_SANDBOX_HYPERLIQUID_{AGENT_PRIVATE_KEY,ACCOUNT_ADDRESS}`, `JOSEPH_SANDBOX_LIGHTER_{API_PRIVATE_KEY,ACCOUNT_INDEX,API_KEY_INDEX}`; optional `JOSEPH_SANDBOX_<VENUE>_{SYMBOL,QTY}`.

## API

All routes except `/health` need `Authorization: Bearer <token>`; errors use the contract `ErrorResponse`.

| Area | Endpoints |
|---|---|
| Accounts | `GET /v1/accounts`, `POST /v1/accounts/connect/start`, `POST /v1/accounts/{id}/verify`, `…/revoke`, `GET …/summary` (carries `live_armed_until_ms`), `GET …/capabilities`, `POST`/`DELETE …/live-consent` (arm/disarm: `{"step_up_code", "acknowledge_risk": true, "duration_ms"}`) |
| Orders | `POST /v1/orders/preview`, `POST /v1/orders/intents` (`Idempotency-Key` required), `GET /v1/orders?account_id=&state=a,b`, `POST /v1/orders/{id}/cancel`, `…/amend` |
| Positions | `GET /v1/positions`, `POST /v1/positions/{id}/close-preview`, `…/close` (`Idempotency-Key`), `GET /v1/fills` |
| Risk | `GET`/`POST /v1/risk/kill-switch`, `GET`/`PUT /v1/risk/limits?account_id=`, `GET /v1/audit/events` (`chain_valid`) |
| Strategies | `GET`/`POST /v1/deployments`, `POST /v1/deployments/{id}/disable`, `…/live-consent` |
| Internal | `POST /internal/v1/signals` (`X-Joseph-Service-Token`), `GET /metrics` (service token) |
| Stream | `WS /v1/stream/user?token=`: `{"action":"subscribe","channels":[{"channel":"orders"}],"last_sequence":N}`; unknown gaps return `snapshot_required` |

## Safety guarantees and their tests

| Guarantee | Tests |
|---|---|
| LIVE needs the global flag, account LIVE mode, an AVAILABLE adapter, a preview token and, for manual orders, an unexpired account arming (`LIVE_NOT_ARMED`, 403); strategies need scoped consent and a venue-native stop-loss | `unit/test_risk.py`, `security/test_security.py::test_live_gate_positive_control...`, `integration/test_live_venues.py`, `integration/test_live_arming.py` |
| Arming needs step-up, acknowledgement and a bounded duration; paper and unverified accounts are refused; outcomes are audited; risk-reducing actions work while disarmed | `integration/test_live_arming.py` |
| Gate off or no credentials: no venue request; kill switch or missing consent: no order request | `integration/test_live_venues.py`, `unit/venues/` |
| Signatures match official vectors (Binance docs, Hyperliquid SDK, Lighter signer) | `unit/venues/test_signing_vectors.py` |
| A lost ACK never produces a second order; resubmission only after the venue confirms absence; no query capability means manual intervention | `chaos/test_fault_injection.py` |
| Duplicate, reordered or dropped events apply once; reconciliation repairs missed fills and blocks on position mismatch | `chaos/`, `property/test_invariants.py` |
| Idempotency keys replay the original result; a different body under the same key is rejected (DB unique constraints back this) | `integration/test_api.py` |
| The kill switch blocks opening orders and allows reduce-only closes; cancel and flatten only when requested; closes never flip; reverse is two steps | `integration/test_oms_flows.py` |
| Tenants never see or change each other's data (HTTP and WebSocket) | `integration/test_api.py` |
| Secrets never appear in responses, logs or audit; the audit log is an append-only hash chain | `security/`, `unit/test_preview_platform.py` |

## Test

```bash
uv run pytest                 # unit, integration, chaos, security, property, contract, architecture, system
uv run ruff check src tests && uv run mypy src && uv run lint-imports
```

Opt-in broker checks: `docs/BROKERS.md`. System tests start a real uvicorn process.

## Known gaps

- Testnet round trips need real keys and are not run in CI; MEXC has no sandbox.
- Bitget Classic accounts are unsupported (UTA only); MEXC stop orders are plan orders that expire after 7 days.
- No production MFA, KMS/HSM master-key custody or key rotation tooling: the vault uses a process-held master key.
- One worker per database: outbox in-flight guards are per process until row leases exist.
- Paper positions model no funding accrual or liquidation price.
- Broker-specific limits: [`docs/BROKERS.md`](../docs/BROKERS.md#known-limitations).
