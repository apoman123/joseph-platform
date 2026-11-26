# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Instrument registry aggregated across venues.

Results are always exact-venue instruments: searching ``BTC`` returns
``binance:perp:BTCUSDT`` and ``hyperliquid:perp:BTC`` as separate entries
and never merges them. A venue that cannot be reached is reported in
``errors`` while the other venues keep serving.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from joseph.domain import errors as domain_errors
from joseph.domain import instruments
from joseph.market_data.app import ports

# Search rank of each status: tradable instruments first, delisted last.
_STATUS_ORDER = {
    instruments.InstrumentStatus.TRADING: 0,
    instruments.InstrumentStatus.PRE_LAUNCH: 1,
    instruments.InstrumentStatus.HALTED: 2,
    instruments.InstrumentStatus.UNKNOWN: 3,
    instruments.InstrumentStatus.DELISTED: 4,
}


class InstrumentRegistry:
    """Looks up and searches instruments of the configured venues.

    Attributes:
        errors: Last error message of each venue whose instrument list could
            not be fetched; a venue's entry is removed once it answers
            again.
    """

    def __init__(
        self, adapters: Mapping[instruments.Venue, ports.MarketDataPort]
    ) -> None:
        """Initializes the registry.

        Args:
            adapters: Adapter per venue.
        """
        self._adapters = dict(adapters)
        self.errors: dict[instruments.Venue, str] = {}
        self._loads: dict[
            instruments.Venue, asyncio.Task[list[instruments.Instrument]]
        ] = {}
        self._warmup: asyncio.Task[list[instruments.Instrument]] | None = None

    def start_warmup(self) -> None:
        """Starts provider metadata loading in the background.

        Returns:
            None; stores the background task for shutdown and shared requests.
        """
        if self._warmup is None:
            self._warmup = asyncio.create_task(self.all_instruments())

    async def close(self) -> None:
        """Cancels outstanding metadata requests before adapters close.

        Returns:
            None; awaits cancellation of warmup and provider tasks.
        """
        tasks = [*self._loads.values()]
        if self._warmup is not None:
            tasks.append(self._warmup)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loads.clear()

    async def _load(
        self, venue: instruments.Venue
    ) -> list[instruments.Instrument]:
        """Shares an in-progress metadata download between simultaneous callers.

        Args:
            venue: Enabled provider venue.

        Returns:
            Loaded instruments from the adapter's metadata cache.

        Raises:
            domain_errors.JosephError: If the provider request fails.
        """
        task = self._loads.get(venue)
        if task is None:
            task = asyncio.create_task(self._adapters[venue].list_instruments())
            self._loads[venue] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._loads.get(venue) is task:
                del self._loads[venue]

    @property
    def venues(self) -> tuple[instruments.Venue, ...]:
        """The configured venues, in declaration order."""
        return tuple(self._adapters)

    async def all_instruments(
        self, venue: instruments.Venue | None = None
    ) -> list[instruments.Instrument]:
        """Lists instruments of one or all venues.

        Without a filter, a venue that fails with a ``JosephError`` is
        skipped and its message kept in ``errors``.

        Args:
            venue: Optional venue filter.

        Returns:
            Instruments from reachable venues.

        Raises:
            domain_errors.NotFoundError: If ``venue`` is not configured.
            domain_errors.JosephError: If ``venue`` is given and its adapter
                fails.
        """
        targets = [venue] if venue is not None else list(self._adapters)
        for target in targets:
            if target not in self._adapters:
                raise domain_errors.NotFoundError(
                    f"venue {target.value} is not enabled"
                )
        results = await asyncio.gather(
            *(self._load(t) for t in targets),
            return_exceptions=True,
        )
        rows: list[instruments.Instrument] = []
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, domain_errors.JosephError):
                self.errors[target] = result.message
                if venue is not None:
                    raise result
                continue
            if isinstance(result, BaseException):
                raise result
            self.errors.pop(target, None)
            rows.extend(result)
        return rows

    async def get(
        self, instrument_id: instruments.InstrumentId
    ) -> instruments.Instrument:
        """Resolves one instrument.

        Args:
            instrument_id: Canonical id.

        Returns:
            The instrument with its precision metadata.

        Raises:
            domain_errors.NotFoundError: If unknown or the venue is disabled.
            domain_errors.JosephError: If the venue's adapter fails.
        """
        for row in await self.all_instruments(instrument_id.venue):
            if row.instrument_id == instrument_id:
                return row
        raise domain_errors.NotFoundError(f"unknown instrument {instrument_id}")

    async def search(
        self,
        query: str | None = None,
        venue: instruments.Venue | None = None,
        market_type: instruments.MarketType | None = None,
        limit: int = 200,
    ) -> list[instruments.Instrument]:
        """Searches by base asset, native id, display symbol or name.

        Args:
            query: Case-insensitive text such as ``btc``; ``None`` or blank
                matches every instrument.
            venue: Optional venue filter.
            market_type: Optional market-type filter.
            limit: Maximum results.

        Returns:
            Exact-venue instruments, best matches first: an exact base or
            native id match, then a base prefix match, then by status,
            venue order and native id.

        Raises:
            domain_errors.NotFoundError: If ``venue`` is not configured.
            domain_errors.JosephError: If ``venue`` is given and its adapter
                fails.
        """
        needle = (query or "").strip().upper()
        venue_rank = {v: i for i, v in enumerate(self._adapters)}
        matches = []
        for row in await self.all_instruments(venue):
            if market_type is not None and (
                row.instrument_id.market_type is not market_type
            ):
                continue
            base = row.key.canonical_base.upper()
            native = row.instrument_id.venue_market_id.upper()
            names = (row.display_symbol, row.name or "", row.local_name or "")
            if (
                needle
                and needle not in base
                and needle not in native
                and not any(needle in text.upper() for text in names)
            ):
                continue
            exact = 0 if needle in (base, native) else 1
            prefix = 0 if base.startswith(needle) else 1
            matches.append(
                (
                    (
                        exact,
                        prefix,
                        _STATUS_ORDER[row.status],
                        venue_rank[row.instrument_id.venue],
                        native,
                    ),
                    row,
                )
            )
        matches.sort(key=lambda item: item[0])
        return [row for _, row in matches[:limit]]
