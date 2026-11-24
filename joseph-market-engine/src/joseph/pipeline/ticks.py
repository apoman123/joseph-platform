# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""``TickSizeSource`` backed by the market-data instrument registry."""

from __future__ import annotations

import decimal

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.app import registry as registry_lib

_TTL_MS = 6 * 3_600_000
"""How long a resolved tick size is reused."""


class RegistryTickSizes:
    """Resolves tick sizes from venue instrument lists, with a cache."""

    def __init__(
        self,
        registry: registry_lib.InstrumentRegistry,
        clock: clock_lib.Clock,
        ttl_ms: int = _TTL_MS,
    ) -> None:
        """Initializes the source.

        Args:
            registry: Instrument registry.
            clock: Time source for cache expiry.
            ttl_ms: Cache lifetime.
        """
        self._registry = registry
        self._clock = clock
        self._ttl_ms = ttl_ms
        self._cache: dict[
            instruments.InstrumentId, tuple[int, decimal.Decimal | None]
        ] = {}

    async def tick_size(
        self, instrument_id: instruments.InstrumentId
    ) -> decimal.Decimal | None:
        """Returns the instrument's tick size.

        Args:
            instrument_id: Exact venue instrument.

        Returns:
            Tick size, or ``None`` when unknown or non-positive.
        """
        now = self._clock.now_ms()
        cached = self._cache.get(instrument_id)
        if cached is not None and now - cached[0] < self._ttl_ms:
            return cached[1]
        instrument = await self._registry.get(instrument_id)
        tick = instrument.tick_size if instrument.tick_size > 0 else None
        self._cache[instrument_id] = (now, tick)
        return tick
