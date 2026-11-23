"""In-memory stand-in for ``shioaji.Shioaji`` used by tests (no network).

Mirrors the Shioaji 1.7 behaviours the adapter depends on: ``place_order``
returns a live ``Trade`` and the order/deal callbacks report the outcome
(``op_code`` "00" = success); Common quantities are lots and IntradayOdd
quantities are shares.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any


@dataclasses.dataclass
class FakeAccount:
    """``sj.Account``.

    Attributes:
        account_type: ``S`` for stock accounts.
        broker_id: Branch code.
        account_id: Account number.
        signed: API agreement signed (production).
        person_id: National id.
    """

    account_type: str = "S"
    broker_id: str = "9A95"
    account_id: str = "0000001"
    signed: bool = True
    person_id: str = "A123456789"


@dataclasses.dataclass
class FakeStock:
    """``sj.Contract`` of a stock.

    Attributes:
        code: Security code.
        exchange: ``TSE`` (TWSE), ``OTC`` (TPEx) or ``OES`` (emerging).
        unit: Shares per lot.
    """

    code: str
    exchange: str = "TSE"
    unit: float = 1000.0


class FakeCategory:
    """``contracts.stocks`` (a ``ContractCategory``).

    ``[code]`` searches every exchange; the attributes ``TSE``, ``OTC`` and
    ``OES`` are the per-exchange groups, as in Shioaji 1.7.

    Attributes:
        items: Contracts by code.
    """

    def __init__(self, items: dict[str, FakeStock]) -> None:
        """Initializes the category.

        Args:
            items: Contracts by code.
        """
        self.items = items

    def __getitem__(self, code: str) -> FakeStock:
        """Looks up a contract.

        Args:
            code: Code.

        Returns:
            Contract.
        """
        return self.items[code]

    def __getattr__(self, exchange: str) -> FakeCategory:
        """Returns the contracts of one exchange (``ContractGroup``).

        Args:
            exchange: ``TSE``, ``OTC`` or ``OES``.

        Returns:
            Group of that exchange's contracts.

        Raises:
            AttributeError: For names that are not exchange codes.
        """
        if exchange not in ("TSE", "OTC", "OES"):
            raise AttributeError(exchange)
        return FakeCategory(
            {
                code: stock
                for code, stock in self.items.items()
                if stock.exchange == exchange
            }
        )


@dataclasses.dataclass
class FakeContracts:
    """``api.contracts``.

    Attributes:
        stocks: Stock contracts.
    """

    stocks: FakeCategory


@dataclasses.dataclass
class FakeStockOrder:
    """``sj.StockOrder``.

    Attributes:
        action: Buy or Sell.
        price: Price.
        quantity: Lots (Common) or shares (IntradayOdd).
        price_type: LMT or MKT.
        time_in_force: ROD, IOC or FOK.
        order_lot: Common or IntradayOdd.
        order_cond: Cash.
        custom_field: Tag.
        account: Account.
    """

    action: str
    price: float
    quantity: int
    price_type: str
    time_in_force: str
    order_lot: str
    order_cond: str
    custom_field: str
    account: Any


@dataclasses.dataclass
class FakeOrderResult:
    """``trade.order``.

    Attributes:
        id: Trade id.
        action: Side.
        price: Price.
        quantity: Quantity.
        order_lot: Lot.
        custom_field: Tag.
    """

    id: str
    action: str
    price: float
    quantity: int
    order_lot: str
    custom_field: str


@dataclasses.dataclass
class FakeStatus:
    """``trade.status``.

    Attributes:
        id: Trade id.
        status: Status text.
        msg: Message.
        deal_quantity: Dealt quantity.
        cancel_quantity: Canceled quantity.
    """

    id: str
    status: str = "PendingSubmit"
    msg: str = ""
    deal_quantity: int = 0
    cancel_quantity: int = 0


@dataclasses.dataclass
class FakeSjTrade:
    """``sj.Trade``.

    Attributes:
        contract: Contract.
        order: Order result.
        status: Status.
    """

    contract: FakeStock
    order: FakeOrderResult
    status: FakeStatus


@dataclasses.dataclass
class FakeStockPosition:
    """``sj.StockPosition``.

    Attributes:
        code: Code.
        direction: Buy or Sell.
        quantity: Shares (``unit=Share``).
        price: Average price.
        last_price: Last price.
    """

    code: str
    direction: str
    quantity: int
    price: float
    last_price: float


@dataclasses.dataclass
class FakeBalance:
    """``sj.AccountBalance``.

    Attributes:
        acc_balance: Bank balance.
    """

    acc_balance: float


@dataclasses.dataclass
class FakeSettlement:
    """``sj.Settlement`` (legacy t/t1/t2 format).

    Attributes:
        t_money: Today.
        t1_money: T+1.
        t2_money: T+2.
    """

    t_money: float
    t1_money: float
    t2_money: float


@dataclasses.dataclass
class FakeLimits:
    """``sj.TradingLimits``.

    Attributes:
        trading_available: Remaining trading limit (TWD).
    """

    trading_available: int


class FakeShioaji:
    """Fake ``sj.Shioaji`` (see the module docstring).

    Attributes:
        simulation: Mode requested.
        accounts: Accounts of the login.
        login_failures: Logins that fail first.
        ca_ok: CA activation result.
        place_error: Exception raised by the next ``place_order``.
        reject_next: Rejection message for the next order.
        cancel_mode: ``ok``, ``fail`` or ``hold``.
        balance: Bank balance.
        settlement_rows: Pending settlements.
        limit: Remaining trading limit.
        position_rows: Positions.
        calls: SDK methods called.
        order_cb: Order callback.
        event_cb: Session event callback.
        trades: Trades of the session.
    """

    def __init__(self) -> None:
        """Initializes a logged-out fake."""
        self.simulation = True
        self.accounts = [FakeAccount(), FakeAccount("F", account_id="F01")]
        self.login_failures = 0
        self.ca_ok = True
        self.place_error: Exception | None = None
        self.reject_next: str | None = None
        self.cancel_mode = "ok"
        self.balance = 1_000_000.0
        self.settlement_rows = [FakeSettlement(0.0, -120_000.0, 30_000.0)]
        self.limit = 5_000_000
        self.position_rows: list[FakeStockPosition] = []
        self.calls: list[str] = []
        self.order_cb: Callable[[Any, Any], None] | None = None
        self.event_cb: Callable[[int, int, str, str], None] | None = None
        self.trades: list[FakeSjTrade] = []
        self._contracts = FakeContracts(
            FakeCategory(
                {
                    "2330": FakeStock("2330"),
                    "0050": FakeStock("0050"),
                    "6488": FakeStock("6488", exchange="OTC"),
                    "00679B": FakeStock("00679B", exchange="OTC"),
                    "7777": FakeStock("7777", exchange="OES"),
                }
            )
        )
        self._next = 1

    # -- session -----------------------------------------------------------

    def login(
        self, api_key: str, secret_key: str, subscribe_trade: bool = True
    ) -> list[FakeAccount]:
        """Logs in.

        Args:
            api_key: API key.
            secret_key: Secret key.
            subscribe_trade: Subscribe callbacks.

        Returns:
            Accounts.

        Raises:
            ConnectionError: While failures remain.
        """
        del api_key, secret_key, subscribe_trade
        self.calls.append("login")
        if self.login_failures:
            self.login_failures -= 1
            raise ConnectionError("login failed")
        return self.accounts

    def activate_ca(
        self, ca_path: str, ca_passwd: str, person_id: str | None = None
    ) -> bool:
        """Activates the CA.

        Args:
            ca_path: Path.
            ca_passwd: Password.
            person_id: National id.

        Returns:
            ``ca_ok``.
        """
        del ca_path, ca_passwd, person_id
        self.calls.append("activate_ca")
        return self.ca_ok

    def logout(self) -> bool:
        """Logs out.

        Returns:
            True.
        """
        self.calls.append("logout")
        return True

    def list_accounts(self) -> list[FakeAccount]:
        """Lists accounts.

        Returns:
            Accounts.
        """
        return self.accounts

    @property
    def contracts(self) -> FakeContracts:
        """Returns the contract tree."""
        return self._contracts

    def set_order_callback(self, callback: Callable[[Any, Any], None]) -> None:
        """Sets the order callback.

        Args:
            callback: Callback.
        """
        self.order_cb = callback

    def set_event_callback(
        self, callback: Callable[[int, int, str, str], None]
    ) -> None:
        """Sets the session event callback.

        Args:
            callback: Callback.
        """
        self.event_cb = callback

    # -- orders --------------------------------------------------------------

    def _order_event(
        self, trade: FakeSjTrade, op_type: str, code: str, message: str = ""
    ) -> None:
        """Sends a StockOrder callback.

        Args:
            trade: Trade.
            op_type: New, Cancel, UpdatePrice or UpdateQty.
            code: ``00`` success.
            message: Text.
        """
        assert self.order_cb is not None
        self.order_cb(
            "SORDER",
            {
                "operation": {
                    "op_type": op_type,
                    "op_code": code,
                    "op_msg": message,
                },
                "order": {
                    "id": trade.order.id,
                    "custom_field": trade.order.custom_field,
                    "order_lot": trade.order.order_lot,
                },
                "status": {"id": trade.order.id},
                "contract": {"code": trade.contract.code},
            },
        )

    def place_order(
        self, contract: FakeStock, order: FakeStockOrder, timeout: int = 0
    ) -> FakeSjTrade:
        """Places an order.

        Args:
            contract: Contract.
            order: Order.
            timeout: Milliseconds.

        Returns:
            Trade.

        Raises:
            Exception: ``place_error`` when set.
        """
        del timeout
        self.calls.append("place_order")
        if self.place_error is not None:
            error, self.place_error = self.place_error, None
            raise error
        trade_id = f"t{self._next:04d}"
        self._next += 1
        trade = FakeSjTrade(
            contract,
            FakeOrderResult(
                trade_id,
                order.action,
                order.price,
                order.quantity,
                order.order_lot,
                order.custom_field,
            ),
            FakeStatus(trade_id),
        )
        self.trades.append(trade)
        if self.reject_next is not None:
            message, self.reject_next = self.reject_next, None
            trade.status.status = "Failed"
            trade.status.msg = message
            self._order_event(trade, "New", "88", message)
        else:
            trade.status.status = "Submitted"
            self._order_event(trade, "New", "00")
        return trade

    def cancel_order(self, trade: FakeSjTrade, timeout: int = 0) -> FakeSjTrade:
        """Cancels an order.

        Args:
            trade: Trade.
            timeout: Milliseconds.

        Returns:
            Trade.
        """
        del timeout
        self.calls.append("cancel_order")
        if self.cancel_mode == "ok":
            trade.status.status = "Cancelled"
            trade.status.cancel_quantity = (
                trade.order.quantity - trade.status.deal_quantity
            )
            self._order_event(trade, "Cancel", "00")
        elif self.cancel_mode == "fail":
            self._order_event(trade, "Cancel", "99", "already filled")
        return trade

    def update_order(
        self,
        trade: FakeSjTrade,
        price: float | None = None,
        qty: int | None = None,
        timeout: int = 0,
    ) -> FakeSjTrade:
        """Updates price or reduces quantity.

        Args:
            trade: Trade.
            price: New price.
            qty: New quantity.
            timeout: Milliseconds.

        Returns:
            Trade.
        """
        del timeout
        self.calls.append("update_order")
        if price is not None:
            trade.order.price = price
            self._order_event(trade, "UpdatePrice", "00")
        if qty is not None:
            trade.order.quantity = qty
            self._order_event(trade, "UpdateQty", "00")
        return trade

    def update_status(self, account: Any = None, timeout: int = 0) -> None:
        """Refreshes statuses.

        Args:
            account: Account.
            timeout: Milliseconds.
        """
        del account, timeout
        self.calls.append("update_status")

    def list_trades(self) -> list[FakeSjTrade]:
        """Lists trades.

        Returns:
            Trades.
        """
        return list(self.trades)

    def list_positions(
        self, account: Any = None, unit: Any = None, timeout: int = 0
    ) -> list[FakeStockPosition]:
        """Lists positions.

        Args:
            account: Account.
            unit: Unit.
            timeout: Milliseconds.

        Returns:
            Positions.
        """
        del account, timeout
        self.calls.append(f"list_positions:{unit}")
        return list(self.position_rows)

    def account_balance(self, account: Any = None, timeout: int = 0) -> Any:
        """Reads the balance.

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Balance.
        """
        del account, timeout
        self.calls.append("account_balance")
        return FakeBalance(self.balance)

    def settlements(
        self, account: Any = None, timeout: int = 0
    ) -> list[FakeSettlement]:
        """Reads settlements.

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Settlements.
        """
        del account, timeout
        self.calls.append("settlements")
        return list(self.settlement_rows)

    def trading_limits(self, account: Any = None, timeout: int = 0) -> Any:
        """Reads the trading limit.

        Args:
            account: Account.
            timeout: Milliseconds.

        Returns:
            Limits.
        """
        del account, timeout
        self.calls.append("trading_limits")
        return FakeLimits(self.limit)

    # -- test drivers --------------------------------------------------------

    def deal(
        self, trade: FakeSjTrade, quantity: int, price: float, seq: str
    ) -> None:
        """Reports a deal (quantity in the order's units).

        Args:
            trade: Trade.
            quantity: Lots (Common) or shares (IntradayOdd).
            price: Price.
            seq: Exchange sequence.
        """
        trade.status.deal_quantity += quantity
        trade.status.status = (
            "Filled"
            if trade.status.deal_quantity >= trade.order.quantity
            else "PartFilled"
        )
        assert self.order_cb is not None
        self.order_cb(
            "SDEAL",
            {
                "trade_id": trade.order.id,
                "exchange_seq": seq,
                "order_lot": trade.order.order_lot,
                "quantity": quantity,
                "price": price,
                "custom_field": trade.order.custom_field,
                "ts": 1_795_400_000.5,
            },
        )

    def session_event(self, code: int) -> None:
        """Sends a session event.

        Args:
            code: Event code.
        """
        assert self.event_cb is not None
        self.event_cb(0, code, "info", "event")


class FakeSjFactory:
    """``SjFactory`` returning one ``FakeShioaji``.

    Attributes:
        api: Fake client.
        created: Clients handed out.
    """

    def __init__(self, api: FakeShioaji) -> None:
        """Initializes the factory.

        Args:
            api: Fake client.
        """
        self.api = api
        self.created = 0

    def client(self, simulation: bool) -> FakeShioaji:
        """Returns the fake client.

        Args:
            simulation: Mode.

        Returns:
            Client.
        """
        self.created += 1
        self.api.simulation = simulation
        return self.api

    def stock_order(self, fields: dict[str, Any]) -> FakeStockOrder:
        """Builds a stock order.

        Args:
            fields: Fields.

        Returns:
            Order.
        """
        return FakeStockOrder(**fields)

    def share_unit(self) -> str:
        """Returns the share unit marker.

        Returns:
            ``Share``.
        """
        return "Share"


async def run_inline(fn: Callable[[], Any]) -> Any:
    """Runs an SDK call on the event loop thread (deterministic tests).

    Args:
        fn: Call.

    Returns:
        Result.
    """
    return fn()
