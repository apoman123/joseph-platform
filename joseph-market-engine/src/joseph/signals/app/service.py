# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal recording and query service (tenant scoped)."""

from __future__ import annotations

from collections.abc import Sequence

from joseph.domain import errors
from joseph.domain import identity
from joseph.signals.app import ports
from joseph.signals.domain import signal as signal_domain

MAX_LIMIT = 500
"""Maximum rows per query."""


class SignalService:
    """Records signals idempotently and answers tenant-scoped queries.

    Attributes:
        repository: Signal store.
    """

    def __init__(self, repository: ports.SignalRepository) -> None:
        """Initializes the service.

        Args:
            repository: Signal store.
        """
        self.repository = repository

    def record(self, signal: signal_domain.Signal) -> bool:
        """Stores a signal once.

        Args:
            signal: Signal to store.

        Returns:
            True when new, False when its dedupe key already existed.
        """
        return self.repository.add(signal)

    def query(
        self, principal: identity.Principal, filters: ports.SignalFilter
    ) -> Sequence[signal_domain.Signal]:
        """Lists the principal's signals.

        Args:
            principal: Caller.
            filters: Filters.

        Returns:
            Signals newest first.

        Raises:
            errors.ValidationFailedError: If the range or limit is invalid.
        """
        if not 1 <= filters.limit <= MAX_LIMIT:
            raise errors.ValidationFailedError(
                f"limit must be within 1..{MAX_LIMIT}"
            )
        if (
            filters.from_ms is not None
            and filters.to_ms is not None
            and filters.from_ms >= filters.to_ms
        ):
            raise errors.ValidationFailedError("from_ms must be < to_ms")
        return self.repository.query(principal.tenant_id, filters)

    def get(
        self, principal: identity.Principal, signal_id: str
    ) -> signal_domain.Signal:
        """Fetches one of the principal's signals.

        Args:
            principal: Caller.
            signal_id: Signal id.

        Returns:
            The signal.

        Raises:
            errors.NotFoundError: If absent or owned by another tenant.
        """
        found = self.repository.get(principal.tenant_id, signal_id)
        if found is None:
            raise errors.NotFoundError(f"signal {signal_id} not found")
        return found
