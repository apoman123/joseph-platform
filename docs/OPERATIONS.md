# Operations runbook

For operators: starting Joseph, settings, monitoring, maintenance, incidents and the live-trading gates.

## Topology

| Service | Port | Secrets |
|---|---|---|
| `joseph-terminal` | 3000 | session token only (layouts stay in the browser) |
| `joseph-market-engine` | 8000 | notification tokens, Alpaca and SinoPac data keys |
| `joseph-execution-engine` | 8100 | encrypted venue credentials, vault key, broker secrets |

Engines store state in SQLite (`./var/*.db`) or PostgreSQL (`postgresql+psycopg://…`, install `psycopg`). Run one execution worker per database until row leases exist.

## Start

### Indicator library access

The market engine installs its indicator library from the **private** repository `apoman123/PineScripts` (`python_indicators/`, pinned `rev` in `joseph-market-engine/pyproject.toml`). Without read access it cannot be built or run; the terminal still runs alone in labelled DEMO mode, and the execution engine does not need the library. Grant read access to that repository only:

```bash
# either an SSH key with access to the library, in ssh-agent:
git config --global url."ssh://git@github.com/apoman123/PineScripts".insteadOf https://github.com/apoman123/PineScripts
eval "$(ssh-agent -s)" && ssh-add <key>
# or a read-only token (CI_CD.md) exported as PINESCRIPTS_TOKEN:
git config --global credential.https://github.com/apoman123/PineScripts.git.helper \
  '!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$PINESCRIPTS_TOKEN"; }; f'
```

- `make images` uses `PINESCRIPTS_TOKEN` as a BuildKit secret when set, else the ssh-agent; compose builds need a running agent. Neither enters an image.
- To develop an indicator: `cd joseph-market-engine && uv pip install -e <clone>/python_indicators` (undo: `uv sync`); to adopt new ports, bump `rev` and run `uv lock`.

### Local

`make setup && make dev`: market :8000, execution :8100 (paper, LIVE off), terminal :3000, dev tokens.

### Containers

1. Copy `deploy/.env.example` to `deploy/.env` and replace every `change-me`.
2. Put secret files in `deploy/secrets/`, mounted at `/run/joseph-secrets` for the `*_FILE` variables.
3. Run `make compose-up`.

