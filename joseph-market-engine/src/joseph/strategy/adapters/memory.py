"""In-memory strategy repositories for tests and DEMO deployments."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Sequence

from joseph.domain import errors
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import versions


class InMemoryStrategyRepository:
    """Thread-safe in-memory ``StrategyRepository``."""

    def __init__(self) -> None:
        """Initializes an empty store."""
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str, int], versions.StrategyVersion] = {}

    def add(self, version: versions.StrategyVersion) -> None:
        """Stores a new version.

        Args:
            version: Version to store.

        Raises:
            errors.ConflictError: If the version already exists.
        """
        key = (version.tenant_id, version.strategy_id, version.version)
        with self._lock:
            if key in self._rows:
                raise errors.ConflictError(
                    f"{version.strategy_version_id} already exists"
                )
            self._rows[key] = version

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
        with self._lock:
            return self._rows.get((tenant_id, strategy_id, version))

    def list_versions(
        self, tenant_id: str, strategy_id: str
    ) -> Sequence[versions.StrategyVersion]:
        """Lists versions newest first.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.

        Returns:
            Versions.
        """
        with self._lock:
            found = [
                v
                for (t, s, _), v in self._rows.items()
                if t == tenant_id and s == strategy_id
            ]
        return sorted(found, key=lambda v: -v.version)

    def list_latest(self, tenant_id: str) -> Sequence[versions.StrategyVersion]:
        """Lists the latest version per strategy.

        Args:
            tenant_id: Owning tenant.

        Returns:
            Versions sorted by strategy id.
        """
        latest: dict[str, versions.StrategyVersion] = {}
        with self._lock:
            for (tenant, strategy_id, _), version in self._rows.items():
                if tenant != tenant_id:
                    continue
                current = latest.get(strategy_id)
                if current is None or version.version > current.version:
                    latest[strategy_id] = version
        return [latest[k] for k in sorted(latest)]

    def update_lifecycle(
        self,
        tenant_id: str,
        strategy_id: str,
        version: int,
        state: versions.StrategyState,
        activation: versions.Activation | None,
    ) -> versions.StrategyVersion:
        """Updates lifecycle metadata only.

        Args:
            tenant_id: Owning tenant.
            strategy_id: Strategy slug.
            version: Version number.
            state: New state.
            activation: New activation.

        Returns:
            Updated version.

        Raises:
            errors.NotFoundError: If missing.
        """
        key = (tenant_id, strategy_id, version)
        with self._lock:
            current = self._rows.get(key)
            if current is None:
                raise errors.NotFoundError(f"{strategy_id}:v{version}")
            updated = dataclasses.replace(
                current, state=state, activation=activation
            )
            self._rows[key] = updated
            return updated

    def list_active(self) -> Sequence[versions.StrategyVersion]:
        """Lists ACTIVE versions across tenants.

        Returns:
            Active versions.
        """
        with self._lock:
            return [
                v
                for v in self._rows.values()
                if v.state is versions.StrategyState.ACTIVE
            ]


class InMemoryCheckpointRepository:
    """In-memory ``CheckpointRepository``."""

    def __init__(self) -> None:
        """Initializes an empty store."""
        self._rows: dict[str, dict[str, object]] = {}

    def load(
        self, key: setup_fsm.InstanceKey
    ) -> setup_fsm.InstanceState | None:
        """Loads a checkpoint.

        Args:
            key: Instance key.

        Returns:
            State or ``None``.
        """
        row = self._rows.get(key.as_text())
        return None if row is None else setup_fsm.InstanceState.from_dict(row)

    def save(self, state: setup_fsm.InstanceState, updated_at_ms: int) -> None:
        """Saves a checkpoint.

        Args:
            state: Instance state.
            updated_at_ms: Save time (unused in memory).
        """
        del updated_at_ms
        self._rows[state.key.as_text()] = state.to_dict()
