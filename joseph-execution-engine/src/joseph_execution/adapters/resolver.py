# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Adapter resolver used by the order management system (OMS).

A composition detail: the OMS asks the registry which adapter serves an
account in a given order mode.
"""

from __future__ import annotations

from collections.abc import Mapping

from joseph_execution.adapters.venues import base
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.ports import trading

# Capabilities of the fail-closed fallback for LIVE orders on a venue with no
# registered adapter. Every crypto venue and broker is registered, so in
# practice this serves the paper-only demo venue (``mock``).
_UNKNOWN_VENUE = accounts.TradingCapabilities(
    venue="mock",
    status=enums.TradingStatus.TRADING_UNAVAILABLE,
    order_types=(),
    time_in_force=(),
    native_reduce_only=False,
    native_stop_loss=False,
    native_take_profit=False,
    native_oco=False,
    amend=False,
    batch=False,
    position_modes=(),
    margin_modes=(),
    client_order_id=False,
    query_by_client_id=False,
    notes=("The demo venue never trades live.",),
)


class AdapterRegistry:
    """Maps (account, mode) to an adapter: PAPER, SHADOW or LIVE venue.

    Attributes:
        paper: Adapter for PAPER orders (the simulator).
        shadow: Adapter for SHADOW orders (records, never sends).
        live: LIVE adapters keyed by venue or broker code.
    """

    def __init__(
        self,
        paper: trading.TradingAdapter,
        shadow: trading.TradingAdapter,
        live: Mapping[str, trading.TradingAdapter],
    ) -> None:
        """Initializes the registry.

        Args:
            paper: Paper simulator.
            shadow: Shadow recorder.
            live: LIVE adapters keyed by venue or broker code (copied).
        """
        self.paper = paper
        self.shadow = shadow
        self.live = dict(live)
        self._fallback = base.FailClosedVenueAdapter(_UNKNOWN_VENUE)

    def resolve(
        self, account: accounts.Account, mode: enums.OrderMode
    ) -> trading.TradingAdapter:
        """Returns the adapter for an account and mode.

        Args:
            account: Account the order is for; its venue selects the LIVE
                adapter.
            mode: Order mode.

        Returns:
            The paper or shadow adapter for those modes; for LIVE, the
            venue's adapter, or a fail-closed adapter that refuses every
            call when the venue has none.
        """
        if mode is enums.OrderMode.PAPER:
            return self.paper
        if mode is enums.OrderMode.SHADOW:
            return self.shadow
        return self.live.get(account.venue, self._fallback)
