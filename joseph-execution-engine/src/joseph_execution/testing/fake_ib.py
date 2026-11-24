# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""In-memory stand-in for ``ib_async.IB`` used by tests (no network).

It mirrors the ``ib_async`` behaviours the adapter depends on: trades are
live-updated objects, placement errors mark the trade ``Cancelled`` with
the error code in its log, cancels go through ``PendingCancel``, and
events are ``eventkit``-style objects with ``connect``/``emit``.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Callable
from typing import Any

_EPOCH = datetime.datetime(2026, 11, 23, tzinfo=datetime.UTC)


class FakeEvent:
    """Minimal ``eventkit.Event``.

    Attributes:
        handlers: Connected callbacks.
    """

    def __init__(self) -> None:
        """Initializes an event without handlers."""
        self.handlers: list[Callable[..., object]] = []

    def connect(self, handler: Callable[..., object]) -> FakeEvent:
        """Connects a handler.

        Args:
            handler: Callback.

        Returns:
            The event.
        """
        self.handlers.append(handler)
        return self

    def emit(self, *args: Any) -> None:
        """Calls every handler.

        Args:
            *args: Event arguments.
        """
        for handler in list(self.handlers):
            handler(*args)


@dataclasses.dataclass
class FakeContract:
    """``ib_async.Contract``.

    Attributes:
        symbol: Symbol.
        primaryExchange: Primary exchange.
        secType: Security type.
        exchange: Routing exchange.
        currency: Currency.
        conId: Contract id.
    """

    symbol: str
    primaryExchange: str = ""  # noqa: N815
    secType: str = "STK"  # noqa: N815
    exchange: str = "SMART"
    currency: str = "USD"
    conId: int = 0  # noqa: N815


@dataclasses.dataclass
class FakeOrder:
    """``ib_async.Order``.

    Attributes:
        action: BUY or SELL.
        totalQuantity: Quantity.
        orderType: MKT, LMT, STP, STP LMT.
        tif: Time in force.
        outsideRth: Outside regular hours.
        orderRef: Client reference.
        account: Account.
        transmit: Transmit flag.
        lmtPrice: Limit price.
        auxPrice: Stop price.
        orderId: Order id.
        permId: Permanent id.
    """

    action: str = ""
    totalQuantity: float = 0.0  # noqa: N815
    orderType: str = ""  # noqa: N815
    tif: str = ""
    outsideRth: bool = False  # noqa: N815
    orderRef: str = ""  # noqa: N815
    account: str = ""
    transmit: bool = True
    lmtPrice: Any = None  # noqa: N815
    auxPrice: Any = None  # noqa: N815
    orderId: int = 0  # noqa: N815
    permId: int = 0  # noqa: N815


@dataclasses.dataclass
class FakeOrderStatus:
    """``ib_async.OrderStatus``.

    Attributes:
        status: Status text.
        filled: Filled quantity.
        remaining: Remaining quantity.
    """

    status: str = "PendingSubmit"
    filled: float = 0.0
    remaining: float = 0.0


@dataclasses.dataclass
class FakeLogEntry:
    """``ib_async.TradeLogEntry``.

    Attributes:
        status: Status.
        message: Message.
        errorCode: Error code.
    """

    status: str
    message: str = ""
    errorCode: int = 0  # noqa: N815


@dataclasses.dataclass
class FakeTrade:
    """``ib_async.Trade``.

    Attributes:
        contract: Contract.
        order: Order.
        orderStatus: Status.
        log: Log entries.
    """

    contract: FakeContract
    order: FakeOrder
    orderStatus: FakeOrderStatus  # noqa: N815
    log: list[FakeLogEntry] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class FakeExecution:
    """``ib_async.Execution``.

    Attributes:
        execId: Execution id.
        acctNumber: Account.
        shares: Shares.
        price: Price.
        orderRef: Client reference.
        orderId: Order id.
        lastLiquidity: 1 added, 2 removed.
        time: Execution time.
    """

    execId: str  # noqa: N815
    acctNumber: str  # noqa: N815
    shares: float
    price: float
    orderRef: str  # noqa: N815
    orderId: int  # noqa: N815
    lastLiquidity: int = 2  # noqa: N815
    time: datetime.datetime = _EPOCH


