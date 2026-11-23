"""Read-only view of the OMS order book for live venue adapters.

Several venues need context the trading port does not carry: Binance,
Bitget and MEXC require the symbol to query or cancel an order by client
id, and Lighter's integer ``client_order_index`` cannot hold a Joseph client
id. Adapters therefore look up the instrument and recent client ids of an
account through this directory (backed by the order repository), which
keeps working after a restart. It never writes.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from joseph_execution.domain import enums
from joseph_execution.domain import orders
from joseph_execution.ports import repositories


@dataclasses.dataclass(frozen=True, slots=True)
class KnownOrder:
    """An order the OMS knows for an account.

    Attributes:
        client_order_id: Client order id.
        instrument_id: Instrument.
        mode: Order mode.
        updated_at_ms: Last update.
        terminal: Whether the OMS considers the order finished.
        order_type: Order type.
        exchange_order_id: Venue order id once acknowledged.
    """

    client_order_id: str
    instrument_id: str
    mode: enums.OrderMode
    updated_at_ms: int
    terminal: bool
    order_type: enums.OrderType = enums.OrderType.LIMIT
    exchange_order_id: str | None = None


class OrderDirectory(Protocol):
    """Looks up OMS orders of an account."""

    def find(self, account_id: str, client_order_id: str) -> KnownOrder | None:
        """Finds one order by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        ...

    def recent(self, account_id: str, since_ms: int) -> list[KnownOrder]:
        """Lists LIVE orders updated since a time, newest first.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Orders.
        """
        ...


class EmptyDirectory:
    """A directory that knows no orders (tests, sandbox scripts)."""

    def find(self, account_id: str, client_order_id: str) -> KnownOrder | None:
        """Returns ``None``.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Always ``None``.
        """
        del account_id, client_order_id
        return None

    def recent(self, account_id: str, since_ms: int) -> list[KnownOrder]:
        """Returns nothing.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Empty list.
        """
        del account_id, since_ms
        return []


class RepositoryOrderDirectory:
    """Order directory over the OMS repositories."""

    def __init__(
        self, uow_factory: repositories.UnitOfWorkFactory, limit: int = 500
    ) -> None:
        """Initializes the directory.

        Args:
            uow_factory: Unit of work factory.
            limit: Maximum orders scanned by ``recent``.
        """
        self._uow = uow_factory
        self._limit = limit

    def find(self, account_id: str, client_order_id: str) -> KnownOrder | None:
        """Finds one order by client id.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        with self._uow() as uow:
            order = uow.orders.get_by_client_id(account_id, client_order_id)
        return None if order is None else _known(order)

    def recent(self, account_id: str, since_ms: int) -> list[KnownOrder]:
        """Lists LIVE orders updated since a time, newest first.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Orders.
        """
        with self._uow() as uow:
            rows = uow.orders.list(account_id=account_id, limit=self._limit)
        return [
            _known(o)
            for o in rows
            if o.mode is enums.OrderMode.LIVE
            and (o.updated_at_ms >= since_ms or not o.state.is_terminal)
        ]


def _known(order: orders.Order) -> KnownOrder:
    """Projects an OMS order.

    Args:
        order: Order.

    Returns:
        Known order.
    """
    return KnownOrder(
        client_order_id=order.client_order_id,
        instrument_id=order.instrument_id,
        mode=order.mode,
        updated_at_ms=order.updated_at_ms,
        terminal=order.state.is_terminal,
        order_type=order.order_type,
        exchange_order_id=order.exchange_order_id,
    )