CI deploys the same `deploy/docker-compose.yml` ([CI/CD](CI_CD.md#servers)). It enables every venue, turns the global LIVE gate on and passes the [settings](#settings) not marked †; add others to its `environment:`. Compose-only: `JOSEPH_{MARKET,EXECUTION}_DATABASE_URL`, build args `MARKET_UV_EXTRAS=--extra sinopac` (SinoPac data) and `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"` (broker SDKs), `PUBLIC_*` terminal URLs, image and port variables, and `IBKR_*` ([BROKERS](BROKERS.md#interactive-brokers)).

### Production checklist

- Execution engine: `JOSEPH_ENV=production` (no default dev or service token; requires `JOSEPH_VAULT_MASTER_KEY`), `JOSEPH_PREVIEW_SECRET`, `JOSEPH_STEP_UP_TOTP_SECRET` (also in an authenticator app).
- The same `JOSEPH_SERVICE_TOKEN` on both engines; empty `JOSEPH_DEV_TOKENS` (listed tokens work in any environment); restricted `JOSEPH_CORS_ORIGINS`.
- TLS in front of every service; a BFF or identity provider in front of the browser (dev tokens are not production auth).

### Market data setup

Crypto, TWSE/TPEx official data and Treasury yields need no keys.

- **US stocks and ETFs:** a free Alpaca market-data key (`JOSEPH_ALPACA_KEY_ID`, `JOSEPH_ALPACA_SECRET_KEY`); without it US data calls fail with "configure Alpaca keys". `JOSEPH_ALPACA_FEED`: `iex` (default; free, real time, IEX volume only), `delayed_sip` (free, all volume, ≥15 min late) or `sip` (paid). Only consolidated feeds carry the closing print.
- **Taiwan, optional SinoPac Shioaji data:** `JOSEPH_TW_DATA_SOURCE=sinopac`, `JOSEPH_SINOPAC_API_KEY_FILE`, `JOSEPH_SINOPAC_SECRET_KEY_FILE` (no CA; the trading key files work) and `MARKET_UV_EXTRAS=--extra sinopac`. Test outside trading hours: `JOSEPH_SINOPAC_DATA_TESTS=1 uv run --extra sinopac pytest tests/network/test_live_sinopac.py -m sinopac_data` (in `joseph-market-engine/`).

## Settings

`[_FILE]` secrets may come from the file named by `NAME_FILE`. Secrets are never logged and are redacted from `repr`. † = not passed by `deploy/docker-compose.yml`; "compose:" gives its value where it differs from the code default.

### Both engines

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_DEV_TOKENS` | market none; execution `dev-local-token:tenant-01:user-01` in development | `token:tenant:user,…`; compose: empty |
| `JOSEPH_SERVICE_TOKEN` | execution `dev-service-token` in development | Signal forwarding and execution `/metrics`; compose: required |
| `JOSEPH_DATABASE_URL` | `sqlite:///./var/market.db`, `…/execution.db` | SQLAlchemy URL; compose: see [Containers](#containers) |
| `JOSEPH_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Browser origins; compose: `http://localhost:3000` |

### Market engine

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_ENABLED_VENUES` | `binance,hyperliquid,lighter,bitget,mexc,twse,tpex,nasdaq,nyse,cboe,treasury,mock` | Disabled venues return `NOT_FOUND` |
| `JOSEPH_DEMO_ONLY` | `false` | Only `mock` |
| `JOSEPH_EXECUTION_URL` | `http://localhost:8100` | Signal forwarding; compose: `http://execution:8100` |
| `JOSEPH_REPLAY_DIR`† | unset | JSONL candles for the replay adapter |
| `JOSEPH_HTTP_TIMEOUT_S`†, `JOSEPH_POLL_INTERVAL_S`† | `10`, `5` (≥1) | Venue HTTP timeout; REST polling |
| `JOSEPH_SNAPSHOT_STALE_AFTER_MS`†, `JOSEPH_STREAM_RETENTION`† | `30000`, `2000` | Stale age; envelopes kept per stream for resume |
| `JOSEPH_QUALIFICATION_TOP_N`†, `JOSEPH_COINGECKO_ENABLED`† | `50`, `false` | Binance screening preselection; trending heat bonus |
| `JOSEPH_MAX_ANCHORED_BARS`† | `50000` (≥2000) | Anchored-study reach |
| `JOSEPH_PIPELINE_ENABLED`†, `_INTERVAL_S`†, `_MAX_INSTRUMENTS`† | `true`, `10` (≥1), `10` | Live signal pipeline |
| `JOSEPH_FORWARD_SIGNALS`†, `JOSEPH_ALERT_INTERVAL_S`† | `true`, `2` (≥0.5) | Forward PAPER/SHADOW/AUTO_LIVE entries; alert outbox pass |
| `JOSEPH_TELEGRAM_BOT_TOKEN`, `JOSEPH_LINE_CHANNEL_TOKEN`, `JOSEPH_WEBHOOK_SECRET`, `JOSEPH_SMTP_HOST`, `_PORT`† (`587`), `_FROM`, `_USERNAME`, `_PASSWORD` | unset | Notification channels |
| `JOSEPH_ALPACA_KEY_ID`, `JOSEPH_ALPACA_SECRET_KEY` | unset | [US data](#market-data-setup) |
| `JOSEPH_ALPACA_FEED` | `iex` | `iex`, `delayed_sip` or `sip`; closing prints cost ≤20 requests per fetch, ≤60/min |
| `JOSEPH_ALPACA_SECONDS_HISTORY_MS`† | `1800000` | US seconds-bar history |
| `JOSEPH_TW_DATA_SOURCE` | `twse` | `sinopac`: Shioaji data with official fallback |
| `JOSEPH_SINOPAC_API_KEY[_FILE]`, `JOSEPH_SINOPAC_SECRET_KEY[_FILE]` | unset | Shioaji data login (no CA); compose: `_FILE` only |
| `JOSEPH_SINOPAC_MAX_SYMBOLS`† | `60` (1–100) | Codes streamed at once (2 of 200 subscriptions each) |
| `JOSEPH_SINOPAC_INTRADAY_TICKS_CALLS`†, `_INTRADAY_KBARS_CALLS`† | `8` (≤10), `200` (≤270) | Daily trading-hours query budget |
| `JOSEPH_SINOPAC_SECONDS_HISTORY_DAYS`† | `5` (≤30) | Sessions of tick history |
| `JOSEPH_TRADE_RECORDING`, `JOSEPH_TRADE_BAR_RETENTION_DAYS` | `true`, `30` (≥1) | Record public trades; retention |
| `JOSEPH_TRADE_RECORD_INSTRUMENTS`†, `_MAX_INSTRUMENTS`† | none, `16` | Always recorded; concurrent cap |
| `JOSEPH_TRADE_BACKFILL_MAX_CALLS`† | `20` | REST calls per backfill |
| `JOSEPH_TRADE_ARCHIVE_URL` | `JOSEPH_DATABASE_URL` | Recorded trades and TWSE/TPEx recorded bars |
| `JOSEPH_BINANCE_ARCHIVE`, `_DIR`†; `JOSEPH_BITGET_ARCHIVE`, `_DIR`† | `true`, `./var/binance-vision`; `true`, `./var/bitget-archive` | Import past days from official archives |
| `JOSEPH_LIGHTER_ARCHIVE` | `true` | Lighter's AWS Parquet archive by range reads (about 14–19 MB per market-day, nothing on disk) |
| `JOSEPH_MEXC_WS_FORMAT` | `protobuf` | `json` works only until 2026-12-01 10:00 UTC ([Maintenance](#maintenance)) |

### Execution engine

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_ENV` | `development` | Any other value: no default dev or service token; vault key required |
| `JOSEPH_VAULT_MASTER_KEY` | ephemeral in development | Base64 32 bytes; wraps per-credential AES-GCM keys |
| `JOSEPH_PREVIEW_SECRET` | random per start | Preview-token HMAC key |
| `JOSEPH_STEP_UP_TOTP_SECRET` | unset (step-up always fails) | Base32 TOTP for arming and AUTO_LIVE consent |
| `JOSEPH_LIVE_TRADING_ENABLED` | `false` | Global LIVE gate; false stops new live orders and venue negotiation; compose: `true` |
| `JOSEPH_LIVE_ARM_MAX_MS` | `28800000` (8 h) | Longest and default arming; startup fails above 24 h |
| `JOSEPH_EMERGENCY_HALT`, `JOSEPH_LIVE_STREAMS` | `false`, `true` | Block all new exposure; private account streams |
| `JOSEPH_VENUE_SANDBOX` | empty | `all` or crypto venues defaulting to testnet/demo |
| `JOSEPH_VENUE_URLS` | empty | `venue.key=url,…` (`rest_url`, `testnet_rest_url`, `ws_url`, `testnet_ws_url`, `demo_ws_url`) |
| `JOSEPH_VENUE_TIMEOUT_S`, `JOSEPH_VENUE_RECV_WINDOW_MS`† | `10`, `5000` | Venue timeout; signed-request window |
| `JOSEPH_VENUE_MARKET_SLIPPAGE_BPS` | `50` | Hyperliquid/Lighter market-order price band |
| `JOSEPH_MARKET_GATEWAY`, `JOSEPH_MARKET_URL` | `static`, `http://localhost:8000` | `http`: real market data for risk and paper fills; compose: `http`, `http://market:8000` |
| `JOSEPH_BACKGROUND_TASKS`† | `true` | Outbox, paper ticker, reconciler, broker loops |
| `JOSEPH_TICK_INTERVAL_MS`†, `JOSEPH_RECONCILE_INTERVAL_MS`† | `1000`, `10000` | Paper tick; reconciliation |
| `JOSEPH_STREAM_HEARTBEAT_S`†, `JOSEPH_DEMO_WALK_BPS`† | `15`, `3` | User-stream heartbeat; DEMO price walk |
| `JOSEPH_PAPER_STARTING_EQUITY`†, `JOSEPH_PAPER_TWD_STARTING_CASH` | `100000`, `3000000` | Paper accounts (`off` disables TWD) |
| `JOSEPH_TWD_LIMIT_FACTOR` | `30` | TWD multiple of USD-sized limits and hard caps; saved limits are clamped to the new caps |
| `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS` | `reject` | `queue`: DAY market orders wait for the open |
| `JOSEPH_BROKER_RESYNC_INTERVAL_MS`† | `60000` | Broker resync |
| `JOSEPH_IBKR_ENABLED`, `_HOST`, `_PORT`, `_PAPER`, `_CLIENT_ID`, `_SHORT_SELLING`, `_READONLY` | `false`, `127.0.0.1`, `4002`, `true`, `17`, `false`, `false` | [Interactive Brokers](BROKERS.md#interactive-brokers); compose: host `ib-gateway`, port `4004` |
| `JOSEPH_SINOPAC_ENABLED`, `_SIMULATION`, `_API_KEY[_FILE]`, `_SECRET_KEY[_FILE]`, `_CA_PATH`, `_CA_PASSWORD[_FILE]`, `_PERSON_ID[_FILE]` | `false`, `true`, unset | [SinoPac trading](BROKERS.md#sinopac-shioaji); compose: `_FILE` only |

**Terminal:** `NEXT_PUBLIC_{MARKET,EXECUTION}_{API,WS}_URL` and `NEXT_PUBLIC_DEV_TOKEN`, baked in at build time; no market URL means DEMO.

## Monitoring

- `GET /health` on each engine; `GET /metrics` (Prometheus; execution needs the service token). Watch `joseph_feed_lag_ms`, `joseph_feed_reconnects`, `joseph_candle_revisions_total`, `joseph_http_requests_total{status=5xx}`, `joseph_order_unknown_total`, `joseph_risk_block_total`, `joseph_reconcile_lag_ms`.
- The terminal's *Data health* tab shows per-venue transport, lag, reconnects and gaps. Unrecorded time is a gap, never invented bars; data sources per venue are in the [market engine README](../joseph-market-engine/README.md#venues).
- SinoPac data failures (quota, outage, login) fall back to official data and show `POLL` / `DEGRADED` with the reason.

## Routine tasks

- **Notification channel:** set its variables and restart the market engine; failed deliveries retry and dead-letter after 6 attempts.
- **Probe venues:** `cd joseph-market-engine && uv run python scripts/probe_venues.py`. **API docs:** `make openapi`.
- **Backup:** SQLite: stop and copy `var/*.db`; PostgreSQL: `pg_dump`. After a restore, `GET /v1/audit/events` must show `chain_valid: true`.
- **Upgrade:** `make ci` on the new version; saved documents migrate on read. **Rollback:** redeploy the previous build ([CI/CD](CI_CD.md#release-and-rollback)).

## Maintenance

| When | Task |
|---|---|
| After 2026-12-01 10:00 UTC | MEXC retires its JSON market data: `make test-network` (the MEXC JSON case should fail); remove any `JOSEPH_MEXC_WS_FORMAT=json` |
| Taiwan extends the bond-ETF tax suspension (ends 2026-12-31; a 2036 extension is not law yet) | Update `BOND_ETF_TAX_SUSPENSION` in `joseph-execution-engine/src/joseph_execution/domain/equities.py` |
| Yearly, or after an ad hoc closure | In `joseph-market-engine/`: `uv run python scripts/generate_xtai_calendar.py` and `scripts/generate_usbond_calendar.py` (TWSE's published holidays also apply at runtime) |
| Before `PINESCRIPTS_TOKEN` expires | [Rotate it](CI_CD.md#indicator-library-access) |
| Weekly | IBKR 2FA login ([BROKERS](BROKERS.md#interactive-brokers)) |

## Incidents

| Symptom | Action |
|---|---|
| Feed STALE or gaps | Check *Data health* and `probe_venues.py`; new exposure is already blocked |
| `ORDER_UNKNOWN` / `UNKNOWN_RECONCILING` | Do **not** resubmit. The reconciler queries by client order id; if it cannot, the order is flagged for manual intervention: check the venue, then cancel or confirm |
| Position mismatch blocks an account | A holding Joseph did not open, or a missed fill. Resolve it at the venue, or use a dedicated account |
| Runaway strategy or unexpected exposure | Kill switch (*Risk controls* or `POST /v1/risk/kill-switch`, GLOBAL) blocks new opening orders; tick *Cancel open orders* / *Close positions* only if intended. Then disable the deployment and pause the strategy |
| Audit `chain_valid: false` | Treat as tampering: freeze writes, restore from backup, compare chains |
| Alerts not arriving | Dead letters in `alert_deliveries`, channel credentials, quiet hours |

## Live enablement

Live adapters cover Binance, Bitget (UTA), Hyperliquid, Lighter, MEXC, IBKR and SinoPac, verified only against mocks and fakes; Cathay refuses every order. The deploy compose enables the global gate (`JOSEPH_LIVE_TRADING_ENABLED`); code defaults to off. With the gate on, an account trades LIVE only when all hold:

1. Withdrawal-disabled credentials are stored (`POST /v1/accounts/connect/start`); brokers also need `JOSEPH_IBKR_ENABLED` / `JOSEPH_SINOPAC_ENABLED`.
2. `POST /v1/accounts/{id}/verify` negotiates `AVAILABLE`: healthy venue, signed permission probe, one-way mode.
3. Manual orders: the account is armed (`POST /v1/accounts/{id}/live-consent` with a step-up code, at most `JOSEPH_LIVE_ARM_MAX_MS`; `DELETE` disarms) and the order previewed. Strategies: an AUTO_LIVE deployment with scoped consent, step-up and a venue-native stop-loss.
4. The risk gateway passes and no kill switch is engaged.

Before the first real order, meet the [per-venue prerequisites](BROKERS.md) and run a kill-switch drill.
