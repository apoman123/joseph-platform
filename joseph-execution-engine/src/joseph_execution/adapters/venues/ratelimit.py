# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Weighted sliding-window rate limiting for venue requests.

Each venue documents its limits as "weight W per window T" budgets (for
example Binance USDⓈ-M: 2400 request weight per minute per IP and 1200
orders per minute per account). A ``WindowLimiter`` holds several named
budgets; a request names its cost in each budget it consumes and waits
until every budget has room. The sliding window is stricter than the fixed
windows most venues use, so the venue limit is never exceeded.

Waiting is bounded: when the wait would exceed ``max_wait_s`` the request
is refused *before sending* with ``RateLimitedError`` (the OMS then knows
nothing was sent). A venue 429/418 answer or a ``Retry-After`` header
pauses every budget through ``penalize``.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph_execution.domain import errors


@dataclasses.dataclass(frozen=True, slots=True)
class Budget:
    """One documented limit.

    Attributes:
        name: Budget name used by request costs (e.g. ``weight``).
        capacity: Units allowed per window.
        window_s: Window length in seconds.
    """

    name: str
    capacity: int
    window_s: float


class WindowLimiter:
    """Sliding-window limiter over several named budgets.

    Attributes:
        waits: Number of times a request had to wait (metrics, tests).
    """

    def __init__(
        self,
        venue: str,
        budgets: Sequence[Budget],
        *,
        max_wait_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initializes the limiter.

        Args:
            venue: Venue code (for messages).
            budgets: Documented budgets.
            max_wait_s: Longest wait before refusing a request.
            monotonic: Monotonic clock in seconds (injectable).
            sleep: Sleep function (injectable).
        """
        self._venue = venue
        self._budgets = {b.name: b for b in budgets}
        self._events: dict[str, collections.deque[tuple[float, int]]] = {
            b.name: collections.deque() for b in budgets
        }
        self._max_wait = max_wait_s
        self._now = monotonic
        self._sleep = sleep
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()
        self.waits = 0

    def used(self, name: str) -> int:
        """Returns the units consumed in the current window.

        Args:
            name: Budget name.

        Returns:
            Units used.
        """
        self._expire(self._now())
        return sum(w for _, w in self._events[name])

    def _expire(self, now: float) -> None:
        """Drops events older than their window.

        Args:
            now: Current monotonic time.
        """
        for name, events in self._events.items():
            horizon = now - self._budgets[name].window_s
            while events and events[0][0] <= horizon:
                events.popleft()

    def _wait_needed(self, costs: Mapping[str, int], now: float) -> float:
        """Computes how long until every budget has room.

        Args:
            costs: Units per budget.
            now: Current monotonic time.

        Returns:
            Seconds to wait (0 when the request can go now).

        Raises:
            ValueError: For unknown budgets or a cost above capacity.
        """
        wait = max(0.0, self._blocked_until - now)
        for name, cost in costs.items():
            budget = self._budgets.get(name)
            if budget is None:
                raise ValueError(f"unknown rate budget {name!r}")
            if cost > budget.capacity:
                raise ValueError(f"cost {cost} exceeds {name} capacity")
            events = self._events[name]
            used = sum(w for _, w in events)
            excess = used + cost - budget.capacity
            if excess <= 0:
                continue
            freed = 0
            for ts, weight in events:
                freed += weight
                if freed >= excess:
                    wait = max(wait, ts + budget.window_s - now)
                    break
        return wait

    async def acquire(self, costs: Mapping[str, int]) -> None:
        """Waits for room in every budget, then records the costs.

        Args:
            costs: Units per budget, e.g. ``{"weight": 1, "orders": 1}``.

        Raises:
            RateLimitedError: When the wait would exceed ``max_wait_s``;
                nothing has been sent.
        """
        async with self._lock:
            while True:
                now = self._now()
                self._expire(now)
                wait = self._wait_needed(costs, now)
                if wait <= 0:
                    for name, cost in costs.items():
                        if cost:
                            self._events[name].append((now, cost))
                    return
                if wait > self._max_wait:
                    raise errors.RateLimitedError(
                        f"{self._venue} rate budget exhausted; retry in "
                        f"{wait:.1f}s (request not sent)",
                        retry_after_s=wait,
                    )
                self.waits += 1
                await self._sleep(wait)

    def penalize(self, seconds: float) -> None:
        """Pauses every budget (venue 429/418 or ``Retry-After``).

        Args:
            seconds: Pause length.
        """
        self._blocked_until = max(
            self._blocked_until, self._now() + max(seconds, 0.0)
        )

    def observe(self, name: str, used: int) -> None:
        """Raises local usage to a venue-reported figure (never lowers it).

        Args:
            name: Budget name.
            used: Units the venue reports as used in the window.
        """
        local = self.used(name)
        if used > local:
            self._events[name].append((self._now(), used - local))