@dataclasses.dataclass
class FakeCommission:
    """``ib_async.CommissionReport``.

    Attributes:
        execId: Execution id (empty until reported).
        commission: Commission.
        currency: Currency.
    """

    execId: str = ""  # noqa: N815
    commission: float = 0.0
    currency: str = ""


@dataclasses.dataclass
class FakeFill:
    """``ib_async.Fill``.

    Attributes:
        contract: Contract.
        execution: Execution.
        commissionReport: Commission report.
        time: Time.
    """

    contract: FakeContract
    execution: FakeExecution
    commissionReport: FakeCommission  # noqa: N815
    time: datetime.datetime = _EPOCH


@dataclasses.dataclass
class FakePosition:
    """``ib_async.Position``.

    Attributes:
        account: Account.
        contract: Contract.
        position: Signed shares.
        avgCost: Average cost.
    """

    account: str
    contract: FakeContract
    position: float
    avgCost: float  # noqa: N815


@dataclasses.dataclass
class FakeAccountValue:
    """``ib_async.AccountValue``.

    Attributes:
        account: Account.
        tag: Tag.
        value: Value text.
        currency: Currency.
    """

    account: str
    tag: str
    value: str
    currency: str


class FakeIB:
    """Fake ``ib_async.IB`` (see the module docstring).

    Attributes:
        managed: Accounts of the login.
        connect_failures: Connection attempts that fail before success.
        ack: Status after placement (``None`` keeps PendingSubmit).
        reject_next: ``(code, message)`` rejecting the next placement.
        cancel_mode: ``ok``, ``error`` (10148) or ``hold``.
        unknown_symbols: Symbols that cannot be qualified.
        summary: Account summary values.
        position_rows: Positions.
        connects: Connection attempts.
        placed: Orders placed (including modifications).
        canceled: Cancel requests.
        requests: Sync requests issued.
    """

    def __init__(self, managed: tuple[str, ...] = ("DU1234567",)) -> None:
        """Initializes a disconnected fake.

        Args:
            managed: Managed accounts.
        """
        self.orderStatusEvent = FakeEvent()
        self.execDetailsEvent = FakeEvent()
        self.commissionReportEvent = FakeEvent()
        self.errorEvent = FakeEvent()
        self.disconnectedEvent = FakeEvent()
        self.managed = list(managed)
        self.connect_failures = 0
        self.ack: str | None = "Submitted"
        self.reject_next: tuple[int, str] | None = None
        self.cancel_mode = "ok"
        self.unknown_symbols: set[str] = set()
        self.summary: list[FakeAccountValue] = []
        self.position_rows: list[FakePosition] = []
        self.connects: list[dict[str, Any]] = []
        self.placed: list[FakeOrder] = []
        self.canceled: list[int] = []
        self.requests: list[str] = []
        self._connected = False
        self._trades: list[FakeTrade] = []
        self._fills: list[FakeFill] = []
        self._next_id = 1
        self._exec = 0

    # -- connection ------------------------------------------------------

    def isConnected(self) -> bool:  # noqa: N802
        """Whether connected.

        Returns:
            Flag.
        """
        return self._connected

    async def connectAsync(  # noqa: N802, PLR0917
        self,
        host: str,
        port: int,
        clientId: int,  # noqa: N803
        timeout: float,  # noqa: ASYNC109
        readonly: bool,
        account: str,
    ) -> FakeIB:
        """Connects (or fails ``connect_failures`` times).

        Args:
            host: Host.
            port: Port.
            clientId: Client id.
            timeout: Timeout.
            readonly: Read-only.
            account: Account.

        Returns:
            Self.

        Raises:
            ConnectionRefusedError: While failures remain.
        """
        self.connects.append(
            {"host": host, "port": port, "client_id": clientId}
        )
        if self.connect_failures:
            self.connect_failures -= 1
            raise ConnectionRefusedError("gateway down")
        self._connected = True
        return self

    def disconnect(self) -> str:
        """Disconnects and emits ``disconnectedEvent``.

        Returns:
            Status text.
        """
        self._connected = False
        self.disconnectedEvent.emit()
        return "disconnected"

    def managedAccounts(self) -> list[str]:  # noqa: N802
        """Lists managed accounts.

        Returns:
            Accounts.
        """
        return list(self.managed)

    async def qualifyContractsAsync(  # noqa: N802
        self, *contracts: FakeContract
    ) -> list[FakeContract | None]:
        """Qualifies contracts.

        Args:
            *contracts: Contracts.

        Returns:
            Contracts with conIds, ``None`` for unknown symbols.
        """
        result: list[FakeContract | None] = []
        for contract in contracts:
            if contract.symbol in self.unknown_symbols:
                result.append(None)
                continue
            contract.conId = 1000 + len(contract.symbol)
            result.append(contract)
        return result

    # -- orders ------------------------------------------------------------

    def placeOrder(  # noqa: N802
        self, contract: FakeContract, order: FakeOrder
    ) -> FakeTrade:
        """Places or modifies an order.

        Args:
            contract: Contract.
            order: Order.

        Returns:
            Trade.
        """
        self.placed.append(order)
        existing = next(
            (t for t in self._trades if t.order.orderId == order.orderId),
            None,
        )
        if existing is not None and order.orderId:
            existing.log.append(FakeLogEntry("Submitted", "Modified"))
            self.orderStatusEvent.emit(existing)
            return existing
        order.orderId = self._next_id
        order.permId = 900_000 + self._next_id
        self._next_id += 1
        trade = FakeTrade(
            contract,
            order,
            FakeOrderStatus(remaining=float(order.totalQuantity)),
            [FakeLogEntry("PendingSubmit")],
        )
        self._trades.append(trade)
        if self.reject_next is not None:
            code, message = self.reject_next
            self.reject_next = None
            self._fail(trade, code, message)
        elif self.ack is not None:
            trade.orderStatus.status = self.ack
            trade.log.append(FakeLogEntry(self.ack))
            self.orderStatusEvent.emit(trade)
        return trade

    def _fail(self, trade: FakeTrade, code: int, message: str) -> None:
        """Applies an order error the way ``ib_async`` does.

        Args:
            trade: Trade.
            code: Error code.
            message: Error text.
        """
        trade.orderStatus.status = "Cancelled"
        trade.log.append(FakeLogEntry("Cancelled", message, code))
        self.orderStatusEvent.emit(trade)
        self.errorEvent.emit(trade.order.orderId, code, message, None)

    def cancelOrder(self, order: FakeOrder) -> FakeTrade | None:  # noqa: N802
        """Requests cancellation.

        Args:
            order: Order.

        Returns:
            Trade.
        """
        self.canceled.append(order.orderId)
        trade = next(t for t in self._trades if t.order is order)
        trade.orderStatus.status = "PendingCancel"
        if self.cancel_mode == "ok":
            self._fail(trade, 202, "Order Canceled - reason:")
        elif self.cancel_mode == "error":
            self.errorEvent.emit(
                order.orderId,
                10148,
                "OrderId that needs to be cancelled "
                "cannot be cancelled, state: Filled.",
                None,
            )
        return trade

    def trades(self) -> list[FakeTrade]:
        """Lists trades.

        Returns:
            Trades.
        """
        return list(self._trades)

    def fills(self) -> list[FakeFill]:
        """Lists fills.

        Returns:
            Fills.
        """
        return list(self._fills)

    def positions(self, account: str = "") -> list[FakePosition]:
        """Lists positions.

        Args:
            account: Account filter.

        Returns:
            Positions.
        """
        return [p for p in self.position_rows if p.account in ("", account)]

    async def accountSummaryAsync(  # noqa: N802
        self, account: str = ""
    ) -> list[FakeAccountValue]:
        """Returns the account summary.

        Args:
            account: Account.

        Returns:
            Values.
        """
        return [v for v in self.summary if v.account == account]

    async def reqAllOpenOrdersAsync(self) -> list[FakeTrade]:  # noqa: N802
        """Returns open trades.

        Returns:
            Trades.
        """
        self.requests.append("open")
        return [t for t in self._trades if t.orderStatus.status != "Filled"]

    async def reqCompletedOrdersAsync(  # noqa: N802
        self,
        apiOnly: bool,  # noqa: N803
    ) -> list[FakeTrade]:
        """Returns completed trades.

        Args:
            apiOnly: API orders only.

        Returns:
            Trades.
        """
        del apiOnly
        self.requests.append("completed")
        return [t for t in self._trades if t.orderStatus.status == "Filled"]

    async def reqExecutionsAsync(  # noqa: N802
        self,
        execFilter: Any = None,  # noqa: N803
    ) -> list[FakeFill]:
        """Returns executions.

        Args:
            execFilter: Filter.

        Returns:
            Fills.
        """
        del execFilter
        self.requests.append("executions")
        return list(self._fills)

    # -- test drivers ------------------------------------------------------

    def execute(  # noqa: PLR0917
        self,
        trade: FakeTrade,
        shares: float,
        price: float,
        commission: float | None = 1.0,
        emit: bool = True,
        exec_id: str | None = None,
    ) -> FakeFill:
        """Fills (part of) an order and emits the events.

        Args:
            trade: Trade.
            shares: Shares.
            price: Price.
            commission: Commission (``None``: no report yet).
            emit: Emit events (False simulates events lost in an outage).
            exec_id: Execution id override.

        Returns:
            The fill.
        """
        self._exec += 1
        execution = FakeExecution(
            exec_id or f"0000e0d5.65f1a2b3.{self._exec:02d}.01",
            trade.order.account,
            shares,
            price,
            trade.order.orderRef,
            trade.order.orderId,
        )
        report = FakeCommission()
        fill = FakeFill(trade.contract, execution, report)
        self._fills.append(fill)
        status = trade.orderStatus
        status.filled += shares
        status.remaining = float(trade.order.totalQuantity) - status.filled
        status.status = "Filled" if status.remaining <= 0 else "Submitted"
        if emit:
            self.execDetailsEvent.emit(trade, fill)
            self.orderStatusEvent.emit(trade)
        if commission is not None:
            report.execId = execution.execId
            report.commission = commission
            report.currency = "USD"
            if emit:
                self.commissionReportEvent.emit(trade, fill, report)
        return fill


class FakeIbFactory:
    """``IbFactory`` returning one ``FakeIB``.

    Attributes:
        ib: The fake client.
        created: Clients handed out.
    """

    def __init__(self, ib: FakeIB) -> None:
        """Initializes the factory.

        Args:
            ib: Fake client.
        """
        self.ib = ib
        self.created = 0

    def client(self) -> FakeIB:
        """Returns the fake client.

        Returns:
            Client.
        """
        self.created += 1
        return self.ib

    def stock(self, symbol: str, primary_exchange: str) -> FakeContract:
        """Builds a stock contract.

        Args:
            symbol: Symbol.
            primary_exchange: Primary exchange.

        Returns:
            Contract.
        """
        return FakeContract(symbol, primary_exchange)

    def order(self, fields: dict[str, Any]) -> FakeOrder:
        """Builds an order.

        Args:
            fields: Fields.

        Returns:
            Order.
        """
        return FakeOrder(**fields)

    def execution_filter(self, account: str) -> dict[str, str]:
        """Builds an execution filter.

        Args:
            account: Account.

        Returns:
            Filter.
        """
        return {"acctCode": account}
