# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Interactive Brokers adapter (US stocks and ETFs: Nasdaq, NYSE, Cboe).

Talks to IB Gateway or TWS through ``ib_async`` (BSD-2-Clause, optional
extra ``ibkr``). IB Gateway ports: paper 4002 / live 4001 (TWS: 7497 /
7496); the ``gnzsnz/ib-gateway`` container relays them on 4004 / 4003.

* Contracts: ``Stock(symbol, "SMART", "USD", primaryExchange=...)`` with
  the primary exchange from the instrument's ``listing_exchange`` (NASDAQ,
  NYSE, ARCA, AMEX, and BATS for Cboe BZX), qualified with
  ``qualifyContractsAsync``.
* Orders: MKT, LMT, STP, STP LMT; TIF DAY/GTC/IOC; ``outsideRth``; the
  Joseph client order id travels in ``orderRef`` and is used to find the
  order again after a timeout or restart.
* Events: ``orderStatusEvent``, ``execDetailsEvent``,
  ``commissionReportEvent`` (fills wait briefly for their commission),
  ``errorEvent`` (201 rejected, 202 canceled, 1100/1101/1102
  connectivity, 100 pacing) and ``disconnectedEvent`` (jittered
  reconnect followed by a resync of open/completed orders and
  executions).
* Pacing: at most ``max_messages_per_s`` order-path requests per second
  (IBKR allows 50 messages/s; ``ib_async`` itself throttles at 45/s).

Nothing connects while the deployment gates the adapter (LIVE off or IBKR
not enabled): every call is refused before the client is even created.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import importlib
import logging
import random
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from joseph_execution.adapters.brokers import base
from joseph_execution.adapters.brokers import capabilities as broker_caps
from joseph_execution.domain import accounts
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import errors
from joseph_execution.domain import market
from joseph_execution.ports import market as market_port
from joseph_execution.ports import services
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)
S = enums.OrderState
BROKER = enums.Broker.IBKR.value

_PRIMARY_BY_LISTING = {
    "nasdaq": "NASDAQ",
    "nyse": "NYSE",
    "nyse arca": "ARCA",
    "arca": "ARCA",
    "nyse american": "AMEX",
    "amex": "AMEX",
    "cboe bzx": "BATS",
    "bats": "BATS",
}
_PRIMARY_BY_VENUE = {
    "nasdaq": "NASDAQ",
    "nyse": "NYSE",
    "cboe": "BATS",
}
_VENUE_BY_PRIMARY = {
    "NASDAQ": "nasdaq",
    "ISLAND": "nasdaq",
    "NYSE": "nyse",
    "ARCA": "nyse",
    "AMEX": "nyse",
    "BATS": "cboe",
}
_ORDER_TYPES = {
    enums.OrderType.MARKET: "MKT",
    enums.OrderType.LIMIT: "LMT",
    enums.OrderType.STOP_MARKET: "STP",
    enums.OrderType.STOP_LIMIT: "STP LMT",
}
_TIFS = {
    enums.TimeInForce.DAY: "DAY",
    enums.TimeInForce.GTC: "GTC",
    enums.TimeInForce.IOC: "IOC",
}
_WORKING = frozenset(
    {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}
)
_DONE = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})
_TRANSITIONAL = frozenset({"PendingCancel", "ValidationError", "ApiUpdate"})
_CANCEL_ERRORS = frozenset({135, 161, 10147, 10148})
_ORDER_ERRORS = frozenset({103, 110, 200, 201, 203, 434})
_CANCELED_CODE = 202
_COMPLETED_WINDOW_MS = 12 * 3_600_000
_CANCEL_GRACE_MS = 30_000


# ---------------------------------------------------------------------------
# Structural views of the ib_async objects the adapter reads
# ---------------------------------------------------------------------------


class IbEvent(Protocol):
    """An ``eventkit`` event (``ib.orderStatusEvent`` etc.)."""

    def connect(self, handler: Callable[..., object]) -> object:
        """Subscribes a handler.

        Args:
            handler: Callback.

        Returns:
            The event.
        """
        ...


