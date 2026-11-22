"""Qualification service: scans venues and applies the policy.

Per venue: list instruments, fetch bulk snapshots, preselect the top
``top_n`` by 24h quote turnover (default 50, as in the Binance reference
job; applied to every venue to bound API usage), then fetch order books and
any missing open interest / funding per candidate and evaluate. Every
candidate yields a record; failed data fetches become missing inputs and
therefore ``INSUFFICIENT_DATA``, never a pass.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Mapping
from typing import Protocol

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.app import ports
from joseph.market_data.domain import books
from joseph.screening.domain import policy as policy_lib

_LOG = logging.getLogger(__name__)


class TrendingPort(Protocol):
    """Source of trending base-asset symbols (ordering bonus only)."""

    async def symbols(self) -> frozenset[str]:
        """Returns upper-case trending symbols."""
        ...


@dataclasses.dataclass(frozen=True, slots=True)
class QualificationReport:
    """Contract ``QualificationReport``.

    Attributes:
        policy_version: Policy version.
        as_of_ms: Report time.
        venue: Venue filter, if any.
        records: Ranked records.
    """

    policy_version: str
    as_of_ms: int
    venue: instruments.Venue | None
    records: tuple[policy_lib.QualificationRecord, ...]

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "policy_version": self.policy_version,
            "as_of_ms": self.as_of_ms,
            "venue": None if self.venue is None else self.venue.value,
            "records": [r.to_contract() for r in self.records],
        }


class QualificationService:
    """Computes and caches qualification reports per venue."""

    def __init__(
        self,
        adapters: Mapping[instruments.Venue, ports.MarketDataPort],
        clock: clock_lib.Clock,
        policy: policy_lib.QualificationPolicy | None = None,
        *,
        top_n: int = 50,
        trending: TrendingPort | None = None,
        ttl_ms: int = 120_000,
        concurrency: int = 4,
        book_depth: int = 100,
    ) -> None:
        """Initializes the service.

        Args:
            adapters: Adapter per venue.
            clock: Time source.
            policy: Qualification policy (default parity policy).
            top_n: Candidates per venue by turnover.
            trending: Optional trending source.
            ttl_ms: Report cache lifetime.
            concurrency: Concurrent per-candidate fetches.
            book_depth: Book levels requested for the slippage walk.
        """
        self._adapters = dict(adapters)
        self._clock = clock
        self.policy = policy or policy_lib.QualificationPolicy()
        self._top_n = top_n
        self._trending = trending
        self._ttl_ms = ttl_ms
        self._semaphore = asyncio.Semaphore(concurrency)
        self._book_depth = book_depth
        self._cache: dict[
            instruments.Venue, tuple[int, list[policy_lib.QualificationRecord]]
        ] = {}
        self._locks: dict[instruments.Venue, asyncio.Lock] = {}

    async def _trending_symbols(self) -> frozenset[str]:
        """Fetches trending symbols, tolerating failures.

        Returns:
            Symbols (empty when disabled or unavailable).
        """
        if self._trending is None:
            return frozenset()
        try:
            return await self._trending.symbols()
        except errors.JosephError as err:
            _LOG.warning("trending source unavailable: %s", err.message)
            return frozenset()

    async def _input_for(
        self,
        adapter: ports.MarketDataPort,
        listed: instruments.Instrument,
        snapshot: market.MarketSnapshot,
        trending: frozenset[str],
    ) -> policy_lib.QualificationInput:
        """Collects gate inputs for one candidate.

        Args:
            adapter: Venue adapter.
            listed: Instrument.
            snapshot: Bulk snapshot.
            trending: Trending symbols.

        Returns:
            Qualification input.
        """
        instrument = listed.instrument_id
        async with self._semaphore:
            book: market.OrderBook | None
            try:
                book = await adapter.fetch_order_book(
                    instrument, self._book_depth
                )
            except errors.JosephError:
                book = None
            open_interest = snapshot.open_interest
            if open_interest is None:
                try:
                    open_interest = await adapter.fetch_open_interest(
                        instrument
                    )
                except errors.JosephError:
                    open_interest = None
            funding = snapshot.funding
            if funding is None:
                try:
                    funding = await adapter.fetch_funding(instrument)
                except errors.JosephError:
                    funding = None
        spread = policy_lib.Observation(snapshot.spread_pct, snapshot.as_of_ms)
        slippage = policy_lib.Observation(None, None)
        if book is not None and not book.stale:
            if book.best_bid is not None and book.best_ask is not None:
                mid = (book.best_bid + book.best_ask) / 2
                spread = policy_lib.Observation(
                    (book.best_ask - book.best_bid) / mid * 100,
                    book.received_at_ms,
                )
            slippage = policy_lib.Observation(
                books.worst_side_slippage_pct(
                    book, self.policy.slippage_notional_usd
                ),
                book.received_at_ms,
            )
        funding_obs = policy_lib.Observation(None, None)
        if funding is not None:
            funding_obs = policy_lib.Observation(
                policy_lib.normalized_8h_pct(
                    funding.rate, funding.interval_hours
                ),
                funding.as_of_ms,
            )
        return policy_lib.QualificationInput(
            instrument_id=instrument,
            display_symbol=listed.display_symbol,
            asset_class=listed.asset_class,
            turnover_24h_usd=policy_lib.Observation(
                snapshot.turnover_24h_quote, snapshot.as_of_ms
            ),
            open_interest_usd=policy_lib.Observation(
                None if open_interest is None else open_interest.notional_usd,
                None if open_interest is None else open_interest.as_of_ms,
            ),
            spread_pct=spread,
            slippage_pct=slippage,
            funding_8h_pct=funding_obs,
            funding_interval_hours=(
                None if funding is None else funding.interval_hours
            ),
            trending=listed.key.canonical_base.upper() in trending,
            tradable=listed.status is instruments.InstrumentStatus.TRADING,
        )

    async def evaluate_venue(
        self, venue: instruments.Venue
    ) -> list[policy_lib.QualificationRecord]:
        """Scans one venue now (bypassing the cache).

        Args:
            venue: Venue.

        Returns:
            Ranked records.

        Raises:
            errors.NotFoundError: If the venue is not enabled.
        """
        adapter = self._adapters.get(venue)
        if adapter is None:
            raise errors.NotFoundError(f"venue {venue.value} is not enabled")
        listed = {
            row.instrument_id: row for row in await adapter.list_instruments()
        }
        snapshots = await adapter.fetch_snapshots()
        eligible = [
            s
            for s in snapshots
            if s.instrument_id in listed
            and (
                venue is not instruments.Venue.BINANCE
                or listed[s.instrument_id].key.quote == "USDT"
            )
        ]
        eligible.sort(key=lambda s: s.turnover_24h_quote or 0, reverse=True)
        candidates = eligible[: self._top_n]
        trending = await self._trending_symbols()
        inputs = await asyncio.gather(
            *(
                self._input_for(adapter, listed[s.instrument_id], s, trending)
                for s in candidates
            )
        )
        now = self._clock.now_ms()
        return policy_lib.rank(
            [policy_lib.evaluate(item, self.policy, now) for item in inputs]
        )

    async def _records(
        self, venue: instruments.Venue, refresh: bool
    ) -> list[policy_lib.QualificationRecord]:
        """Returns cached or fresh records for a venue.

        Args:
            venue: Venue.
            refresh: Force a new scan.

        Returns:
            Records.
        """
        lock = self._locks.setdefault(venue, asyncio.Lock())
        async with lock:
            cached = self._cache.get(venue)
            now = self._clock.now_ms()
            if (
                cached is not None
                and not refresh
                and (now - cached[0] < self._ttl_ms)
            ):
                return cached[1]
            records = await self.evaluate_venue(venue)
            self._cache[venue] = (now, records)
            return records

    async def report(
        self,
        venue: instruments.Venue | None = None,
        status: policy_lib.Status | None = None,
        refresh: bool = False,
    ) -> QualificationReport:
        """Builds a report for one or all venues.

        Venues that cannot be reached are skipped when reporting all venues
        and raise when requested explicitly.

        Args:
            venue: Optional venue.
            status: Optional status filter.
            refresh: Force new scans.

        Returns:
            Report.
        """
        venues = [venue] if venue is not None else list(self._adapters)
        records: list[policy_lib.QualificationRecord] = []
        for target in venues:
            try:
                records.extend(await self._records(target, refresh))
            except errors.VenueUnavailableError as err:
                if venue is not None:
                    raise
                _LOG.warning(
                    "qualification skipped %s: %s", target.value, err.message
                )
        if venue is None:
            records = policy_lib.rank(records)
        if status is not None:
            records = [r for r in records if r.status is status]
        return QualificationReport(
            policy_version=self.policy.version,
            as_of_ms=self._clock.now_ms(),
            venue=venue,
            records=tuple(records),
        )
