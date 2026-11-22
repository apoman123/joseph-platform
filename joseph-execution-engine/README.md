# Joseph Execution Engine (private)

The trust boundary that owns accounts, credentials, the Risk Gateway, the OMS, reconciliation, the audit log and kill switches. Every manual, chart, emergency and strategy order follows one path:

```text
OrderIntent -> Risk Gateway (deny by default) -> OMS (transactional outbox) -> TradingAdapter
```

**LIVE trading is disabled.** Live venue adapters for Binance, Hyperliquid, Lighter, Bitget and MEXC declare their documented capabilities but report `TRADING_UNAVAILABLE` and refuse every call: signing and private streams are not implemented or sandbox-verified in this build. PAPER runs on a simulated venue. SHADOW records the orders Joseph would send but never sends them.

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
| `JOSEPH_LIVE_TRADING_ENABLED` | `false` | Global LIVE gate; one of five gates that must all pass |
| `JOSEPH_EMERGENCY_HALT` | `false` | Platform-wide block on new exposure |
| `JOSEPH_VAULT_MASTER_KEY` | ephemeral in dev | Base64 32-byte key that wraps per-credential AES-GCM data keys |
| `JOSEPH_PREVIEW_SECRET` | random | HMAC key for preview tokens |
| `JOSEPH_STEP_UP_TOTP_SECRET` | unset (step-up always fails) | Base32 TOTP secret for live consent |
| `JOSEPH_CORS_ORIGINS` | `http://localhost:3000,...` | Browser origins |
| `JOSEPH_PAPER_STARTING_EQUITY` | `100000` | Paper account equity |
| `JOSEPH_BACKGROUND_TASKS` | `true` | Outbox worker, paper ticker and reconciler loops |

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
| LIVE needs the global flag, account LIVE mode, an AVAILABLE adapter, a preview token for manual orders and, for strategies, unexpired scoped consent with venue-native stop-loss | `unit/test_risk.py`, `security/test_security.py::test_live_gate_positive_control...` |
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
uv run ruff check src tests && uv run mypy src && uv run lint-imports
```

The system tests start a real uvicorn process and talk to it over HTTP and WebSocket.

## Known gaps

- Real venue signing, private user streams and sandbox verification for the five venues (P7).
- Production MFA, KMS/HSM master-key custody and key rotation tooling. The vault uses a process-held master key.
- Multi-instance deployment: outbox in-flight guards are per process, so multiple workers need row leases.
- Funding accrual and liquidation prices are not modelled for paper positions.
