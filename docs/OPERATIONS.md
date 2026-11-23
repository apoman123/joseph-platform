# Operations runbook

## Topology

| Service | Port | State | Holds secrets? |
|---|---|---|---|
| `joseph-terminal` (Next.js) | 3000 | none (browser storage for layouts) | No — session token only |
| `joseph-market-engine` (FastAPI) | 8000 | strategies, versions, checkpoints, signals, alert outbox | Notification-channel tokens |
| `joseph-execution-engine` (FastAPI) | 8100 | intents, orders, fills, reservations, audit chain, deployments | Encrypted venue credentials, vault key |

Both engines use SQLAlchemy: SQLite by default (`./var/*.db`), PostgreSQL via `JOSEPH_DATABASE_URL=postgresql+psycopg://…` (install `psycopg`). Run one execution-engine worker per database until row leases are implemented.

## Start

```bash
# Once, either: fetch the indicator library over SSH (key with access to PineScripts) …
git config --global url."ssh://git@github.com/apoman123/PineScripts".insteadOf https://github.com/apoman123/PineScripts
eval "$(ssh-agent -s)" && ssh-add <that key>
# … or with a read-only token (see CI_CD.md) exported as PINESCRIPTS_TOKEN:
git config --global credential.https://github.com/apoman123/PineScripts.git.helper \
  '!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$PINESCRIPTS_TOKEN"; }; f'
make setup     # locked dependencies for all projects (fetches the indicator library)
make dev       # market :8000, execution :8100, terminal :3000 (dev tokens)
```

The market engine's indicators come from the private `apoman123/PineScripts` repository (`python_indicators/`), pinned by commit in `joseph-market-engine/pyproject.toml` (`https://github.com/apoman123/PineScripts.git`). Both settings above apply to that repository only. `make images` passes `PINESCRIPTS_TOKEN` to the image build as a BuildKit secret when it is set and forwards the ssh-agent otherwise; `make compose-up` does both, so it needs a running agent (it may hold no key when the token is set). Neither credential enters an image. To work on an indicator and the platform together: `cd joseph-market-engine && uv pip install -e ../../PineScripts/python_indicators` (undo with `uv sync`).

Containers: `deploy/docker-compose.yml` runs the published images (`make compose-up` builds them locally); GitHub Actions deploys the same file — see `CI_CD.md`.

Production checklist: `JOSEPH_ENV=production` on the execution engine (refuses dev tokens and ephemeral keys); set `JOSEPH_VAULT_MASTER_KEY`, `JOSEPH_PREVIEW_SECRET`, `JOSEPH_SERVICE_TOKEN` (same value on both engines); restrict `JOSEPH_CORS_ORIGINS`; terminate TLS in front of every service; put a BFF/identity provider in front of the browser (dev bearer tokens are not a production auth scheme).

## Key settings

| Variable | Service | Default | Notes |
|---|---|---|---|
| `JOSEPH_DEV_TOKENS` | both | — | `token:tenant:user,…` (development only) |
| `JOSEPH_SERVICE_TOKEN` | both | — | market → execution signal forwarding |
| `JOSEPH_ENABLED_VENUES` / `JOSEPH_DEMO_ONLY` | market | all / false | `binance,hyperliquid,lighter,bitget,mexc,mock` |
| `JOSEPH_PIPELINE_ENABLED` / `_INTERVAL_S` | market | true / 10 | live signal pipeline |
| `JOSEPH_FORWARD_SIGNALS` | market | true | forward PAPER/SHADOW entries to execution |
| `JOSEPH_TELEGRAM_BOT_TOKEN`, `JOSEPH_LINE_CHANNEL_TOKEN`, `JOSEPH_WEBHOOK_SECRET`, `JOSEPH_SMTP_*` | market | — | enable notification channels |
| `JOSEPH_MARKET_GATEWAY` / `JOSEPH_MARKET_URL` | execution | static / :8000 | `http` = risk and paper fills on real market data |
| `JOSEPH_LIVE_TRADING_ENABLED` | execution | false in code, **true** in `deploy/` | global LIVE gate; false stops all new live orders and venue negotiation |
| `JOSEPH_VENUE_SANDBOX` / `JOSEPH_VENUE_URLS` | execution | — | default accounts to venue testnets; endpoint overrides |
| `JOSEPH_EMERGENCY_HALT` | execution | false | platform-wide block on new exposure |
| `NEXT_PUBLIC_MARKET_API_URL`, `…_WS_URL`, `NEXT_PUBLIC_EXECUTION_API_URL`, `…_WS_URL` | terminal | empty = DEMO | baked in at build time |

