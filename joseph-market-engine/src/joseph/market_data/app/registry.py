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
import time
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
        self._available: dict[
            instruments.Venue, list[instruments.Instrument]
        ] = {}
        self._warmup_started = 0.0
        self._maintenance: asyncio.Task[None] | None = None

    def start_warmup(self) -> None:
        """Starts background metadata loading and recovery.

        Returns:
            None; stores the background task for shutdown and shared requests.
        """
        if self._warmup is None or (
            self._warmup.done()
            and time.monotonic() - self._warmup_started >= 60
        ):
            self._warmup = asyncio.create_task(self.all_instruments())
            self._warmup_started = time.monotonic()
        if self._maintenance is None:
            self._maintenance = asyncio.create_task(self._maintain())

    async def _maintain(self) -> None:
        """Retries failed providers and refreshes expired cached metadata.

        Returns:
            None; runs until cancelled by close(), with no startup blocking.
        """
        while True:
            await asyncio.sleep(60)
            self.start_warmup()

    async def close(self) -> None:
        """Cancels outstanding metadata requests before adapters close.

        Returns:
            None; awaits cancellation of warmup and provider tasks.
        """
        tasks = [*self._loads.values()]
        if self._warmup is not None:
            tasks.append(self._warmup)
        if self._maintenance is not None:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
            self._maintenance = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loads.clear()

    async def _load_source(
        self, venue: instruments.Venue
    ) -> list[instruments.Instrument]:
        """Bounds a single source's complete metadata request sequence.

        Args:
            venue: Configured public or credential-backed source venue.

        Returns:
            Canonical listed instruments received within twenty seconds.

        Raises:
            domain_errors.VenueUnavailableError: If metadata loading times out.
            domain_errors.JosephError: For provider or transport failures.
        """
        try:
            async with asyncio.timeout(20):
                return await self._adapters[venue].list_instruments()
        except TimeoutError as exc:
            raise domain_errors.VenueUnavailableError(
                f"{venue.value} instrument discovery timed out; "
                "retrying in background"
            ) from exc

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
            task = asyncio.create_task(self._load_source(venue))
            self._loads[venue] = task
        try:
            result = await asyncio.shield(task)
            self._available[venue] = result
            self.errors.pop(venue, None)
            return result
        except domain_errors.JosephError as exc:
            self.errors[venue] = exc.message
            raise
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
        *,
        interactive: bool = False,
    ) -> list[instruments.Instrument]:
        """Searches by base asset, native id, display symbol or name.

        Args:
            query: Case-insensitive text such as ``btc``; ``None`` or blank
                matches every instrument.
            venue: Optional venue filter.
            market_type: Optional market-type filter.
            limit: Maximum results.
            interactive: If true for an unfiltered venue search, return ready
                metadata within one second while other sources load in the
                background. Authoritative universe scans leave this false.

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
        if interactive and venue is None:
            self.start_warmup()
            if self._warmup is not None and not self._warmup.done():
                await asyncio.wait({self._warmup}, timeout=1.0)
            rows = []
            for available in self._available.values():
                rows.extend(available)
        else:
            rows = await self.all_instruments(venue)
        for row in rows:
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
