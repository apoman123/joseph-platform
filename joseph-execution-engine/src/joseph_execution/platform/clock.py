# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Clock and id generator implementations."""

from __future__ import annotations

import time

from joseph_execution.domain import ids


class SystemClock:
    """Wall clock."""

    def now_ms(self) -> int:
        """Returns UTC epoch milliseconds."""
        return int(time.time() * 1000)


class ManualClock:
    """Controllable clock for tests.

    Attributes:
        current_ms: Current time.
    """

    def __init__(self, start_ms: int = 1_795_000_000_000) -> None:
        """Initializes the clock.

        Args:
            start_ms: Start time.
        """
        self.current_ms = start_ms

    def now_ms(self) -> int:
        """Returns the configured time."""
        return self.current_ms

    def advance(self, delta_ms: int) -> int:
        """Advances the clock.

        Args:
            delta_ms: Non-negative delta.

        Returns:
            New time.

        Raises:
            ValueError: On negative deltas.
        """
        if delta_ms < 0:
            raise ValueError("clocks never move backwards")
        self.current_ms += delta_ms
        return self.current_ms


class Uuid7Generator:
    """UUIDv7 id generator."""

    def new_id(self) -> str:
        """Returns a new UUIDv7."""
        return ids.uuid7()
