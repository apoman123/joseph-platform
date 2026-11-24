# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fail-closed venue adapter.

Used wherever no trading adapter can serve an account: venues without
credentials when no credential source is wired, venues or brokers that
have no live adapter (for example Cathay), and the resolver fallback for
unknown venues. It reports ``TRADING_UNAVAILABLE`` and refuses every
account and trading call before anything is sent.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import AsyncIterator
from collections.abc import Sequence

from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.ports import trading


class FailClosedVenueAdapter:
    """Declares documented venue capabilities but cannot trade.

    Attributes:
        calls: Names of methods invoked (used by safety tests).
    """

    def __init__(
        self,
        documented: accounts.TradingCapabilities,
        reason: str = (
            "No live trading adapter or credentials are configured for "
            "this venue; trading is disabled."
        ),
    ) -> None:
        """Initializes the adapter.

        Args:
            documented: Capabilities documented by the venue's official API.
            reason: Why the venue cannot trade (shown in capability notes).
        """
        self._documented = documented
        self._reason = reason
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        """Returns the venue code."""
        return self._documented.venue

    def _refuse(self, method: str) -> errors.AdapterUnavailableError:
        """Records a call and builds the refusal.

        Args:
            method: Method name.

        Returns:
            Error to raise.
        """
        self.calls.append(method)
        return errors.AdapterUnavailableError(
            f"{self.name} live trading is not available: {self._reason}",
            code="LIVE_TRADING_DISABLED",
        )

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
        return dataclasses.replace(
            self._documented,
            status=enums.TradingStatus.TRADING_UNAVAILABLE,
            notes=(*self._documented.notes, self._reason),
        )

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