## Monitoring

- `GET /health` on each engine; `GET /metrics` (Prometheus text; execution needs the service token).
- Watch: `joseph_feed_lag_ms`, `joseph_feed_reconnects`, `joseph_candle_revisions_total`, `joseph_http_requests_total{status=5xx}` (market); `joseph_order_unknown_total`, `joseph_risk_block_total`, `joseph_reconcile_lag_ms` (execution).
- The terminal's *Data health* tab shows per-venue transport (native WS / REST poll), lag, reconnects and gaps.
- Seconds charts (`1s`–`30s`) are built from each venue's public trade stream. Their history is only what the venue's trade history serves (Binance 48 h, Bitget 90 days, Lighter about 53 minutes; none on Hyperliquid and MEXC), and a reconnect that cannot be backfilled shows up as a gap rather than invented bars. `REST_POLL` of seconds bars is refused on venues without trade history.

## Routine tasks

| Task | How |
|---|---|
| Enable a notification channel | Set its env var(s), restart the market engine; users' *Notifications* rows switch from "Not delivered" to "On". Failed deliveries retry with backoff and dead-letter after 6 attempts. |
| Add/remove a venue | `JOSEPH_ENABLED_VENUES`; a disabled venue returns `NOT_FOUND` and charts show *Unavailable* |
| Probe venue connectivity | `cd joseph-market-engine && uv run python scripts/probe_venues.py` |
| Regenerate API docs | `make openapi` (writes `joseph-terminal/packages/contracts/openapi/*.json`) |
| Backup | SQLite: stop the service and copy `var/*.db`; PostgreSQL: `pg_dump`. The audit table is append-only; verify with `GET /v1/audit/events` → `chain_valid: true` after restore. |
| Upgrade | Run `make ci` on the new version; contracts carry `schema_version`, saved workspaces/drawings/strategies migrate on read. |
| Rollback | Redeploy the previous build; databases are forward-compatible within a minor version. Strategy versions are immutable, so no rule changes meaning. |

## Incidents

| Symptom | Action |
|---|---|
| Feed STALE / gaps | Check *Data health* and `probe_venues.py`. New exposure is blocked automatically; no action needed to stay safe. AUTO mode labels any REST fallback as *Polled*. |
| `ORDER_UNKNOWN` / `UNKNOWN_RECONCILING` | Do **not** resubmit. The reconciler queries by client order id; if the venue cannot be queried the order is flagged for manual intervention — check the venue UI, then cancel or confirm. |
| Unexpected exposure / runaway strategy | Engage the kill switch (*Risk controls* or `POST /v1/risk/kill-switch`, scope GLOBAL). It blocks new opening orders; tick *Cancel open orders* and/or *Close positions* only if you intend those actions. Disable the deployment, then pause the strategy. |
| Audit `chain_valid: false` | Treat as tampering or corruption: freeze writes, restore from backup, compare chains. |
| Alerts not arriving | Check the alert outbox (dead letters in `alert_deliveries`), channel credentials, and quiet hours on the subscription. |

## LIVE enablement

LIVE adapters for Binance, Bitget (UTA), Hyperliquid, Lighter and MEXC are implemented. The deploy compose sets `JOSEPH_LIVE_TRADING_ENABLED=true` (owner decision); the code default stays false. An account trades LIVE only after: storing withdrawal-disabled credentials (`POST /v1/accounts/connect/start`); `POST /v1/accounts/{id}/verify` negotiating `AVAILABLE` (healthy venue, permission probe, one-way mode); a previewed manual order, or an AUTO_LIVE deployment with scoped consent + step-up (MFA) and venue-native stop-loss; and no engaged kill switch. Before the first real order: run `make test-sandbox` against each venue's testnet, use a dedicated (sub)account (reconciliation flags any position Joseph did not open and blocks new exposure), and run a kill-switch drill. MEXC has no sandbox and needs KYC plus the "Order Placing" key permission.
