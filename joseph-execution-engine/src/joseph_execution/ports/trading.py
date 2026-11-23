"""Venue trading port.

Only the OMS holds a reference to adapters (through ``AdapterResolver``);
strategies, UI features and notification channels never do.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import AsyncIterator
from collections.abc import Sequence
from typing import Protocol

from joseph_execution.domain import accounts
from joseph_execution.domain import enums


@dataclasses.dataclass(frozen=True, slots=True)
class VenueOrderRequest:
    """Order as sent to a venue adapter.

    Attributes:
        account_id: Account.
        instrument_id: Instrument.
        client_order_id: Idempotent client order id.
        side: Side.
        order_type: Type.
        quantity_base: Quantity.
        time_in_force: Time in force.
        reduce_only: Reduce-only flag.
        limit_price: Limit price.
        trigger_price: Trigger price.
        trigger_basis: Trigger basis.
        leverage: Leverage.
        margin_mode: Margin mode.
    """

    account_id: str
    instrument_id: str
    client_order_id: str
    side: enums.OrderSide
    order_type: enums.OrderType
    quantity_base: decimal.Decimal
    time_in_force: enums.TimeInForce
    reduce_only: bool
    limit_price: decimal.Decimal | None = None
    trigger_price: decimal.Decimal | None = None
    trigger_basis: enums.PriceBasis | None = None
    leverage: decimal.Decimal = decimal.Decimal(1)
    margin_mode: enums.MarginMode = enums.MarginMode.ISOLATED


@dataclasses.dataclass(frozen=True, slots=True)
class VenueFill:
    """Fill reported by a venue.

    Attributes:
        exchange_fill_id: Venue fill id.
        client_order_id: Order client id.
        price: Price.
        quantity_base: Quantity.
        fee: Fee (positive = paid).
        fee_asset: Fee currency.
        liquidity: ``maker`` or ``taker``.
        ts_ms: Venue time.
    """

    exchange_fill_id: str
    client_order_id: str
    price: decimal.Decimal
    quantity_base: decimal.Decimal
    fee: decimal.Decimal
    fee_asset: str
    liquidity: str
    ts_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class VenueOrderState:
    """Order state as reported by the venue.

    Attributes:
        client_order_id: Client order id.
        exchange_order_id: Venue order id.
        status: ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, CANCELED,
            REJECTED or EXPIRED.
        filled_base: Cumulative filled quantity.
        reason: Venue reason text, if any.
        ts_ms: Venue time.
    """

    client_order_id: str
    exchange_order_id: str | None
    status: enums.OrderState
    filled_base: decimal.Decimal
    reason: str | None
    ts_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class VenueSubmitResult:
    """Synchronous response of a submit.

    Attributes:
        accepted: Whether the venue accepted the order.
        state: Order state at response time.
        fills: Fills included in the response.
    """

    accepted: bool
    state: VenueOrderState
    fills: tuple[VenueFill, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class VenueEvent:
    """Private account stream event.

    Attributes:
        account_id: Account.
        sequence: Venue/adapter sequence number.
        order: Order update, if any.
        fill: Fill, if any.
        position: Position update, if any (live venue streams).
        balance: Balance/margin update, if any (live venue streams).
        resync: True for events replayed from a REST snapshot after a
            (re)connect rather than pushed by the venue.
    """

    account_id: str
    sequence: int
    order: VenueOrderState | None = None
    fill: VenueFill | None = None
    position: VenuePosition | None = None
    balance: VenueAccountSnapshot | None = None
    resync: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class VenuePosition:
    """Position as reported by the venue.

    Attributes:
        instrument_id: Instrument.
        signed_qty: Signed base quantity.
        entry_price: Average entry.
    """

    instrument_id: str
    signed_qty: decimal.Decimal
    entry_price: decimal.Decimal | None


@dataclasses.dataclass(frozen=True, slots=True)
class VenueAccountSnapshot:
    """Account balances as reported by the venue.

    Attributes:
        equity: Total equity.
        available_margin: Free margin.
        currency: Currency.
        ts_ms: Venue time.
    """

    equity: decimal.Decimal
    available_margin: decimal.Decimal
    currency: str
    ts_ms: int


class TradingAdapter(Protocol):
    """Venue execution adapter (one per venue or simulator)."""

    @property
    def name(self) -> str:
        """Returns the adapter name, e.g. ``paper`` or ``binance``."""
        ...

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities for an account.

        Args:
            account_id: Account.

        Returns:
            Capabilities.
        """
        ...

    async def health(self) -> enums.HealthState:
        """Returns adapter/private-stream health."""
        ...

    async def account_snapshot(self, account_id: str) -> VenueAccountSnapshot:
        """Fetches balances.

        Args:
            account_id: Account.

        Returns:
            Snapshot.
        """
        ...

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[VenueOrderState]:
        """Lists open orders.

        Args:
            account_id: Account.

        Returns:
            Open orders.
        """
        ...

    async def list_positions(self, account_id: str) -> Sequence[VenuePosition]:
        """Lists positions.

        Args:
            account_id: Account.

        Returns:
            Positions.
        """
        ...

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[VenueFill]:
        """Lists fills since a time.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Fills.
        """
        ...

    async def submit_order(
        self, request: VenueOrderRequest
    ) -> VenueSubmitResult:
        """Submits an order.

        Args:
            request: Order.

        Returns:
            Venue response.

        Raises:
            AdapterTimeoutError: Outcome unknown.
            AdapterUnavailableError: Refused before sending.
        """
        ...

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> VenueOrderState:
        """Requests cancellation.

        Args:
            account_id: Account.
            client_order_id: Order.

        Returns:
            Venue state after the request (may be FILLED on a race).
        """
        ...

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> VenueOrderState:
        """Amends a working order.

        Args:
            account_id: Account.
            client_order_id: Order.
            limit_price: New limit price.
            trigger_price: New trigger price.
            quantity_base: New quantity.

        Returns:
            Venue state after amendment.
        """
        ...

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> VenueOrderState | None:
        """Queries an order by client id.

        Args:
            account_id: Account.
            client_order_id: Order.

        Returns:
            Venue state, or ``None`` if the venue has no such order.
        """
        ...

    def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[VenueEvent]:
        """Streams private account events.

        Args:
            account_id: Account.

        Returns:
            Async iterator of events.
        """
        ...


class AdapterResolver(Protocol):
    """Resolves the adapter for an account and order mode."""

    def resolve(
        self, account: accounts.Account, mode: enums.OrderMode
    ) -> TradingAdapter:
        """Returns the adapter.

        Args:
            account: Account.
            mode: Order mode.

        Returns:
            Adapter (PAPER -> paper, SHADOW -> shadow, LIVE -> venue).
        """
        ...
