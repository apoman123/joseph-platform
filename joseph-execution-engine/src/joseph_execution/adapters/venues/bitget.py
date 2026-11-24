# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bitget USDT-M futures trading adapter (Unified Trading Account, API v3).

Sources (Bitget docs, read 2026-11-23):

* UTA introduction (UTA recommended; Classic in maintenance mode):
  https://www.bitget.com/docs/uta/Introduction
* Quick start, signing, limits: https://www.bitget.com/docs/uta/quick-start
* Demo trading: https://www.bitget.com/docs/uta/demo-trading/rest-api and
  https://www.bitget.com/docs/uta/demo-trading/websocket
* Account settings, assets, set-leverage:
  https://www.bitget.com/docs/catalog/account/account-settings and
  https://www.bitget.com/docs/catalog/account/assets-balance
* Orders, positions, trigger orders:
  https://www.bitget.com/docs/catalog/trading/order-management,
  https://www.bitget.com/docs/catalog/trading/position-management,
  https://www.bitget.com/docs/catalog/trading/strategy-trading
* Private WebSocket: https://www.bitget.com/docs/uta/websocket/private/Order-Channel
  (and Positions/Account/Fill channels)
* Errors: https://www.bitget.com/docs/uta/error-code/restapi
* Broker notice (UTA keys cannot use Classic endpoints):
  https://www.bitget.com/support/articles/12560603886018

Protocol facts this module relies on:

* REST ``https://api.bitget.com``; demo trading uses the same host with the
  header ``paptrading: 1`` and a separate demo API key; private WebSocket
  ``wss://ws.bitget.com/v3/ws/private`` (demo ``wss://wspap.bitget.com``).
* ``ACCESS-SIGN`` = Base64(HMAC-SHA256(secret, timestamp + METHOD +
  requestPath [+ "?" + query] + body)) with ``ACCESS-KEY``,
  ``ACCESS-TIMESTAMP`` (ms, within 30 s of server time) and
  ``ACCESS-PASSPHRASE``. Query parameters are not URL-encoded before
  signing. WebSocket login signs ``timestamp(seconds) + "GET" +
  "/user/verify"``.
* Limits: place/cancel/modify/strategy orders 10 req/s per UID; queries
  (settings, assets, positions, orders, fills) 20 req/s per UID; server
  time 20 req/s per IP; overall 6000-8000 per IP per minute.
* ``GET /api/v3/account/settings`` reports ``accountMode`` and ``holdMode``
  (``one_way_mode``/``hedge_mode``); a Classic account gets ``25245``.
* ``clientOid`` holds at most 32 of ``A-Z a-z 0-9 . : / _ -``; ``modify-order``
  keeps it (no new client id, unlike Classic v2).
* Trigger (stop) orders: ``place-strategy-order`` with ``type=trigger``,
  ``triggerBy`` ``market``/``mark``, ``triggerOrderType`` ``market``/
  ``limit``. Status ``pending``/``submitting``/``success``/``failed``/
  ``cancelled``.

