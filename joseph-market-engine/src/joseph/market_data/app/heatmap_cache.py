# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Stale-while-revalidate caching of slow public heatmap sources.

A request waits for a load only while no value is cached, and then at most
its time budget; the load keeps running in the background so the next
request finds it. A stale value is served at once while it refreshes. Load
failures are kept as messages beside the last good value, never replacing it
with invented data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from collections.abc import Callable

from joseph.domain import clock as clock_lib
from joseph.domain import errors

_LOG = logging.getLogger(__name__)


class SourceCache[T]:
    """One cached value with a single in-flight refresh.

    Attributes:
        name: Source label for error messages.
        ttl_ms: Age after which the value is refreshed.
        value: Last good value, or None before the first success.
        loaded_ms: Time of the last success, or None.
        error: Message of the last failed load, or None after a success.
    """

    def __init__(self, name: str, clock: clock_lib.Clock, ttl_ms: int) -> None:
        """Creates an empty cache.

        Args:
            name: Source label for error messages.
            clock: Time source.
            ttl_ms: Freshness lifetime in milliseconds.
        """
        self.name = name
        self.ttl_ms = ttl_ms
        self.value: T | None = None
        self.loaded_ms: int | None = None
        self.error: str | None = None
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    @property
    def pending(self) -> bool:
        """Whether a load is running."""
        return self._task is not None and not self._task.done()

    def fresh(self, ttl_ms: int | None = None) -> bool:
        """Tells whether the value is younger than a lifetime.

        Args:
            ttl_ms: Lifetime override, default ``ttl_ms``.

        Returns:
            True when a value exists and is fresh.
        """
        if self.loaded_ms is None:
            return False
        age = self._clock.now_ms() - self.loaded_ms
        return age < (self.ttl_ms if ttl_ms is None else ttl_ms)

    async def _run(self, load: Callable[[], Awaitable[T]]) -> None:
        """Runs one load and records its value or failure.

        Args:
            load: Loader.
        """
        try:
            value = await load()
        except errors.JosephError as err:
            self.error = err.message[:300]
            _LOG.info("heatmap source %s failed: %s", self.name, err.message)
            return
        self.value = value
        self.loaded_ms = self._clock.now_ms()
        self.error = None

    async def get(
        self,
        load: Callable[[], Awaitable[T]],
        *,
        budget_s: float,
        ttl_ms: int | None = None,
    ) -> T | None:
        """Returns the value, refreshing it when stale.

        Args:
            load: Loader of a new value.
            budget_s: Longest wait when nothing is cached yet.
            ttl_ms: Lifetime override, default ``ttl_ms``.

        Returns:
            The fresh or stale value, or None while the first load runs or
            after it failed (``error`` then says why).
        """
        if self.fresh(ttl_ms):
            return self.value
        if not self.pending:
            self._task = asyncio.create_task(self._run(load))
        if self.value is None and self._task is not None and budget_s > 0:
            await asyncio.wait({self._task}, timeout=budget_s)
        return self.value

    async def close(self) -> None:
        """Cancels a running load."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
