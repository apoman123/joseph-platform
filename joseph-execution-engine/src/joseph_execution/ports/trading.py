# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Venue trading port.

Only the execution core (the OMS, or order management system, the account
service and reconciliation) reaches adapters, through ``AdapterResolver``;
strategies, UI features and notification channels never do. Quantities are
in base units and times are UTC epoch milliseconds.
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
    """An order as sent to a venue adapter.

    Attributes:
        account_id: Account.
        instrument_id: Canonical instrument id.
        client_order_id: Client order id, unique per account; the OMS
            reconciles unknown outcomes by it.
        side: Side.
        order_type: Order type.
        quantity_base: Quantity.
        time_in_force: Time in force.
        reduce_only: Reduce-only flag.
        limit_price: Limit price, for limit and stop-limit orders.
        trigger_price: Trigger price, for stop orders.
        trigger_basis: Trigger basis, for stop orders.
        leverage: Leverage.
        margin_mode: Margin mode.
        outside_rth: Allow execution outside regular trading hours
            (brokers that support it; securities only).
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
    outside_rth: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class VenueFill:
    """A fill reported by a venue.

    The OMS deduplicates fills by ``exchange_fill_id``, so adapters may
    report the same fill more than once.

    Attributes:
        exchange_fill_id: Venue fill id, unique per account.
        client_order_id: Client order id of the filled order.
        price: Fill price.
        quantity_base: Filled quantity.
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
    """An order's state as reported by the venue.

    Attributes:
        client_order_id: Client order id.
        exchange_order_id: Venue order id, if the venue assigned one.
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
    """The synchronous response to a submit.

    Attributes:
        accepted: Whether the venue accepted the order; False means the
            venue rejected it and ``state`` is REJECTED.
        state: Order state at response time.
        fills: Fills included in the response; the OMS fetches the rest
            when ``state.filled_base`` exceeds their total.
    """

    accepted: bool
    state: VenueOrderState
    fills: tuple[VenueFill, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class VenueEvent:
    """A private account stream event.

    Attributes:
        account_id: Account.
        sequence: Venue or adapter sequence number.
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
    """A position as reported by the venue.

    Attributes:
        instrument_id: Canonical instrument id.
        signed_qty: Signed base quantity (> 0 long, < 0 short).
        entry_price: Average entry price, if the venue reports it.
        unmanaged_note: Why Joseph does not manage this holding (e.g. an
            emerging-board stock in a SinoPac account); ``None`` for
            positions on a venue Joseph routes. Reconciliation skips
            unmanaged holdings.
    """

    instrument_id: str
    signed_qty: decimal.Decimal
    entry_price: decimal.Decimal | None
    unmanaged_note: str | None = None

    @property
    def managed(self) -> bool:
        """Whether the holding is on a venue Joseph routes."""
        return self.unmanaged_note is None


@dataclasses.dataclass(frozen=True, slots=True)
class VenueAccountSnapshot:
    """Account balances as reported by the venue.

    Attributes:
        equity: Total equity, in ``currency``.
        available_margin: Free margin (cash accounts: cash available for
            purchases).
        currency: Currency.
        ts_ms: Venue time.
        settled_cash: Settled cash of a securities account, if known.
        unsettled_cash: Net unsettled cash (negative = payable), if known.
    """

    equity: decimal.Decimal
    available_margin: decimal.Decimal
    currency: str
    ts_ms: int
    settled_cash: decimal.Decimal | None = None
    unsettled_cash: decimal.Decimal | None = None


class TradingAdapter(Protocol):
    """A venue execution adapter (one per venue or simulator).

    A venue's rejection of a submit is a result with ``accepted=False``,
    never an exception. Read methods raise ``ExecutionError`` when the
    venue cannot be read, and callers then treat the data as unknown; the
    order methods' ``Raises`` sections say how the OMS reads each
    exception.
    """

    @property
    def name(self) -> str:
        """The adapter name, e.g. ``paper`` or ``binance``."""
        ...

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Negotiates capabilities for an account.

        Args:
            account_id: Account.

        Returns:
            Capabilities; LIVE orders need ``status`` ``AVAILABLE``.

        Raises:
            ExecutionError: If they cannot be determined; the Risk Gateway
                then fails LIVE orders closed.
        """
        ...

    async def health(self) -> enums.HealthState:
        """Returns the health of the adapter and its private stream."""
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
            since_ms: Earliest fill time to include; 0 for every fill the
                venue still reports.

        Returns:
            Fills, possibly including ones already recorded.
        """
        ...

    async def submit_order(
        self, request: VenueOrderRequest
    ) -> VenueSubmitResult:
        """Submits an order.

        Any exception other than ``AdapterUnavailableError`` leaves the
        outcome unknown to the OMS, which then reconciles the order.

        Args:
            request: Order.

        Returns:
            Venue response; a venue rejection is a result with
            ``accepted=False``.

        Raises:
            AdapterTimeoutError: Outcome unknown; the OMS reconciles.
            AdapterUnavailableError: Refused before sending; the OMS marks
                the order FAILED.
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
            Venue state after the request; an order that finished first
            reports its final state (may be FILLED on a race).

        Raises:
            AdapterTimeoutError: Outcome unknown; the OMS reconciles.
            ExecutionError: The cancel definitely failed; the order keeps
                working.
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
            limit_price: New limit price, or ``None`` to keep it.
            trigger_price: New trigger price, or ``None`` to keep it.
            quantity_base: New quantity, or ``None`` to keep it.

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
            Venue state, or ``None`` only when the venue confirms it has no
            such order: the OMS may then resubmit with the same client id.

        Raises:
            ExecutionError: If the venue cannot be queried; the OMS retries
                later.
        """
        ...

    def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[VenueEvent]:
        """Streams private account events.

        Events may repeat (the OMS applies them idempotently); after a
        reconnect an implementation replays a REST snapshot as events with
        ``resync=True``. When the iterator ends or raises, the stream
        supervisor restarts it after a delay.

        Args:
            account_id: Account.

        Returns:
            Async iterator of events.
        """
        ...


class AdapterResolver(Protocol):
    """A resolver of the adapter for an account and order mode."""

    def resolve(
        self, account: accounts.Account, mode: enums.OrderMode
    ) -> TradingAdapter:
        """Returns the adapter.

        Args:
            account: Account.
            mode: Order mode.

        Returns:
            Adapter (PAPER -> paper, SHADOW -> shadow, LIVE -> venue); a
            fail-closed adapter for a LIVE venue without one.
        """
        ...
