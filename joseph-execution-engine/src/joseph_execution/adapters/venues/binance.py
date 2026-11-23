"""Binance USDⓈ-M futures trading adapter.

Sources (Binance developer docs, read 2026-11-23):

* General info, signing, limits, errors:
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info
  and ``.../error-code``, ``.../change-log``.
* REST endpoints (official OpenAPI rendering):
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api
* User data streams:
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams
* WebSocket routing change (``/public``, ``/market``, ``/private``; legacy
  ``/ws`` retired 2026-04-23):
  ``.../websocket-market-streams/Important-WebSocket-Change-Notice``.
* API key permissions: ``GET /sapi/v1/account/apiRestrictions`` on
  ``api.binance.com`` (core-trading-wallet docs).

Protocol facts this module relies on:

* REST ``https://fapi.binance.com``; testnet ``https://demo-fapi.binance.com``
  (general-info; the OpenAPI ``servers`` list also names
  ``testnet.binancefuture.com``, so both can be overridden).
* SIGNED requests carry ``X-MBX-APIKEY`` and a lowercase hex HMAC-SHA256
  ``signature`` of the exact query string, which ends with ``recvWindow``
  and ``timestamp``. The server accepts ``serverTime - timestamp <=
  recvWindow`` and ``timestamp < serverTime + 1000``, so the clock offset is
  measured with ``GET /fapi/v1/time`` and resynced on ``-1021``.
* Limits: 2400 request weight per minute per IP and 1200 orders per minute
  plus 300 per 10 s per account (``X-MBX-USED-WEIGHT-1M`` is observed).
  Weights: time 1, account/balance/positionRisk/userTrades/allOrders/
  accountConfig 5, openOrders and openAlgoOrders 40 (all symbols), order
  query/cancel 1, new/modify order 0 weight + 1 order count.
* Since 2025-12-09 conditional orders (``STOP``, ``STOP_MARKET`` …) must use
  ``POST /fapi/v1/algoOrder`` (``algoType=CONDITIONAL``, ``triggerPrice``,
  ``workingType``, ``clientAlgoId``); ``/fapi/v1/order`` answers ``-4120``.
  Untriggered conditional orders cannot be modified.
* ``PUT /fapi/v1/order`` modifies LIMIT orders only and needs ``side``,
  ``quantity`` and ``price``.
* User data: ``POST /fapi/v1/listenKey`` (valid 60 minutes, kept alive with
  ``PUT``), stream ``wss://fstream.binance.com/private/ws/<listenKey>``;
  a connection lives at most 24 hours; events ``ORDER_TRADE_UPDATE``,
  ``ALGO_UPDATE``, ``ACCOUNT_UPDATE`` and ``listenKeyExpired``.

Joseph trades one-way position mode only (``dualSidePosition=false``) so
the venue position equals the OMS's signed position.
"""

from __future__ import annotations

import dataclasses
import decimal
import logging
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from joseph_execution.adapters.venues import credentials as creds_lib
from joseph_execution.adapters.venues import http
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import ratelimit
from joseph_execution.adapters.venues import signing
from joseph_execution.adapters.venues import wire
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)
S = enums.OrderState
VENUE = "binance"
REST_URL = "https://fapi.binance.com"
TESTNET_REST_URL = "https://demo-fapi.binance.com"
WS_URL = "wss://fstream.binance.com/private/ws/"
TESTNET_WS_URL = "wss://demo-fstream.binance.com/private/ws/"
SAPI_URL = "https://api.binance.com"
_SEVEN_DAYS_MS = 7 * 86_400_000
_STATUS = {
    "NEW": S.ACKNOWLEDGED,
    "PARTIALLY_FILLED": S.PARTIALLY_FILLED,
    "FILLED": S.FILLED,
    "CANCELED": S.CANCELED,
    "REJECTED": S.REJECTED,
    "EXPIRED": S.EXPIRED,
    "EXPIRED_IN_MATCH": S.EXPIRED,
}
_TIF = {
    enums.TimeInForce.GTC: "GTC",
    enums.TimeInForce.IOC: "IOC",
    enums.TimeInForce.FOK: "FOK",
    enums.TimeInForce.POST_ONLY: "GTX",
}
_MARGIN_NOT_ENOUGH = (-2018, -2019, -2027, -2028)
_PERMISSION = (-1002, -1022, -2014, -2015, -4088, -4109)
_RATE = (-1003, -1008, -1015)
_NOT_FOUND = (-2011, -2013)
_LOT = (-4003, -4004, -4005)
_TICK = (-4014,)
_IGNORABLE = (-4046, -4059)


def budgets() -> list[ratelimit.Budget]:
    """Returns the documented IP budget.

    Returns:
        Budgets.
    """
    return [ratelimit.Budget("weight", 2400, 60.0)]


def order_budgets() -> list[ratelimit.Budget]:
    """Returns the documented per-account order budgets.

    Returns:
        Budgets.
    """
    return [
        ratelimit.Budget("orders_10s", 300, 10.0),
        ratelimit.Budget("orders_1m", 1200, 60.0),
    ]


