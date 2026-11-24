# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Adapter resolver used by the OMS (composition detail)."""

from __future__ import annotations

from collections.abc import Mapping

from joseph_execution.adapters.venues import base
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.ports import trading

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
    """Maps (account, mode) to an adapter: PAPER, SHADOW or LIVE venue."""

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
            live: Live adapters by venue.
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
            account: Account.
            mode: Mode.

        Returns:
            Adapter; unknown live venues get a fail-closed adapter.
        """
        if mode is enums.OrderMode.PAPER:
            return self.paper
        if mode is enums.OrderMode.SHADOW:
            return self.shadow
        return self.live.get(account.venue, self._fallback)
