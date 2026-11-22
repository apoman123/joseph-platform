"""In-memory signal repository."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from joseph.signals.app import ports
from joseph.signals.domain import signal as signal_domain


def matches(signal: signal_domain.Signal, filters: ports.SignalFilter) -> bool:
    """Checks a signal against query filters.

    Args:
        signal: Signal.
        filters: Filters.

    Returns:
        True when the signal matches.
    """
    if filters.strategy_id and signal.strategy_id != filters.strategy_id:
        return False
    if filters.kinds and signal.kind not in filters.kinds:
        return False
    if filters.instrument_id and signal.instrument_id != filters.instrument_id:
        return False
    if filters.from_ms is not None and signal.bar_close_ms < filters.from_ms:
        return False
    return not (
        filters.to_ms is not None and signal.bar_close_ms >= filters.to_ms
    )


class InMemorySignalRepository:
    """Thread-safe in-memory ``SignalRepository``."""

    def __init__(self) -> None:
        """Initializes an empty store."""
        self._lock = threading.Lock()
        self._by_key: dict[tuple[str, str], signal_domain.Signal] = {}

    def add(self, signal: signal_domain.Signal) -> bool:
        """Stores a signal once per dedupe key.

        Args:
            signal: Signal.

        Returns:
            True when new.
        """
        key = (signal.tenant_id, signal.dedupe_key)
        with self._lock:
            if key in self._by_key:
                return False
            self._by_key[key] = signal
            return True

    def get(
        self, tenant_id: str, signal_id: str
    ) -> signal_domain.Signal | None:
        """Fetches one signal.

        Args:
            tenant_id: Owning tenant.
            signal_id: Signal id.

        Returns:
            Signal or ``None``.
        """
        with self._lock:
            for (tenant, _), signal in self._by_key.items():
                if tenant == tenant_id and signal.signal_id == signal_id:
                    return signal
        return None

    def query(
        self, tenant_id: str, filters: ports.SignalFilter
    ) -> Sequence[signal_domain.Signal]:
        """Lists matching signals newest first.

        Args:
            tenant_id: Owning tenant.
            filters: Filters.

        Returns:
            Signals.
        """
        with self._lock:
            found = [
                s
                for (t, _), s in self._by_key.items()
                if t == tenant_id and matches(s, filters)
            ]
        found.sort(key=lambda s: (-s.bar_close_ms, s.signal_id))
        return found[: filters.limit]