def map_error(  # noqa: PLR0911, PLR0912 - one branch per error family.
    status: int, payload: Any
) -> errors.ExecutionError:
    """Maps a Binance error answer to a domain error.

    Args:
        status: HTTP status.
        payload: ``{"code": -2019, "msg": "..."}`` or text.

    Returns:
        Domain error (never containing credentials).
    """
    code = 0
    message = str(payload)[:200]
    if isinstance(payload, Mapping):
        try:
            code = int(payload.get("code", 0))
        except (TypeError, ValueError):
            code = 0
        message = str(payload.get("msg", ""))[:200]
    venue_code = str(code) if code else str(status)
    text = f"{message} ({venue_code})"
    if status >= 500:
        return errors.AdapterTimeoutError(
            f"binance answered HTTP {status}; outcome unknown"
        )
    if code in _MARGIN_NOT_ENOUGH:
        return errors.InsufficientMarginError(
            text, venue=VENUE, venue_code=venue_code
        )
    if code in _PERMISSION or status in (401, 403):
        return errors.VenuePermissionError(
            "permission missing: the API key is invalid, IP-restricted or "
            f"lacks USDⓈ-M futures trading ({text})",
            venue=VENUE,
            venue_code=venue_code,
        )
    if code in _RATE:
        return errors.RateLimitedError(f"binance throttled: {text}")
    if code == -1021:
        return errors.AdapterUnavailableError(
            f"binance rejected the request timestamp ({text}); not executed"
        )
    if code in _NOT_FOUND:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="NOT_FOUND"
        )
    if code in _LOT:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_LOT_SIZE"
        )
    if code in _TICK:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_TICK_SIZE"
        )
    if code == -4164:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="MIN_NOTIONAL"
        )
    if code == -4120:
        return errors.OrderRejectedError(
            text,
            venue=VENUE,
            venue_code=venue_code,
            code="UNSUPPORTED_ORDER_TYPE",
        )
    if code == -4116:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="CONFLICT"
        )
    return errors.OrderRejectedError(text, venue=VENUE, venue_code=venue_code)


@dataclasses.dataclass(slots=True)
class BinanceSession(live.Session):
    """Per-account Binance state.

    Attributes:
        orders: Per-account order-count limiter.
        order_ids: Venue order id to client id (submits, streams, queries).
        symbols: Client id to symbol for orders seen by this process.
        traded: Symbols traded by this process (fills scan).
        configured: Leverage and margin mode applied per symbol.
        listen_key: Current user-data listen key.
    """

    orders: ratelimit.WindowLimiter | None = None
    order_ids: dict[str, str] = dataclasses.field(default_factory=dict)
    symbols: dict[str, str] = dataclasses.field(default_factory=dict)
    traded: set[str] = dataclasses.field(default_factory=set)
    configured: dict[str, tuple[int, str]] = dataclasses.field(
        default_factory=dict
    )
    listen_key: str | None = None


def _state(
    row: Mapping[str, Any], client_order_id: str
) -> trading.VenueOrderState:
    """Converts an order object to a venue state.

    Args:
        row: ``GET /fapi/v1/order`` style object.
        client_order_id: Client id to report.

    Returns:
        State.
    """
    status = _STATUS.get(str(row.get("status")), S.ACKNOWLEDGED)
    return trading.VenueOrderState(
        client_order_id=client_order_id,
        exchange_order_id=str(row["orderId"]) if "orderId" in row else None,
        status=status,
        filled_base=wire.dec(row.get("executedQty")),
        reason=None if status is not S.REJECTED else "rejected by binance",
        ts_ms=int(row.get("updateTime") or row.get("time") or 0),
    )


def _algo_state(
    row: Mapping[str, Any], actual: Mapping[str, Any] | None
) -> trading.VenueOrderState:
    """Converts an algo (conditional) order to a venue state.

    Args:
        row: Algo order object.
        actual: Triggered matching-engine order, if any.

    Returns:
        State.
    """
    cid = str(row.get("clientAlgoId"))
    status = str(row.get("algoStatus"))
    ts = int(row.get("updateTime") or row.get("createTime") or 0)
    if actual is not None:
        state = _state(actual, cid)
        return dataclasses.replace(
            state, exchange_order_id=f"algo:{row.get('algoId')}"
        )
    mapped = {
        "NEW": S.ACKNOWLEDGED,
        "TRIGGERING": S.ACKNOWLEDGED,
        "TRIGGERED": S.ACKNOWLEDGED,
        "CANCELED": S.CANCELED,
        "REJECTED": S.REJECTED,
        "EXPIRED": S.EXPIRED,
        "FINISHED": S.CANCELED,
    }.get(status, S.ACKNOWLEDGED)
    return trading.VenueOrderState(
        client_order_id=cid,
        exchange_order_id=f"algo:{row.get('algoId')}",
        status=mapped,
        filled_base=decimals.ZERO,
        reason=str(row.get("rm") or "") or None,
        ts_ms=ts,
    )


