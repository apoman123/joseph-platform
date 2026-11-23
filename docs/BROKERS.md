# Live trading: brokers and crypto venues

For operators preparing real orders: Interactive Brokers, SinoPac Shioaji and Cathay for stocks and ETFs, and the crypto venues' prerequisites. Live adapters are verified only against mocks and fakes: start on paper, simulation or testnet. See also the [LIVE gates](OPERATIONS.md#live-enablement) and [settings](OPERATIONS.md#execution-engine).

## Securities brokers

Stocks and ETFs follow the crypto path (`OrderIntent → Risk Gateway → OMS → adapter`). A broker client exists only with `JOSEPH_IBKR_ENABLED` / `JOSEPH_SINOPAC_ENABLED`.

| Broker | Routes | Currency | Adapter (`adapters/brokers/`) | Status |
|---|---|---|---|---|
| `ibkr` Interactive Brokers | `nasdaq`, `nyse` (incl. Arca, American), `cboe` (BZX) | USD | `ibkr.py` (`ib_async` → IB Gateway/TWS) | Implemented |
| `sinopac` SinoPac Shioaji | `twse`, `tpex` | TWD | `sinopac.py` (`shioaji`) | Implemented |
| `cathay` Cathay Securities | `twse`, `tpex` | TWD | `cathay.py` | Refuses every order until Cathay's SDK is available |

- Paper accounts: `acct-paper-<tenant>` (USD: crypto, Nasdaq, NYSE, Cboe) and `acct-twd-paper-<tenant>` (TWSE, TPEx). Crypto accounts trade only their venue; `treasury:rate:*` never trades.
- `tpex` is the Taipei Exchange (櫃買中心) mainboard: most Taiwan bond ETFs (`tpex:etf:00679B`) and many stocks. Its rules match TWSE's for everything below ([TPEx rules](https://www.tpex.org.tw/zh-tw/mainboard/trading/rules/system.html)). The 興櫃 emerging board is not offered.
- `cboe` listings (e.g. `cboe:etf:GOVT`) use the XNYS calendar; IBKR routes them with primaryExchange `BATS` (unchecked against a live gateway).

## Order rules (Risk Gateway)

| Check | Rule |
|---|---|
| `routing.venue` | The account must serve the instrument's exchange; rate series are rejected |
| `market.session` | Closed market: market orders rejected, or queued as DAY for the open with `JOSEPH_EQUITY_CLOSED_MARKET_ORDERS=queue` where the broker allows (IBKR yes, SinoPac no). IOC/FOK need an open session; limits rest. `DAY` and `outside_rth` are refused on crypto |
| `size.tick` | Taiwan stocks 0.01 < 10, 0.05 < 50, 0.1 < 100, 0.5 < 500, 1 < 1000, else 5; Taiwan ETFs 0.01 < 50, else 0.05; US 0.01, or 0.0001 below $1 |
| `size.price_limit` | Taiwan limits within ±10% of the previous close; none for foreign-constituent ETFs (most bond ETFs) or a listing's first five days; an unknown reference blocks |
| `size.board_lot` | Taiwan: whole 1,000-share lots, or one odd-lot order of 1–999 shares (limit, ROD/DAY); mixed quantities are rejected with the split |
| `account.cash` | Cash in the instrument's currency; selling held shares needs none |
| `size.cash_account` | Leverage 1; long-only unless an IBKR margin account sets `JOSEPH_IBKR_SHORT_SELLING=true` |
| `protection.bracket_orders` | Need stop-market, limit and GTC orders (none on SinoPac, so no brackets) |