Joseph trades UTA accounts in one-way mode only. Classic accounts report
``TRADING_UNAVAILABLE`` (upgrade to UTA).
"""

from __future__ import annotations

import dataclasses
import decimal
import json
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
_State = enums.OrderState
VENUE = "bitget"
CATEGORY = "USDT-FUTURES"
REST_URL = "https://api.bitget.com"
WS_URL = "wss://ws.bitget.com/v3/ws/private"
DEMO_WS_URL = "wss://wspap.bitget.com/v3/ws/private"
# ``code`` of every successful Bitget answer.
_OK = "00000"
_STATUS = {
    "live": _State.ACKNOWLEDGED,
    "new": _State.ACKNOWLEDGED,
    "partially_filled": _State.PARTIALLY_FILLED,
    "filled": _State.FILLED,
    "cancelled": _State.CANCELED,
    "canceled": _State.CANCELED,
}
_TRIGGER_STATUS = {
    "pending": _State.ACKNOWLEDGED,
    "submitting": _State.ACKNOWLEDGED,
    "success": _State.ACKNOWLEDGED,
    "failed": _State.REJECTED,
    "cancelled": _State.CANCELED,
}
_TIF = {
    enums.TimeInForce.GTC: "gtc",
    enums.TimeInForce.IOC: "ioc",
    enums.TimeInForce.FOK: "fok",
    enums.TimeInForce.POST_ONLY: "post_only",
}
# Bitget error codes (``code`` in error bodies) grouped by meaning.
_MARGIN = {"25202", "25203", "12001", "43012", "40754", "40798", "40800"}
_PERMISSION = {
    "40006",
    "40009",
    "40012",
    "40014",
    "40018",
    "40036",
    "40037",
    "40038",
    "25245",
    "22030",
    "40110",
}
_RATE = {"429", "25004", "40500", "30006", "30007"}
_UNKNOWN = {"40010", "40725", "45001"}
_NOT_FOUND = {"25204", "43001", "22001", "43025"}
_DUPLICATE = {"25212", "45034", "43118"}


def budgets() -> list[ratelimit.Budget]:
    """Returns the documented per-UID budgets.

    Returns:
        ``trade`` (10/s) and ``query`` (20/s) budgets, plus ``ip`` (6000
        per minute, the low end of the documented per-IP limit).
    """
    return [
        ratelimit.Budget("trade", 10, 1.0),
        ratelimit.Budget("query", 20, 1.0),
        ratelimit.Budget("ip", 6000, 60.0),
    ]


def map_error(  # noqa: PLR0911 - one return per error family.
    status: int, payload: Any
) -> errors.ExecutionError:
    """Maps a Bitget error answer to a domain error.

    Args:
        status: HTTP status.
        payload: ``{"code": "...", "msg": "..."}`` or text.

    Returns:
        Domain error (never containing credentials). Codes in ``_UNKNOWN``
        and HTTP 5xx mean the outcome is unknown (``AdapterTimeoutError``);
        ``40008`` (timestamp out of range) means the request was not
        executed.
    """
    code = str(status)
    message = str(payload)[:200]
    if isinstance(payload, Mapping):
        code = str(payload.get("code", status))
        message = str(payload.get("msg", ""))[:200]
    text = f"{message} ({code})"
    if code in _UNKNOWN or status >= 500:
        return errors.AdapterTimeoutError(
            f"bitget could not confirm the request ({code}); outcome unknown"
        )
    if code in _MARGIN:
        return errors.InsufficientMarginError(
            text, venue=VENUE, venue_code=code
        )
    if code == "25245":
        return errors.VenuePermissionError(
            "permission missing: this is a Classic account; Joseph trades "
            "Bitget Unified Trading Accounts (upgrade the account to UTA)",
            venue=VENUE,
            venue_code=code,
        )
    if code in _PERMISSION or status in (401, 403):
        return errors.VenuePermissionError(
            f"permission missing: the API key, passphrase, IP whitelist or "
            f"UTA trade permission was refused ({text})",
            venue=VENUE,
            venue_code=code,
        )
    if code in _RATE:
        return errors.RateLimitedError(f"bitget throttled: {text}")
    if code == "40008":
        return errors.AdapterUnavailableError(
            f"bitget rejected the request timestamp ({text}); not executed"
        )
    if code in _NOT_FOUND:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=code, code="NOT_FOUND"
        )
    if code in _DUPLICATE:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=code, code="CONFLICT"
        )
    return errors.OrderRejectedError(text, venue=VENUE, venue_code=code)


def sign(secret: str, timestamp: str, method: str, path: str, body: str) -> str:
    """Computes ``ACCESS-SIGN``.

    Bitget's signing rule: Base64 of the HMAC-SHA256, keyed with the API
    secret, of ``timestamp + METHOD + path + body`` concatenated without
    separators. REST requests use the millisecond timestamp and the
    request path with its unencoded query; the WebSocket login signs the
    second timestamp, ``GET`` and ``/user/verify`` with an empty body.

    Args:
        secret: API secret.
        timestamp: Milliseconds (REST) or seconds (WS login).
        method: Upper-case method.
        path: Request path including ``?query`` when present.
        body: Raw body (empty for GET).

    Returns:
        Base64 signature.
    """
    return signing.hmac_sha256_b64(secret, f"{timestamp}{method}{path}{body}")


@dataclasses.dataclass(slots=True)
class BitgetSession(live.Session):
    """Per-account Bitget state.

    Attributes:
        limiter: Per-UID limiter.
        order_ids: Venue order id to client id.
        venue_ids: Joseph client id to venue ``clientOid``.
        triggers: Client ids placed as trigger (strategy) orders.
        configured: Leverage and margin mode per symbol.
    """

    limiter: ratelimit.WindowLimiter | None = None
    order_ids: dict[str, str] = dataclasses.field(default_factory=dict)
    venue_ids: dict[str, str] = dataclasses.field(default_factory=dict)
    triggers: set[str] = dataclasses.field(default_factory=set)
    configured: dict[str, tuple[str, str]] = dataclasses.field(
        default_factory=dict
    )


class BitgetFuturesAdapter(live.LiveVenueAdapter[BitgetSession]):
    """Bitget UTA USDT-M futures over REST v3 and the v3 private stream.

    Attributes:
        REQUIRED_FIELDS: Credential fields the venue needs (API key, secret
            and passphrase).
        SANDBOX_NAME: Name of the venue's test environment, shown in
            sandbox notes.
    """

    REQUIRED_FIELDS = ("api_key", "api_secret", "passphrase")
    SANDBOX_NAME = "Bitget demo trading (paptrading)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the adapter (see ``LiveVenueAdapter``).

        Args:
            *args: Base arguments.
            **kwargs: Base keyword arguments (``urls`` accepts
                ``rest_url``, ``ws_url`` and ``demo_ws_url``).
        """
        super().__init__(*args, **kwargs)
        self._rest_url = self._url("rest_url", REST_URL)
        self._ws_urls = {
            False: self._url("ws_url", WS_URL),
            True: self._url("demo_ws_url", DEMO_WS_URL),
        }
        self._http: http.VenueHttp | None = None

    def _client(self) -> http.VenueHttp:
        """Returns the REST client (one host for live and demo).

        Its own budget covers public endpoints only; signed requests are
        limited by the session's per-UID limiter and cost nothing here.

        Returns:
            Client.
        """
        if self._http is None:
            self._http = http.VenueHttp(
                VENUE,
                self._rest_url,
                ratelimit.WindowLimiter(
                    VENUE,
                    [ratelimit.Budget("public", 20, 1.0)],
                    max_wait_s=self._options.max_rate_wait_s,
                    monotonic=self._monotonic,
                    sleep=self._sleep,
                ),
                timeout_s=self._options.timeout_s,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        """Closes the connection pool."""
        if self._http is not None:
            await self._http.aclose()

    def _new_session(
        self,
        account_id: str,
        credentials: creds_lib.VenueCredentials,
        sandbox: bool,
    ) -> BitgetSession:
        """Builds per-account state.

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Demo trading.

        Returns:
            Session.
        """
        return BitgetSession(
            account_id=account_id,
            credentials=credentials,
            sandbox=sandbox,
            limiter=ratelimit.WindowLimiter(
                VENUE,
                budgets(),
                max_wait_s=self._options.max_rate_wait_s,
                monotonic=self._monotonic,
                sleep=self._sleep,
            ),
        )

    async def _private(
        self,
        session: BitgetSession,
        method: str,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        body: Mapping[str, object] | None = None,
        *,
        trade: bool = False,
    ) -> Any:
        """Sends a signed request and unwraps ``data``.

        The query is the sorted parameters joined without URL-encoding, as
        Bitget signs it, and is sent exactly as signed; every value used
        here (symbols, ids, numbers, categories) is URL-safe. The body is
        compact JSON. ``sign`` covers the millisecond timestamp, method,
        path with query and body; the key, signature, timestamp and
        passphrase go in the ``ACCESS-*`` headers, and demo trading adds
        ``paptrading: 1``.

        Args:
            session: Session.
            method: ``GET`` or ``POST``.
            path: Path.
            params: Query parameters (sorted before signing).
            body: JSON body for POST.
            trade: Counts against the 10/s trading budget.

        Returns:
            ``data`` of a ``00000`` answer.

        Raises:
            ExecutionError: Mapped venue errors.
        """
        query = "&".join(f"{k}={v}" for k, v in sorted(params))
        raw = "" if body is None else json.dumps(body, separators=(",", ":"))
        timestamp = str(self._now_ms())
        target = f"{path}?{query}" if query else path
        creds = session.credentials
        headers = {
            "ACCESS-KEY": creds.get("api_key"),
            "ACCESS-SIGN": sign(
                creds.get("api_secret"), timestamp, method, target, raw
            ),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": creds.get("passphrase"),
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        if session.sandbox:
            headers["paptrading"] = "1"
        if session.limiter is not None:
            await session.limiter.acquire(
                {"trade": 1, "ip": 1} if trade else {"query": 1, "ip": 1}
            )
        reply = await self._client().request(
            method,
            path,
            query=query,
            body=raw if body is not None else None,
            headers=headers,
            costs={"public": 0},
        )
        payload = reply.payload
        if (
            reply.status == 200
            and isinstance(payload, Mapping)
            and str(payload.get("code")) == _OK
        ):
            return payload.get("data")
        raise map_error(reply.status, payload)

    # -- negotiation ----------------------------------------------------

    async def _probe_health(self, sandbox: bool) -> None:
        """Checks ``GET /api/v3/market/time``.

        Args:
            sandbox: Demo trading (same host).
        """
        del sandbox  # Unused.
        reply = await self._client().request(
            "GET", "/api/v3/market/time", costs={"public": 1}
        )
        payload = reply.payload
        if not (
            reply.status == 200
            and isinstance(payload, Mapping)
            and str(payload.get("code")) == _OK
        ):
            raise map_error(reply.status, payload)

    async def _probe_permissions(
        self, session: BitgetSession
    ) -> tuple[str, ...]:
        """Checks ``GET /api/v3/account/settings`` (UTA, one-way mode).

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            VenuePermissionError: Classic account or refused key.
            ExecutionError: ``UNSUPPORTED`` for hedge mode.
        """
        settings = await self._private(
            session, "GET", "/api/v3/account/settings"
        )
        mode = str(settings.get("accountMode", ""))
        if mode in ("upgrading", "switching"):
            raise errors.ExecutionError(
                "VENUE_UNAVAILABLE",
                f"the Bitget account is {mode}; retry when it finishes",
            )
        if settings.get("holdMode") != "one_way_mode":
            raise errors.ExecutionError(
                "UNSUPPORTED",
                "the Bitget account uses hedge mode; switch to one-way "
                "mode to trade with Joseph",
            )
        for row in settings.get("symbolConfigList") or []:
            if row.get("category") == CATEGORY:
                session.configured[str(row["symbol"])] = (
                    str(row.get("leverage")),
                    str(row.get("marginMode")),
                )
        return (
            f"Unified Trading Account ({mode or 'unified'}), one-way mode "
            "(verified).",
            "Stop orders are Bitget trigger (strategy) orders.",
        )

    # -- reads ----------------------------------------------------------

    async def _snapshot(
        self, session: BitgetSession
    ) -> trading.VenueAccountSnapshot:
        """Reads ``GET /api/v3/account/assets``.

        Available margin is effective equity minus the initial margin
        requirement (UTA margins every asset together).

        Args:
            session: Session.

        Returns:
            Snapshot in USD.
        """
        data = await self._private(session, "GET", "/api/v3/account/assets")
        equity = wire.dec(data.get("accountEquity"))
        effective = wire.dec(data.get("effEquity") or data.get("accountEquity"))
        available = max(decimals.ZERO, effective - wire.dec(data.get("imr")))
        return trading.VenueAccountSnapshot(
            equity=equity,
            available_margin=available,
            currency="USD",
            ts_ms=self._now_ms(),
        )

    async def _positions(
        self, session: BitgetSession
    ) -> list[trading.VenuePosition]:
        """Reads ``GET /api/v3/position/current-position``.

        Args:
            session: Session.

        Returns:
            Non-zero positions (``short`` is negative).
        """
        data = await self._private(
            session,
            "GET",
            "/api/v3/position/current-position",
            [("category", CATEGORY)],
        )
        result = []
        for row in (data or {}).get("list", []):
            qty = wire.dec(row.get("total"))
            if qty == 0:
                continue
            if row.get("posSide") == "short":
                qty = -qty
            result.append(
                trading.VenuePosition(
                    instrument_id=self._instrument(str(row["symbol"])),
                    signed_qty=qty,
                    entry_price=wire.opt_dec(row.get("avgPrice")),
                )
            )
        return result

    def _cid(self, session: BitgetSession, venue_id: str, oid: object) -> str:
        """Maps a venue ``clientOid``/``orderId`` back to a client id.

        Args:
            session: Session.
            venue_id: ``clientOid``.
            oid: ``orderId``.

        Returns:
            Client id (the venue id when unknown).
        """
        known = session.order_ids.get(str(oid))
        if known is not None:
            return known
        decoded = wire.client_id_of_compact(venue_id) if venue_id else None
        cid = decoded or venue_id
        if oid not in (None, "") and cid:
            session.order_ids[str(oid)] = cid
        return cid

    def _order_state(
        self, session: BitgetSession, row: Mapping[str, Any]
    ) -> trading.VenueOrderState:
        """Converts an order object.

        Args:
            session: Session.
            row: ``order-info``/``unfilled-orders`` object or WS push.

        Returns:
            State.
        """
        status = _STATUS.get(str(row.get("orderStatus")), _State.ACKNOWLEDGED)
        reason = str(row.get("cancelReason") or "") or None
        return trading.VenueOrderState(
            client_order_id=self._cid(
                session, str(row.get("clientOid") or ""), row.get("orderId")
            ),
            exchange_order_id=str(row.get("orderId") or "") or None,
            status=status,
            filled_base=wire.dec(row.get("cumExecQty")),
            reason=reason,
            ts_ms=int(row.get("updatedTime") or row.get("createdTime") or 0),
        )

    def _trigger_state(
        self, session: BitgetSession, row: Mapping[str, Any]
    ) -> trading.VenueOrderState:
        """Converts a trigger (strategy) order object.

        Args:
            session: Session.
            row: Strategy order object.

        Returns:
            State.
        """
        cid = self._cid(session, str(row.get("clientOid") or ""), None)
        session.triggers.add(cid)
        return trading.VenueOrderState(
            client_order_id=cid,
            exchange_order_id=f"trigger:{row.get('orderId')}",
            status=_TRIGGER_STATUS.get(
                str(row.get("status")), _State.ACKNOWLEDGED
            ),
            filled_base=decimals.ZERO,
            reason=None,
            ts_ms=int(row.get("updatedTime") or row.get("createdTime") or 0),
        )

    async def _open_orders(
        self, session: BitgetSession
    ) -> list[trading.VenueOrderState]:
        """Reads unfilled orders and pending trigger orders.

        Args:
            session: Session.

        Returns:
            Orders.
        """
        data = await self._private(
            session,
            "GET",
            "/api/v3/trade/unfilled-orders",
            [("category", CATEGORY), ("limit", "100")],
        )
        result = [
            self._order_state(session, r) for r in (data or {}).get("list", [])
        ]
        triggers = await self._private(
            session,
            "GET",
            "/api/v3/trade/unfilled-strategy-orders",
            [("category", CATEGORY)],
        )
        rows = (
            triggers.get("list", [])
            if isinstance(triggers, Mapping)
            else triggers
        )
        result.extend(self._trigger_state(session, r) for r in rows or [])
        return result

    def _fill(
        self, session: BitgetSession, row: Mapping[str, Any]
    ) -> trading.VenueFill:
        """Converts a fill (REST ``fills`` or the ``fill`` topic).

        Args:
            session: Session.
            row: Fill object.

        Returns:
            Fill (fee positive when paid).
        """
        detail = (row.get("feeDetail") or [{}])[0] or {}
        return trading.VenueFill(
            exchange_fill_id=str(row.get("execId")),
            client_order_id=self._cid(
                session, str(row.get("clientOid") or ""), row.get("orderId")
            ),
            price=wire.dec(row.get("execPrice")),
            quantity_base=wire.dec(row.get("execQty")),
            fee=abs(wire.dec(detail.get("fee"))),
            fee_asset=str(detail.get("feeCoin") or "USDT"),
            liquidity="maker" if row.get("tradeScope") == "maker" else "taker",
            ts_ms=int(row.get("execTime") or row.get("createdTime") or 0),
        )

    async def _fills(
        self, session: BitgetSession, since_ms: int
    ) -> list[trading.VenueFill]:
        """Reads ``GET /api/v3/trade/fills`` (cursor pages of 100).

        Reads at most ten pages (1000 fills) per call.

        Args:
            session: Session.
            since_ms: Lower bound (30-day window).

        Returns:
            Fills, oldest first.
        """
        # Bitget keeps 30 days of fills; one day of margin keeps startTime
        # inside the window.
        start = max(since_ms, self._now_ms() - 29 * 86_400_000)
        fills: list[trading.VenueFill] = []
        cursor = ""
        for _ in range(10):
            params = [
                ("category", CATEGORY),
                ("startTime", str(start)),
                ("limit", "100"),
            ]
            if cursor:
                params.append(("cursor", cursor))
            data = await self._private(
                session, "GET", "/api/v3/trade/fills", params
            )
            rows = (data or {}).get("list", [])
            fills.extend(self._fill(session, r) for r in rows)
            cursor = str((data or {}).get("cursor") or "")
            if len(rows) < 100 or not cursor:
                break
        fills.sort(key=lambda f: f.ts_ms)
        return fills

    def _venue_id(self, session: BitgetSession, cid: str) -> str:
        """Returns the ``clientOid`` used for a client id.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            Venue client id.
        """
        venue_id = session.venue_ids.get(cid) or wire.compact_id_of(cid)
        session.venue_ids[cid] = venue_id
        return venue_id

    def _is_trigger(self, session: BitgetSession, cid: str) -> bool:
        """Tells whether a client id was placed as a trigger order.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            True for stop orders. Without a local or OMS record, protective
            stop-loss legs (``-sl`` suffix) count as trigger orders.
        """
        if cid in session.triggers:
            return True
        known = self._directory.find(session.account_id, cid)
        if known is not None:
            return known.order_type in (
                enums.OrderType.STOP_MARKET,
                enums.OrderType.STOP_LIMIT,
            )
        return cid.endswith("-sl")

    async def _query(
        self, session: BitgetSession, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries ``order-info`` or the trigger order lists by clientOid.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State or ``None``.
        """
        venue_id = self._venue_id(session, client_order_id)
        if self._is_trigger(session, client_order_id):
            for path in (
                "/api/v3/trade/unfilled-strategy-orders",
                "/api/v3/trade/history-strategy-orders",
            ):
                data = await self._private(
                    session, "GET", path, [("category", CATEGORY)]
                )
                rows = (
                    data.get("list", []) if isinstance(data, Mapping) else data
                )
                for row in rows or []:
                    if row.get("clientOid") == venue_id:
                        return self._trigger_state(session, row)
            return None
        try:
            row = await self._private(
                session,
                "GET",
                "/api/v3/trade/order-info",
                [("clientOid", venue_id)],
            )
        except errors.OrderRejectedError as err:
            if err.code == "NOT_FOUND":
                return None
            raise
        if not row:
            return None
        session.order_ids[str(row.get("orderId"))] = client_order_id
        return self._order_state(session, row)

    # -- trading --------------------------------------------------------

    async def _configure(
        self,
        session: BitgetSession,
        symbol: str,
        request: trading.VenueOrderRequest,
    ) -> None:
        """Sets leverage before an opening order (``account/set-leverage``).

        Args:
            session: Session.
            symbol: Symbol.
            request: Order.
        """
        if request.reduce_only:
            return
        mode = (
            "isolated"
            if request.margin_mode is enums.MarginMode.ISOLATED
            else "crossed"
        )
        leverage = wire.text(request.leverage)
        if session.configured.get(symbol) == (leverage, mode):
            return
        body: dict[str, object] = {
            "category": CATEGORY,
            "symbol": symbol,
            "leverage": leverage,
            "marginMode": mode,
        }
        if mode == "isolated":
            body["posSide"] = (
                "long" if request.side is enums.OrderSide.BUY else "short"
            )
        await self._private(
            session,
            "POST",
            "/api/v3/account/set-leverage",
            body=body,
            trade=True,
        )
        session.configured[symbol] = (leverage, mode)

    async def _submit(
        self, session: BitgetSession, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places ``place-order`` or a trigger ``place-strategy-order``.

        Args:
            session: Session.
            request: Order.

        Returns:
            Acknowledgement (Bitget answers with ids only).

        Raises:
            AdapterUnavailableError: ``UNSUPPORTED_ORDER_TYPE`` for a
                post-only market order (nothing is sent).
        """
        symbol = self._symbol(request.instrument_id)
        kind = request.order_type
        if (
            kind is enums.OrderType.MARKET
            and request.time_in_force is enums.TimeInForce.POST_ONLY
        ):
            raise errors.AdapterUnavailableError(
                "post-only market orders are not possible",
                code="UNSUPPORTED_ORDER_TYPE",
            )
        await self._configure(session, symbol, request)
        cid = request.client_order_id
        venue_id = self._venue_id(session, cid)
        side = "buy" if request.side is enums.OrderSide.BUY else "sell"
        margin = (
            "isolated"
            if request.margin_mode is enums.MarginMode.ISOLATED
            else "crossed"
        )
        body: dict[str, object] = {
            "category": CATEGORY,
            "symbol": symbol,
            "qty": wire.text(request.quantity_base),
            "side": side,
            "clientOid": venue_id,
            "marginMode": margin,
            "reduceOnly": "yes" if request.reduce_only else "no",
        }
        if kind in (enums.OrderType.STOP_MARKET, enums.OrderType.STOP_LIMIT):
            if request.trigger_price is None:
                raise AssertionError("stop orders carry a trigger price")
            body.update(
                {
                    "type": "trigger",
                    "triggerBy": "market"
                    if request.trigger_basis is enums.PriceBasis.LAST
                    else "mark",
                    "triggerPrice": wire.text(request.trigger_price),
                    "triggerOrderType": "market"
                    if kind is enums.OrderType.STOP_MARKET
                    else "limit",
                }
            )
            if kind is enums.OrderType.STOP_LIMIT:
                if request.limit_price is None:
                    raise AssertionError(
                        "STOP_LIMIT orders carry a limit price"
                    )
                body["triggerOrderPrice"] = wire.text(request.limit_price)
            session.triggers.add(cid)
            path = "/api/v3/trade/place-strategy-order"
        else:
            body["orderType"] = (
                "market" if kind is enums.OrderType.MARKET else "limit"
            )
            if kind is enums.OrderType.LIMIT:
                if request.limit_price is None:
                    raise AssertionError("LIMIT orders carry a limit price")
                body["price"] = wire.text(request.limit_price)
                body["timeInForce"] = _TIF[request.time_in_force]
            path = "/api/v3/trade/place-order"
        try:
            data = await self._private(
                session, "POST", path, body=body, trade=True
            )
        except errors.OrderRejectedError as err:
            if err.code != "CONFLICT":
                raise
            existing = await self._query(session, cid)
            if existing is None:
                raise
            return trading.VenueSubmitResult(True, existing)
        oid = str((data or {}).get("orderId") or "")
        if oid:
            session.order_ids[oid] = cid
        prefix = "trigger:" if path.endswith("strategy-order") else ""
        return trading.VenueSubmitResult(
            True,
            trading.VenueOrderState(
                client_order_id=cid,
                exchange_order_id=f"{prefix}{oid}" if oid else None,
                status=_State.ACKNOWLEDGED,
                filled_base=decimals.ZERO,
                reason=None,
                ts_ms=self._now_ms(),
            ),
        )

    async def _cancel(
        self, session: BitgetSession, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels by clientOid (``cancel-order``/``cancel-strategy-order``).

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State re-read after the cancel.
        """
        venue_id = self._venue_id(session, client_order_id)
        if self._is_trigger(session, client_order_id):
            await self._private(
                session,
                "POST",
                "/api/v3/trade/cancel-strategy-order",
                body={"clientOid": venue_id},
                trade=True,
            )
        else:
            await self._private(
                session,
                "POST",
                "/api/v3/trade/cancel-order",
                body={"category": CATEGORY, "clientOid": venue_id},
                trade=True,
            )
        state = await self._query(session, client_order_id)
        if state is None:
            return trading.VenueOrderState(
                client_order_id,
                None,
                _State.CANCEL_PENDING,
                decimals.ZERO,
                "cancel accepted by bitget",
                self._now_ms(),
            )
        if state.status in (_State.ACKNOWLEDGED, _State.PARTIALLY_FILLED):
            # Accepted but not processed yet: the stream reports the end.
            return dataclasses.replace(
                state, status=_State.CANCEL_PENDING, reason="cancel accepted"
            )
        return state

    async def _amend(
        self,
        session: BitgetSession,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Modifies a limit order in place (``modify-order``).

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New price.
            trigger_price: Unsupported (trigger orders).
            quantity_base: New quantity.

        Returns:
            State re-read after the modification.

        Raises:
            OrderRejectedError: ``UNSUPPORTED`` for trigger orders or a
                trigger change; ``NOT_FOUND`` when the modified order
                cannot be read back.
        """
        if trigger_price is not None or self._is_trigger(
            session, client_order_id
        ):
            raise errors.OrderRejectedError(
                "trigger orders are cancelled and re-placed, not modified",
                venue=VENUE,
                code="UNSUPPORTED",
            )
        body: dict[str, object] = {
            "category": CATEGORY,
            "clientOid": self._venue_id(session, client_order_id),
            "autoCancel": "no",
        }
        known = self._directory.find(session.account_id, client_order_id)
        if known is not None:
            body["symbol"] = self._symbol(known.instrument_id)
        if limit_price is not None:
            body["price"] = wire.text(limit_price)
        if quantity_base is not None:
            body["qty"] = wire.text(quantity_base)
        await self._private(
            session, "POST", "/api/v3/trade/modify-order", body=body, trade=True
        )
        state = await self._query(session, client_order_id)
        if state is None:
            raise errors.OrderRejectedError(
                "modified order not found", venue=VENUE, code="NOT_FOUND"
            )
        return state

    # -- private stream -------------------------------------------------

    def parse(
        self, session: BitgetSession, frame: str | bytes
    ) -> list[ws.StreamUpdate]:
        """Parses one private v3 frame.

        Args:
            session: Session.
            frame: Raw frame.

        Returns:
            Updates (``pong`` and acks are empty).
        """
        text = frame if isinstance(frame, str) else frame.decode()
        if text == "pong":
            return []
        message = http.parse_json(text)
        if not isinstance(message, Mapping) or "data" not in message:
            if isinstance(message, Mapping) and message.get("event") == "error":
                _LOG.warning("bitget stream error %s", message.get("code"))
            return []
        topic = str((message.get("arg") or {}).get("topic"))
        rows = message.get("data") or []
        updates: list[ws.StreamUpdate] = []
        for row in rows:
            if topic == "order":
                if str(row.get("category", "")).upper() != CATEGORY:
                    continue
                updates.append(
                    ws.StreamUpdate(order=self._order_state(session, row))
                )
            elif topic == "fill":
                if str(row.get("category", "")).upper() != CATEGORY:
                    continue
                updates.append(ws.StreamUpdate(fill=self._fill(session, row)))
            elif topic == "position":
                qty = wire.dec(row.get("total"))
                if row.get("posSide") == "short":
                    qty = -qty
                updates.append(
                    ws.StreamUpdate(
                        position=trading.VenuePosition(
                            instrument_id=self._instrument(str(row["symbol"])),
                            signed_qty=qty,
                            entry_price=wire.opt_dec(row.get("avgPrice")),
                        )
                    )
                )
            elif topic == "account":
                equity = wire.dec(row.get("accountEquity"))
                effective = wire.dec(row.get("effEquity") or equity)
                updates.append(
                    ws.StreamUpdate(
                        balance=trading.VenueAccountSnapshot(
                            equity=equity,
                            available_margin=max(
                                decimals.ZERO,
                                effective - wire.dec(row.get("imr")),
                            ),
                            currency="USD",
                            ts_ms=int(message.get("ts", 0)),
                        )
                    )
                )
        return updates

    def _stream_spec(self, session: BitgetSession) -> ws.PrivateStreamSpec:
        """Describes the v3 private stream.

        Args:
            session: Session.

        Returns:
            Spec (login, subscribe order/fill/position/account, text
            ``ping`` every 25 s; the server drops silent clients after
            2 minutes).
        """
        url = self._ws_urls[session.sandbox]

        async def connect_url() -> str:
            """Returns the stream URL."""
            return url

        async def on_open(connection: ws.WsConnection) -> None:
            """Logs in and subscribes, waiting for the login ack.

            The login carries the API key, passphrase, a timestamp in
            seconds and ``sign`` over that timestamp, ``GET`` and
            ``/user/verify``, as Bitget's WebSocket login requires. Up to
            five frames are read for the answer.

            Args:
                connection: Connection.

            Raises:
                VenuePermissionError: Login refused.
                AdapterUnavailableError: No login answer within five
                    frames.
            """
            timestamp = str(self._now_ms() // 1000)
            creds = session.credentials
            login = {
                "op": "login",
                "args": [
                    {
                        "apiKey": creds.get("api_key"),
                        "passphrase": creds.get("passphrase"),
                        "timestamp": timestamp,
                        "sign": sign(
                            creds.get("api_secret"),
                            timestamp,
                            "GET",
                            "/user/verify",
                            "",
                        ),
                    }
                ],
            }
            await connection.send(json.dumps(login))
            for _ in range(5):
                reply = await connection.recv()
                if reply == "pong":
                    continue
                message = http.parse_json(
                    reply if isinstance(reply, str) else reply.decode()
                )
                if message.get("event") == "login":
                    break
                if message.get("event") == "error":
                    raise errors.VenuePermissionError(
                        "permission missing: Bitget refused the stream login "
                        f"({message.get('code')})",
                        venue=VENUE,
                        venue_code=str(message.get("code")),
                    )
            else:
                raise errors.AdapterUnavailableError("bitget login timed out")
            await connection.send(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            {"instType": "UTA", "topic": topic}
                            for topic in (
                                "order",
                                "fill",
                                "position",
                                "account",
                            )
                        ],
                    }
                )
            )

        async def resync() -> Sequence[ws.StreamUpdate]:
            """Returns the REST snapshot replayed as stream updates."""
            return await self._resync(session)

        return ws.PrivateStreamSpec(
            name="bitget private stream",
            url=connect_url,
            on_open=on_open,
            parse=lambda frame: self.parse(session, frame),
            resync=resync,
            ping_message="ping",
            ping_interval_s=25.0,
            idle_timeout_s=110.0,
        )
