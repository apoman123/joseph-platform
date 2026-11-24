# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Ports of the strategy application layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from joseph.signals.domain import signal as signal_domain
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.domain import versions


class StrategyRepository(Protocol):
    """Store of immutable strategy versions."""

    def add(self, version: versions.StrategyVersion) -> None:
        """Stores a new version.

        Args:
            version: Version to store.

        Raises:
            errors.ConflictError: If ``(tenant, strategy, version)`` exists.
        """
        ...

    def get(
        self, tenant_id: str, strategy_id: str, version: int
    ) -> versions.StrategyVersion | None:
        """Fetches one version.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.
            version: Version number.

        Returns:
            The version or ``None``.
        """
        ...

    def list_versions(
        self, tenant_id: str, strategy_id: str
    ) -> Sequence[versions.StrategyVersion]:
        """Lists every version of a strategy, newest first.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.

        Returns:
            Versions.
        """
        ...

    def list_latest(self, tenant_id: str) -> Sequence[versions.StrategyVersion]:
        """Lists the latest version of each of a tenant's strategies.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Versions sorted by strategy id.
        """
        ...

    def update_lifecycle(
        self,
        tenant_id: str,
        strategy_id: str,
        version: int,
        state: versions.StrategyState,
        activation: versions.Activation | None,
    ) -> versions.StrategyVersion:
        """Changes lifecycle metadata; the definition is immutable.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.
            version: Version number.
            state: New state.
            activation: New activation metadata.

        Returns:
            The updated version.

        Raises:
            errors.NotFoundError: If the version does not exist.
        """
        ...

    def list_active(self) -> Sequence[versions.StrategyVersion]:
        """Lists every ACTIVE version across tenants (for the runtime).

        Returns:
            Active versions.
        """
        ...


class CheckpointRepository(Protocol):
    """Store of setup state machine checkpoints."""

    def load(
        self, key: setup_fsm.InstanceKey
    ) -> setup_fsm.InstanceState | None:
        """Loads a checkpoint.

        Args:
            key: Instance key.

        Returns:
            The state or ``None``.
        """
        ...

    def save(self, state: setup_fsm.InstanceState, updated_at_ms: int) -> None:
        """Saves (upserts) a checkpoint.

        Args:
            state: Instance state.
            updated_at_ms: Save time.
        """
        ...


class SignalPublisher(Protocol):
    """Receives newly recorded signals (WS hub, alert dispatcher)."""

    async def publish(self, signal: signal_domain.Signal) -> None:
        """Publishes a new signal.

        Args:
            signal: Signal that was recorded for the first time.
        """
        ...


class QualificationGate(Protocol):
    """Answers whether an instrument is in a QUALIFIED_ONLY universe."""

    def qualification(
        self, instrument_id: str, at_ms: int, min_minutes: int
    ) -> tristate.TriState:
        """Returns the qualification of an instrument at a time.

        Args:
            instrument_id: Canonical instrument id.
            at_ms: Bar close.
            min_minutes: Minimum continuous qualification time.

        Returns:
            TRUE, FALSE, or UNKNOWN when data is missing.
        """
        ...


class MarketContextProvider(Protocol):
    """Supplies market context (funding, OI, spread) for MARKET operands."""

    def context(
        self, instrument_id: str, at_ms: int
    ) -> evaluator.MarketContext | None:
        """Returns market observations up to a time.

        Args:
            instrument_id: Canonical instrument id.
            at_ms: Bar close.

        Returns:
            Context or ``None`` when unavailable.
        """
        ...