class IbClient(Protocol):
    """The ``ib_async.IB`` surface used by the adapter."""

    orderStatusEvent: IbEvent  # noqa: N815
    execDetailsEvent: IbEvent  # noqa: N815
    commissionReportEvent: IbEvent  # noqa: N815
    errorEvent: IbEvent  # noqa: N815
    disconnectedEvent: IbEvent  # noqa: N815

    def isConnected(self) -> bool:  # noqa: N802 - ib_async method name.
        """Whether the API connection is ready."""
        ...

    async def connectAsync(  # noqa: N802, PLR0917
        self,
        host: str,
        port: int,
        clientId: int,  # noqa: N803
        timeout: float,  # noqa: ASYNC109 - ib_async signature.
        readonly: bool,
        account: str,
    ) -> object:
        """Connects and synchronizes.

        Args:
            host: Gateway host.
            port: Gateway port.
            clientId: API client id.
            timeout: Seconds.
            readonly: Read-only API.
            account: Main account.

        Returns:
            The client.
        """
        ...

    def disconnect(self) -> object:
        """Disconnects.

        Returns:
            Status text.
        """
        ...

    def managedAccounts(self) -> list[str]:  # noqa: N802
        """Lists the accounts of the login.

        Returns:
            Account numbers.
        """
        ...

    async def qualifyContractsAsync(  # noqa: N802
        self, *contracts: Any
    ) -> list[Any]:
        """Fills in contract details.

        Args:
            *contracts: Contracts.

        Returns:
            Qualified contracts (``None`` when ambiguous or unknown).
        """
        ...

    def placeOrder(self, contract: Any, order: Any) -> Any:  # noqa: N802
        """Places or modifies an order.

        Args:
            contract: Contract.
            order: Order.

        Returns:
            Live-updated trade.
        """
        ...

    def cancelOrder(self, order: Any) -> Any:  # noqa: N802
        """Requests cancellation.

        Args:
            order: Order.

        Returns:
            Trade or ``None``.
        """
        ...

    def trades(self) -> list[Any]:
        """Lists the session's trades.

        Returns:
            Trades.
        """
        ...

    def fills(self) -> list[Any]:
        """Lists the session's fills.

        Returns:
            Fills.
        """
        ...

    def positions(self, account: str = "") -> list[Any]:
        """Lists positions.

        Args:
            account: Account filter.

        Returns:
            Positions.
        """
        ...

    async def accountSummaryAsync(  # noqa: N802
        self, account: str = ""
    ) -> list[Any]:
        """Reads the account summary.

        Args:
            account: Account.

        Returns:
            Account values.
        """
        ...

    def reqAllOpenOrdersAsync(self) -> Awaitable[list[Any]]:  # noqa: N802
        """Requests open orders of every client.

        Returns:
            Awaitable trades.
        """
        ...

    def reqCompletedOrdersAsync(  # noqa: N802
        self,
        apiOnly: bool,  # noqa: N803
    ) -> Awaitable[list[Any]]:
        """Requests today's completed orders.

        Args:
            apiOnly: Only API orders.

        Returns:
            Awaitable trades.
        """
        ...

    def reqExecutionsAsync(  # noqa: N802
        self,
        execFilter: Any = None,  # noqa: N803
    ) -> Awaitable[list[Any]]:
        """Requests executions.

        Args:
            execFilter: Filter.

        Returns:
            Awaitable fills.
        """
        ...


class IbFactory(Protocol):
    """Creates ``ib_async`` objects (fakes implement it in tests)."""

    def client(self) -> IbClient:
        """Creates an ``IB`` client.

        Returns:
            Client.
        """
        ...

    def stock(self, symbol: str, primary_exchange: str) -> Any:
        """Creates a SMART-routed USD stock contract.

        Args:
            symbol: IBKR symbol.
            primary_exchange: Listing exchange (NASDAQ, NYSE, ARCA, AMEX,
                BATS).

        Returns:
            Contract.
        """
        ...

    def order(self, fields: dict[str, Any]) -> Any:
        """Creates an ``Order``.

        Args:
            fields: Order fields.

        Returns:
            Order.
        """
        ...

    def execution_filter(self, account: str) -> Any:
        """Creates an ``ExecutionFilter`` for an account.

        Args:
            account: Account number.

        Returns:
            Filter.
        """
        ...


class IbAsyncFactory:
    """``IbFactory`` backed by the real ``ib_async`` package."""

    def __init__(self) -> None:
        """Imports ``ib_async``.

        Raises:
            AdapterUnavailableError: When the ``ibkr`` extra is missing.
        """
        try:
            self._ib: Any = importlib.import_module("ib_async")
        except ImportError as err:
            raise errors.AdapterUnavailableError(
                "ib_async is not installed (install the 'ibkr' extra)"
            ) from err

    def client(self) -> IbClient:
        """Creates an ``ib_async.IB``.

        Returns:
            Client.
        """
        client: IbClient = self._ib.IB()
        return client

    def stock(self, symbol: str, primary_exchange: str) -> Any:
        """Creates ``ib_async.Stock``.

        Args:
            symbol: Symbol.
            primary_exchange: Primary exchange.

        Returns:
            Contract.
        """
        return self._ib.Stock(
            symbol, "SMART", "USD", primaryExchange=primary_exchange
        )

    def order(self, fields: dict[str, Any]) -> Any:
        """Creates ``ib_async.Order``.

        Args:
            fields: Fields.

        Returns:
            Order.
        """
        return self._ib.Order(**fields)

    def execution_filter(self, account: str) -> Any:
        """Creates ``ib_async.ExecutionFilter``.

        Args:
            account: Account.

        Returns:
            Filter.
        """
        return self._ib.ExecutionFilter(acctCode=account)


# ---------------------------------------------------------------------------
# Pure mappings
# ---------------------------------------------------------------------------


def primary_exchange(listing_exchange: str | None, venue: str) -> str:
    """Maps a listing exchange to the IBKR ``primaryExchange``.

    Args:
        listing_exchange: Instrument ``listing_exchange`` (e.g. ``NYSE
            Arca``), if known.
        venue: Joseph venue (``nasdaq``, ``nyse`` or ``cboe``).

    Returns:
        IBKR primary exchange code.

    Raises:
        AdapterUnavailableError: For venues IBKR is not used for here.
    """
    if listing_exchange:
        code = _PRIMARY_BY_LISTING.get(listing_exchange.strip().lower())
        if code is not None:
            return code
    code = _PRIMARY_BY_VENUE.get(venue)
    if code is None:
        raise errors.AdapterUnavailableError(
            f"Interactive Brokers is not configured for {venue} listings",
            code="UNSUPPORTED",
        )
    return code


def ib_symbol(symbol: str) -> str:
    """Converts a Joseph symbol to IBKR form (share classes use a space).

    Args:
        symbol: Native symbol such as ``BRK.B``.

    Returns:
        IBKR symbol such as ``BRK B``.
    """
    return symbol.replace(".", " ").replace("/", " ")


def joseph_symbol(symbol: str) -> str:
    """Converts an IBKR symbol back to Joseph form.

    Args:
        symbol: IBKR symbol such as ``BRK B``.

    Returns:
        Joseph symbol such as ``BRK.B``.
    """
    return symbol.replace(" ", ".")