class BinanceFuturesAdapter(live.LiveVenueAdapter[BinanceSession]):
    """Binance USDⓈ-M futures over REST and the private user stream."""

    REQUIRED_FIELDS = ("api_key", "api_secret")
    SANDBOX_NAME = "the Binance USDⓈ-M futures testnet"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the adapter (see ``LiveVenueAdapter``).

        Args:
            *args: Base arguments.
            **kwargs: Base keyword arguments (``urls`` accepts
                ``rest_url``, ``testnet_rest_url``, ``ws_url`` and
                ``testnet_ws_url``).
        """
        super().__init__(*args, **kwargs)
        self._urls = {
            "rest": self._url("rest_url", REST_URL),
            "testnet_rest": self._url("testnet_rest_url", TESTNET_REST_URL),
            "ws": self._url("ws_url", WS_URL),
            "testnet_ws": self._url("testnet_ws_url", TESTNET_WS_URL),
        }
        self._http: dict[bool, http.VenueHttp] = {}
        self._sapi: http.VenueHttp | None = None
        self._offset: dict[bool, int] = {}

    # -- plumbing -------------------------------------------------------

    def _client(self, sandbox: bool) -> http.VenueHttp:
        """Returns the REST client of an environment.

        Args:
            sandbox: Testnet.

        Returns:
            Client.
        """
        client = self._http.get(sandbox)
        if client is None:
            limiter = ratelimit.WindowLimiter(
                VENUE,
                budgets(),
                max_wait_s=self._options.max_rate_wait_s,
                monotonic=self._monotonic,
                sleep=self._sleep,
            )
            client = http.VenueHttp(
                VENUE,
                self._urls["testnet_rest" if sandbox else "rest"],
                limiter,
                timeout_s=self._options.timeout_s,
                transport=self._transport,
            )
            self._http[sandbox] = client
        return client

    def _sapi_client(self) -> http.VenueHttp:
        """Returns the spot-domain client used for key permissions.

        Returns:
            Client.
        """
        if self._sapi is None:
            self._sapi = http.VenueHttp(
                VENUE,
                SAPI_URL,
                ratelimit.WindowLimiter(
                    VENUE,
                    [ratelimit.Budget("weight", 600, 60.0)],
                    max_wait_s=self._options.max_rate_wait_s,
                    monotonic=self._monotonic,
                    sleep=self._sleep,
                ),
                timeout_s=self._options.timeout_s,
                transport=self._transport,
            )
        return self._sapi

    async def aclose(self) -> None:
        """Closes connection pools."""
        for client in self._http.values():
            await client.aclose()
        if self._sapi is not None:
            await self._sapi.aclose()

    def _new_session(
        self,
        account_id: str,
        credentials: creds_lib.VenueCredentials,
        sandbox: bool,
    ) -> BinanceSession:
        """Builds per-account state.

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Testnet.

        Returns:
            Session.
        """
        return BinanceSession(
            account_id=account_id,
            credentials=credentials,
            sandbox=sandbox,
            orders=ratelimit.WindowLimiter(
                VENUE,
                order_budgets(),
                max_wait_s=self._options.max_rate_wait_s,
                monotonic=self._monotonic,
                sleep=self._sleep,
            ),
        )

    async def _sync_time(self, sandbox: bool) -> int:
        """Measures the server clock offset (``GET /fapi/v1/time``).

        Args:
            sandbox: Testnet.

        Returns:
            Offset in milliseconds (server minus local).

        Raises:
            ExecutionError: When the venue does not answer.
        """
        before = self._now_ms()
        reply = await self._client(sandbox).request("GET", "/fapi/v1/time")
        after = self._now_ms()
        if reply.status != 200 or not isinstance(reply.payload, Mapping):
            raise map_error(reply.status, reply.payload)
        offset = int(reply.payload["serverTime"]) - (before + after) // 2
        self._offset[sandbox] = offset
        return offset

    async def _signed(
        self,
        session: BinanceSession,
        method: str,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        *,
        weight: int = 1,
        order: bool = False,
        client: http.VenueHttp | None = None,
    ) -> Any:
        """Sends a SIGNED request, resyncing the clock once on ``-1021``.

        Args:
            session: Session.
            method: HTTP method.
            path: Path.
            params: Business parameters in order.
            weight: Request weight.
            order: Counts against the order budgets.
            client: Alternative client (spot domain).

        Returns:
            Parsed payload.

        Raises:
            ExecutionError: Mapped venue errors.
        """
        target = client or self._client(session.sandbox)
        if session.sandbox not in self._offset and client is None:
            await self._sync_time(session.sandbox)
        key = session.credentials.get("api_key")
        secret = session.credentials.get("api_secret")
        for attempt in range(2):
            offset = self._offset.get(session.sandbox, 0)
            query = signing.encode_query(
                [
                    *params,
                    ("recvWindow", str(self._options.recv_window_ms)),
                    ("timestamp", str(self._now_ms() + offset)),
                ]
            )
            query = (
                f"{query}&signature={signing.hmac_sha256_hex(secret, query)}"
            )
            if order and session.orders is not None:
                await session.orders.acquire({"orders_10s": 1, "orders_1m": 1})
            reply = await target.request(
                method,
                path,
                query=query,
                headers={"X-MBX-APIKEY": key},
                costs={"weight": 0 if order else weight},
            )
            used = reply.headers.get("x-mbx-used-weight-1m")
            if used is not None and used.isdigit():
                target.limiter.observe("weight", int(used))
            payload = reply.payload
            failed = reply.status >= 400 or (
                isinstance(payload, Mapping)
                and isinstance(payload.get("code"), int)
                and payload["code"] < 0
            )
            if not failed:
                return payload
            if (
                attempt == 0
                and client is None
                and isinstance(payload, Mapping)
                and payload.get("code") == -1021
            ):
                await self._sync_time(session.sandbox)
                continue
            raise map_error(reply.status, payload)
        raise errors.AdapterUnavailableError("binance clock resync failed")

    # -- negotiation ----------------------------------------------------

    async def _probe_health(self, sandbox: bool) -> None:
        """Checks ``GET /fapi/v1/time`` and records the clock offset.

        Args:
            sandbox: Testnet.
        """
        offset = await self._sync_time(sandbox)
        if abs(offset) > 60_000:
            _LOG.warning("binance clock offset is %d ms", offset)

    async def _probe_permissions(
        self, session: BinanceSession
    ) -> tuple[str, ...]:
        """Checks the account configuration and the key's permissions.

        ``GET /fapi/v1/accountConfig`` (weight 5) proves the key can sign
        futures requests and reports ``canTrade`` and ``dualSidePosition``.
        On mainnet ``GET /sapi/v1/account/apiRestrictions`` must show
        ``enableFutures`` and no withdrawal permission (not available on
        the futures testnet).

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            VenuePermissionError: Missing trading permission.
            ExecutionError: ``UNSUPPORTED`` for hedge mode.
        """
        config = await self._signed(
            session, "GET", "/fapi/v1/accountConfig", weight=5
        )
        if not config.get("canTrade", False):
            raise errors.VenuePermissionError(
                "permission missing: this futures account cannot trade "
                "(canTrade=false)",
                venue=VENUE,
            )
        if config.get("dualSidePosition", False):
            raise errors.ExecutionError(
                "UNSUPPORTED",
                "the account uses Hedge position mode; switch Binance "
                "USDⓈ-M to One-way mode to trade with Joseph",
            )
        notes = ["Position mode: One-way (verified)."]
        if config.get("multiAssetsMargin"):
            notes.append("Multi-assets margin is on: balances are in USD.")
        if not session.sandbox:
            rules = await self._signed(
                session,
                "GET",
                "/sapi/v1/account/apiRestrictions",
                client=self._sapi_client(),
            )
            if not rules.get("enableFutures", False):
                raise errors.VenuePermissionError(
                    "permission missing: enable Futures on this API key",
                    venue=VENUE,
                )
            if rules.get("enableWithdrawals", False):
                raise errors.VenuePermissionError(
                    "the API key can withdraw funds; create a key without "
                    "withdrawal permission",
                    venue=VENUE,
                )
            notes.append("API key: futures enabled, withdrawals disabled.")
        notes.append(
            "Stop orders use the Algo Order API (conditional orders "
            "cannot be modified until triggered)."
        )
        return tuple(notes)

    # -- reads ----------------------------------------------------------

    async def _snapshot(
        self, session: BinanceSession
    ) -> trading.VenueAccountSnapshot:
        """Reads ``GET /fapi/v3/account`` (weight 5).

        Args:
            session: Session.

        Returns:
            Snapshot (margin balance and available balance).
        """
        payload = await self._signed(
            session, "GET", "/fapi/v3/account", weight=5
        )
        return trading.VenueAccountSnapshot(
            equity=wire.dec(payload.get("totalMarginBalance")),
            available_margin=wire.dec(payload.get("availableBalance")),
            currency="USDT",
            ts_ms=self._now_ms(),
        )

    async def _positions(
        self, session: BinanceSession
    ) -> list[trading.VenuePosition]:
        """Reads ``GET /fapi/v3/positionRisk`` (weight 5).

        Args:
            session: Session.

        Returns:
            Non-zero positions (one-way: ``positionAmt`` is signed).
        """
        rows = await self._signed(
            session, "GET", "/fapi/v3/positionRisk", weight=5
        )
        result = []
        for row in rows:
            qty = wire.dec(row.get("positionAmt"))
            if qty == 0:
                continue
            symbol = str(row["symbol"])
            session.traded.add(symbol)
            result.append(
                trading.VenuePosition(
                    instrument_id=self._instrument(symbol),
                    signed_qty=qty,
                    entry_price=wire.opt_dec(row.get("entryPrice")),
                )
            )
        return result

    def _remember(
        self, session: BinanceSession, symbol: str, cid: str, oid: object
    ) -> None:
        """Records an order's symbol and venue id.

        Args:
            session: Session.
            symbol: Symbol.
            cid: Client id.
            oid: Venue order id.
        """
        session.symbols[cid] = symbol
        session.traded.add(symbol)
        if oid not in (None, ""):
            session.order_ids[str(oid)] = cid

    async def _open_orders(
        self, session: BinanceSession
    ) -> list[trading.VenueOrderState]:
        """Reads open orders and open conditional orders (weight 40 each).

        Args:
            session: Session.

        Returns:
            Orders.
        """
        rows = await self._signed(
            session, "GET", "/fapi/v1/openOrders", weight=40
        )
        result = []
        for row in rows:
            cid = str(row.get("clientOrderId"))
            self._remember(session, str(row["symbol"]), cid, row.get("orderId"))
            result.append(_state(row, cid))
        algos = await self._signed(
            session, "GET", "/fapi/v1/openAlgoOrders", weight=40
        )
        for row in algos:
            cid = str(row.get("clientAlgoId"))
            self._remember(session, str(row["symbol"]), cid, None)
            result.append(_algo_state(row, None))
        return result

    async def _map_symbol_orders(
        self, session: BinanceSession, symbol: str, since_ms: int
    ) -> None:
        """Learns venue order ids of a symbol (``allOrders``/``allAlgoOrders``).

        Args:
            session: Session.
            symbol: Symbol.
            since_ms: Lower bound.
        """
        start = str(since_ms)
        rows = await self._signed(
            session,
            "GET",
            "/fapi/v1/allOrders",
            [("symbol", symbol), ("startTime", start), ("limit", "1000")],
            weight=5,
        )
        for row in rows:
            self._remember(
                session, symbol, str(row.get("clientOrderId")), row["orderId"]
            )
        algos = await self._signed(
            session,
            "GET",
            "/fapi/v1/allAlgoOrders",
            [("symbol", symbol), ("startTime", start), ("limit", "1000")],
            weight=5,
        )
        for row in algos:
            actual = row.get("actualOrderId")
            self._remember(
                session, symbol, str(row.get("clientAlgoId")), actual or None
            )

    async def _fills(
        self, session: BinanceSession, since_ms: int
    ) -> list[trading.VenueFill]:
        """Reads ``GET /fapi/v1/userTrades`` per traded symbol (weight 5).

        ``userTrades`` needs a symbol and has no client order id, so symbols
        come from this process and the OMS directory, and unknown order ids
        are learned once per pass through ``allOrders``.

        Args:
            session: Session.
            since_ms: Lower bound (clamped to the 7-day window).

        Returns:
            Fills of known client orders, oldest first.
        """
        start = max(since_ms, self._now_ms() - _SEVEN_DAYS_MS + 60_000)
        symbols = set(session.traded)
        for known in self._directory.recent(session.account_id, start):
            if known.instrument_id.startswith(f"{VENUE}:perp:"):
                symbols.add(self._symbol(known.instrument_id))
        fills: list[trading.VenueFill] = []
        for symbol in sorted(symbols):
            rows = await self._signed(
                session,
                "GET",
                "/fapi/v1/userTrades",
                [
                    ("symbol", symbol),
                    ("startTime", str(start)),
                    ("limit", "1000"),
                ],
                weight=5,
            )
            if any(
                str(r.get("orderId")) not in session.order_ids for r in rows
            ):
                await self._map_symbol_orders(session, symbol, start)
            for row in rows:
                cid = session.order_ids.get(str(row.get("orderId")))
                if cid is None:
                    continue
                fills.append(
                    trading.VenueFill(
                        exchange_fill_id=f"{symbol}:{row['id']}",
                        client_order_id=cid,
                        price=wire.dec(row.get("price")),
                        quantity_base=wire.dec(row.get("qty")),
                        fee=wire.dec(row.get("commission")),
                        fee_asset=str(row.get("commissionAsset", "USDT")),
                        liquidity="maker" if row.get("maker") else "taker",
                        ts_ms=int(row.get("time", 0)),
                    )
                )
        fills.sort(key=lambda f: f.ts_ms)
        return fills

    def _symbol_for(self, session: BinanceSession, cid: str) -> str:
        """Finds the symbol of a client order id.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            Symbol.

        Raises:
            AdapterUnavailableError: When neither this process nor the OMS
                knows the order (cannot query safely).
        """
        symbol = session.symbols.get(cid)
        if symbol is not None:
            return symbol
        known = self._directory.find(session.account_id, cid)
        if known is None:
            raise errors.AdapterUnavailableError(
                f"binance needs the symbol of {cid}; the OMS has no record"
            )
        symbol = self._symbol(known.instrument_id)
        session.symbols[cid] = symbol
        return symbol

    def _is_algo(self, session: BinanceSession, cid: str) -> bool:
        """Tells whether a client id names a conditional order.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            True for stop orders.
        """
        known = self._directory.find(session.account_id, cid)
        if known is not None:
            return known.order_type in (
                enums.OrderType.STOP_MARKET,
                enums.OrderType.STOP_LIMIT,
            )
        return cid.endswith("-sl")

    async def _query(
        self, session: BinanceSession, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries ``GET /fapi/v1/order`` or ``GET /fapi/v1/algoOrder``.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State or ``None`` when Binance has no such order.
        """
        symbol = self._symbol_for(session, client_order_id)
        if self._is_algo(session, client_order_id):
            try:
                algo = await self._signed(
                    session,
                    "GET",
                    "/fapi/v1/algoOrder",
                    [("clientAlgoId", client_order_id)],
                )
            except errors.OrderRejectedError as err:
                if err.code == "NOT_FOUND":
                    return None
                raise
            return await self._algo_with_actual(session, symbol, algo)
        try:
            row = await self._signed(
                session,
                "GET",
                "/fapi/v1/order",
                [("symbol", symbol), ("origClientOrderId", client_order_id)],
            )
        except errors.OrderRejectedError as err:
            if err.code == "NOT_FOUND":
                return None
            raise
        self._remember(session, symbol, client_order_id, row.get("orderId"))
        return _state(row, client_order_id)

    async def _algo_with_actual(
        self, session: BinanceSession, symbol: str, algo: Mapping[str, Any]
    ) -> trading.VenueOrderState:
        """Resolves a triggered conditional order through its real order.

        Args:
            session: Session.
            symbol: Symbol.
            algo: Algo order object.

        Returns:
            State.
        """
        cid = str(algo.get("clientAlgoId"))
        actual_id = algo.get("actualOrderId")
        if not actual_id:
            return _algo_state(algo, None)
        self._remember(session, symbol, cid, actual_id)
        actual = await self._signed(
            session,
            "GET",
            "/fapi/v1/order",
            [("symbol", symbol), ("orderId", str(actual_id))],
        )
        return _algo_state(algo, actual)

    # -- trading --------------------------------------------------------

    async def _configure(
        self,
        session: BinanceSession,
        symbol: str,
        request: trading.VenueOrderRequest,
    ) -> None:
        """Applies margin type and leverage before an opening order.

        ``POST /fapi/v1/marginType`` (``-4046`` means already set) and
        ``POST /fapi/v1/leverage`` (integer, weight 1 each).

        Args:
            session: Session.
            symbol: Symbol.
            request: Order.

        Raises:
            OrderRejectedError: Fractional leverage or a refused change.
        """
        if request.reduce_only:
            return
        leverage = request.leverage
        if leverage != leverage.to_integral_value() or leverage < 1:
            raise errors.OrderRejectedError(
                "binance leverage must be a whole number", venue=VENUE
            )
        mode = (
            "ISOLATED"
            if request.margin_mode is enums.MarginMode.ISOLATED
            else "CROSSED"
        )
        wanted = (int(leverage), mode)
        if session.configured.get(symbol) == wanted:
            return
        try:
            await self._signed(
                session,
                "POST",
                "/fapi/v1/marginType",
                [("symbol", symbol), ("marginType", mode)],
            )
        except errors.OrderRejectedError as err:
            if err.venue_code not in {str(c) for c in _IGNORABLE}:
                raise
        await self._signed(
            session,
            "POST",
            "/fapi/v1/leverage",
            [("symbol", symbol), ("leverage", str(int(leverage)))],
        )
        session.configured[symbol] = wanted

    async def _submit(
        self, session: BinanceSession, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places ``POST /fapi/v1/order`` or ``POST /fapi/v1/algoOrder``.

        Args:
            session: Session.
            request: Order.

        Returns:
            Response state (fills are pulled by the OMS when filled).

        Raises:
            AdapterUnavailableError: Unsupported combination (not sent).
        """
        symbol = self._symbol(request.instrument_id)
        kind = request.order_type
        tif = _TIF[request.time_in_force]
        if kind is enums.OrderType.MARKET and tif == "GTX":
            raise errors.AdapterUnavailableError(
                "post-only market orders are not possible",
                code="UNSUPPORTED_ORDER_TYPE",
            )
        await self._configure(session, symbol, request)
        cid = request.client_order_id
        side = "BUY" if request.side is enums.OrderSide.BUY else "SELL"
        params: list[tuple[str, str]] = [("symbol", symbol), ("side", side)]
        conditional = kind in (
            enums.OrderType.STOP_MARKET,
            enums.OrderType.STOP_LIMIT,
        )
        if conditional:
            assert request.trigger_price is not None
            params = [
                ("algoType", "CONDITIONAL"),
                *params,
                (
                    "type",
                    "STOP_MARKET"
                    if kind is enums.OrderType.STOP_MARKET
                    else "STOP",
                ),
                ("quantity", wire.text(request.quantity_base)),
                ("triggerPrice", wire.text(request.trigger_price)),
                (
                    "workingType",
                    "CONTRACT_PRICE"
                    if request.trigger_basis is enums.PriceBasis.LAST
                    else "MARK_PRICE",
                ),
                ("clientAlgoId", cid),
            ]
            if kind is enums.OrderType.STOP_LIMIT:
                assert request.limit_price is not None
                params += [
                    ("price", wire.text(request.limit_price)),
                    ("timeInForce", tif),
                ]
            if request.reduce_only:
                params.append(("reduceOnly", "true"))
            path = "/fapi/v1/algoOrder"
        else:
            params += [
                (
                    "type",
                    "MARKET" if kind is enums.OrderType.MARKET else "LIMIT",
                ),
                ("quantity", wire.text(request.quantity_base)),
                ("newClientOrderId", cid),
                ("newOrderRespType", "RESULT"),
            ]
            if kind is enums.OrderType.LIMIT:
                assert request.limit_price is not None
                params += [
                    ("price", wire.text(request.limit_price)),
                    ("timeInForce", tif),
                ]
            if request.reduce_only:
                params.append(("reduceOnly", "true"))
            path = "/fapi/v1/order"
        session.symbols[cid] = symbol
        session.traded.add(symbol)
        try:
            row = await self._signed(session, "POST", path, params, order=True)
        except errors.OrderRejectedError as err:
            if err.code != "CONFLICT":
                raise
            existing = await self._query(session, cid)
            if existing is None:
                raise
            return trading.VenueSubmitResult(True, existing)
        if conditional:
            state = _algo_state(row, None)
        else:
            self._remember(session, symbol, cid, row.get("orderId"))
            state = _state(row, cid)
        return trading.VenueSubmitResult(state.status is not S.REJECTED, state)

    async def _cancel(
        self, session: BinanceSession, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels ``DELETE /fapi/v1/order`` or ``DELETE /fapi/v1/algoOrder``.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State.
        """
        symbol = self._symbol_for(session, client_order_id)
        if self._is_algo(session, client_order_id):
            await self._signed(
                session,
                "DELETE",
                "/fapi/v1/algoOrder",
                [("clientAlgoId", client_order_id)],
            )
            state = await self._query(session, client_order_id)
            if state is None:
                raise errors.OrderRejectedError(
                    "conditional order vanished", venue=VENUE, code="NOT_FOUND"
                )
            return state
        row = await self._signed(
            session,
            "DELETE",
            "/fapi/v1/order",
            [("symbol", symbol), ("origClientOrderId", client_order_id)],
        )
        return _state(row, client_order_id)

    async def _amend(
        self,
        session: BinanceSession,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Modifies a LIMIT order (``PUT /fapi/v1/order``).

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New price.
            trigger_price: Not supported (conditional orders).
            quantity_base: New quantity.

        Returns:
            State.

        Raises:
            OrderRejectedError: For conditional orders or trigger changes.
        """
        if trigger_price is not None or self._is_algo(session, client_order_id):
            raise errors.OrderRejectedError(
                "binance cannot modify conditional orders",
                venue=VENUE,
                code="UNSUPPORTED",
            )
        symbol = self._symbol_for(session, client_order_id)
        current = await self._signed(
            session,
            "GET",
            "/fapi/v1/order",
            [("symbol", symbol), ("origClientOrderId", client_order_id)],
        )
        row = await self._signed(
            session,
            "PUT",
            "/fapi/v1/order",
            [
                ("symbol", symbol),
                ("origClientOrderId", client_order_id),
                ("side", str(current["side"])),
                (
                    "quantity",
                    wire.text(quantity_base)
                    if quantity_base is not None
                    else str(current["origQty"]),
                ),
                (
                    "price",
                    wire.text(limit_price)
                    if limit_price is not None
                    else str(current["price"]),
                ),
            ],
            order=True,
        )
        return _state(row, client_order_id)

    # -- private stream -------------------------------------------------

    async def _listen_key(self, session: BinanceSession, method: str) -> str:
        """Creates or extends the listen key (USER_STREAM, weight 1).

        Args:
            session: Session.
            method: ``POST`` or ``PUT``.

        Returns:
            Listen key.
        """
        reply = await self._client(session.sandbox).request(
            method,
            "/fapi/v1/listenKey",
            headers={"X-MBX-APIKEY": session.credentials.get("api_key")},
        )
        if reply.status >= 400 or not isinstance(reply.payload, Mapping):
            raise map_error(reply.status, reply.payload)
        key = str(reply.payload["listenKey"])
        session.listen_key = key
        return key

    def parse(
        self, session: BinanceSession, frame: str | bytes
    ) -> list[ws.StreamUpdate]:
        """Parses one user-data frame.

        Args:
            session: Session.
            frame: Raw frame.

        Returns:
            Updates.
        """
        message = http.parse_json(
            frame if isinstance(frame, str) else frame.decode()
        )
        if not isinstance(message, Mapping):
            return []
        event = message.get("e")
        if event == "listenKeyExpired":
            return [ws.StreamUpdate(reconnect=True)]
        if event == "ORDER_TRADE_UPDATE":
            return self._order_update(session, message["o"])
        if event == "ALGO_UPDATE":
            return self._algo_update(session, message["o"])
        if event == "ACCOUNT_UPDATE":
            return self._account_update(message["a"], int(message.get("E", 0)))
        return []

    def _order_update(
        self, session: BinanceSession, o: Mapping[str, Any]
    ) -> list[ws.StreamUpdate]:
        """Converts ``ORDER_TRADE_UPDATE.o``.

        Args:
            session: Session.
            o: Order payload.

        Returns:
            Order (and fill on ``x=TRADE``) updates.
        """
        oid = str(o.get("i"))
        cid = session.order_ids.get(oid) or str(o.get("c"))
        self._remember(session, str(o.get("s")), cid, oid)
        status = _STATUS.get(str(o.get("X")), S.ACKNOWLEDGED)
        ts = int(o.get("T", 0))
        updates = []
        if o.get("x") == "TRADE" and wire.dec(o.get("l")) > 0:
            updates.append(
                ws.StreamUpdate(
                    fill=trading.VenueFill(
                        exchange_fill_id=f"{o.get('s')}:{o.get('t')}",
                        client_order_id=cid,
                        price=wire.dec(o.get("L")),
                        quantity_base=wire.dec(o.get("l")),
                        fee=wire.dec(o.get("n")),
                        fee_asset=str(o.get("N") or "USDT"),
                        liquidity="maker" if o.get("m") else "taker",
                        ts_ms=ts,
                    )
                )
            )
        updates.append(
            ws.StreamUpdate(
                order=trading.VenueOrderState(
                    client_order_id=cid,
                    exchange_order_id=oid,
                    status=status,
                    filled_base=wire.dec(o.get("z")),
                    reason=None
                    if status is not S.EXPIRED
                    else f"expired (reason {o.get('er', '?')})",
                    ts_ms=ts,
                )
            )
        )
        return updates

    def _algo_update(
        self, session: BinanceSession, o: Mapping[str, Any]
    ) -> list[ws.StreamUpdate]:
        """Converts ``ALGO_UPDATE.o`` (conditional orders).

        Args:
            session: Session.
            o: Algo payload.

        Returns:
            Order updates (``TRIGGERED`` only maps the real order id).
        """
        cid = str(o.get("caid"))
        actual = o.get("ai")
        self._remember(session, str(o.get("s")), cid, actual or None)
        status = str(o.get("X"))
        if status in ("TRIGGERING", "TRIGGERED"):
            return []
        mapped = {
            "NEW": S.ACKNOWLEDGED,
            "CANCELED": S.CANCELED,
            "REJECTED": S.REJECTED,
            "EXPIRED": S.EXPIRED,
        }.get(status)
        filled = wire.dec(o.get("aq"))
        if status == "FINISHED":
            mapped = S.FILLED if filled >= wire.dec(o.get("q")) else S.CANCELED
        if mapped is None:
            return []
        return [
            ws.StreamUpdate(
                order=trading.VenueOrderState(
                    client_order_id=cid,
                    exchange_order_id=f"algo:{o.get('aid')}",
                    status=mapped,
                    filled_base=filled,
                    reason=str(o.get("rm") or "") or None,
                    ts_ms=int(o.get("tt") or 0),
                )
            )
        ]

    def _account_update(
        self, a: Mapping[str, Any], ts_ms: int
    ) -> list[ws.StreamUpdate]:
        """Converts ``ACCOUNT_UPDATE.a`` (positions and wallet balance).

        Args:
            a: Account payload.
            ts_ms: Event time.

        Returns:
            Position and balance updates. The balance carries the wallet
            balance (``wb``) and cross wallet balance (``cw``); exact
            available margin comes from the REST snapshot.
        """
        updates = [
            ws.StreamUpdate(
                position=trading.VenuePosition(
                    instrument_id=self._instrument(str(p["s"])),
                    signed_qty=wire.dec(p.get("pa")),
                    entry_price=wire.opt_dec(p.get("ep")),
                )
            )
            for p in a.get("P", [])
            if p.get("ps", "BOTH") == "BOTH"
        ]
        for balance in a.get("B", []):
            if balance.get("a") == "USDT":
                updates.append(
                    ws.StreamUpdate(
                        balance=trading.VenueAccountSnapshot(
                            equity=wire.dec(balance.get("wb")),
                            available_margin=wire.dec(balance.get("cw")),
                            currency="USDT",
                            ts_ms=ts_ms,
                        )
                    )
                )
        return updates

    def _stream_spec(self, session: BinanceSession) -> ws.PrivateStreamSpec:
        """Describes the user-data stream of a session.

        Args:
            session: Session.

        Returns:
            Spec (listen key per connection, PUT keepalive every 30 min,
            proactive reconnect before the 24 h limit).
        """
        base = self._urls["testnet_ws" if session.sandbox else "ws"]

        async def url() -> str:
            """Returns a fresh stream URL."""
            return f"{base}{await self._listen_key(session, 'POST')}"

        async def on_open(connection: ws.WsConnection) -> None:
            """Nothing to send: the listen key selects the account.

            Args:
                connection: Connection.
            """
            del connection

        async def keepalive() -> None:
            """Extends the listen key."""
            await self._listen_key(session, "PUT")

        async def resync() -> Sequence[ws.StreamUpdate]:
            """Replays the REST snapshot."""
            return await self._resync(session)

        return ws.PrivateStreamSpec(
            name="binance user stream",
            url=url,
            on_open=on_open,
            parse=lambda frame: self.parse(session, frame),
            resync=resync,
            idle_timeout_s=600.0,
            keepalive=keepalive,
            keepalive_interval_s=1800.0,
            max_session_s=23 * 3600.0,
        )
