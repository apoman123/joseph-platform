# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Building blocks shared by the stock, ETF and crypto heatmap builders."""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.app import heatmap_cache
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import registry as registry_lib
from joseph.market_data.domain import heatmap_metrics

INSTRUMENTS_TTL_MS = 10 * 60_000
"""How long the instrument index used to open charts is reused."""


class Budget:
    """A deadline shared by the source loads of one request.

    Attributes:
        deadline: Event-loop time after which loads are not awaited.
    """

    def __init__(self, seconds: float) -> None:
        """Starts the budget now.

        Args:
            seconds: Seconds the request may wait in total.
        """
        self.deadline = asyncio.get_running_loop().time() + seconds

    def left(self) -> float:
        """Returns the remaining seconds (never negative).

        Returns:
            Seconds left.
        """
        return max(0.0, self.deadline - asyncio.get_running_loop().time())


@dataclasses.dataclass
class Part:
    """What a builder contributes to a snapshot.

    Attributes:
        tiles: Tiles, unordered.
        universe_count: Members before the limit.
        provenance: Sources used.
        source_errors: Failures by source label.
        pending: Whether a source is still loading.
        reasons: Why a metric or grouping is unavailable, by id; ids
            without a reason are available when at least one tile has them.
    """

    tiles: list[models.HeatmapTile] = dataclasses.field(default_factory=list)
    universe_count: int = 0
    provenance: list[models.HeatmapProvenance] = dataclasses.field(
        default_factory=list
    )
    source_errors: dict[str, str] = dataclasses.field(default_factory=dict)
    pending: bool = False
    reasons: dict[str, str] = dataclasses.field(default_factory=dict)

    def note_cache(
        self, label: str, cache: heatmap_cache.SourceCache[Any]
    ) -> None:
        """Records a cache's failure and loading state.

        Args:
            label: Source label.
            cache: Cache consulted for this request.
        """
        if cache.error:
            self.source_errors[label] = cache.error
        if cache.pending and cache.value is None:
            self.pending = True


def texts(values: Mapping[str, decimal.Decimal | None]) -> dict[str, str]:
    """Formats metric values, dropping missing ones.

    Args:
        values: Values by metric id.

    Returns:
        Decimal strings by metric id.
    """
    return {
        key: heatmap_metrics.to_text(value)
        for key, value in values.items()
        if value is not None
    }


def size_value(tile: models.HeatmapTile, metric: str) -> decimal.Decimal:
    """Reads a tile's size metric for ordering.

    Args:
        tile: Tile.
        metric: Size metric id.

    Returns:
        The value, or -1 when missing (sorted last).
    """
    text = tile.metrics.get(metric)
    return decimal.Decimal(text) if text is not None else decimal.Decimal(-1)


def rank(
    tiles: Sequence[models.HeatmapTile], metric: str
) -> list[models.HeatmapTile]:
    """Orders tiles largest first by a size metric, then by symbol.

    Args:
        tiles: Tiles.
        metric: Size metric id.

    Returns:
        A new ordered list.
    """
    return sorted(
        tiles, key=lambda tile: (-size_value(tile, metric), tile.symbol)
    )


class InstrumentIndex:
    """Cached lookup of enabled instruments for opening charts.

    Attributes:
        registry: Instrument registry of the enabled venues.
    """

    def __init__(
        self, registry: registry_lib.InstrumentRegistry, clock: clock_lib.Clock
    ) -> None:
        """Creates an empty index.

        Args:
            registry: Registry of the enabled venues.
            clock: Time source.
        """
        self.registry = registry
        self._cache: heatmap_cache.SourceCache[list[instruments.Instrument]] = (
            heatmap_cache.SourceCache(
                "instrument registry", clock, INSTRUMENTS_TTL_MS
            )
        )

    def has_venue(self, venue: instruments.Venue) -> bool:
        """Tells whether a venue is enabled.

        Args:
            venue: Venue.

        Returns:
            True when the registry serves it.
        """
        return venue in self.registry.venues

    async def rows(
        self, budget: Budget, venues: Sequence[instruments.Venue]
    ) -> list[instruments.Instrument] | None:
        """Lists enabled instruments of some venues.

        Args:
            budget: Request budget.
            venues: Venues of interest.

        Returns:
            Instruments of those venues, or None while the registry loads.
        """
        if not any(self.has_venue(venue) for venue in venues):
            return []
        rows = await self._cache.get(
            self.registry.all_instruments, budget_s=budget.left()
        )
        if rows is None:
            return None
        wanted = set(venues)
        return [row for row in rows if row.instrument_id.venue in wanted]

    async def close(self) -> None:
        """Cancels a running registry load."""
        await self._cache.close()
