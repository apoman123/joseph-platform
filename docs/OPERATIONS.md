# Operations runbook

## Topology

| Service | Port | Secrets |
|---|---|---|
| `joseph-terminal` | 3000 | session token only (layouts in the browser) |
| `joseph-market-engine` | 8000 | notification tokens, Alpaca and SinoPac data keys |
| `joseph-execution-engine` | 8100 | encrypted venue credentials, vault key, broker secrets |

Engines store state in SQLite (`./var/*.db`) or PostgreSQL (`postgresql+psycopg://…`, install `psycopg`). Run one execution worker per database until row leases exist.

## Start

The market engine installs the private indicator library from `apoman123/PineScripts`. Give the machine read access to that repository only:

```bash
# either an SSH key with access to PineScripts:
git config --global url."ssh://git@github.com/apoman123/PineScripts".insteadOf https://github.com/apoman123/PineScripts
eval "$(ssh-agent -s)" && ssh-add <that key>
# or a read-only token (CI_CD.md) exported as PINESCRIPTS_TOKEN:
git config --global credential.https://github.com/apoman123/PineScripts.git.helper \
  '!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$PINESCRIPTS_TOKEN"; }; f'
make setup && make dev   # market :8000, execution :8100, terminal :3000 (dev tokens)
```

- `make images` uses `PINESCRIPTS_TOKEN` as a BuildKit secret when set, else the ssh-agent; `make compose-up` needs a running agent. Neither enters an image.
- To develop an indicator: `cd joseph-market-engine && uv pip install -e ../../PineScripts/python_indicators` (undo: `uv sync`).
- **Containers:** `deploy/docker-compose.yml` (CI deploys the same file, [CI_CD](CI_CD.md)). It passes only the variables it lists, so add any other setting below to its `environment:`. Optional SDKs are build args: `UV_EXTRAS="--extra sinopac"` (market, SinoPac data); `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"` in `deploy/.env` (execution).
- **Production:** `JOSEPH_ENV=production` on the execution engine (refuses dev tokens, requires `JOSEPH_VAULT_MASTER_KEY`); set `JOSEPH_PREVIEW_SECRET` and `JOSEPH_STEP_UP_TOTP_SECRET`; the same `JOSEPH_SERVICE_TOKEN` on both engines; empty `JOSEPH_DEV_TOKENS`; restricted `JOSEPH_CORS_ORIGINS`; TLS in front of every service; a BFF or identity provider in front of the browser (dev tokens are not production auth).

## Settings

`[_FILE]` secrets may come from the file named by `NAME_FILE`. Secrets are never logged and are redacted from `repr`.

