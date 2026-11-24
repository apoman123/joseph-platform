# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Ports of the signals application layer."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Protocol

from joseph.signals.domain import signal as signal_domain


@dataclasses.dataclass(frozen=True, slots=True)
class SignalFilter:
    """Query filters (mirrors the terminal ``listSignals`` parameters).

    Attributes:
        strategy_id: Restrict to one strategy.
        kinds: Restrict to signal kinds.
        instrument_id: Restrict to one instrument.
        from_ms: Inclusive lower bound on bar close.
        to_ms: Exclusive upper bound on bar close.
        limit: Maximum rows.
    """

    strategy_id: str | None = None
    kinds: tuple[str, ...] = ()
    instrument_id: str | None = None
    from_ms: int | None = None
    to_ms: int | None = None
    limit: int = 100


class SignalRepository(Protocol):
    """Durable store of signals with a unique ``(tenant, dedupe_key)``."""

    def add(self, signal: signal_domain.Signal) -> bool:
        """Stores a signal unless its dedupe key already exists.

        Args:
            signal: Signal to store.

        Returns:
            True when stored, False for a duplicate.
        """
        ...

    def get(
        self, tenant_id: str, signal_id: str
    ) -> signal_domain.Signal | None:
        """Fetches one signal.

        Args:
            tenant_id: Owning tenant.
            signal_id: Signal id.

        Returns:
            The signal or ``None``.
        """
        ...

    def query(
        self, tenant_id: str, filters: SignalFilter
    ) -> Sequence[signal_domain.Signal]:
        """Lists signals newest first.

        Args:
            tenant_id: Owning tenant.
            filters: Filters.

        Returns:
            Matching signals ordered by bar close descending.
        """
        ...