def plain(price: decimal.Decimal | None) -> decimal.Decimal | None:
    """Returns a price whose text form has no exponent.

    ``ib_async`` sends ``Decimal`` fields as ``str(value)``; ``6E+2`` would
    reach the gateway verbatim.

    Args:
        price: Price or ``None``.

    Returns:
        Equal price printed in plain notation.
    """
    return None if price is None else decimal.Decimal(decimals.fmt(price))


def order_fields(
    request: trading.VenueOrderRequest, account: str
) -> dict[str, Any]:
    """Maps a venue request to ``ib_async.Order`` fields.

    Args:
        request: Venue request.
        account: IBKR account number.

    Returns:
        Field mapping (prices stay ``Decimal``).

    Raises:
        AdapterUnavailableError: For combinations IBKR is not used with
            here (nothing is sent).
    """
    tif = _TIFS.get(request.time_in_force)
    if tif is None:
        raise errors.AdapterUnavailableError(
            f"time in force {request.time_in_force.value} is not used with "
            "Interactive Brokers (DAY, GTC or IOC)",
            code="UNSUPPORTED_ORDER_TYPE",
        )
    if not decimals.is_multiple(request.quantity_base, decimals.ONE):
        raise errors.AdapterUnavailableError(
            "IBKR stock orders are in whole shares", code="INVALID_LOT_SIZE"
        )
    fields: dict[str, Any] = {
        "action": "BUY" if request.side is enums.OrderSide.BUY else "SELL",
        "totalQuantity": int(request.quantity_base),
        "orderType": _ORDER_TYPES[request.order_type],
        "tif": tif,
        "outsideRth": request.outside_rth,
        "orderRef": request.client_order_id,
        "account": account,
        "transmit": True,
    }
    if request.order_type in (
        enums.OrderType.LIMIT,
        enums.OrderType.STOP_LIMIT,
    ):
        fields["lmtPrice"] = plain(request.limit_price)
    if request.order_type in (
        enums.OrderType.STOP_MARKET,
        enums.OrderType.STOP_LIMIT,
    ):
        fields["auxPrice"] = plain(request.trigger_price)
    return fields


def _last_error(trade: Any) -> int:
    """Returns the error code of the trade's latest log entry.

    Args:
        trade: ``ib_async.Trade``.

    Returns:
        Error code, 0 when none.
    """
    for entry in reversed(list(trade.log)):
        code = int(getattr(entry, "errorCode", 0) or 0)
        if code:
            return code
    return 0


def _last_message(trade: Any) -> str | None:
    """Returns the latest non-empty log message.

    Args:
        trade: ``ib_async.Trade``.

    Returns:
        Message or ``None``.
    """
    for entry in reversed(list(trade.log)):
        if getattr(entry, "message", ""):
            return str(entry.message)
    return None


def map_status(trade: Any, error_code: int = 0) -> enums.OrderState | None:
    """Maps an IBKR order status to a venue state.

    ``ib_async`` marks an order whose placement failed as ``Cancelled``
    with the error code in the trade log, so a ``Cancelled`` order with a
    non-202 error is a rejection.

    Args:
        trade: ``ib_async.Trade``.
        error_code: Error recorded by the adapter for the order, if any.

    Returns:
        State, or ``None`` for transitional statuses (PendingCancel,
        ValidationError) that must not be reported.
    """
    status = str(trade.orderStatus.status)
    filled = exact_filled(trade)
    if status in _WORKING:
        return S.PARTIALLY_FILLED if filled > 0 else S.ACKNOWLEDGED
    if status == "Filled":
        return S.FILLED
    if status in ("Cancelled", "ApiCancelled"):
        code = _last_error(trade) or error_code
        if filled == 0 and code and code != _CANCELED_CODE:
            return S.REJECTED
        return S.CANCELED
    if status == "Inactive":
        return S.REJECTED
    return None


def exact_filled(trade: Any) -> decimal.Decimal:
    """Returns the filled quantity of a trade.

    Args:
        trade: ``ib_async.Trade``.

    Returns:
        Shares filled.
    """
    return base.exact_decimal(float(trade.orderStatus.filled or 0.0))


def venue_state(
    trade: Any, now_ms: int, error_code: int = 0
) -> trading.VenueOrderState | None:
    """Builds the venue state of a trade.

    Args:
        trade: ``ib_async.Trade``.
        now_ms: Report time.
        error_code: Recorded error, if any.

    Returns:
        State, or ``None`` for transitional statuses.
    """
    status = map_status(trade, error_code)
    if status is None:
        return None
    order = trade.order
    exchange_id = order.permId or order.orderId
    return trading.VenueOrderState(
        client_order_id=str(order.orderRef),
        exchange_order_id=str(exchange_id) if exchange_id else None,
        status=status,
        filled_base=exact_filled(trade),
        reason=_last_message(trade)
        if status in (S.REJECTED, S.CANCELED)
        else None,
        ts_ms=now_ms,
    )


def fill_id(exec_id: str) -> str:
    """Returns a stable fill id; execution corrections share it.

    IBKR corrects an execution by re-sending it with the last segment of
    the execution id incremented. Keying on the id without that segment
    ignores corrections instead of double counting them.

    Args:
        exec_id: IBKR execution id such as ``0000e0d5.65f1a2b3.01.01``.

    Returns:
        Joseph fill id.
    """
    head, dot, _ = exec_id.rpartition(".")
    return f"ibkr-{head if dot and head.count('.') >= 2 else exec_id}"


