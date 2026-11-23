"""MEXC futures (contract) trading adapter.

Sources (MEXC docs, read 2026-11-23):

* Integration guide (host, signing, KYC requirement):
  https://www.mexc.com/api-docs/futures/integration-guide
* Account and trading endpoints:
  https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/
  (``place-order``, ``get-order-by-external-id``, ``cancel-orders``,
  ``get-open-positions``, ``get-user-position-mode``,
  ``get-historical-order-deal-details``, ``place-plan-order``,
  ``cancel-planned-orders``, ``get-plan-order-list``)
* Market endpoints: https://www.mexc.com/api-docs/futures/market-endpoints/
* WebSocket: https://www.mexc.com/api-docs/futures/websocket-api/
  (``login-authentication``, ``subscription-filtering``, ``order``,
  ``fill-details``, ``position``, ``assets``)
* Error codes: https://www.mexc.com/api-docs/futures/error-code
* Update log (order API maintenance 2022-07-25 → reopened 2026-03-31;
  host moved to ``api.mexc.com`` 2026-01-19):
  https://www.mexc.com/api-docs/futures/update-log
* Futures API trading launch (KYC-only):
  https://www.mexc.com/announcements/article/introducing-api-futures-trading-on-mar-31-2026-17827791534551
* No sandbox ("MEXC API connects directly to the live trading
  environment"): https://www.mexc.com/mexc-api

Protocol facts this module relies on:

* REST ``https://api.mexc.com``; WebSocket ``wss://contract.mexc.com/edge``
  (JSON pushes). MEXC migrates futures WebSocket *market data* to Protobuf
  by 2026-12-01 10:00 UTC; nothing announced covers the private channels.
  The stream therefore stays on the JSON endpoint but parses binary
  Protobuf pushes as well (``mexc_protobuf``, which cites the sources).
  Setting ``JOSEPH_VENUE_URLS=mexc.ws_url=<WS_PROTOBUF_URL>`` selects the
  Protobuf endpoint explicitly.
* Signature = lowercase hex HMAC-SHA256(secret, accessKey + Request-Time +
  parameterString); GET parameters sorted by key and ``&``-joined (nulls
  excluded, path parameters excluded), POST signs the raw JSON body.
  Headers ``ApiKey``, ``Request-Time`` (ms), ``Signature``, ``Recv-Window``
  (seconds, max 60). WS login signs ``apiKey + reqTime`` and then pushes
  every personal channel by default.
* Order placement needs KYC and an API key with the "Order Placing"
  permission; refusals (``702``/``704``/``511``/``200005``/``604``/``1002``…)
  are reported as ``permission missing``.
* Sizes are contracts: base quantity = ``vol * contractSize`` (contract
  ``detail``); ``externalOid`` holds at most 32 characters.
* Limits: create/plan order 4 per 2 s, cancels and queries 20 per 2 s,
  contract detail 1 per 5 s (cached).
* ``side``: 1 open long, 2 close short, 3 open short, 4 close long; ``type``
  1 limit, 2 post-only, 3 IOC, 4 FOK, 5 market; ``openType`` 1 isolated,
  2 cross; ``positionMode`` 2 one-way. One-way mode does not support limit
  order editing (``2053``), so amend is not offered.
* Stop orders are plan orders (``planorder/place/v2``) with ``triggerType``
  1 (≥) or 2 (≤), ``trend`` 1 last or 2 fair price, and ``executeCycle`` at
  most 7 days.
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
from joseph_execution.adapters.venues import mexc_protobuf
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
VENUE = "mexc"
REST_URL = "https://api.mexc.com"
WS_URL = "wss://contract.mexc.com/edge"
"""Default stream endpoint (JSON pushes; binary pushes are parsed too)."""
WS_PROTOBUF_URL = f"{WS_URL}?Trans-Protocol=Protobuf"
"""Protobuf endpoint (requests, acks and errors stay JSON text)."""
_DETAIL_TTL_S = 3600.0
_STATUS = {1: S.ACKNOWLEDGED, 2: S.ACKNOWLEDGED, 3: S.FILLED, 4: S.CANCELED}
_PLAN_STATUS = {
    1: S.ACKNOWLEDGED,
    2: S.CANCELED,
    3: S.ACKNOWLEDGED,
    4: S.EXPIRED,
    5: S.REJECTED,
}
_TYPE = {
    enums.TimeInForce.GTC: 1,
    enums.TimeInForce.POST_ONLY: 2,
    enums.TimeInForce.IOC: 3,
    enums.TimeInForce.FOK: 4,
}
_MARGIN = {2005, 2008, 2044, 6002, 6009}
_PERMISSION = {
    401,
    402,
    406,
    506,
    511,
    602,
    604,
    701,
    702,
    703,
    704,
    801,
    1002,
    6001,
    9998,
    11444,
    200005,
}
_RATE = {510, 2037}
_UNKNOWN = {500, 501, 9999}
_NOT_FOUND = {2009, 2040, 5002}
_LOT = {2011, 2015}
_TICK = {2007}


def budgets() -> list[ratelimit.Budget]:
    """Returns the documented budgets.

    Returns:
        ``create`` (4/2 s), ``query`` (20/2 s) and ``detail`` (1/5 s).
    """
    return [
        ratelimit.Budget("create", 4, 2.0),
        ratelimit.Budget("query", 20, 2.0),
    ]


def map_error(  # noqa: PLR0911 - one return per error family.
    status: int, payload: Any
) -> errors.ExecutionError:
    """Maps a MEXC error answer to a domain error.

    Args:
        status: HTTP status.
        payload: ``{"success": false, "code": 2005, "message": "..."}``.

    Returns:
        Domain error.
    """
    code = status
    message = str(payload)[:200]
    if isinstance(payload, Mapping):
        try:
            code = int(payload.get("code", status))
        except (TypeError, ValueError):
            code = status
        message = str(payload.get("message", ""))[:200]
    venue_code = str(code)
    text = f"{message} ({venue_code})"
    if code in _UNKNOWN or status >= 500:
        return errors.AdapterTimeoutError(
            f"mexc could not confirm the request ({venue_code}); outcome "
            "unknown"
        )
    if code in _MARGIN:
        return errors.InsufficientMarginError(
            text, venue=VENUE, venue_code=venue_code
        )
    if code in _PERMISSION or status in (401, 403):
        return errors.VenuePermissionError(
            "permission missing: MEXC futures order placement needs KYC and "
            "an API key with the 'Order Placing' permission, and the "
            f"futures API must be available in your region ({text})",
            venue=VENUE,
            venue_code=venue_code,
        )
    if code in _RATE:
        return errors.RateLimitedError(f"mexc throttled: {text}")
    if code in _NOT_FOUND:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="NOT_FOUND"
        )
    if code == 2042:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="CONFLICT"
        )
    if code in _LOT:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_LOT_SIZE"
        )
    if code in _TICK:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="INVALID_TICK_SIZE"
        )
    if code == 7008:
        return errors.OrderRejectedError(
            text, venue=VENUE, venue_code=venue_code, code="MIN_NOTIONAL"
        )
    return errors.OrderRejectedError(text, venue=VENUE, venue_code=venue_code)


@dataclasses.dataclass(frozen=True, slots=True)
class Contract:
    """Contract metadata needed to convert sizes.

    Attributes:
        symbol: Symbol.
        contract_size: Base units per contract.
        vol_unit: Contract step.
        api_allowed: API trading allowed for the contract.
    """

    symbol: str
    contract_size: decimal.Decimal
    vol_unit: decimal.Decimal
    api_allowed: bool


@dataclasses.dataclass(slots=True)
class MexcSession(live.Session):
    """Per-account MEXC state.

    Attributes:
        limiter: Per-key limiter.
        order_ids: Venue order id to client id.
        plans: Plan order id to client id.
        plan_of: Client id to plan order id.
        symbols: Client id to symbol.
    """

    limiter: ratelimit.WindowLimiter | None = None
    order_ids: dict[str, str] = dataclasses.field(default_factory=dict)
    plans: dict[str, str] = dataclasses.field(default_factory=dict)
    plan_of: dict[str, str] = dataclasses.field(default_factory=dict)
    symbols: dict[str, str] = dataclasses.field(default_factory=dict)


class MexcFuturesAdapter(live.LiveVenueAdapter[MexcSession]):
    """MEXC USDT-M perpetual futures (mainnet only)."""

    REQUIRED_FIELDS = ("api_key", "api_secret")
    SANDBOX_NAME = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initializes the adapter (see ``LiveVenueAdapter``).

        Args:
            *args: Base arguments.
            **kwargs: Base keyword arguments (``urls`` accepts
                ``rest_url`` and ``ws_url``).
        """
        super().__init__(*args, **kwargs)
        self._rest_url = self._url("rest_url", REST_URL)
        self._ws_url = self._url("ws_url", WS_URL)
        self._http: http.VenueHttp | None = None
        self._contracts: dict[str, tuple[float, Contract]] = {}

    def _client(self) -> http.VenueHttp:
        """Returns the REST client.

        Returns:
            Client.
        """
        if self._http is None:
            self._http = http.VenueHttp(
                VENUE,
                self._rest_url,
                ratelimit.WindowLimiter(
                    VENUE,
                    [
                        ratelimit.Budget("public", 20, 2.0),
                        ratelimit.Budget("detail", 1, 5.0),
                    ],
                    max_wait_s=max(self._options.max_rate_wait_s, 5.0),
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
    ) -> MexcSession:
        """Builds per-account state.

        Args:
            account_id: Account.
            credentials: Credentials.
            sandbox: Always False (no sandbox).

        Returns:
            Session.
        """
        return MexcSession(
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

    async def _public(
        self, path: str, query: str = "", cost: str = "public"
    ) -> Any:
        """Sends a public request and unwraps ``data``.

        Args:
            path: Path.
            query: Query string.
            cost: Budget name.

        Returns:
            ``data``.
        """
        reply = await self._client().request(
            "GET", path, query=query, costs={cost: 1}
        )
        payload = reply.payload
        if (
            reply.status == 200
            and isinstance(payload, Mapping)
            and payload.get("success")
        ):
            return payload.get("data")
        raise map_error(reply.status, payload)

    async def _private(
        self,
        session: MexcSession,
        method: str,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        body: object = None,
        *,
        budget: str = "query",
    ) -> Any:
        """Sends a signed request and unwraps ``data``.

        Args:
            session: Session.
            method: ``GET`` or ``POST``.
            path: Path.
            params: GET parameters (sorted for signing).
            body: POST JSON body (object or array).
            budget: ``create`` or ``query``.

        Returns:
            ``data``.

        Raises:
            ExecutionError: Mapped venue errors.
        """
        key = session.credentials.get("api_key")
        timestamp = str(self._now_ms())
        query = "&".join(f"{k}={v}" for k, v in sorted(params) if v != "")
        raw = "" if body is None else json.dumps(body, separators=(",", ":"))
        signed = raw if method == "POST" else query
        window = max(5, min(30, self._options.recv_window_ms // 1000))
        headers = {
            "ApiKey": key,
            "Request-Time": timestamp,
            "Signature": signing.hmac_sha256_hex(
                session.credentials.get("api_secret"),
                f"{key}{timestamp}{signed}",
            ),
            "Recv-Window": str(window),
            "Content-Type": "application/json",
            "Language": "en-US",
        }
        if session.limiter is not None:
            await session.limiter.acquire({budget: 1})
        reply = await self._client().request(
            method,
            path,
            query=query,
            body=raw if method == "POST" else None,
            headers=headers,
            costs={"public": 0},
        )
        payload = reply.payload
        if (
            reply.status == 200
            and isinstance(payload, Mapping)
            and payload.get("success")
        ):
            return payload.get("data")
        raise map_error(reply.status, payload)

    async def contract(self, symbol: str) -> Contract:
        """Returns cached contract metadata (``GET /api/v1/contract/detail``).

        Args:
            symbol: Symbol such as ``BTC_USDT``.

        Returns:
            Contract.
        """
        now = self._monotonic()
        cached = self._contracts.get(symbol)
        if cached is not None and now - cached[0] < _DETAIL_TTL_S:
            return cached[1]
        data = await self._public(
            "/api/v1/contract/detail", f"symbol={symbol}", cost="detail"
        )
        row = data[0] if isinstance(data, list) else data
        contract = Contract(
            symbol=symbol,
            contract_size=wire.dec(row.get("contractSize")),
            vol_unit=wire.dec(row.get("volUnit") or 1),
            api_allowed=bool(row.get("apiAllowed", True)),
        )
        self._contracts[symbol] = (now, contract)
        return contract

    def _remember(
        self, session: MexcSession, symbol: str, cid: str, oid: object
    ) -> None:
        """Records an order's symbol and venue id.

        Args:
            session: Session.
            symbol: Symbol.
            cid: Client id.
            oid: Venue order id.
        """
        session.symbols[cid] = symbol
        if oid not in (None, "", 0):
            session.order_ids[str(oid)] = cid

    def _cid(self, session: MexcSession, external: object, oid: object) -> str:
        """Maps ``externalOid``/``orderId`` to a client id.

        Args:
            session: Session.
            external: ``externalOid``.
            oid: ``orderId``.

        Returns:
            Client id.
        """
        known = session.order_ids.get(str(oid))
        if known is not None:
            return known
        text = str(external or "")
        cid = (wire.client_id_of_compact(text) or text) if text else str(oid)
        if oid not in (None, ""):
            session.order_ids[str(oid)] = cid
        return cid

    async def _order_state(
        self, session: MexcSession, row: Mapping[str, Any]
    ) -> trading.VenueOrderState:
        """Converts an order object (REST or ``push.personal.order``).

        Args:
            session: Session.
            row: Order object.

        Returns:
            State (``dealVol`` converted to base units).
        """
        symbol = str(row.get("symbol"))
        contract = await self.contract(symbol)
        filled = wire.dec(row.get("dealVol")) * contract.contract_size
        state = int(row.get("state", 2))
        status = _STATUS.get(state, S.REJECTED)
        if status is S.ACKNOWLEDGED and filled > 0:
            status = S.PARTIALLY_FILLED
        cid = self._cid(session, row.get("externalOid"), row.get("orderId"))
        session.symbols[cid] = symbol
        error_code = row.get("errorCode")
        return trading.VenueOrderState(
            client_order_id=cid,
            exchange_order_id=str(row.get("orderId")),
            status=status,
            filled_base=filled,
            reason=f"mexc errorCode {error_code}" if error_code else None,
            ts_ms=int(row.get("updateTime") or row.get("createTime") or 0),
        )

    # -- negotiation ----------------------------------------------------

    async def _probe_health(self, sandbox: bool) -> None:
        """Checks ``GET /api/v1/contract/ping``.

        Args:
            sandbox: Unused (no sandbox).
        """
        del sandbox
        await self._public("/api/v1/contract/ping")

    async def _probe_permissions(self, session: MexcSession) -> tuple[str, ...]:
        """Checks position mode and the order permission.

        ``GET /api/v1/private/position/position_mode`` must be 2 (one-way).
        The order permission is probed by cancelling order id ``0``
        (``POST /api/v1/private/order/cancel``, which needs "Order Placing"
        and has no effect): a per-order ``2040`` proves the permission.

        Args:
            session: Session.

        Returns:
            Notes.

        Raises:
            VenuePermissionError: Missing KYC or order permission.
            ExecutionError: ``UNSUPPORTED`` in hedge mode.
        """
        mode = await self._private(
            session, "GET", "/api/v1/private/position/position_mode"
        )
        if int(mode) != 2:
            raise errors.ExecutionError(
                "UNSUPPORTED",
                "the MEXC futures account uses hedge mode; switch to one-way "
                "mode to trade with Joseph",
            )
        await self._private(
            session, "POST", "/api/v1/private/order/cancel", body=[0]
        )
        return (
            "One-way position mode (verified); API order permission granted.",
            "MEXC has no sandbox: every order is real.",
            "Stop orders are plan orders that expire after 7 days.",
            "Amend is unavailable in one-way mode (cancel and re-place).",
        )

    # -- reads ----------------------------------------------------------

    async def _snapshot(
        self, session: MexcSession
    ) -> trading.VenueAccountSnapshot:
        """Reads ``GET /api/v1/private/account/assets`` (USDT row).

        Args:
            session: Session.

        Returns:
            Snapshot.
        """
        rows = await self._private(
            session, "GET", "/api/v1/private/account/assets"
        )
        row: Mapping[str, Any] = next(
            (r for r in rows or [] if r.get("currency") == "USDT"), {}
        )
        return trading.VenueAccountSnapshot(
            equity=wire.dec(row.get("equity")),
            available_margin=wire.dec(
                row.get("availableOpen") or row.get("availableBalance")
            ),
            currency="USDT",
            ts_ms=self._now_ms(),
        )

    async def _positions(
        self, session: MexcSession
    ) -> list[trading.VenuePosition]:
        """Reads ``GET /api/v1/private/position/open_positions``.

        Args:
            session: Session.

        Returns:
            Positions in base units (short negative).
        """
        rows = await self._private(
            session, "GET", "/api/v1/private/position/open_positions"
        )
        result = []
        for row in rows or []:
            symbol = str(row["symbol"])
            contract = await self.contract(symbol)
            qty = wire.dec(row.get("holdVol")) * contract.contract_size
            if qty == 0:
                continue
            if int(row.get("positionType", 1)) == 2:
                qty = -qty
            result.append(
                trading.VenuePosition(
                    instrument_id=self._instrument(symbol),
                    signed_qty=qty,
                    entry_price=wire.opt_dec(row.get("holdAvgPrice")),
                )
            )
        return result

    async def _open_orders(
        self, session: MexcSession
    ) -> list[trading.VenueOrderState]:
        """Reads ``GET /api/v1/private/order/list/open_orders``.

        Args:
            session: Session.

        Returns:
            Orders.
        """
        data = await self._private(
            session,
            "GET",
            "/api/v1/private/order/list/open_orders",
            [("page_num", "1"), ("page_size", "100")],
        )
        rows = data.get("resultList", []) if isinstance(data, Mapping) else data
        return [await self._order_state(session, r) for r in rows or []]

    def _symbols(self, session: MexcSession, since_ms: int) -> set[str]:
        """Collects symbols this account traded recently.

        Args:
            session: Session.
            since_ms: Lower bound.

        Returns:
            Symbols.
        """
        symbols = set(session.symbols.values())
        for known in self._directory.recent(session.account_id, since_ms):
            if not known.instrument_id.startswith(f"{VENUE}:perp:"):
                continue
            symbol = self._symbol(known.instrument_id)
            symbols.add(symbol)
            session.symbols[known.client_order_id] = symbol
            oid = known.exchange_order_id
            if oid and not oid.startswith("plan:"):
                session.order_ids.setdefault(oid, known.client_order_id)
            elif oid:
                session.plans.setdefault(oid[5:], known.client_order_id)
        return symbols

    async def _fills(
        self, session: MexcSession, since_ms: int
    ) -> list[trading.VenueFill]:
        """Reads ``order_deals/v3`` per symbol (newest 100).

        Args:
            session: Session.
            since_ms: Lower bound.

        Returns:
            Fills of known orders, oldest first.
        """
        fills: list[trading.VenueFill] = []
        for symbol in sorted(self._symbols(session, since_ms)):
            contract = await self.contract(symbol)
            data = await self._private(
                session,
                "GET",
                "/api/v1/private/order/list/order_deals/v3",
                [("symbol", symbol), ("page_num", "1"), ("page_size", "100")],
            )
            rows = (
                data.get("resultList", [])
                if isinstance(data, Mapping)
                else data
            )
            for row in rows or []:
                ts = int(row.get("timestamp", 0))
                cid = session.order_ids.get(str(row.get("orderId")))
                if ts < since_ms or cid is None:
                    continue
                fills.append(self._fill(row, cid, contract.contract_size, ts))
        fills.sort(key=lambda f: f.ts_ms)
        return fills

    @staticmethod
    def _fill(
        row: Mapping[str, Any],
        cid: str,
        contract_size: decimal.Decimal,
        ts: int,
    ) -> trading.VenueFill:
        """Converts a deal row (REST ``order_deals`` or WS ``order.deal``).

        Args:
            row: Deal.
            cid: Client id.
            contract_size: Base units per contract.
            ts: Deal time.

        Returns:
            Fill (fee positive when paid).
        """
        taker = row.get("isTaker", row.get("taker", True))
        return trading.VenueFill(
            exchange_fill_id=str(row.get("id")),
            client_order_id=cid,
            price=wire.dec(row.get("price")),
            quantity_base=wire.dec(row.get("vol")) * contract_size,
            fee=wire.dec(row.get("fee")),
            fee_asset=str(row.get("feeCurrency") or "USDT"),
            liquidity="taker" if taker else "maker",
            ts_ms=ts,
        )

    def _symbol_for(self, session: MexcSession, cid: str) -> str:
        """Finds the symbol of a client order id.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            Symbol.

        Raises:
            AdapterUnavailableError: When nobody knows the order.
        """
        symbol = session.symbols.get(cid)
        if symbol is None:
            known = self._directory.find(session.account_id, cid)
            if known is None:
                raise errors.AdapterUnavailableError(
                    f"mexc needs the symbol of {cid}; the OMS has no record"
                )
            symbol = self._symbol(known.instrument_id)
            session.symbols[cid] = symbol
            if known.exchange_order_id and known.exchange_order_id.startswith(
                "plan:"
            ):
                session.plan_of[cid] = known.exchange_order_id[5:]
        return symbol

    def _is_plan(self, session: MexcSession, cid: str) -> bool:
        """Tells whether a client id was placed as a plan (stop) order.

        Args:
            session: Session.
            cid: Client id.

        Returns:
            True for plan orders.
        """
        if cid in session.plan_of:
            return True
        known = self._directory.find(session.account_id, cid)
        return known is not None and known.order_type in (
            enums.OrderType.STOP_MARKET,
            enums.OrderType.STOP_LIMIT,
        )

    async def _plan_state(
        self, session: MexcSession, cid: str, symbol: str
    ) -> trading.VenueOrderState | None:
        """Finds a plan order in ``planorder/list/orders`` (last 7 days).

        Args:
            session: Session.
            cid: Client id.
            symbol: Symbol.

        Returns:
            State, or ``None`` when absent.
        """
        plan_id = session.plan_of.get(cid)
        if plan_id is None:
            return None
        now = self._now_ms()
        data = await self._private(
            session,
            "GET",
            "/api/v1/private/planorder/list/orders",
            [
                ("symbol", symbol),
                ("start_time", str(now - 7 * 86_400_000)),
                ("end_time", str(now)),
                ("page_num", "1"),
                ("page_size", "100"),
            ],
        )
        rows = data.get("resultList", []) if isinstance(data, Mapping) else data
        for row in rows or []:
            if str(row.get("id")) != plan_id:
                continue
            triggered = row.get("orderId")
            if triggered:
                session.order_ids[str(triggered)] = cid
                if int(row.get("state", 1)) == 3:
                    order = await self._private(
                        session,
                        "GET",
                        f"/api/v1/private/order/get/{triggered}",
                    )
                    state = await self._order_state(session, order)
                    return dataclasses.replace(
                        state,
                        client_order_id=cid,
                        exchange_order_id=f"plan:{plan_id}",
                    )
            return trading.VenueOrderState(
                client_order_id=cid,
                exchange_order_id=f"plan:{plan_id}",
                status=_PLAN_STATUS.get(int(row.get("state", 1)), S.REJECTED),
                filled_base=decimals.ZERO,
                reason=None,
                ts_ms=int(row.get("updateTime") or 0),
            )
        return None

    async def _query(
        self, session: MexcSession, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Queries ``order/external/{symbol}/{externalOid}`` or plan orders.

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State or ``None``.
        """
        symbol = self._symbol_for(session, client_order_id)
        if self._is_plan(session, client_order_id):
            return await self._plan_state(session, client_order_id, symbol)
        external = wire.compact_id_of(client_order_id)
        try:
            row = await self._private(
                session,
                "GET",
                f"/api/v1/private/order/external/{symbol}/{external}",
            )
        except errors.OrderRejectedError as err:
            if err.code == "NOT_FOUND":
                return None
            raise
        if not row:
            return None
        self._remember(session, symbol, client_order_id, row.get("orderId"))
        return await self._order_state(session, row)

    # -- trading --------------------------------------------------------

    async def _submit(
        self, session: MexcSession, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places ``order/create`` or ``planorder/place/v2``.

        Args:
            session: Session.
            request: Order.

        Returns:
            Acknowledgement.

        Raises:
            OrderRejectedError: Size not a whole number of contracts or the
                contract disallows API trading (before sending).
        """
        symbol = self._symbol(request.instrument_id)
        contract = await self.contract(symbol)
        if not contract.api_allowed:
            raise errors.VenuePermissionError(
                f"permission missing: MEXC disallows API trading on {symbol}",
                venue=VENUE,
            )
        vol = request.quantity_base / contract.contract_size
        step = contract.vol_unit
        if vol <= 0 or not decimals.is_multiple(vol, step):
            raise errors.OrderRejectedError(
                f"{request.quantity_base} is not a whole number of "
                f"{contract.contract_size} contracts",
                venue=VENUE,
                code="INVALID_LOT_SIZE",
            )
        buy = request.side is enums.OrderSide.BUY
        # 1 open long, 2 close short, 3 open short, 4 close long.
        side = (2 if buy else 4) if request.reduce_only else (1 if buy else 3)
        open_type = 1 if request.margin_mode is enums.MarginMode.ISOLATED else 2
        cid = request.client_order_id
        session.symbols[cid] = symbol
        body: dict[str, object] = {
            "symbol": symbol,
            "vol": wire.text(vol),
            "side": side,
            "openType": open_type,
            "positionMode": 2,
            "leverage": int(request.leverage),
        }
        if request.reduce_only:
            body["reduceOnly"] = True
        kind = request.order_type
        if kind in (enums.OrderType.STOP_MARKET, enums.OrderType.STOP_LIMIT):
            return await self._place_plan(session, request, body)
        if kind is enums.OrderType.MARKET:
            if request.time_in_force is enums.TimeInForce.POST_ONLY:
                raise errors.AdapterUnavailableError(
                    "post-only market orders are not possible",
                    code="UNSUPPORTED_ORDER_TYPE",
                )
            body["type"] = 5
            ticker = await self._public(
                "/api/v1/contract/ticker", f"symbol={symbol}"
            )
            body["price"] = wire.text(wire.dec(ticker.get("lastPrice")))
        else:
            assert request.limit_price is not None
            body["type"] = _TYPE[request.time_in_force]
            body["price"] = wire.text(request.limit_price)
        body["externalOid"] = wire.compact_id_of(cid)
        try:
            data = await self._private(
                session,
                "POST",
                "/api/v1/private/order/create",
                body=body,
                budget="create",
            )
        except errors.OrderRejectedError as err:
            if err.code != "CONFLICT":
                raise
            existing = await self._query(session, cid)
            if existing is None:
                raise
            return trading.VenueSubmitResult(True, existing)
        oid = data.get("orderId") if isinstance(data, Mapping) else data
        self._remember(session, symbol, cid, oid)
        return trading.VenueSubmitResult(
            True,
            trading.VenueOrderState(
                cid,
                str(oid),
                S.ACKNOWLEDGED,
                decimals.ZERO,
                None,
                self._now_ms(),
            ),
        )

    async def _place_plan(
        self,
        session: MexcSession,
        request: trading.VenueOrderRequest,
        body: dict[str, object],
    ) -> trading.VenueSubmitResult:
        """Places a stop as a plan order (``planorder/place/v2``).

        A buy stop triggers at or above the price (``triggerType`` 1), a
        sell stop at or below (2); ``trend`` 1 is the last price and 2 the
        fair (mark) price; ``executeCycle`` 2 keeps it for 7 days.

        Args:
            session: Session.
            request: Order.
            body: Common order fields.

        Returns:
            Acknowledgement with ``plan:<id>`` as the venue id.
        """
        assert request.trigger_price is not None
        stop_market = request.order_type is enums.OrderType.STOP_MARKET
        body.update(
            {
                "triggerPrice": wire.text(request.trigger_price),
                "triggerType": 1 if request.side is enums.OrderSide.BUY else 2,
                "trend": 1
                if request.trigger_basis is enums.PriceBasis.LAST
                else 2,
                "executeCycle": 2,
                "orderType": 5 if stop_market else 1,
            }
        )
        if not stop_market:
            assert request.limit_price is not None
            body["price"] = wire.text(request.limit_price)
        data = await self._private(
            session,
            "POST",
            "/api/v1/private/planorder/place/v2",
            body=body,
            budget="create",
        )
        cid = request.client_order_id
        plan_id = str(data)
        session.plans[plan_id] = cid
        session.plan_of[cid] = plan_id
        return trading.VenueSubmitResult(
            True,
            trading.VenueOrderState(
                cid,
                f"plan:{plan_id}",
                S.ACKNOWLEDGED,
                decimals.ZERO,
                None,
                self._now_ms(),
            ),
        )

    async def _cancel(
        self, session: MexcSession, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels by venue id (``order/cancel`` or ``planorder/cancel``).

        Args:
            session: Session.
            client_order_id: Client id.

        Returns:
            State after the cancel.

        Raises:
            OrderRejectedError: ``NOT_FOUND`` when nothing can be cancelled.
        """
        symbol = self._symbol_for(session, client_order_id)
        if self._is_plan(session, client_order_id):
            plan_id = session.plan_of.get(client_order_id)
            if plan_id is None:
                raise errors.OrderRejectedError(
                    "plan order id unknown", venue=VENUE, code="NOT_FOUND"
                )
            await self._private(
                session,
                "POST",
                "/api/v1/private/planorder/cancel",
                body=[{"symbol": symbol, "orderId": int(plan_id)}],
            )
            return trading.VenueOrderState(
                client_order_id,
                f"plan:{plan_id}",
                S.CANCELED,
                decimals.ZERO,
                "canceled",
                self._now_ms(),
            )
        current = await self._query(session, client_order_id)
        if current is None or current.exchange_order_id is None:
            raise errors.OrderRejectedError(
                "order not found", venue=VENUE, code="NOT_FOUND"
            )
        if current.status.is_terminal:
            return current
        results = await self._private(
            session,
            "POST",
            "/api/v1/private/order/cancel",
            body=[int(current.exchange_order_id)],
        )
        for row in results or []:
            code = int(row.get("errorCode", 0))
            if code not in (0, 2041):
                raise map_error(
                    200, {"code": code, "message": row.get("errorMsg")}
                )
        final = await self._query(session, client_order_id)
        if final is None:
            raise errors.OrderRejectedError(
                "order vanished", venue=VENUE, code="NOT_FOUND"
            )
        if final.status in (S.ACKNOWLEDGED, S.PARTIALLY_FILLED):
            return dataclasses.replace(
                final, status=S.CANCEL_PENDING, reason="cancel accepted"
            )
        return final

    async def _amend(
        self,
        session: MexcSession,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Not offered: one-way mode cannot edit limit orders (``2053``).

        Args:
            session: Session.
            client_order_id: Client id.
            limit_price: New price.
            trigger_price: New trigger.
            quantity_base: New quantity.

        Raises:
            OrderRejectedError: Always.
        """
        del session, client_order_id, limit_price, trigger_price
        del quantity_base
        raise errors.OrderRejectedError(
            "MEXC one-way mode does not support order editing",
            venue=VENUE,
            code="UNSUPPORTED",
        )

    # -- private stream -------------------------------------------------

    async def parse(
        self, session: MexcSession, frame: str | bytes
    ) -> list[ws.StreamUpdate]:
        """Parses one private frame (JSON text or Protobuf bytes).

        Args:
            session: Session.
            frame: Raw frame.

        Returns:
            Updates.
        """
        message = mexc_protobuf.loads(frame)
        if not isinstance(message, Mapping):
            return []
        channel = str(message.get("channel"))
        data = message.get("data")
        ts = int(message.get("ts", 0))
        if channel == "rs.error":
            _LOG.warning("mexc stream error: %s", str(data)[:120])
        if not isinstance(data, Mapping):
            return []
        if channel == "push.personal.order":
            updates = [
                ws.StreamUpdate(order=await self._order_state(session, data))
            ]
        elif channel == "push.personal.order.deal":
            contract = await self.contract(str(data.get("symbol")))
            cid = self._cid(
                session, data.get("externalOid"), data.get("orderId")
            )
            fill = self._fill(
                data, cid, contract.contract_size, int(data.get("timestamp", 0))
            )
            updates = [ws.StreamUpdate(fill=fill)]
        elif channel == "push.personal.position":
            updates = [await self._position_update(data)]
        elif channel == "push.personal.asset":
            updates = self._asset_update(data, ts)
        elif channel == "push.personal.plan.order":
            updates = self._plan_update(session, data, ts)
        else:
            updates = []
        return updates

    async def _position_update(
        self, data: Mapping[str, Any]
    ) -> ws.StreamUpdate:
        """Converts ``push.personal.position``.

        Args:
            data: Push data.

        Returns:
            Position update (closed positions are flat).
        """
        symbol = str(data.get("symbol"))
        contract = await self.contract(symbol)
        qty = wire.dec(data.get("holdVol")) * contract.contract_size
        if int(data.get("positionType", 1)) == 2:
            qty = -qty
        if int(data.get("state", 1)) == 3:
            qty = decimals.ZERO
        return ws.StreamUpdate(
            position=trading.VenuePosition(
                self._instrument(symbol),
                qty,
                wire.opt_dec(data.get("holdAvgPrice")),
            )
        )

    @staticmethod
    def _asset_update(
        data: Mapping[str, Any], ts: int
    ) -> list[ws.StreamUpdate]:
        """Converts ``push.personal.asset`` (USDT only).

        Args:
            data: Push data.
            ts: Push time.

        Returns:
            Balance update (equity = available + position margin + frozen).
        """
        if data.get("currency") != "USDT":
            return []
        available = wire.dec(data.get("availableBalance"))
        equity = (
            available
            + wire.dec(data.get("positionMargin"))
            + wire.dec(data.get("frozenBalance"))
        )
        return [
            ws.StreamUpdate(
                balance=trading.VenueAccountSnapshot(
                    equity, available, "USDT", ts
                )
            )
        ]

    @staticmethod
    def _plan_update(
        session: MexcSession, data: Mapping[str, Any], ts: int
    ) -> list[ws.StreamUpdate]:
        """Converts ``push.personal.plan.order``.

        Args:
            session: Session.
            data: Push data.
            ts: Push time.

        Returns:
            Terminal plan states (a triggered plan maps its real order).
        """
        plan_id = str(data.get("id"))
        cid = session.plans.get(plan_id)
        if cid is None:
            return []
        oid = data.get("orderId")
        if oid not in (None, "", 0, "0"):
            session.order_ids[str(oid)] = cid
        status = _PLAN_STATUS.get(int(data.get("state", 1)), S.REJECTED)
        if status is S.ACKNOWLEDGED:
            return []
        return [
            ws.StreamUpdate(
                order=trading.VenueOrderState(
                    cid, f"plan:{plan_id}", status, decimals.ZERO, None, ts
                )
            )
        ]

    def _stream_spec(self, session: MexcSession) -> ws.PrivateStreamSpec:
        """Describes the private stream.

        Args:
            session: Session.

        Returns:
            Spec (login, default personal pushes, ping every 15 s; MEXC
            closes connections without a ping for one minute).
        """

        async def url() -> str:
            """Returns the stream URL."""
            return self._ws_url

        async def on_open(connection: ws.WsConnection) -> None:
            """Logs in and waits for ``rs.login``.

            Args:
                connection: Connection.

            Raises:
                VenuePermissionError: Login refused.
            """
            key = session.credentials.get("api_key")
            now = str(self._now_ms())
            await connection.send(
                json.dumps(
                    {
                        "method": "login",
                        "param": {
                            "apiKey": key,
                            "reqTime": now,
                            "signature": signing.hmac_sha256_hex(
                                session.credentials.get("api_secret"),
                                f"{key}{now}",
                            ),
                        },
                    }
                )
            )
            for _ in range(5):
                reply = mexc_protobuf.loads(await connection.recv())
                if not isinstance(reply, Mapping):
                    continue
                channel = reply.get("channel")
                if channel == "rs.login":
                    return
                if channel == "rs.error":
                    raise errors.VenuePermissionError(
                        "permission missing: MEXC refused the stream login",
                        venue=VENUE,
                    )
            raise errors.AdapterUnavailableError("mexc login timed out")

        async def resync() -> Sequence[ws.StreamUpdate]:
            """Replays the REST snapshot."""
            return await self._resync(session)

        return ws.PrivateStreamSpec(
            name="mexc private stream",
            url=url,
            on_open=on_open,
            parse=lambda frame: self.parse(session, frame),
            resync=resync,
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=15.0,
            idle_timeout_s=60.0,
        )
