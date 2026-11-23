# Securities brokers: Interactive Brokers, SinoPac, Cathay

Joseph routes exchange-listed stocks and ETFs through brokers. Every order still travels `OrderIntent -> Risk Gateway -> OMS -> adapter`, and every LIVE order still needs all the gates: `JOSEPH_LIVE_TRADING_ENABLED=true`, the broker explicitly enabled, an account verified into LIVE mode, a preview token for manual orders, AUTO_LIVE consent with step-up for strategies (plus a venue-held stop-loss), the Risk Gateway, the kill switch, idempotent client ids and the audit log.

| Broker (account venue) | Routes | Currency | Adapter | Status |
|---|---|---|---|---|
| `ibkr` Interactive Brokers | `nasdaq`, `nyse` (incl. NYSE Arca/American) | USD | `adapters/brokers/ibkr.py` (`ib_async` -> IB Gateway/TWS) | Implemented |
| `sinopac` SinoPac Shioaji | `twse` | TWD | `adapters/brokers/sinopac.py` (`shioaji`) | Implemented |
| `cathay` Cathay Securities | `twse` | TWD | `adapters/brokers/cathay.py` | Fail-closed: awaiting the Cathay API SDK/spec from the owner's Cathay representative |

Crypto accounts trade only their own venue. `treasury:rate:*` series are never tradable. Paper trading has two accounts per tenant: `acct-paper-<tenant>` (USD: crypto, Nasdaq, NYSE) and `acct-twd-paper-<tenant>` (TWD: TWSE).

## Order rules (Risk Gateway)

| Check | Rule |
|---|---|
| `routing.venue` | Account venue must serve the instrument's exchange (table above); rate series rejected |
| `market.session` | From the instrument calendar (market engine `GET /v1/calendars/{id}/sessions`). Closed market: market orders are rejected, or with `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS=queue` a DAY market order queues for the next open where the broker allows it (IBKR yes, SinoPac no). IOC/FOK need an open session; limit orders rest. `DAY` and `outside_rth` are refused for 24x7 crypto markets |
| `size.tick` | Ladder-aware (TWSE `tick_ladder`) |
| `size.price_limit` | Limit prices within +-`price_limit_pct` (TWSE 10%) of the previous close; unknown reference blocks |
| `size.board_lot` | TWSE: whole 1000-share lots, or one intraday odd-lot order of 1-999 shares (limit, ROD/DAY only). Mixed quantities are rejected with the split to send |
| `account.cash` | Cash buying power in the instrument's currency (no margin); sells of held shares need none |
| `size.cash_account` | Leverage 1; long-only unless the account may short (IBKR margin account with `JOSEPH_IBKR_SHORT_SELLING=true`) |
| `protection.bracket_orders` | Brackets need stop-market, limit and GTC orders (SinoPac has none, so no brackets) |

Notional and loss limits are in the account currency; for TWD accounts the defaults and hard caps are the USD values x30 (a sizing convention, not an FX rate).

Paper securities fill only in session, clamp market fills into the daily limits on the tick ladder, charge TWSE brokerage (0.1425%) and sale tax (0.3% stocks / 0.1% ETFs), and model settlement as unsettled cash until the T+2 (TWSE) / T+1 (US) session opens. The ledger is in memory; unsettled sale proceeds count as buying power; no funding applies.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `JOSEPH_LIVE_TRADING_ENABLED` | `false` | Global LIVE gate |
| `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS` | `reject` | `reject` or `queue` closed-market market orders |
| `JOSEPH_PAPER_TWD_STARTING_CASH` | `3000000` | TWD paper account (`0`/`off` disables it) |
| `JOSEPH_IBKR_ENABLED` | `false` | Allow connecting to IB Gateway |
| `JOSEPH_IBKR_HOST` / `JOSEPH_IBKR_PORT` | `127.0.0.1` / `4002` | Gateway API (paper 4002, live 4001; container relays 4004 / 4003; TWS 7497 / 7496) |
| `JOSEPH_IBKR_PAPER` | `true` | Must match the port (startup fails on a paper/live mismatch) |
| `JOSEPH_IBKR_CLIENT_ID` | `17` | API client id (unique per connection) |
| `JOSEPH_IBKR_SHORT_SELLING` / `JOSEPH_IBKR_READONLY` | `false` | Margin-account shorting / read-only API |
| `JOSEPH_SINOPAC_ENABLED` | `false` | Allow logging in to Shioaji |
| `JOSEPH_SINOPAC_SIMULATION` | `true` | Shioaji simulation mode |
| `JOSEPH_SINOPAC_API_KEY[_FILE]`, `JOSEPH_SINOPAC_SECRET_KEY[_FILE]` | unset | Shioaji API key and secret |
| `JOSEPH_SINOPAC_CA_PATH`, `JOSEPH_SINOPAC_CA_PASSWORD[_FILE]`, `JOSEPH_SINOPAC_PERSON_ID[_FILE]` | unset | CA certificate for production orders |
| `JOSEPH_BROKER_RESYNC_INTERVAL_MS` | `60000` | Periodic broker resync |

Broker secrets come only from the environment or `*_FILE` secret files, are registered for log redaction and never appear in `repr`. A broker *connection* in Joseph (`POST /v1/accounts/connect/start`) carries only `{"account": "<broker account number>"}`; any other field is refused, so logins never pass through the browser. Build the image with `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"`.

## Interactive Brokers setup