- **Limits** are in the account currency. TWD accounts multiply the USD-sized defaults and hard caps by `JOSEPH_TWD_LIMIT_FACTOR` (default 30; a sizing convention, not an FX rate). Saved limits stay but are clamped to the new caps.
- **Paper fills** happen only in session, clamp market fills into the daily limits, and settle as unsettled cash until the T+2 (Taiwan) or T+1 (US) session opens; unsettled proceeds count as buying power. The ledger is in memory.
- **Taiwan fees** (paper and estimates): brokerage at the statutory maximum 0.1425% (no discounts or minimums) plus the transaction tax on sales by Taipei trade date: stocks 0.3% (the 0.15% day-trade rate is never applied, since it depends on the broker agreement and the whole day's trades); ETFs 0.1%; index bond ETFs 0 through 2026-12-31 (證券交易稅條例 §2-1), then 0.1% unless extended ([Maintenance](OPERATIONS.md#maintenance)). Leveraged, inverse, futures and active bond ETFs pay 0.1%. The broker statement decides.

## Credentials

Broker secrets come only from the environment or `*_FILE` files, are redacted from logs and never appear in `repr`. A Joseph broker connection (`POST /v1/accounts/connect/start`) carries only `{"account": "<number>"}` and refuses any other field, so logins never pass through the browser. Build with `EXECUTION_UV_EXTRAS="--extra ibkr --extra sinopac"`.

## Interactive Brokers

1. **Paper first:** Client Portal → *Settings → Paper Trading Account* gives a separate login (accounts `DU…`). Use it until the checks pass.
2. **Secrets:** password in `deploy/secrets/ibkr_password`, `IBKR_USERNAME` and `IBKR_TRADING_MODE=paper` in `deploy/.env` (git-ignored).
3. **Gateway:** `docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile ibkr up -d` runs `ghcr.io/gnzsnz/ib-gateway:10.50.1h` (IB Gateway + IBC; `IB_GATEWAY_IMAGE` overrides). The execution container reaches `ib-gateway:4004` (paper) or `:4003` (live); a local gateway uses 4002/4001, TWS 7497/7496.
4. **2FA:** each full login needs an IBKR Mobile approval within about three minutes (IBC retries). Daily restarts at `AUTO_RESTART_TIME` (11:45 PM New York) skip 2FA, but IBKR requires one 2FA login a week, after 01:00 ET Sunday. First login: set `IBKR_VNC_PASSWORD_FILE` and use VNC on `127.0.0.1:5900`.
5. **Enable:** `JOSEPH_IBKR_ENABLED=true`, `JOSEPH_IBKR_PORT=4004`, `JOSEPH_IBKR_PAPER=true` (startup fails if it mismatches the port); restart.
6. **Connect:** `POST /v1/accounts/connect/start` `{"venue": "ibkr", "credentials": {"account": "DU1234567"}, "withdrawal_permission_disabled": true}`, then `POST /v1/accounts/{id}/verify`; LIVE appears only if the gateway manages that account and the gate is on. Arm with `POST /v1/accounts/{id}/live-consent` `{"step_up_code": "<TOTP>", "acknowledge_risk": true, "duration_ms": 3600000}`.
7. **Check:** `JOSEPH_IBKR_PAPER_TESTS=1 JOSEPH_IBKR_PAPER_ACCOUNT=DU1234567 uv run --extra ibkr pytest -m ibkr_paper tests/brokers` (in `joseph-execution-engine/`) places and cancels a far 1-share limit order. Also try a `cboe:` listing to confirm `BATS`.
8. **Live:** `IBKR_TRADING_MODE=live`, `JOSEPH_IBKR_PORT=4003`, `JOSEPH_IBKR_PAPER=false`; connect the `U…` account, verify, arm.

## SinoPac (Shioaji)

1. **API key** with *Trading* permission (phone/email 2FA), restricted to the server's IP; the secret is shown once.
2. **Sign the API agreement** (stock API) in the Sign Center.
3. **CA certificate:** the `.pfx` and its password into `deploy/secrets/`; `JOSEPH_SINOPAC_CA_PATH=/run/joseph-secrets/Sinopac.pfx`.
4. **Secrets:** API key, secret key, CA password and person id in `deploy/secrets/sinopac_*`, referenced by the `JOSEPH_SINOPAC_*_FILE` variables (`/run/joseph-secrets/…`).
5. **Simulation test** (SinoPac requires it): with `JOSEPH_SINOPAC_SIMULATION=true`, Mon–Fri 08:00–20:00 Taipei (18:00–20:00 only from a Taiwan IP), run `JOSEPH_SINOPAC_SIM_TESTS=1 JOSEPH_SINOPAC_ACCOUNT=9A95-0000000 uv run --extra sinopac pytest -m sinopac_sim tests/brokers`, then wait at least five minutes for SinoPac's review.
6. **Signing:** in production the stock account must report `signed=True`; until then it stays `TRADING_UNAVAILABLE`.
7. **Production:** `JOSEPH_SINOPAC_SIMULATION=false`, `JOSEPH_SINOPAC_ENABLED=true`; connect `{"venue": "sinopac", "credentials": {"account": "9A95-0000000"}}`, verify, arm. One account trades `twse:*` and `tpex:*`.

Orders: LMT/MKT; ROD (DAY), IOC, FOK; Common (1,000-share lots) or IntradayOdd (shares); Cash. No stop orders, so no brackets or AUTO_LIVE. Limits: 250 order calls per 10 s, 25 account queries per 5 s (Joseph stays below). TWSE codes resolve in `Stocks.TSE`, TPEx in `Stocks.OTC`; a wrong venue is refused before sending. SinoPac allows 5 connections per person, shared with the market engine's optional [data login](OPERATIONS.md#market-data-setup).

## Cathay Securities

Cathay has no public API; trading needs Cathay's SDK and specification. Until then `cathay` declares TWSE/TPEx, TWD and cash, and refuses every call. With the SDK, implement `TradingAdapter` in `cathay.py` like `sinopac.py` (client protocol, fake, pure order mappings), keep `cathay.build()` and update `capabilities.cathay()`. Routing, risk and the OMS already handle it; the SDK must be told the exchange of TPEx codes.

## Crypto venues

Credential fields, probes and test environments per venue: [execution engine README](../joseph-execution-engine/README.md#live-venues).

- **Testnet first:** `make test-sandbox` with `JOSEPH_SANDBOX_<VENUE>_*` keys places and cancels a far order on each testnet (MEXC has none).
- **A dedicated account or subaccount:** positions Joseph did not open block new exposure.
- **Keys without withdrawal permission** and **one-way position mode** (hedge mode fails verification).
- **Bitget:** a Unified Trading Account (UTA). **MEXC:** KYC and the "Order Placing" key permission.

## Reconciliation and holdings

LIVE broker accounts are reconciled like crypto ones. A position that differs from Joseph's (shares bought elsewhere, a missed fill) is marked unknown and blocks new exposure on that account: use a dedicated account. SinoPac holdings take their exchange from Shioaji (TSE → `twse`, OTC → `tpex`); 興櫃 (`oes:…`) and unresolvable (`unknown:…`) codes are reported as unmanaged, logged and skipped by reconciliation.

## Known limitations

- The Taiwan reference price is the previous daily close; on ex-rights or ex-dividend days the exchange's reference may differ, and the exchange decides.
- IBKR corrections (same exec id prefix) are ignored rather than double counted; a fill before its commission report keeps fee 0.
- Shioaji fills carry estimated fees and come from deal callbacks of the running process (no client order id: matched by trade id or the 6-character `custom_field`).
- TWSE/TPEx after-hours fixed-price and odd-lot sessions are not offered.
