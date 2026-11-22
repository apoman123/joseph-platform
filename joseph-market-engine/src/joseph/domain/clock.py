"""Clock port so time-dependent logic stays deterministic under test."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Source of the current UTC time in epoch milliseconds."""

    def now_ms(self) -> int:
        """Returns the current UTC time in epoch milliseconds."""
        ...


class SystemClock:
    """Wall-clock implementation of ``Clock``."""

    def now_ms(self) -> int:
        """Returns the current UTC time in epoch milliseconds."""
        return int(time.time() * 1000)


class ManualClock:
    """Controllable clock for tests and replays.

    Attributes:
        current_ms: The time returned by ``now_ms``.
    """

    def __init__(self, start_ms: int = 0) -> None:
        """Initializes the clock.

        Args:
            start_ms: Initial time in UTC epoch milliseconds.
        """
        self.current_ms = start_ms

    def now_ms(self) -> int:
        """Returns the configured time."""
        return self.current_ms

    def advance(self, delta_ms: int) -> int:
        """Moves the clock forward.

        Args:
            delta_ms: Non-negative milliseconds to add.

        Returns:
            The new current time.

        Raises:
            ValueError: If ``delta_ms`` is negative.
        """
        if delta_ms < 0:
            raise ValueError("clocks never move backwards")
        self.current_ms += delta_ms
        return self.current_ms
