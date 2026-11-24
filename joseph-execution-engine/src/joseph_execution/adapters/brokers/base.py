# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared machinery of the securities broker adapters.

* ``EventBuffer`` turns broker callbacks into ordered ``VenueEvent``s per
  Joseph account; the broker pump in ``bootstrap`` drains it into the OMS.
* ``Pacer`` keeps request rates under broker limits (IBKR 50 msg/s,
  Shioaji 250 orders/10 s and 25 queries/5 s).
* ``FailClosedBrokerAdapter`` declares a broker's documented capabilities
  but refuses every call before anything is sent (Cathay until its SDK
  arrives; any broker while the deployment gates it).
* Helpers decode Joseph client order ids and mask account numbers.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import decimal
import re
import time
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from typing import Protocol

from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.ports import trading

AccountLookup = Callable[[str], str | None]
"""Maps a Joseph account id to its broker account number (or ``None``)."""


class BrokerAdapter(trading.TradingAdapter, Protocol):
    """A broker ``TradingAdapter`` plus the hooks the broker pump uses."""

    def bind(self, account_id: str) -> bool:
        """Binds a Joseph account so broker events route to it.

        Args:
            account_id: Joseph account.

        Returns:
            True when the broker account number is known.
        """
        ...

    def event_accounts(self) -> list[str]:
        """Lists Joseph accounts with buffered events.

        Returns:
            Account ids.
        """
        ...

    def drain_events(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns and clears buffered events of an account.

        Args:
            account_id: Joseph account.

        Returns:
            Events.
        """
        ...

    async def resync(self) -> int:
        """Re-reads broker state and buffers changes.

        Returns:
            Events buffered.
        """
        ...

    async def close(self) -> None:
        """Disconnects."""
        ...


_CLIENT_ID = re.compile(r"^j([0-9a-f]{12})[0-9a-f]{18}(-[a-z0-9]{1,8})?$")


def is_joseph_client_id(text: str) -> bool:
    """Checks whether a broker order reference is a Joseph client id.

    Args:
        text: Order reference (IBKR ``orderRef``).

    Returns:
        True for ids made by ``ids.client_order_id`` (plus child suffix).
    """
    return _CLIENT_ID.match(text) is not None


def client_order_ms(client_order_id: str) -> int | None:
    """Decodes the creation time embedded in a Joseph client order id.

    Client ids are ``j`` + the first 30 hex digits of a UUIDv7, whose first
    48 bits are the creation time in milliseconds.

    Args:
        client_order_id: Client id.

    Returns:
        Creation time, or ``None`` for foreign ids.
    """
    match = _CLIENT_ID.match(client_order_id)
    return None if match is None else int(match.group(1), 16)


def mask(account_number: str) -> str:
    """Masks a broker account number for notes and logs.

    Args:
        account_number: Account number.

    Returns:
        Text keeping only the last four characters.
    """
    tail = account_number[-4:]
    return f"***{tail}" if len(account_number) > 4 else "***"


def unavailable(
    documented: accounts.TradingCapabilities, reason: str
) -> accounts.TradingCapabilities:
    """Returns documented capabilities marked TRADING_UNAVAILABLE.

    Args:
        documented: Documented capabilities.
        reason: Why trading is unavailable (shown to users).

    Returns:
        Capabilities with the reason appended to the notes.
    """
    return dataclasses.replace(
        documented,
        status=enums.TradingStatus.TRADING_UNAVAILABLE,
        notes=(*documented.notes, reason),
    )


def gated_error(broker: str, reason: str) -> errors.AdapterUnavailableError:
    """Builds the refusal raised before anything is sent.

    Args:
        broker: Broker code.
        reason: Why the adapter is gated.

    Returns:
        Error with code ``LIVE_TRADING_DISABLED``.
    """
    return errors.AdapterUnavailableError(
        f"{broker} trading is unavailable: {reason}",
        code="LIVE_TRADING_DISABLED",
    )


def to_ms(seconds: float) -> int:
    """Converts epoch seconds to milliseconds.

    Args:
        seconds: Epoch seconds.

    Returns:
        Epoch milliseconds.
    """
    return round(seconds * 1000)


def exact_decimal(value: float) -> decimal.Decimal:
    """Converts a broker float (price, shares) to its shortest decimal.

    Args:
        value: Float from a broker SDK.

    Returns:
        Decimal of the float's ``repr``.
    """
    return decimal.Decimal(repr(float(value)))


def exact_float(value: decimal.Decimal) -> float:
    """Converts a decimal to the float a broker SDK requires, losslessly.

    Args:
        value: Price or quantity already on the tick/lot grid.

    Returns:
        Float whose shortest representation equals ``value``.

    Raises:
        AdapterUnavailableError: When the value cannot round-trip (nothing
            is sent).
    """
    result = float(value)
    if decimal.Decimal(repr(result)) != value:
        raise errors.AdapterUnavailableError(
            f"{value} cannot be represented exactly", code="VALIDATION_FAILED"
        )
    return result


class EventBuffer:
    """Ordered private-stream events per Joseph account."""

    def __init__(self) -> None:
        """Initializes an empty buffer."""
        self._events: dict[str, list[trading.VenueEvent]] = {}
        self._seq = 0
        self._last: dict[str, trading.VenueOrderState] = {}
        self._fills: set[str] = set()

    def _next(self) -> int:
        """Returns the next sequence number.

        Returns:
            Sequence.
        """
        self._seq += 1
        return self._seq

    def order(self, account_id: str, state: trading.VenueOrderState) -> bool:
        """Buffers an order update unless it repeats the last one.

        Args:
            account_id: Joseph account.
            state: Venue state.

        Returns:
            True when buffered.
        """
        key = f"{account_id}|{state.client_order_id}"
        last = self._last.get(key)
        if last is not None and (last.status, last.filled_base) == (
            state.status,
            state.filled_base,
        ):
            return False
        self._last[key] = state
        self._events.setdefault(account_id, []).append(
            trading.VenueEvent(account_id, self._next(), order=state)
        )
        return True

    def fill(self, account_id: str, fill: trading.VenueFill) -> bool:
        """Buffers a fill once per venue fill id.

        Args:
            account_id: Joseph account.
            fill: Fill.

        Returns:
            True when buffered.
        """
        if fill.exchange_fill_id in self._fills:
            return False
        self._fills.add(fill.exchange_fill_id)
        self._events.setdefault(account_id, []).append(
            trading.VenueEvent(account_id, self._next(), fill=fill)
        )
        return True

    def drain(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns and clears the buffered events of an account.

        Args:
            account_id: Joseph account.

        Returns:
            Events in arrival order.
        """
        return self._events.pop(account_id, [])

    def account_ids(self) -> list[str]:
        """Lists accounts with buffered events.

        Returns:
            Account ids.
        """
        return [a for a, events in self._events.items() if events]

    async def stream(
        self, account_id: str, poll_s: float = 0.1
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams events by polling the buffer.

        Args:
            account_id: Joseph account.
            poll_s: Poll interval.

        Yields:
            Events.
        """
        while True:
            for event in self.drain(account_id):
                yield event
            await asyncio.sleep(poll_s)


class Pacer:
    """Sliding-window rate limiter (at most ``limit`` per ``window_s``)."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initializes the pacer.

        Args:
            limit: Requests per window (0 disables pacing).
            window_s: Window length in seconds.
            clock: Monotonic clock.
            sleep: Async sleep.
        """
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._sleep = sleep
        self._sent: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()
        self.waits = 0

    async def acquire(self) -> None:
        """Waits until one more request fits the window."""
        if self._limit <= 0:
            return
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self._window:
                    self._sent.popleft()
                if len(self._sent) < self._limit:
                    self._sent.append(now)
                    return
                self.waits += 1
                await self._sleep(self._window - (now - self._sent[0]))


class FailClosedBrokerAdapter:
    """Documented broker capabilities without any ability to trade.

    Attributes:
        calls: Names of methods invoked (used by safety tests).
    """

    def __init__(
        self, documented: accounts.TradingCapabilities, reason: str
    ) -> None:
        """Initializes the adapter.

        Args:
            documented: Capabilities the broker would offer.
            reason: Why trading is unavailable (shown to users).
        """
        self._documented = documented
        self._reason = reason
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        """Returns the broker code."""
        return self._documented.venue

    def _refuse(self, method: str) -> errors.AdapterUnavailableError:
        """Records a call and builds the refusal.

        Args:
            method: Method name.

        Returns:
            Error to raise.
        """
        self.calls.append(method)
        return gated_error(self.name, self._reason)

    async def capabilities(
        self, account_id: str
    ) -> accounts.TradingCapabilities:
        """Returns documented capabilities with TRADING_UNAVAILABLE.

        Args:
            account_id: Account.

        Returns:
            Capabilities.
        """
        del account_id
        return unavailable(self._documented, self._reason)

    async def health(self) -> enums.HealthState:
        """Returns UNAVAILABLE."""
        return enums.HealthState.UNAVAILABLE

    async def account_snapshot(
        self, account_id: str
    ) -> trading.VenueAccountSnapshot:
        """Refuses.

        Args:
            account_id: Account.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("account_snapshot")

    async def list_open_orders(
        self, account_id: str
    ) -> Sequence[trading.VenueOrderState]:
        """Refuses.

        Args:
            account_id: Account.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("list_open_orders")

    async def list_positions(
        self, account_id: str
    ) -> Sequence[trading.VenuePosition]:
        """Refuses.

        Args:
            account_id: Account.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("list_positions")

    async def list_recent_fills(
        self, account_id: str, since_ms: int = 0
    ) -> Sequence[trading.VenueFill]:
        """Refuses.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("list_recent_fills")

    async def submit_order(
        self, request: trading.VenueOrderRequest
    ) -> trading.VenueSubmitResult:
        """Refuses before sending anything.

        Args:
            request: Order.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("submit_order")

    async def cancel_order(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState:
        """Refuses.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("cancel_order")

    async def amend_order(
        self,
        account_id: str,
        client_order_id: str,
        limit_price: decimal.Decimal | None,
        trigger_price: decimal.Decimal | None,
        quantity_base: decimal.Decimal | None,
    ) -> trading.VenueOrderState:
        """Refuses.

        Args:
            account_id: Account.
            client_order_id: Client id.
            limit_price: Limit.
            trigger_price: Trigger.
            quantity_base: Quantity.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("amend_order")

    async def get_order_by_client_id(
        self, account_id: str, client_order_id: str
    ) -> trading.VenueOrderState | None:
        """Refuses.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Raises:
            AdapterUnavailableError: Always.
        """
        raise self._refuse("get_order_by_client_id")

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Refuses.

        Args:
            account_id: Account.

        Raises:
            AdapterUnavailableError: Always.

        Yields:
            Nothing.
        """
        raise self._refuse("stream_account_events")
        yield  # pragma: no cover

    def drain_events(self, account_id: str) -> list[trading.VenueEvent]:
        """Returns no events (nothing is ever sent).

        Args:
            account_id: Account.

        Returns:
            Empty list.
        """
        del account_id
        return []

    def event_accounts(self) -> list[str]:
        """Lists no accounts (nothing is ever buffered).

        Returns:
            Empty list.
        """
        return []

    def bind(self, account_id: str) -> bool:
        """Accepts no account bindings.

        Args:
            account_id: Account.

        Returns:
            False.
        """
        del account_id
        return False

    async def resync(self) -> int:
        """Does nothing.

        Returns:
            Zero.
        """
        return 0

    async def close(self) -> None:
        """Does nothing."""
