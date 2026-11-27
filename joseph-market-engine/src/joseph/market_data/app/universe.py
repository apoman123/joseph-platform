# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared real-market universe and bounded snapshot loading.

Shared real-market universe and bounded snapshot loading for scans/heatmaps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.app import service


class UniverseService:
    """Shares actual instrument metadata.

    Shares actual instrument metadata and quote coverage across user workflows.

    Attributes:
        market_data: Authoritative source registry and quote service.
    """

    def __init__(self, market_data: service.MarketDataService) -> None:
        """Creates an initially empty cache without requesting provider data.

        Args:
            market_data: Authoritative registry and snapshot service.
        """
        self.market_data = market_data
        self._snapshots: dict[str, market.MarketSnapshot] = {}
        self._loaded_at: dict[str, int] = {}
        self._slots = asyncio.Semaphore(4)
        self._lock = asyncio.Lock()

    async def select(
        self,
        *,
        venue: instruments.Venue | None = None,
        market_type: instruments.MarketType | None = None,
        query: str = "",
    ) -> list[instruments.Instrument]:
        """Selects an actual provider universe.

        Selects an actual provider universe without inventing index membership.

        Args:
            venue: Optional configured venue, default all enabled sources.
            market_type: Optional stock, ETF, perp or other market category.
            query: Optional symbol or localized name search.

        Returns:
            Matching real instruments sorted by canonical identifier.

        Raises:
            errors.JosephError: If an explicitly selected venue is unavailable.
        """
        rows = await self.market_data.registry.all_instruments(venue)
        needle = query.strip().casefold()
        return sorted(
            (
                row
                for row in rows
                if (
                    market_type is None
                    or row.instrument_id.market_type == market_type
                )
                and (
                    not needle
                    or needle
                    in " ".join(
                        [
                            str(row.instrument_id),
                            row.name or "",
                            row.local_name or "",
                        ]
                    ).casefold()
                )
            ),
            key=lambda row: str(row.instrument_id),
        )

    async def quotes(
        self,
        rows: Sequence[instruments.Instrument],
        *,
        budget_s: float = 12,
        max_age_ms: int = 15000,
    ) -> tuple[dict[str, market.MarketSnapshot], dict[str, str]]:
        """Loads real quotes with bulk requests, honest partial coverage.

        Loads real quotes with bulk requests, honest partial coverage and
        bounds.

        Args:
            rows: User-selected canonical instrument metadata.
            budget_s: Maximum waiting seconds for this request, default twelve.
            max_age_ms: Desired cache age in milliseconds; older entries remain
                visible with their original source timestamp while refreshing.

        Returns:
            Actual available snapshots and per-instrument missing/error reasons.
            Missing quotes are prioritized over refreshing already known ones.
        """
        now = self.market_data.now_ms()
        selected = {str(row.instrument_id): row for row in rows}
        pending = [
            row
            for key, row in selected.items()
            if key not in self._snapshots
            or now - self._loaded_at.get(key, 0) > max_age_ms
        ]
        pending.sort(
            key=lambda row: (
                str(row.instrument_id) in self._snapshots,
                self._loaded_at.get(str(row.instrument_id), 0),
            )
        )
        failures: dict[str, str] = {}

        async def one(row: instruments.Instrument) -> None:
            """Fetches one real quote for providers.

            Fetches one real quote for providers without a free bulk endpoint.

            Args:
                row: Actual selected provider instrument.

            Returns:
                None after caching a quote or recording its source error.
            """
            async with self._slots:
                try:
                    snapshot = await self.market_data.get_snapshot(
                        row.instrument_id
                    )
                    self._snapshots[str(row.instrument_id)] = snapshot
                    self._loaded_at[str(row.instrument_id)] = (
                        self.market_data.now_ms()
                    )
                except errors.JosephError as exc:
                    failures[str(row.instrument_id)] = exc.message

        async def batch(group: list[instruments.Instrument]) -> None:
            """Uses provider-supported selected-symbol batches.

            Uses provider-supported selected-symbol batches with a safe
            fallback.

            Args:
                group: At most two hundred listings from one venue.

            Returns:
                None after collecting actual returned records or per-symbol
                errors.
            """
            try:
                snapshots = await self.market_data.get_snapshot_batch(
                    [row.instrument_id for row in group]
                )
                for snapshot in snapshots:
                    self._snapshots[str(snapshot.instrument_id)] = snapshot
                    self._loaded_at[str(snapshot.instrument_id)] = (
                        self.market_data.now_ms()
                    )
                returned = {
                    str(snapshot.instrument_id) for snapshot in snapshots
                }
                for row in group:
                    if str(row.instrument_id) not in returned:
                        failures[str(row.instrument_id)] = (
                            "provider returned no quote"
                        )
            except errors.UnsupportedError:
                await asyncio.gather(*(one(row) for row in group))
            except errors.JosephError as exc:
                failures.update(
                    {str(row.instrument_id): exc.message for row in group}
                )

        # Serialize refresh sweeps so multiple chart tabs cannot multiply a free
        # provider's load. Later sweeps read the shared quote cache.
        try:
            async with asyncio.timeout(budget_s), self._lock:
                now = self.market_data.now_ms()
                pending = [
                    row
                    for row in pending
                    if str(row.instrument_id) not in self._snapshots
                    or now - self._loaded_at.get(str(row.instrument_id), 0)
                    > max_age_ms
                ]
                for venue in dict.fromkeys(
                    row.instrument_id.venue for row in pending
                ):
                    group = [
                        row
                        for row in pending
                        if row.instrument_id.venue == venue
                    ]
                    for offset in range(0, len(group), 200):
                        await batch(group[offset : offset + 200])
        except TimeoutError:
            for key in selected:
                if key not in self._snapshots:
                    failures.setdefault(
                        key, "quote pending or provider request budget reached"
                    )
        return (
            {
                key: self._snapshots[key]
                for key in selected
                if key in self._snapshots
            },
            failures,
        )