def venue_fill(fill: Any) -> trading.VenueFill:
    """Maps an ``ib_async.Fill`` to a venue fill.

    Args:
        fill: Fill (commission 0 until the report arrives).

    Returns:
        Venue fill.
    """
    execution = fill.execution
    report = fill.commissionReport
    has_report = bool(getattr(report, "execId", ""))
    commission = float(getattr(report, "commission", 0.0) or 0.0)
    moment = getattr(execution, "time", None) or fill.time
    return trading.VenueFill(
        exchange_fill_id=fill_id(str(execution.execId)),
        client_order_id=str(execution.orderRef),
        price=base.exact_decimal(float(execution.price)),
        quantity_base=base.exact_decimal(float(execution.shares)),
        fee=base.exact_decimal(commission) if has_report else decimals.ZERO,
        fee_asset=str(getattr(report, "currency", "") or "USD"),
        liquidity="maker"
        if int(execution.lastLiquidity or 0) == 1
        else "taker",
        ts_ms=base.to_ms(moment.timestamp()),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IbkrConfig:
    """Connection and behaviour settings.

    Attributes:
        host: IB Gateway/TWS host.
        port: API port (gateway paper 4002 / live 4001).
        client_id: API client id (unique per connection).
        simulation: The login is the IBKR paper account.
        short_selling: The account may short (margin accounts only).
        readonly: Connect read-only (orders refused by the gateway).
        connect_timeout_s: Connection and sync timeout.
        ack_timeout_s: Wait for an order acknowledgement before
            reporting the outcome as unknown.
        max_messages_per_s: Order-path pacing (IBKR limit 50/s).
        reconnect_base_s: First reconnect delay (doubles, jittered).
        reconnect_max_s: Maximum reconnect delay.
        commission_wait_s: Wait for a fill's commission report.
    """

    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 17
    simulation: bool = True
    short_selling: bool = False
    readonly: bool = False
    connect_timeout_s: float = 10.0
    ack_timeout_s: float = 3.0
    max_messages_per_s: int = 40
    reconnect_base_s: float = 1.0
    reconnect_max_s: float = 60.0
    commission_wait_s: float = 2.0


@dataclasses.dataclass(slots=True)
class _Tracked:
    """An order this adapter placed or resolved.

    Attributes:
        account_id: Joseph account.
        trade: ``ib_async.Trade``.
        instrument_id: Joseph instrument id.
    """

    account_id: str
    trade: Any
    instrument_id: str


class IbkrAdapter:
    """``TradingAdapter`` for Interactive Brokers.

    Attributes:
        calls: Methods invoked (safety tests assert none while gated).
    """

    def __init__(  # noqa: PLR0917
        self,
        config: IbkrConfig,
        factory: Callable[[], IbFactory],
        market_data: market_port.MarketDataGateway,
        clock: services.Clock,
        account_lookup: base.AccountLookup,
        gate_reason: str | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            config: Settings.
            factory: Creates the ``ib_async`` factory (called on first
                connect only).
            market_data: Market gateway (listing exchange of instruments).
            clock: Time source.
            account_lookup: Joseph account id -> IBKR account number.
            gate_reason: When set, the adapter is gated: nothing connects
                and every call is refused with this reason.
        """
        self._config = config
        self._factory_fn = factory
        self._factory: IbFactory | None = None
        self._market = market_data
        self._clock = clock
        self._lookup = account_lookup
        self._gate = gate_reason
        self._documented = broker_caps.ibkr(
            short_selling=config.short_selling, simulation=config.simulation
        )
        self._ib: IbClient | None = None
        self._health = enums.HealthState.UNAVAILABLE
        self._connect_lock = asyncio.Lock()
        self._pacer = base.Pacer(config.max_messages_per_s, 1.0)
        self._accounts: dict[str, str] = {}
        self._tracked: dict[str, _Tracked] = {}
        self._contracts: dict[str, Any] = {}
        self._instruments: dict[int, str] = {}
        self._order_errors: dict[int, tuple[int, str]] = {}
        self._cancel_errors: dict[int, str] = {}
        self._cancel_requested: dict[str, int] = {}
        self._pending_fills: dict[str, Any] = {}
        self._events = base.EventBuffer()
        self._reconnect: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        """Returns ``ibkr``."""
        return BROKER

    # -- connection ------------------------------------------------------

    def _require_open(self, method: str) -> None:
        """Records a call and refuses it while gated.

        Args:
            method: Method name.

        Raises:
            AdapterUnavailableError: ``LIVE_TRADING_DISABLED`` when gated.
        """
        self.calls.append(method)
        if self._gate is not None:
            raise base.gated_error(BROKER, self._gate)

    def _broker_account(self, account_id: str) -> str:
        """Resolves (and remembers) the IBKR account of a Joseph account.

        Args:
            account_id: Joseph account.

        Returns:
            IBKR account number.

        Raises:
            AdapterUnavailableError: When none is bound.
        """
        number = self._accounts.get(account_id)
        if number is None:
            number = self._lookup(account_id)
            if not number:
                raise errors.AdapterUnavailableError(
                    "no IBKR account number is connected to this account"
                )
            self._accounts[account_id] = number
        return number

    def bind(self, account_id: str) -> bool:
        """Binds a Joseph account so broker events can be routed to it.

        Args:
            account_id: Joseph account.

        Returns:
            True when an IBKR account number is known.
        """
        if self._gate is not None:
            return False
        try:
            self._broker_account(account_id)
        except errors.AdapterUnavailableError:
            return False
        return True

    async def _connected(self) -> IbClient:
        """Returns a connected client, connecting when needed.

        Returns:
            Client.

        Raises:
            AdapterUnavailableError: When the gateway cannot be reached.
        """
        async with self._connect_lock:
            ib = self._ib
            if ib is not None and ib.isConnected():
                return ib
            if self._factory is None:
                self._factory = self._factory_fn()
            if ib is None:
                ib = self._factory.client()
                self._attach(ib)
                self._ib = ib
            cfg = self._config
            try:
                await asyncio.wait_for(
                    ib.connectAsync(
                        cfg.host,
                        cfg.port,
                        clientId=cfg.client_id,
                        timeout=cfg.connect_timeout_s,
                        readonly=cfg.readonly,
                        account="",
                    ),
                    cfg.connect_timeout_s + 1,
                )
            except (OSError, TimeoutError, ConnectionError) as err:
                self._health = enums.HealthState.UNAVAILABLE
                raise errors.AdapterUnavailableError(
                    f"IB Gateway unreachable at {cfg.host}:{cfg.port} "
                    f"({type(err).__name__})"
                ) from err
            self._health = enums.HealthState.HEALTHY
        await self._resync(ib)
        return ib

    async def _session(
        self, method: str, account_id: str
    ) -> tuple[IbClient, str]:
        """Opens a gated, connected session for a Joseph account.

        Args:
            method: Calling method (recorded).
            account_id: Joseph account.

        Returns:
            ``(client, IBKR account)``.
        """
        self._require_open(method)
        number = self._broker_account(account_id)
        return await self._connected(), number

    def _attach(self, ib: IbClient) -> None:
        """Subscribes the adapter to a client's events.

        Args:
            ib: Client.
        """
        ib.orderStatusEvent.connect(self._on_status)
        ib.execDetailsEvent.connect(self._on_exec)
        ib.commissionReportEvent.connect(self._on_commission)
        ib.errorEvent.connect(self._on_error)
        ib.disconnectedEvent.connect(self._on_disconnected)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        """Runs a background task, keeping a reference until it ends.

        Args:
            coro: Coroutine.
        """
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_disconnected(self) -> None:
        """Marks the adapter down and schedules a reconnect."""
        self._health = enums.HealthState.UNAVAILABLE
        if self._closed or self._gate is not None:
            return
        if self._reconnect is None or self._reconnect.done():
            self._reconnect = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnects with jittered exponential backoff."""
        delay = self._config.reconnect_base_s
        while not self._closed:
            jitter = 0.5 + random.random() / 2  # noqa: S311 - not crypto.
            await asyncio.sleep(delay * jitter)
            try:
                await self._connected()
            except errors.AdapterUnavailableError as err:
                _LOG.warning("IBKR reconnect failed: %s", err.message)
                delay = min(delay * 2, self._config.reconnect_max_s)
                continue
            _LOG.info("IBKR reconnected")
            return

    async def close(self) -> None:
        """Disconnects and stops reconnecting."""
        self._closed = True
        if self._reconnect is not None:
            self._reconnect.cancel()
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    # -- event handlers ----------------------------------------------------

    def _route(self, client_order_id: str, ib_account: str) -> str | None:
        """Finds the Joseph account an IBKR order belongs to.

        Args:
            client_order_id: ``orderRef``.
            ib_account: IBKR account number.

        Returns:
            Joseph account, or ``None`` for foreign or unbound orders.
        """
        if not base.is_joseph_client_id(client_order_id):
            return None
        tracked = self._tracked.get(client_order_id)
        if tracked is not None:
            return tracked.account_id
        for joseph, number in self._accounts.items():
            if number == ib_account:
                return joseph
        return None

    def _emit_state(self, trade: Any) -> None:
        """Buffers the state of a trade for its Joseph account.

        Args:
            trade: ``ib_async.Trade``.
        """
        cid = str(trade.order.orderRef)
        account = self._route(cid, str(trade.order.account))
        if account is None:
            return
        code = self._order_errors.get(int(trade.order.orderId), (0, ""))[0]
        state = venue_state(trade, self._clock.now_ms(), code)
        if state is not None:
            self._events.order(account, state)

    def _on_status(self, trade: Any) -> None:
        """Handles ``orderStatusEvent``.

        Args:
            trade: ``ib_async.Trade``.
        """
        self._emit_state(trade)

    def _on_exec(self, trade: Any, fill: Any) -> None:
        """Handles ``execDetailsEvent``: waits briefly for the commission.

        Args:
            trade: ``ib_async.Trade``.
            fill: ``ib_async.Fill``.
        """
        del trade
        exec_id = str(fill.execution.execId)
        self._pending_fills[exec_id] = fill
        asyncio.get_running_loop().call_later(
            self._config.commission_wait_s, self._flush_fill, exec_id
        )

    def _on_commission(self, trade: Any, fill: Any, report: Any) -> None:
        """Handles ``commissionReportEvent``: emits the fill with its fee.

        Args:
            trade: ``ib_async.Trade``.
            fill: ``ib_async.Fill`` (its report is updated in place).
            report: ``ib_async.CommissionReport``.
        """
        del trade, report
        exec_id = str(fill.execution.execId)
        self._pending_fills[exec_id] = fill
        self._flush_fill(exec_id)

    def _flush_fill(self, exec_id: str) -> None:
        """Emits a pending fill (with whatever commission is known).

        Args:
            exec_id: Execution id.
        """
        fill = self._pending_fills.pop(exec_id, None)
        if fill is None:
            return
        execution = fill.execution
        account = self._route(
            str(execution.orderRef), str(execution.acctNumber)
        )
        if account is not None:
            self._events.fill(account, venue_fill(fill))

    def _on_error(
        self, req_id: int, code: int, message: str, contract: Any
    ) -> None:
        """Handles ``errorEvent`` (order errors and connectivity codes).

        Args:
            req_id: Request or order id (-1 when not order related).
            code: TWS API error code.
            message: Error text.
            contract: Related contract, if any.
        """
        del contract
        if code == 1100:
            self._health = enums.HealthState.UNAVAILABLE
        elif code in (1101, 1102):
            self._health = enums.HealthState.HEALTHY
            if self._ib is not None:
                self._spawn(self._resync(self._ib))
        elif code in (100, 2110):
            self._health = enums.HealthState.DEGRADED
        elif code in (502, 504, 1300):
            self._health = enums.HealthState.UNAVAILABLE
        elif code in _CANCEL_ERRORS:
            self._cancel_errors[req_id] = message
        elif code in _ORDER_ERRORS or code == _CANCELED_CODE:
            self._order_errors[req_id] = (code, message)
            trade = self._trade_by_order_id(req_id)
            if trade is not None:
                self._emit_state(trade)
        _LOG.info("IBKR message %s (req %s): %s", code, req_id, message)

    def _trade_by_order_id(self, order_id: int) -> Any | None:
        """Finds a tracked trade by IBKR order id.

        Args:
            order_id: Order id.

        Returns:
            Trade or ``None``.
        """
        for tracked in self._tracked.values():
            if int(tracked.trade.order.orderId) == order_id:
                return tracked.trade
        return None

    # -- sync ----------------------------------------------------------------

    def _find(self, ib: IbClient, client_order_id: str) -> Any | None:
        """Finds a trade by ``orderRef``.

        Args:
            ib: Client.
            client_order_id: Client id.

        Returns:
            Trade or ``None``.
        """
        tracked = self._tracked.get(client_order_id)
        if tracked is not None:
            return tracked.trade
        for trade in ib.trades():
            if str(trade.order.orderRef) == client_order_id:
                return trade
        return None

    async def _resync(self, ib: IbClient) -> int:
        """Re-reads open/completed orders and executions; emits changes.

        Args:
            ib: Client.

        Returns:
            Events buffered.
        """
        try:
            await self._pacer.acquire()
            await ib.reqAllOpenOrdersAsync()
            await self._pacer.acquire()
            await ib.reqCompletedOrdersAsync(True)
            await self._pacer.acquire()
            await ib.reqExecutionsAsync(None)
        except (OSError, ConnectionError, TimeoutError) as err:
            _LOG.warning("IBKR resync failed: %s", type(err).__name__)
            return 0
        emitted = 0
        for trade in ib.trades():
            cid = str(trade.order.orderRef)
            account = self._route(cid, str(trade.order.account))
            if account is None:
                continue
            code = self._order_errors.get(int(trade.order.orderId), (0, ""))[0]
            state = venue_state(trade, self._clock.now_ms(), code)
            if state is not None and self._events.order(account, state):
                emitted += 1
        for fill in ib.fills():
            execution = fill.execution
            account = self._route(
                str(execution.orderRef), str(execution.acctNumber)
            )
            if account is not None and self._events.fill(
                account, venue_fill(fill)
            ):
                emitted += 1
        return emitted

    async def resync(self) -> int:
        """Resynchronizes when connected (periodic safety net).

        Returns:
            Events buffered.
        """
        if self._gate is not None or self._ib is None:
            return 0
        if not self._ib.isConnected():
            return 0
        return await self._resync(self._ib)

    def event_accounts(self) -> list[str]:
        """Lists Joseph accounts with buffered events.

        Returns:
            Account ids.
        """
        return self._events.account_ids()

    def drain_events(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns and clears buffered events of an account.

        Args:
            account_id: Joseph account.

        Returns:
            Events.
        """
        return self._events.drain(account_id)

    # -- TradingAdapter --------------------------------------------------

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities (connects when allowed).

        Args:
            account_id: Joseph account.

        Returns:
            AVAILABLE capabilities, or TRADING_UNAVAILABLE with the reason.
        """
        if self._gate is not None:
            return base.unavailable(self._documented, self._gate)
        try:
            ib, number = await self._session("capabilities", account_id)
        except errors.AdapterUnavailableError as err:
            return base.unavailable(self._documented, err.message)
        if number not in ib.managedAccounts():
            return base.unavailable(
                self._documented,
                f"IB Gateway does not manage account {base.mask(number)}.",
            )
        if self._config.readonly:
            return base.unavailable(
                self._documented, "IB Gateway API is connected read-only."
            )
        return self._documented

    async def health(self) -> enums.HealthState:
        """Returns connection health."""
        if self._gate is not None:
            return enums.HealthState.UNAVAILABLE
        return self._health

    async def account_snapshot(
        self, account_id: str
    ) -> trading.VenueAccountSnapshot:
        """Reads NetLiquidation, AvailableFunds and settled cash.

        Args:
            account_id: Joseph account.

        Returns:
            Snapshot in the account's base currency.

        Raises:
            AdapterUnavailableError: When the summary is incomplete.
        """
        ib, number = await self._session("account_snapshot", account_id)
        values = await ib.accountSummaryAsync(number)
        rows = {
            str(v.tag): (str(v.value), str(v.currency))
            for v in values
            if str(v.account) == number
        }
        if "NetLiquidation" not in rows or "AvailableFunds" not in rows:
            raise errors.AdapterUnavailableError(
                "IBKR account summary is incomplete"
            )
        currency = rows["NetLiquidation"][1] or "USD"

        def amount(tag: str) -> decimal.Decimal | None:
            """Parses one summary value.

            Args:
                tag: Summary tag.

            Returns:
                Decimal or ``None``.
            """
            row = rows.get(tag)
            return None if row is None else decimals.to_optional(row[0])

        equity = amount("NetLiquidation")
        available = amount("AvailableFunds")
        assert equity is not None and available is not None
        settled = amount("SettledCash")
        total = amount("TotalCashValue")
        unsettled = (
            None if settled is None or total is None else total - settled
        )
        return trading.VenueAccountSnapshot(
            equity=equity,
            available_margin=available,
            currency=currency,
            ts_ms=self._clock.now_ms(),
            settled_cash=settled,
            unsettled_cash=unsettled,
        )

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[trading.VenueOrderState]:
        """Lists working Joseph orders of the account.

        Args:
            account_id: Joseph account.

        Returns:
            States.
        """
        ib, number = await self._session("list_open_orders", account_id)
        now = self._clock.now_ms()
        result = []
        for trade in ib.trades():
            if str(trade.order.account) != number or not (
                base.is_joseph_client_id(str(trade.order.orderRef))
            ):
                continue
            if str(trade.orderStatus.status) in _DONE:
                continue
            state = venue_state(trade, now)
            if state is not None:
                result.append(state)
        return result

    def _instrument_for(self, contract: Any) -> str | None:
        """Maps an IBKR contract to a Joseph instrument id.

        Args:
            contract: ``ib_async.Contract``.

        Returns:
            Instrument id, or ``None`` outside Nasdaq/NYSE/Cboe BZX.
        """
        known = self._instruments.get(int(contract.conId or 0))
        if known is not None:
            return known
        venue = _VENUE_BY_PRIMARY.get(str(contract.primaryExchange).upper())
        if venue is None or str(contract.secType) != "STK":
            return None
        return f"{venue}:stock:{joseph_symbol(str(contract.symbol))}"

    async def list_positions(
        self, account_id: str
    ) -> Sequence[trading.VenuePosition]:
        """Lists stock positions of the account (shares).

        Args:
            account_id: Joseph account.

        Returns:
            Positions on Nasdaq/NYSE/Cboe BZX (ETFs traded through Joseph
            keep their ``etf`` id; others default to ``stock``).
        """
        ib, number = await self._session("list_positions", account_id)
        result = []
        for pos in ib.positions(number):
            instrument_id = self._instrument_for(pos.contract)
            if instrument_id is None:
                continue
            result.append(
                trading.VenuePosition(
                    instrument_id=instrument_id,
                    signed_qty=base.exact_decimal(float(pos.position)),
                    entry_price=base.exact_decimal(float(pos.avgCost)),
                )
            )
        return result

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[trading.VenueFill]:
        """Lists Joseph fills of the account since a time.

        Args:
            account_id: Joseph account.
            since_ms: Lower bound.

        Returns:
            Fills.
        """
        ib, number = await self._session("list_recent_fills", account_id)
        result = []
        for fill in ib.fills():
            execution = fill.execution
            if str(execution.acctNumber) != number or not (
                base.is_joseph_client_id(str(execution.orderRef))
            ):
                continue
            mapped = venue_fill(fill)
            if mapped.ts_ms >= since_ms:
                result.append(mapped)
        return result

    async def _contract(self, instrument_id: str) -> Any:
        """Builds and qualifies the SMART stock contract of an instrument.

        Args:
            instrument_id: Joseph instrument id.

        Returns:
            Qualified contract.

        Raises:
            AdapterUnavailableError: When IBKR cannot identify it.
        """
        cached = self._contracts.get(instrument_id)
        if cached is not None:
            return cached
        ref = equities.parse_instrument_id(instrument_id)
        if ref.market_type not in equities.SECURITY_TYPES:
            raise errors.AdapterUnavailableError(
                f"{instrument_id} is not a stock or ETF", code="UNSUPPORTED"
            )
        meta: market.InstrumentMeta | None
        try:
            meta = await self._market.instrument(instrument_id)
        except errors.ExecutionError:
            meta = None
        primary = primary_exchange(
            None if meta is None else meta.listing_exchange, ref.venue
        )
        assert self._factory is not None and self._ib is not None
        contract = self._factory.stock(ib_symbol(ref.symbol), primary)
        await self._pacer.acquire()
        qualified = await self._ib.qualifyContractsAsync(contract)
        result = qualified[0] if qualified else None
        if result is None or isinstance(result, list):
            raise errors.AdapterUnavailableError(
                f"IBKR could not identify {instrument_id} on {primary}",
                code="UNSUPPORTED",
            )
        self._contracts[instrument_id] = result
        self._instruments[int(result.conId or 0)] = instrument_id
        return result

    async def _await(
        self,
        trade: Any,
        done: Callable[[Any], bool],
        failed: Callable[[], str | None] = lambda: None,
    ) -> bool:
        """Waits for a trade condition until the acknowledgement timeout.

        Args:
            trade: Trade.
            done: Condition on the trade.
            failed: Returns an error text that stops the wait.

        Returns:
            True when the condition held; False on timeout.

        Raises:
            ExecutionError: ``CONFLICT`` when ``failed`` reports an error.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.ack_timeout_s
        while True:
            if done(trade):
                return True
            problem = failed()
            if problem is not None:
                raise errors.ExecutionError("CONFLICT", problem)
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.02)

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Places an order (idempotent by ``orderRef``).

        Args:
            request: Venue request.

        Returns:
            Result once IBKR acknowledges or rejects it.

        Raises:
            AdapterUnavailableError: Refused before sending.
            AdapterTimeoutError: Sent, but the outcome is unknown.
        """
        ib, number = await self._session("submit_order", request.account_id)
        cid = request.client_order_id
        now = self._clock.now_ms()
        existing = self._find(ib, cid)
        if existing is not None:
            state = venue_state(existing, now)
            if state is None:
                raise errors.AdapterTimeoutError("IBKR order is transitional")
            return trading.VenueSubmitResult(
                state.status is not S.REJECTED, state
            )
        fields = order_fields(request, number)
        contract = await self._contract(request.instrument_id)
        assert self._factory is not None
        order = self._factory.order(fields)
        await self._pacer.acquire()
        try:
            trade = ib.placeOrder(contract, order)
        except Exception as err:
            raise errors.AdapterTimeoutError(
                f"IBKR placeOrder raised {type(err).__name__}"
            ) from err
        self._tracked[cid] = _Tracked(
            request.account_id, trade, request.instrument_id
        )
        acknowledged = await self._await(
            trade, lambda t: str(t.orderStatus.status) != "PendingSubmit"
        )
        state = venue_state(trade, self._clock.now_ms())
        if not acknowledged or state is None:
            raise errors.AdapterTimeoutError(
                "IBKR did not acknowledge the order in time"
            )
        self._events.order(request.account_id, state)
        return trading.VenueSubmitResult(state.status is not S.REJECTED, state)

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState:
        """Cancels an order and waits for IBKR's answer.

        Args:
            account_id: Joseph account.
            client_order_id: Client id.

        Returns:
            State after the cancel (FILLED on a race).

        Raises:
            ExecutionError: ``NOT_FOUND`` or ``CONFLICT`` (IBKR refused).
            AdapterTimeoutError: No answer in time.
        """
        ib, _ = await self._session("cancel_order", account_id)
        trade = self._find(ib, client_order_id)
        if trade is None:
            raise errors.ExecutionError("NOT_FOUND", "unknown IBKR order")
        order_id = int(trade.order.orderId)
        if str(trade.orderStatus.status) not in _DONE:
            self._cancel_requested[client_order_id] = self._clock.now_ms()
            self._cancel_errors.pop(order_id, None)
            await self._pacer.acquire()
            ib.cancelOrder(trade.order)
            settled = await self._await(
                trade,
                lambda t: str(t.orderStatus.status) in _DONE,
                lambda: self._cancel_errors.get(order_id),
            )
            if not settled:
                raise errors.AdapterTimeoutError("IBKR cancel outcome unknown")
        state = venue_state(trade, self._clock.now_ms())
        assert state is not None
        return state

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Modifies a working order (re-place with the same order id).

        Args:
            account_id: Joseph account.
            client_order_id: Client id.
            limit_price: New limit price.
            trigger_price: New stop price.
            quantity_base: New total quantity.

        Returns:
            State after the modification.

        Raises:
            ExecutionError: ``NOT_FOUND`` or ``CONFLICT``.
        """
        ib, _ = await self._session("amend_order", account_id)
        trade = self._find(ib, client_order_id)
        if trade is None:
            raise errors.ExecutionError("NOT_FOUND", "unknown IBKR order")
        if str(trade.orderStatus.status) not in _WORKING:
            raise errors.ExecutionError("CONFLICT", "IBKR order is not working")
        order = trade.order
        if limit_price is not None:
            order.lmtPrice = plain(limit_price)
        if trigger_price is not None:
            order.auxPrice = plain(trigger_price)
        if quantity_base is not None:
            order.totalQuantity = int(quantity_base)
        order_id = int(order.orderId)
        self._order_errors.pop(order_id, None)
        marker = len(list(trade.log))
        await self._pacer.acquire()
        ib.placeOrder(trade.contract, order)
        await self._await(
            trade,
            lambda t: len(list(t.log)) > marker,
            lambda: (
                None
                if order_id not in self._order_errors
                else self._order_errors[order_id][1]
            ),
        )
        state = venue_state(trade, self._clock.now_ms())
        if state is None:
            raise errors.ExecutionError(
                "CONFLICT", "IBKR order is transitional"
            )
        return state

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Finds an order by ``orderRef`` after refreshing from IBKR.

        Absence is reported only for orders young enough to be covered by
        IBKR's open and completed-order lists; otherwise the outcome stays
        unknown (the OMS retries, then flags manual intervention).

        Args:
            account_id: Joseph account.
            client_order_id: Client id.

        Returns:
            State, or ``None`` when IBKR confirms it never had the order.

        Raises:
            AdapterTimeoutError: When IBKR cannot confirm either way.
        """
        ib, _ = await self._session("get_order_by_client_id", account_id)
        await self._resync(ib)
        trade = self._find(ib, client_order_id)
        now = self._clock.now_ms()
        if trade is None:
            created = base.client_order_ms(client_order_id)
            if created is None or now - created > _COMPLETED_WINDOW_MS:
                raise errors.AdapterTimeoutError(
                    "IBKR cannot confirm the absence of an older order"
                )
            return None
        self._tracked.setdefault(
            client_order_id,
            _Tracked(
                account_id, trade, self._instrument_for(trade.contract) or ""
            ),
        )
        requested = self._cancel_requested.get(client_order_id)
        status = str(trade.orderStatus.status)
        if status in _TRANSITIONAL or (
            requested is not None
            and status in _WORKING
            and now - requested < _CANCEL_GRACE_MS
        ):
            raise errors.AdapterTimeoutError("IBKR cancel still pending")
        state = venue_state(trade, now)
        if state is None:
            raise errors.AdapterTimeoutError("IBKR order is transitional")
        return state

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams buffered events of an account.

        Args:
            account_id: Joseph account.

        Yields:
            Events.
        """
        self._require_open("stream_account_events")
        async for event in self._events.stream(account_id):
            yield event