1. **Paper account first.** In IBKR Client Portal open *Settings -> Paper Trading Account*, create or reset the paper login (a separate username; account ids start with `DU`). Use it until the end-to-end checks pass.
2. **Secrets.** Put the IBKR password in `deploy/secrets/ibkr_password` (git-ignored) and the username in `deploy/.env` (`IBKR_USERNAME`, git-ignored). `IBKR_TRADING_MODE=paper` (or `live`).
3. **Start the gateway.** `docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile ibkr up -d`. The image is `ghcr.io/gnzsnz/ib-gateway` (IB Gateway + IBC), pinned to `10.50.1h`; override with `IB_GATEWAY_IMAGE`. From the execution container the API is `ib-gateway:4004` (paper) or `ib-gateway:4003` (live).
4. **Two-factor authentication.** Every gateway login sends an IBKR Mobile notification; approve it on the phone within about three minutes (IBC cannot do 2FA for you; with `RELOGIN_AFTER_TWOFA_TIMEOUT=yes` it retries until you approve). `AUTO_RESTART_TIME` (default 11:45 PM New York) restarts the gateway daily *without* 2FA, but IBKR requires one full login with 2FA per week after 01:00 ET on Sunday, so expect one approval at the start of each week. For the first login or troubleshooting, set `IBKR_VNC_PASSWORD_FILE` and connect a VNC client to `127.0.0.1:5900`.
5. **Enable Joseph.** `JOSEPH_IBKR_ENABLED=true`, `JOSEPH_IBKR_PORT=4004`, `JOSEPH_IBKR_PAPER=true`; restart the execution engine.
6. **Connect and verify.** `POST /v1/accounts/connect/start` with `{"venue": "ibkr", "credentials": {"account": "DU1234567"}, "withdrawal_permission_disabled": true}`, then `POST /v1/accounts/{id}/verify`. The account becomes `CONNECTED` with LIVE mode only when the gateway manages that account and the global LIVE flag is on.
7. **Opt-in check.** `JOSEPH_IBKR_PAPER_TESTS=1 JOSEPH_IBKR_PAPER_ACCOUNT=DU1234567 uv run --extra ibkr pytest -m ibkr_paper tests/brokers` places and cancels a 1-share limit order far from the market.
8. **Live.** Switch `IBKR_TRADING_MODE=live`, `JOSEPH_IBKR_PORT=4003`, `JOSEPH_IBKR_PAPER=false`, connect the `U...` account and verify again. US equities settle T+1.

## SinoPac (Shioaji) setup

1. **API key.** In SinoPac's API management page create an API key with *Trading* permission (pass the phone/email 2FA); restrict it to the server's IP. The secret key is shown once.
2. **Sign the API agreement** in the Sign Center (stock API), before the test.
3. **CA certificate.** Download the certificate (`.pfx`) from the API management page and note its password; put it in `deploy/secrets/` and set `JOSEPH_SINOPAC_CA_PATH=/run/joseph-secrets/Sinopac.pfx`.
4. **Secrets.** Write the API key, secret key, CA password and person id into `deploy/secrets/sinopac_*` files and point the `JOSEPH_SINOPAC_*_FILE` variables at `/run/joseph-secrets/...`.
5. **Simulation test (required by SinoPac before production).** With `JOSEPH_SINOPAC_SIMULATION=true` run, Mon-Fri 08:00-20:00 Taipei (18:00-20:00 from a Taiwan IP only): `JOSEPH_SINOPAC_SIM_TESTS=1 JOSEPH_SINOPAC_ACCOUNT=9A95-0000000 uv run --extra sinopac pytest -m sinopac_sim tests/brokers` (logs in with `simulation=True` and calls `place_order`). Wait at least five minutes for SinoPac's review.
6. **Check signing.** In production mode the stock account must report `signed=True`; until then Joseph keeps the account `TRADING_UNAVAILABLE` with a note.
7. **Production.** `JOSEPH_SINOPAC_SIMULATION=false`, `JOSEPH_SINOPAC_ENABLED=true`; connect `{"venue": "sinopac", "credentials": {"account": "9A95-0000000"}}` and verify. Orders: `price_type` LMT/MKT, ROD (DAY)/IOC/FOK, `order_lot` Common (lots of 1000) or IntradayOdd (shares), `order_cond` Cash. No stop orders, so no brackets or AUTO_LIVE. SinoPac allows 250 order calls per 10 s and 25 account queries per 5 s; Joseph paces below both.

## Cathay Securities

Cathay publishes no developer API. The `cathay` entry declares TWSE, TWD and a cash account and refuses every call ("Awaiting the Cathay API SDK/spec from the owner's Cathay representative"). When the SDK arrives, implement `TradingAdapter` in `adapters/brokers/cathay.py` (follow `sinopac.py`: a client protocol, a fake for tests, pure order mappings), keep `cathay.build()` as the factory and update `capabilities.cathay()`; routing, risk checks and the OMS already treat `cathay` as a TWSE broker.

## Known limitations

- Position reconciliation is skipped for LIVE accounts (as before); holdings bought outside Joseph are not managed by it, and long-only checks use Joseph's own positions (the broker rejects uncovered sells in a cash account anyway).
- TWSE's reference price is the previous daily close from the market engine; on ex-rights/ex-dividend days the exchange's adjusted reference may differ, and the exchange remains the final authority.
- IBKR executions corrected by IBKR (same exec id prefix) are ignored rather than double counted; a fill emitted before its commission report keeps fee 0.
- Shioaji fills carry estimated fees; fills are recorded from deal callbacks of the running process (Shioaji keeps no client order id; orders are matched by trade id or the 6-character `custom_field` tag).
- TWSE after-hours fixed-price and after-hours odd-lot sessions are not offered.