### Both engines

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_DEV_TOKENS` | market none; execution `dev-local-token:tenant-01:user-01` in development | `token:tenant:user,…` |
| `JOSEPH_SERVICE_TOKEN` | execution `dev-service-token` in development | Signal forwarding and execution `/metrics` |
| `JOSEPH_DATABASE_URL` | `sqlite:///./var/market.db`, `…/execution.db` | SQLAlchemy URL |
| `JOSEPH_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Browser origins |

### Market engine

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_ENABLED_VENUES` | `binance,hyperliquid,lighter,bitget,mexc,twse,tpex,nasdaq,nyse,cboe,treasury,mock` | Disabled venues return `NOT_FOUND`; `deploy/` default is crypto + `mock` |
| `JOSEPH_DEMO_ONLY` | `false` | Only `mock` |
| `JOSEPH_EXECUTION_URL` | `http://localhost:8100` | Signal forwarding |
| `JOSEPH_REPLAY_DIR` | unset | JSONL candles for the replay adapter |
| `JOSEPH_HTTP_TIMEOUT_S`, `JOSEPH_POLL_INTERVAL_S` | `10`, `5` (≥1) | Venue HTTP timeout; REST polling |
| `JOSEPH_SNAPSHOT_STALE_AFTER_MS`, `JOSEPH_STREAM_RETENTION` | `30000`, `2000` | Stale age; envelopes kept per stream for resume |
| `JOSEPH_QUALIFICATION_TOP_N`, `JOSEPH_COINGECKO_ENABLED` | `50`, `false` | Binance screening preselection; trending heat bonus |
| `JOSEPH_MAX_ANCHORED_BARS` | `50000` (≥2000) | Anchored-study reach |
| `JOSEPH_PIPELINE_ENABLED`, `_INTERVAL_S`, `_MAX_INSTRUMENTS` | `true`, `10`, `10` | Live signal pipeline |
| `JOSEPH_FORWARD_SIGNALS`, `JOSEPH_ALERT_INTERVAL_S` | `true`, `2` | Forward PAPER/SHADOW/AUTO_LIVE entries; alert outbox pass |
| `JOSEPH_TELEGRAM_BOT_TOKEN`, `JOSEPH_LINE_CHANNEL_TOKEN`, `JOSEPH_WEBHOOK_SECRET`, `JOSEPH_SMTP_HOST`, `_PORT` (`587`), `_FROM`, `_USERNAME`, `_PASSWORD` | unset | Notification channels |
| `JOSEPH_ALPACA_KEY_ID`, `JOSEPH_ALPACA_SECRET_KEY` | unset | US data; without them US data calls fail with "configure Alpaca keys" |
| `JOSEPH_ALPACA_FEED` | `iex` | `iex` real time, IEX volume · `delayed_sip` free, ≥15 min late · `sip` paid. Consolidated feeds read each session's closing print (≤20 requests per fetch, ≤60/min) |
| `JOSEPH_ALPACA_SECONDS_HISTORY_MS` | `1800000` | US seconds-bar history |
| `JOSEPH_TW_DATA_SOURCE` | `twse` | `sinopac`: Shioaji intraday data with official fallback (`sinopac` extra) |
| `JOSEPH_SINOPAC_API_KEY[_FILE]`, `JOSEPH_SINOPAC_SECRET_KEY[_FILE]` | unset | Shioaji data login (no CA) |
| `JOSEPH_SINOPAC_MAX_SYMBOLS` | `60` (1–100) | Codes streamed at once (2 of 200 subscriptions each) |
| `JOSEPH_SINOPAC_INTRADAY_TICKS_CALLS`, `_INTRADAY_KBARS_CALLS` | `8` (≤10), `200` (≤270) | Daily trading-hours query budget |
| `JOSEPH_SINOPAC_SECONDS_HISTORY_DAYS` | `5` (≤30) | Sessions of tick history |
| `JOSEPH_TRADE_RECORDING`, `JOSEPH_TRADE_BAR_RETENTION_DAYS` | `true`, `30` | Record public trades; retention |
| `JOSEPH_TRADE_RECORD_INSTRUMENTS`, `_MAX_INSTRUMENTS` | none, `16` | Always recorded; concurrent cap |
| `JOSEPH_TRADE_BACKFILL_MAX_CALLS` | `20` | REST calls per backfill |
| `JOSEPH_TRADE_ARCHIVE_URL` | `JOSEPH_DATABASE_URL` | Recorded trades and TWSE/TPEx recorded bars |
| `JOSEPH_BINANCE_ARCHIVE`, `_DIR`; `JOSEPH_BITGET_ARCHIVE`, `_DIR` | `true`, `./var/binance-vision`; `true`, `./var/bitget-archive` | Import past days from official archives |
| `JOSEPH_LIGHTER_ARCHIVE` | `true` | Lighter's AWS Parquet archive by range reads (about 14–19 MB per market-day, nothing on disk) |
| `JOSEPH_MEXC_WS_FORMAT` | `protobuf` | `json` works only until 2026-12-01 10:00 UTC |

### Execution engine

| Variable | Default | Notes |
|---|---|---|
| `JOSEPH_ENV` | `development` | Any other value refuses dev tokens and requires the vault key |
| `JOSEPH_VAULT_MASTER_KEY` | ephemeral in development | Base64 32 bytes; wraps per-credential AES-GCM keys |
| `JOSEPH_PREVIEW_SECRET` | random per start | Preview-token HMAC key |
| `JOSEPH_STEP_UP_TOTP_SECRET` | unset (step-up always fails) | Base32 TOTP for arming and AUTO_LIVE consent |
| `JOSEPH_LIVE_TRADING_ENABLED` | `false`; `true` in `deploy/` | Global LIVE gate; false stops new live orders and venue negotiation |
| `JOSEPH_LIVE_ARM_MAX_MS` | `28800000` (8 h) | Longest and default arming; startup fails above 24 h |
| `JOSEPH_EMERGENCY_HALT`, `JOSEPH_LIVE_STREAMS` | `false`, `true` | Block all new exposure; private account streams |
| `JOSEPH_VENUE_SANDBOX` | empty | `all` or venues defaulting to testnet/demo |
| `JOSEPH_VENUE_URLS` | empty | `venue.key=url,…` (`rest_url`, `testnet_rest_url`, `ws_url`, `testnet_ws_url`, `demo_ws_url`) |
| `JOSEPH_VENUE_TIMEOUT_S`, `JOSEPH_VENUE_RECV_WINDOW_MS` | `10`, `5000` | Venue timeout; signed-request window |
| `JOSEPH_VENUE_MARKET_SLIPPAGE_BPS` | `50` | Hyperliquid/Lighter market-order price band |
| `JOSEPH_MARKET_GATEWAY`, `JOSEPH_MARKET_URL` | `static`, `http://localhost:8000` | `http`: real market data for risk and paper fills |
| `JOSEPH_BACKGROUND_TASKS` | `true` | Outbox, paper ticker, reconciler, broker loops |
| `JOSEPH_TICK_INTERVAL_MS`, `JOSEPH_RECONCILE_INTERVAL_MS` | `1000`, `10000` | Paper tick; reconciliation |
| `JOSEPH_STREAM_HEARTBEAT_S`, `JOSEPH_DEMO_WALK_BPS` | `15`, `3` | User-stream heartbeat; DEMO price walk |
| `JOSEPH_PAPER_STARTING_EQUITY`, `JOSEPH_PAPER_TWD_STARTING_CASH` | `100000`, `3000000` | Paper accounts (`off` disables TWD) |
| `JOSEPH_TWD_LIMIT_FACTOR` | `30` | TWD multiple of USD-sized limits and hard caps; saved limits are clamped to the new caps |
| `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS` | `reject` | `queue`: DAY market orders wait for the open |
| `JOSEPH_BROKER_RESYNC_INTERVAL_MS` | `60000` | Broker resync |
| `JOSEPH_IBKR_ENABLED`, `_HOST`, `_PORT`, `_PAPER`, `_CLIENT_ID`, `_SHORT_SELLING`, `_READONLY` | `false`, `127.0.0.1`, `4002`, `true`, `17`, `false`, `false` | [Interactive Brokers](BROKERS.md#interactive-brokers) |
| `JOSEPH_SINOPAC_ENABLED`, `_SIMULATION`, `_API_KEY[_FILE]`, `_SECRET_KEY[_FILE]`, `_CA_PATH`, `_CA_PASSWORD[_FILE]`, `_PERSON_ID[_FILE]` | `false`, `true`, unset | [SinoPac trading](BROKERS.md#sinopac-shioaji) |

**Terminal:** `NEXT_PUBLIC_{MARKET,EXECUTION}_{API,WS}_URL`, baked in at build time; empty means DEMO.

## Monitoring

- `GET /health` on each engine; `GET /metrics` (Prometheus; execution needs the service token). Watch `joseph_feed_lag_ms`, `joseph_feed_reconnects`, `joseph_candle_revisions_total`, `joseph_http_requests_total{status=5xx}`, `joseph_order_unknown_total`, `joseph_risk_block_total`, `joseph_reconcile_lag_ms`.
- The terminal's *Data health* tab shows per-venue transport, lag, reconnects and gaps. Unrecorded time is a gap, never invented bars; data sources per venue are in the [market engine README](../joseph-market-engine/README.md#venues).
- SinoPac data failures (quota, outage, login) fall back to official data and show `POLL` / `DEGRADED` with the reason.
- Refresh calendars yearly or after an ad hoc closure: `uv run python scripts/generate_xtai_calendar.py` and `scripts/generate_usbond_calendar.py` (TWSE's published holidays also apply at runtime).

## Routine tasks

- **Notification channel:** set its variables and restart the market engine; failed deliveries retry and dead-letter after 6 attempts.
- **Probe venues:** `cd joseph-market-engine && uv run python scripts/probe_venues.py`. **API docs:** `make openapi`.
- **Backup:** SQLite: stop and copy `var/*.db`; PostgreSQL: `pg_dump`. After a restore, `GET /v1/audit/events` must show `chain_valid: true`.
- **Upgrade:** `make ci` on the new version; saved documents migrate on read. **Rollback:** redeploy the previous build ([CI_CD](CI_CD.md#release-and-rollback)).

## Incidents

| Symptom | Action |
|---|---|
| Feed STALE or gaps | Check *Data health* and `probe_venues.py`; new exposure is already blocked |
| `ORDER_UNKNOWN` / `UNKNOWN_RECONCILING` | Do **not** resubmit. The reconciler queries by client order id; if it cannot, the order is flagged for manual intervention: check the venue, then cancel or confirm |
| Position mismatch blocks an account | A holding Joseph did not open, or a missed fill. Resolve it at the venue, or use a dedicated account |
| Runaway strategy or unexpected exposure | Kill switch (*Risk controls* or `POST /v1/risk/kill-switch`, GLOBAL) blocks new opening orders; tick *Cancel open orders* / *Close positions* only if intended. Then disable the deployment and pause the strategy |
| Audit `chain_valid: false` | Treat as tampering: freeze writes, restore from backup, compare chains |
| Alerts not arriving | Dead letters in `alert_deliveries`, channel credentials, quiet hours |

## LIVE enablement

Live adapters cover Binance, Bitget (UTA), Hyperliquid, Lighter, MEXC, IBKR and SinoPac. The deploy compose enables the global gate (owner decision); code defaults to off. An account trades LIVE only when all hold:

1. Withdrawal-disabled credentials are stored (`POST /v1/accounts/connect/start`).
2. `POST /v1/accounts/{id}/verify` negotiates `AVAILABLE`: healthy venue, signed permission probe, one-way mode.
3. Manual orders: the account is armed (`POST /v1/accounts/{id}/live-consent` with a step-up code, at most `JOSEPH_LIVE_ARM_MAX_MS`; `DELETE` disarms) and the order previewed. Strategies: an AUTO_LIVE deployment with scoped consent, step-up and a venue-native stop-loss.
4. The risk gateway passes and no kill switch is engaged.

Before the first real order: `make test-sandbox` against each testnet (MEXC has none; it needs KYC and the "Order Placing" permission); a dedicated account or subaccount, because LIVE accounts are reconciled and positions Joseph did not open block new exposure; and a kill-switch drill. Broker setup: [BROKERS](BROKERS.md).
