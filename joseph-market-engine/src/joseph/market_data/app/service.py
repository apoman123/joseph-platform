# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Market-data application service (read side used by the API and engines).

Implements ``ports.CandleSource`` (``get_candles`` returns final candles
only) for the indicator engine, plus richer queries for the API: candle
batches with provenance and gaps, snapshots, books and venue status.

Seconds bars are built from public trades. Where the venue has a REST
trade history they are fetched like klines (only the fully covered part);
where it has none they exist only from a live trade-stream session, so
batches serve what the store holds and report the rest as gaps.

Every bar boundary (history windows, the live edge, gap repair) follows the
instrument's session calendar (``sessions.for_instrument``): exchange bars
start at the session open, and the time between sessions is never a gap.

On venues with sparse bars (``ports.sparse_bars``: TWSE, TPEx, Nasdaq,
NYSE, Cboe) a quiet interval has no bar. Their fetches report the spans
they delivered completely, which the store keeps as coverage: a missing bar
inside it is neither a reported gap nor re-fetched, while an interval no
fetch or live session covered (an outage) still is both.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import decimal
from collections.abc import Awaitable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import replay as replay_lib
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import ports
from joseph.market_data.app import registry
from joseph.market_data.app import trade_archive
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import price_adjustments

ADJUSTMENT_REFRESH_MS = price_adjustments.REFRESH_MS
"""Fifteen-minute bound on cached corporate-action normalized history."""

DEFAULT_LIMIT = 500
"""Bars a batch returns when the caller gives no limit."""
MAX_LIMIT = 5_000
"""Largest batch limit accepted."""
_MAX_REFRESH_ROUNDS = 64
"""History calls per refresh (adapters may cap one call at 10,000 bars)."""
MAX_SESSION_WINDOW_MS = 400 * 86_400_000
"""Longest window of ``calendar_sessions`` (400 days)."""
_SNAPSHOT_TTL_MS = 1_000
"""How long a fetched snapshot is served from the cache."""
_EDGE_TTL_MIN_MS = 1_000
_EDGE_TTL_MAX_MS = 10_000
"""Bounds of how long a fetched live edge is served from the store.

A batch reads the venue for the bar still forming unless the edge was read
less than a twelfth of a bar ago (within these bounds) and no bar closed
since, or a live session keeps the store's edge current.
"""
_BASIS_WINDOW_MS = 30 * 86_400_000
"""Recent sessions whose corporate-action factors are compared per epoch."""
_BASIS_MAX_AGE_MS = 366 * 86_400_000
"""A price basis older than this is not compared; its history is reloaded."""


def edge_ttl_ms(timeframe: timeframes.Timeframe) -> int:
    """Returns how long a fetched live edge of a series stays fresh.

    Args:
        timeframe: Resolution of the series.

    Returns:
        A twelfth of the bar, between one and ten seconds.
    """
    return min(
        _EDGE_TTL_MAX_MS, max(_EDGE_TTL_MIN_MS, timeframe.duration_ms // 12)
    )


def basis_changed(
    previous: Mapping[int, decimal.Decimal],
    previous_today_ms: int,
    current: Mapping[int, decimal.Decimal],
) -> bool:
    """Tells whether a provider's corporate-action factors changed.

    A session that was still current when ``previous`` was read carried
    native prices (factor one) then; any other session absent from
    ``previous`` was not compared and is skipped. A new split or dividend
    changes the factor of every session before its ex-date, so it shows in
    the most recent sessions of the window.

    Args:
        previous: Factors read before, by daily session start.
        previous_today_ms: Exchange-local midnight of the date on which
            ``previous`` was read.
        current: Factors read now, by daily session start.

    Returns:
        True when any compared session's factor differs.
    """
    for start, factor in current.items():
        old = previous.get(start)
        if old is None:
            if start < previous_today_ms:
                continue
            old = decimal.Decimal(1)
        if factor != old:
            return True
    return False


@dataclasses.dataclass(slots=True)
class _Basis:
    """Corporate-action price basis of one instrument's cached history.

    Attributes:
        epoch: Adjustment epoch (``now // ADJUSTMENT_REFRESH_MS``) of the
            last check.
        since_ms: Start of the epoch in which the cached history's basis
            was established; batches report it as their price-basis epoch.
        today_ms: Exchange-local midnight of the date of the last check.
        factors: Factors read at the last check, or None when unknown.
        pending: Background read of ``factors`` still running, if any.
    """

    epoch: int
    since_ms: int
    today_ms: int
    factors: Mapping[int, decimal.Decimal] | None
    pending: asyncio.Future[Mapping[int, decimal.Decimal] | None] | None = None


class _Flight:
    """One running history refresh that identical requests share.

    The refresh runs as its own task: a waiter that is cancelled (a client
    that went away) stops waiting without cancelling the refresh for the
    others, and a refresh nobody waits for any more still completes into
    the store, ready for the retry.

    Attributes:
        start_ms: Inclusive start of the refreshed range.
        end_ms: Exclusive end of the refreshed range.
        max_bars: Bar budget of the refresh.
        task: The refresh.
    """

    def __init__(
        self,
        start_ms: int,
        end_ms: int,
        max_bars: int,
        task: asyncio.Future[None],
    ) -> None:
        """Initializes the flight.

        Args:
            start_ms: Inclusive start of the refreshed range.
            end_ms: Exclusive end of the refreshed range.
            max_bars: Bar budget of the refresh.
            task: The refresh.
        """
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.max_bars = max_bars
        self.task = task

    def covers(self, start_ms: int, end_ms: int, max_bars: int) -> bool:
        """Tells whether this refresh also serves another request.

        Args:
            start_ms: Inclusive start of the other request.
            end_ms: Exclusive end of the other request.
            max_bars: Bar budget of the other request.

        Returns:
            True when the range and budget include the other request's.
        """
        return (
            self.start_ms <= start_ms
            and end_ms <= self.end_ms
            and max_bars <= self.max_bars
        )

    async def join(self) -> None:
        """Waits for the refresh without letting a cancellation reach it.

        Raises:
            errors.JosephError: Whatever the refresh raised.
        """
        await asyncio.shield(self.task)


def _retrieve[T](task: asyncio.Future[T]) -> None:
    """Marks a background task's exception as retrieved.

    Args:
        task: Finished task (its waiters, if any, saw the exception).
    """
    if not task.cancelled():
        task.exception()


@dataclasses.dataclass(frozen=True, slots=True)
class CandleBatchResult:
    """Candles plus provenance (contract ``CandleBatch``).

    Attributes:
        instrument_id: Instrument.
        timeframe: Resolution.
        candles: Ascending candles (the last may be non-final).
        feed_state: Provenance of the batch.
        source: Origin label, e.g. ``binance.ws`` or ``binance.poll``.
        as_of_ms: Query time, epoch milliseconds.
        price_adjustment: Corporate-action normalization of historical OHLC;
            None when a legacy replay has no reliable price-basis metadata.
        gaps: Missing closed-bar ranges as half-open ``(from_ms, to_ms)``
            pairs.
        basis_epoch_ms: Start of the adjustment epoch in which the batch's
            price basis was established; None reports the epoch of
            ``as_of_ms``.
    """

    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    candles: tuple[market.Candle, ...]
    feed_state: quality.FeedState
    source: str
    as_of_ms: int
    gaps: tuple[tuple[int, int], ...]
    price_adjustment: market.PriceAdjustment | None = None
    basis_epoch_ms: int | None = None

    @property
    def complete(self) -> bool:
        """Whether no closed bar is missing."""
        return not self.gaps

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``CandleBatch``.

        Returns:
            JSON-compatible dict.
        """
        result: dict[str, object] = {
            "schema_version": 1,
            "instrument_id": str(self.instrument_id),
            "timeframe": self.timeframe.value,
            "candles": [c.to_contract() for c in self.candles],
            "feed_state": self.feed_state.value,
            "source": self.source,
            "as_of_ms": self.as_of_ms,
            "complete": self.complete,
            "gaps": [{"from_ms": a, "to_ms": b} for a, b in self.gaps],
        }
        if self.price_adjustment is not None:
            result["price_adjustment"] = self.price_adjustment.value
            if self.price_adjustment is not market.PriceAdjustment.RAW:
                epoch = self.basis_epoch_ms
                if epoch is None:
                    epoch = (
                        self.as_of_ms
                        // ADJUSTMENT_REFRESH_MS
                        * ADJUSTMENT_REFRESH_MS
                    )
                result["price_adjustment_epoch_ms"] = epoch
                result["price_adjustment_refresh_after_ms"] = (
                    self.as_of_ms // ADJUSTMENT_REFRESH_MS + 1
                ) * ADJUSTMENT_REFRESH_MS
        return result


def as_instrument_id(
    value: instruments.InstrumentId | str,
) -> instruments.InstrumentId:
    """Normalizes an instrument id argument.

    Args:
        value: Canonical id object or string.

    Returns:
        Canonical id.

    Raises:
        errors.ValidationFailedError: If the string is not canonical.
    """
    if isinstance(value, instruments.InstrumentId):
        return value
    try:
        return instruments.InstrumentId.parse(value)
    except ValueError as err:
        raise errors.ValidationFailedError(str(err)) from err


def as_timeframe(value: timeframes.Timeframe | str) -> timeframes.Timeframe:
    """Normalizes a timeframe argument.

    Args:
        value: Timeframe or contract string.

    Returns:
        Timeframe.

    Raises:
        errors.ValidationFailedError: If unsupported.
    """
    if isinstance(value, timeframes.Timeframe):
        return value
    try:
        return timeframes.parse(value)
    except ValueError as err:
        raise errors.ValidationFailedError(str(err)) from err


def closed_through(
    calendar: sessions.BarCalendar, timeframe: timeframes.Timeframe, now_ms: int
) -> tuple[int, int | None]:
    """Locates the newest bar that has closed at ``now_ms``.

    Args:
        calendar: Session calendar of the instrument.
        timeframe: Resolution.
        now_ms: Current time, epoch milliseconds.

    Returns:
        ``(closed_end, last_start)``: the exclusive end of that bar (bars
        starting before it are closed) and its start, or ``(0, None)``
        before the first bar.
    """
    last = calendar.last_closed_start(timeframe, now_ms)
    if last is None:
        return 0, None
    return calendar.end_of(timeframe, last), last


def calendar_sessions(
    calendar_id: str, from_ms: int, to_ms: int
) -> dict[str, object]:
    """Lists the trading sessions of a calendar (contract ``SessionList``).

    Args:
        calendar_id: Contract ``CalendarId``.
        from_ms: Inclusive window start, epoch milliseconds.
        to_ms: Exclusive window end, epoch milliseconds.

    Returns:
        Contract ``SessionList`` (empty sessions for ``24x7``).

    Raises:
        errors.NotFoundError: For an unknown calendar.
        errors.ValidationFailedError: For an empty, negative or too long
            window.
    """
    try:
        calendar = sessions.get(calendar_id)
    except ValueError as err:
        raise errors.NotFoundError(
            f"unknown calendar {calendar_id!r}; expected one of "
            f"{', '.join(c.value for c in sessions.CalendarId)}"
        ) from err
    if from_ms < 0 or to_ms <= from_ms:
        raise errors.ValidationFailedError(
            "to_ms must be after from_ms (both non-negative)"
        )
    if to_ms - from_ms > MAX_SESSION_WINDOW_MS:
        raise errors.ValidationFailedError(
            "session window exceeds 400 days; request shorter windows"
        )
    return {
        "calendar": calendar.calendar_id.value,
        "sessions": [
            s.to_contract() for s in calendar.sessions(from_ms, to_ms)
        ],
        "from_ms": from_ms,
        "to_ms": to_ms,
    }


class MarketDataService:
    """Read-side facade over adapters, store, registry and feeds.

    Attributes:
        registry: Instrument registry of the enabled venues.
        stale_after_ms: Snapshot staleness budget, in milliseconds.
    """

    def __init__(
        self,
        adapters: Mapping[instruments.Venue, ports.MarketDataPort],
        instrument_registry: registry.InstrumentRegistry,
        store: candle_store.CandleStore,
        router: feed_router.FeedRouter,
        clock: clock_lib.Clock,
        *,
        replay: replay_lib.ReplaySource | None = None,
        stale_after_ms: int = 30_000,
        max_refresh_bars: int = 60_000,
        recorded: trade_archive.RecordedTrades | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            adapters: Adapter per venue.
            instrument_registry: Registry of the same venues.
            store: Candle store shared with the router.
            router: Feed router of the live sessions.
            clock: Time source.
            replay: Optional replay source for ``REPLAY`` batches.
            stale_after_ms: Snapshot staleness budget, in milliseconds.
            max_refresh_bars: Most bars one ``get_candles`` repair fetches
                across rounds (covers an anchored study's window).
            recorded: Optional recorded-trade archive (seconds history
                across restarts and true delta on covered bars).
        """
        self._adapters = dict(adapters)
        self.registry = instrument_registry
        self._store = store
        self._router = router
        self._clock = clock
        self._replay = replay
        self.stale_after_ms = stale_after_ms
        self._max_refresh_bars = max_refresh_bars
        self._recorded = recorded
        self._adjustment_epochs: dict[
            tuple[instruments.InstrumentId, timeframes.Timeframe], int
        ] = {}
        self._bases: dict[instruments.InstrumentId, _Basis] = {}
        self._basis_locks: dict[instruments.InstrumentId, asyncio.Lock] = {}
        self._flights: dict[
            tuple[instruments.InstrumentId, timeframes.Timeframe],
            list[_Flight],
        ] = {}
        self._edges: dict[
            tuple[instruments.InstrumentId, timeframes.Timeframe],
            tuple[int, int],
        ] = {}
        self._snapshots: dict[
            instruments.InstrumentId, tuple[int, market.MarketSnapshot]
        ] = {}

    def now_ms(self) -> int:
        """Returns the service clock time.

        Returns:
            UTC epoch milliseconds.
        """
        return self._clock.now_ms()

    @property
    def router(self) -> feed_router.FeedRouter:
        """The feed router of the live sessions."""
        return self._router

    def adapter(self, venue: instruments.Venue) -> ports.MarketDataPort:
        """Returns the adapter for a venue.

        Args:
            venue: Venue.

        Returns:
            The venue's adapter.

        Raises:
            errors.NotFoundError: If the venue is not enabled.
        """
        adapter = self._adapters.get(venue)
        if adapter is None:
            raise errors.NotFoundError(f"venue {venue.value} is not enabled")
        return adapter

    async def _call[T](self, venue: instruments.Venue, call: Awaitable[T]) -> T:
        """Awaits a venue call and records truthful REST feed health.

        Args:
            venue: Venue.
            call: Awaitable result of an adapter method.

        Returns:
            The awaited value.

        Raises:
            errors.JosephError: Whatever the call raised, unchanged.
        """
        tracker = self._router.rest_tracker(venue)
        try:
            result = await call
        except errors.JosephError as err:
            # Only an unreachable venue is trouble for its health; other
            # errors (unknown instrument, bad request) are the caller's.
            if isinstance(err, errors.VenueUnavailableError):
                tracker.record_error(self._clock.now_ms(), err.message)
            raise
        ccxt_feed = (
            self._adapters[venue].capabilities().display_name.startswith("CCXT")
        )
        state = quality.FeedState.LIVE
        if ccxt_feed:
            state = quality.FeedState.POLL
        if venue is instruments.Venue.MOCK:
            state = quality.FeedState.DEMO
        tracker.set_transport(
            quality.Transport.MOCK
            if venue is instruments.Venue.MOCK
            else quality.Transport.REST_POLL,
            state,
        )
        tracker.record_message(self._clock.now_ms())
        return result

    def _missing(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        caps: capabilities.VenueCapabilities,
        start_ms: int,
        closed_end: int,
    ) -> list[tuple[int, int]]:
        """Lists closed-bar gaps of the store worth requesting.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.
            caps: Venue capabilities.
            start_ms: Inclusive start, epoch milliseconds.
            closed_end: Exclusive end of the newest closed bar in range.

        Returns:
            Half-open ``(from_ms, to_ms)`` gaps, oldest first.
        """
        gaps = self._store.final_gaps(
            instrument, timeframe, start_ms, closed_end
        )
        if gaps and caps.is_trade_built(timeframe):
            # Seconds history is page-capped and short-lived at the venue:
            # gaps behind the newest stored final bar could not be covered
            # before and are reported, not re-requested on every batch.
            finals = [
                c.start_ms
                for c in self._store.range(
                    instrument, timeframe, start_ms, closed_end
                )
                if c.is_final
            ]
            if finals:
                tail = sessions.for_instrument(instrument).end_of(
                    timeframe, finals[-1]
                )
                gaps = [gap for gap in gaps if gap[0] >= tail]
        return gaps

    async def _fetch(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> candle_rules.CandleCoverage:
        """Fetches history into the store, recording sparse-bar coverage.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            The fetch (dense venues prove no coverage and are never
            ``complete``, so repair rounds continue as before).
        """
        adapter = self.adapter(instrument.venue)
        if ports.sparse_bars(adapter):
            fetched = await self._call(
                instrument.venue,
                adapter.fetch_candle_coverage(
                    instrument, timeframe, start_ms, end_ms
                ),
            )
        else:
            rows = await self._call(
                instrument.venue,
                adapter.fetch_candles(instrument, timeframe, start_ms, end_ms),
            )
            fetched = candle_rules.CandleCoverage(tuple(rows))
        self._store.upsert_many(list(fetched.candles))
        self._store.add_coverage(instrument, timeframe, fetched.covered)
        return fetched

    async def _refresh(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        max_bars: int,
    ) -> None:
        """Refreshes a range, sharing a running refresh that covers it.

        Concurrent identical requests (a chart opened twice, a study and
        its chart, a reconnect storm) wait for one venue fetch instead of
        each sending their own; see ``_Flight`` for cancellation.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            max_bars: Bars after which no further round is started.

        Returns:
            None; see ``_refresh_now``.
        """
        key = (instrument, timeframe)
        flights = self._flights.setdefault(key, [])
        for flight in flights:
            if flight.covers(start_ms, end_ms, max_bars):
                await flight.join()
                return
        task = asyncio.ensure_future(
            self._refresh_now(instrument, timeframe, start_ms, end_ms, max_bars)
        )
        flight = _Flight(start_ms, end_ms, max_bars, task)
        flights.append(flight)

        def done(finished: asyncio.Future[None]) -> None:
            """Forgets the finished flight.

            Args:
                finished: The flight's task.
            """
            flights.remove(flight)
            if not flights and self._flights.get(key) is flights:
                del self._flights[key]
            _retrieve(finished)

        task.add_done_callback(done)
        await flight.join()

    async def _refresh_now(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        max_bars: int,
    ) -> None:
        """Fetches missing or recent bars from the venue into the store.

        Adapters may cap one history call (e.g. 10,000 bars), so long
        ranges are repaired in rounds, each continuing after the newest bar
        the previous one returned, until ``max_bars`` bars were fetched; a
        round that makes no progress ends the repair (what the venue cannot
        serve stays a reported gap). A sparse-bar fetch that reports itself
        complete ends it too: repeating it cannot return more, and its
        coverage already settles the quiet intervals.

        Recorded seconds bars are loaded from the archive first. Seconds
        bars of a venue without a REST trade history are never fetched.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            max_bars: Bars after which no further round is started.

        Returns:
            None; updates normalized candle-store entries and coverage spans.

        Raises:
            errors.JosephError: If provider data or required corporate-action
                evidence cannot be read safely.
        """
        now = self._clock.now_ms()
        adapter = self.adapter(instrument.venue)
        adjusted = (
            isinstance(adapter, ports.PriceAdjustmentPort)
            and adapter.price_adjustment(timeframe)
            is not market.PriceAdjustment.RAW
        )
        if adjusted:
            await self._keep_basis(instrument, timeframe, adapter, now)
        calendar = sessions.for_instrument(instrument)
        current, last_closed = closed_through(calendar, timeframe, now)
        closed_end = min(end_ms, current)
        if self._recorded is not None and timeframe.is_seconds:
            # Recorded seconds (earlier sessions, restarts, archives) first.
            archived = await self._recorded.seconds_bars(
                instrument, timeframe, start_ms, closed_end
            )
            if adjusted:
                if isinstance(adapter, ports.HistoricalPriceNormalizationPort):
                    archived = await adapter.adjust_historical_prices(archived)
                else:
                    archived = []
            self._store.upsert_many(archived)
        caps = self.adapter(instrument.venue).capabilities()
        if caps.is_trade_built(timeframe) and (
            not caps.market_data.trades_history
        ):
            return
        cursor = start_ms
        fetched = 0
        for attempt in range(_MAX_REFRESH_ROUNDS):
            gaps = self._missing(
                instrument, timeframe, caps, cursor, closed_end
            )
            if gaps:
                fetch_from = gaps[0][0]
            elif (
                attempt == 0
                and end_ms > current
                and calendar.ceil(timeframe, current) < end_ms
                and (last_closed is not None or calendar.continuous)
                and not self._edge_fresh(instrument, timeframe, now, current)
            ):
                # A live bar may exist in range; between sessions none does.
                fetch_from = max(
                    start_ms,
                    last_closed
                    if last_closed is not None
                    else timeframe.shift(current, -1),
                )
            else:
                return
            result = await self._fetch(
                instrument, timeframe, fetch_from, end_ms
            )
            fetched += len(result.candles)
            reached = max((c.end_ms for c in result.candles), default=cursor)
            if end_ms > current and (
                reached >= current
                or any(end >= current for _, end in result.covered)
            ):
                # The fetch delivered through the newest closed bar, so it
                # also brought the bar still forming, if any.
                self._edges[instrument, timeframe] = (now, current)
            if (
                not gaps
                or result.complete
                or fetched >= max_bars
                or reached <= max(cursor, fetch_from)
            ):
                return
            cursor = reached

    async def _keep_basis(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        adapter: ports.MarketDataPort,
        now_ms: int,
    ) -> None:
        """Drops cached adjusted history whose price basis may be outdated.

        Sources that report their factors (``ports.PriceBasisPort``) keep
        their cached history until a factor changes; others drop a series
        once per adjustment epoch.

        Args:
            instrument: Instrument of the adjusted series.
            timeframe: Resolution of the adjusted series.
            adapter: Source of the instrument.
            now_ms: Current time, epoch milliseconds.
        """
        if isinstance(adapter, ports.PriceBasisPort):
            await self._check_basis(instrument, adapter, now_ms)
            return
        key = (instrument, timeframe)
        epoch = now_ms // ADJUSTMENT_REFRESH_MS
        if self._adjustment_epochs.get(key) != epoch:
            self._store.invalidate(instrument, timeframe)
            self._edges.pop(key, None)
            self._adjustment_epochs[key] = epoch

    def _edge_fresh(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        now_ms: int,
        closed_end: int,
    ) -> bool:
        """Tells whether the store's live edge of a series is current.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.
            now_ms: Current time, epoch milliseconds.
            closed_end: Exclusive end of the newest closed bar now.

        Returns:
            True when a live session feeds the series, or its edge was
            fetched within ``edge_ttl_ms`` and no bar has closed since.
        """
        session = self._router.session(instrument, timeframe)
        if session is not None and (
            session.state is feed_router.SessionState.LIVE
        ):
            return True
        edge = self._edges.get((instrument, timeframe))
        return (
            edge is not None
            and edge[1] == closed_end
            and 0 <= now_ms - edge[0] < edge_ttl_ms(timeframe)
        )

    def _invalidate_adjusted(
        self,
        instrument: instruments.InstrumentId,
        adapter: ports.PriceAdjustmentPort,
    ) -> None:
        """Drops every adjusted cached series of an instrument.

        Args:
            instrument: Instrument whose price basis changed or is unknown.
            adapter: The instrument's source, which names adjusted series.
        """
        for timeframe in self._store.series_timeframes(instrument):
            if (
                adapter.price_adjustment(timeframe)
                is not market.PriceAdjustment.RAW
            ):
                self._store.invalidate(instrument, timeframe)
                self._edges.pop((instrument, timeframe), None)

    async def _read_basis(
        self,
        adapter: ports.PriceBasisPort,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
    ) -> Mapping[int, decimal.Decimal] | None:
        """Reads an instrument's current factors, None when unavailable.

        Args:
            adapter: Source of the instrument.
            instrument: Instrument.
            start_ms: Inclusive UTC start, epoch milliseconds.
            end_ms: Exclusive UTC end, epoch milliseconds.

        Returns:
            Factors by daily session start, or None when the evidence could
            not be read (the basis is then treated as changed).
        """
        try:
            return dict(
                await self._call(
                    instrument.venue,
                    adapter.price_basis(instrument, start_ms, end_ms),
                )
            )
        except errors.JosephError:
            return None

    async def _check_basis(
        self,
        instrument: instruments.InstrumentId,
        adapter: ports.PriceBasisPort,
        now_ms: int,
    ) -> None:
        """Keeps an instrument's cached adjusted history on a valid basis.

        Once per adjustment epoch the source's recent corporate-action
        factors are read again and compared with the previous reading;
        the cached adjusted history is dropped only when they differ (or
        cannot be compared). The first use of an instrument drops whatever
        the store held and reads the baseline in the background, so a cold
        chart does not wait for it.

        Args:
            instrument: Instrument about to be refreshed.
            adapter: Its source.
            now_ms: Current time, epoch milliseconds.

        Raises:
            AssertionError: If the source declares no price policy.
        """
        if not isinstance(adapter, ports.PriceAdjustmentPort):
            raise AssertionError("price-basis source without a price policy")
        epoch = now_ms // ADJUSTMENT_REFRESH_MS
        basis = self._bases.get(instrument)
        if basis is not None and basis.epoch == epoch:
            return
        lock = self._basis_locks.setdefault(instrument, asyncio.Lock())
        async with lock:
            basis = self._bases.get(instrument)
            if basis is not None and basis.epoch == epoch:
                return
            calendar = sessions.for_instrument(instrument)
            today = price_adjustments.today_start(calendar, now_ms)
            if basis is None:
                self._invalidate_adjusted(instrument, adapter)
                pending = asyncio.ensure_future(
                    self._read_basis(
                        adapter, instrument, today - _BASIS_WINDOW_MS, today
                    )
                )
                pending.add_done_callback(_retrieve)
                self._bases[instrument] = _Basis(
                    epoch,
                    epoch * ADJUSTMENT_REFRESH_MS,
                    today,
                    None,
                    pending,
                )
                return
            previous = basis.factors
            if basis.pending is not None:
                previous = await asyncio.shield(basis.pending)
            current = None
            if previous is not None and now_ms - basis.today_ms < (
                _BASIS_MAX_AGE_MS
            ):
                current = await self._read_basis(
                    adapter,
                    instrument,
                    min(today - _BASIS_WINDOW_MS, basis.today_ms - 86_400_000),
                    today,
                )
            since = basis.since_ms
            if (
                previous is None
                or current is None
                or basis_changed(previous, basis.today_ms, current)
            ):
                self._invalidate_adjusted(instrument, adapter)
                since = epoch * ADJUSTMENT_REFRESH_MS
            self._bases[instrument] = _Basis(epoch, since, today, current)

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId | str,
        timeframe: timeframes.Timeframe | str,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns final candles with start in [start, end) (CandleSource).

        Missing bars are repaired from the venue first (see ``_refresh``).

        Args:
            instrument_id: Canonical instrument id or its string.
            timeframe: Resolution or its contract string.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending final candles.

        Raises:
            errors.ValidationFailedError: If the id or timeframe is invalid.
            errors.NotFoundError: If the venue is not enabled.
        """
        instrument = as_instrument_id(instrument_id)
        tf = as_timeframe(timeframe)
        await self._refresh(
            instrument, tf, start_ms, end_ms, self._max_refresh_bars
        )
        return candle_rules.finals_only(
            self._enrich(self._store.range(instrument, tf, start_ms, end_ms))
        )

    async def get_batch(
        self,
        instrument_id: instruments.InstrumentId | str,
        timeframe: timeframes.Timeframe | str,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int | None = None,
        feed_mode: quality.FeedMode = quality.FeedMode.AUTO,
    ) -> CandleBatchResult:
        """Returns a candle batch with provenance and gaps.

        Args:
            instrument_id: Canonical instrument id or its string.
            timeframe: Resolution or its contract string.
            from_ms: Inclusive start, epoch milliseconds; defaults to
                ``limit`` bars back, bounded by the calendar's first
                supported session.
            to_ms: Exclusive end, epoch milliseconds; defaults to just after
                the live bar.
            limit: Maximum bars (default 500, max 5000); the newest are
                kept.
            feed_mode: ``REPLAY`` serves recordings; others use the venue.

        Returns:
            Candle batch whose gaps cover only closed bars.

        Raises:
            errors.ValidationFailedError: On an invalid id, timeframe, range
                or limit, or a time outside the instrument's calendar.
            errors.MissingDataError: For ``REPLAY`` without a replay source.
            errors.NotFoundError: If the venue is not enabled.
        """
        instrument = as_instrument_id(instrument_id)
        tf = as_timeframe(timeframe)
        bars = DEFAULT_LIMIT if limit is None else limit
        if not 1 <= bars <= MAX_LIMIT:
            raise errors.ValidationFailedError(
                f"limit must be between 1 and {MAX_LIMIT}"
            )
        now = self._clock.now_ms()
        calendar = sessions.for_instrument(instrument)
        with _calendar_errors(instrument):
            end = (
                to_ms
                if to_ms is not None
                else calendar.end_of(tf, calendar.floor(tf, now))
            )
            if from_ms is not None:
                start = from_ms
            else:
                start = sessions.lookback_start(calendar, tf, end, bars)
        if end <= start:
            raise errors.ValidationFailedError("to_ms must be after from_ms")
        price_adjustment: market.PriceAdjustment | None = None
        if feed_mode is quality.FeedMode.REPLAY:
            if self._replay is None:
                raise errors.MissingDataError("no replay source configured")
            rows = await self._replay.fetch_candles(
                instrument, tf, start, end, bars
            )
            state, source = quality.FeedState.REPLAY, "replay"
        else:
            adapter = self.adapter(instrument.venue)
            price_adjustment = market.PriceAdjustment.RAW
            if isinstance(adapter, ports.PriceAdjustmentPort):
                price_adjustment = adapter.price_adjustment(tf)
            await self._refresh(instrument, tf, start, end, bars)
            rows = self._enrich(
                self._store.range(instrument, tf, start, end)[-bars:]
            )
            state, source = self._provenance(instrument, tf)
        closed_end = min(end, closed_through(calendar, tf, now)[0])
        window_start = rows[0].start_ms if rows and from_ms is None else start
        covered = (
            []
            if feed_mode is quality.FeedMode.REPLAY
            else self._store.coverage(instrument, tf, window_start, closed_end)
        )
        gaps = candle_rules.find_gaps(
            [c.start_ms for c in rows if c.is_final],
            tf,
            window_start,
            closed_end,
            calendar=calendar,
            covered=covered,
        )
        basis = self._bases.get(instrument)
        return CandleBatchResult(
            instrument_id=instrument,
            timeframe=tf,
            candles=tuple(rows),
            price_adjustment=price_adjustment,
            basis_epoch_ms=basis.since_ms if basis is not None else None,
            feed_state=state,
            source=source,
            as_of_ms=now,
            gaps=tuple(gaps),
        )

    def _enrich(self, rows: list[market.Candle]) -> list[market.Candle]:
        """Attaches recorded true delta to fully covered final bars.

        Args:
            rows: Stored candles of one series.

        Returns:
            Candles in the same order (``rows`` itself without an archive).
        """
        return rows if self._recorded is None else self._recorded.enrich(rows)

    def _provenance(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> tuple[quality.FeedState, str]:
        """Derives batch provenance from the venue and any live session.

        Args:
            instrument: Instrument of the series.
            timeframe: Resolution of the series.

        Returns:
            ``(feed_state, source)``; the source names the transport, e.g.
            ``binance.ws``, ``binance.poll`` or ``ccxt.binance.rest``.
        """
        if instrument.venue is instruments.Venue.MOCK:
            return quality.FeedState.DEMO, "mock"
        session = self._router.session(instrument, timeframe)
        venue = instrument.venue.value
        caps = self.adapter(instrument.venue).capabilities()
        if caps.is_trade_built(timeframe):
            venue = f"{venue}.trades"
            if session is None and not caps.market_data.trades_history:
                return quality.FeedState.LIVE, venue
        if session is not None:
            if session.state is feed_router.SessionState.POLLING:
                return quality.FeedState.POLL, f"{venue}.poll"
            if session.state in (
                feed_router.SessionState.STALE,
                feed_router.SessionState.UNAVAILABLE,
            ):
                return quality.FeedState.STALE, f"{venue}.stale"
            if session.state is feed_router.SessionState.LIVE:
                return (
                    quality.FeedState.LIVE,
                    venue if venue.endswith(".trades") else f"{venue}.ws",
                )
        return (
            (quality.FeedState.POLL, f"ccxt.{venue}.rest")
            if caps.display_name.startswith("CCXT")
            else (quality.FeedState.LIVE, f"{venue}.rest")
        )

    async def get_snapshot(
        self, instrument_id: instruments.InstrumentId | str
    ) -> market.MarketSnapshot:
        """Returns the latest snapshot (cached for one second).

        Args:
            instrument_id: Canonical instrument id or its string.

        Returns:
            Snapshot with missing fields left ``None``.

        Raises:
            errors.ValidationFailedError: If the id is invalid.
            errors.NotFoundError: If the venue is not enabled.
        """
        instrument = as_instrument_id(instrument_id)
        now = self._clock.now_ms()
        cached = self._snapshots.get(instrument)
        if cached is not None and now - cached[0] < _SNAPSHOT_TTL_MS:
            return cached[1]
        adapter = self.adapter(instrument.venue)
        snapshot: market.MarketSnapshot = await self._call(
            instrument.venue, adapter.fetch_snapshot(instrument)
        )
        self._snapshots[instrument] = (now, snapshot)
        return snapshot

    async def get_snapshot_batch(
        self, instrument_ids: Sequence[instruments.InstrumentId]
    ) -> list[market.MarketSnapshot]:
        """Fetches selected snapshots efficiently when their source supports it.

        Args:
            instrument_ids: One venue's canonical instruments, at most two
                hundred.

        Returns:
            Provider-returned snapshots, also populating the normal quote cache.

        Raises:
            errors.ValidationFailedError: If empty, oversized or mixed-venue.
            errors.UnsupportedError: If selected-symbol bulk snapshots are
                unsupported.
            errors.JosephError: If the configured provider rejects the query.
        """
        if (
            not instrument_ids
            or len(instrument_ids) > 200
            or len({item.venue for item in instrument_ids}) != 1
        ):
            raise errors.ValidationFailedError(
                "snapshot batches need one venue and one through two "
                "hundred instruments"
            )
        venue = instrument_ids[0].venue
        adapter = self.adapter(venue)
        if not isinstance(adapter, ports.SnapshotBatchPort):
            raise errors.UnsupportedError(
                "provider has no selected-symbol bulk quotes"
            )
        snapshots = await self._call(
            venue, adapter.fetch_snapshot_batch(instrument_ids)
        )
        now = self._clock.now_ms()
        for snapshot in snapshots:
            self._snapshots[snapshot.instrument_id] = (now, snapshot)
        return snapshots

    async def get_order_book(
        self, instrument_id: instruments.InstrumentId | str, depth: int = 50
    ) -> market.OrderBook:
        """Returns an order-book snapshot.

        Args:
            instrument_id: Canonical instrument id or its string.
            depth: Levels per side (1..1000).

        Returns:
            Order-book snapshot.

        Raises:
            errors.ValidationFailedError: On an invalid depth or id.
            errors.NotFoundError: If the venue is not enabled.
        """
        if not 1 <= depth <= 1_000:
            raise errors.ValidationFailedError("depth must be 1..1000")
        instrument = as_instrument_id(instrument_id)
        adapter = self.adapter(instrument.venue)
        book: market.OrderBook = await self._call(
            instrument.venue, adapter.fetch_order_book(instrument, depth)
        )
        return book

    def calendar_sessions(
        self, calendar_id: str, from_ms: int, to_ms: int
    ) -> dict[str, object]:
        """Lists a calendar's sessions (see ``calendar_sessions``).

        Args:
            calendar_id: Contract ``CalendarId``.
            from_ms: Inclusive window start, epoch milliseconds.
            to_ms: Exclusive window end, epoch milliseconds.

        Returns:
            Contract ``SessionList``.

        Raises:
            errors.NotFoundError: For an unknown calendar.
            errors.ValidationFailedError: For an empty, negative or too long
                window.
        """
        return calendar_sessions(calendar_id, from_ms, to_ms)

    def venue_status(
        self,
    ) -> list[tuple[capabilities.VenueCapabilities, health.FeedHealth]]:
        """Returns capabilities and observed connection/discovery health.

        Failed discovery is visible before a chart subscribes. A discovery
        failure never overrides an already functioning price connection, and
        a successful symbol list alone never claims a live price connection.

        Returns:
            ``(capabilities, health)`` pairs in venue configuration order.
        """
        statuses = []
        for venue, adapter in self._adapters.items():
            observed = self._router.venue_health(venue)
            failure = self.registry.errors.get(venue)
            if failure and observed.transport is quality.Transport.NONE:
                observed = dataclasses.replace(
                    observed,
                    state=quality.HealthState.UNAVAILABLE,
                    feed_state=quality.FeedState.UNAVAILABLE,
                    last_error=failure,
                )
            statuses.append((adapter.capabilities(), observed))
        return statuses


@contextlib.contextmanager
def _calendar_errors(
    instrument: instruments.InstrumentId,
) -> Iterator[None]:
    """Turns times outside an instrument's calendar into validation errors.

    Exchange calendars start at a fixed date (e.g. 1990-01-02 for XNYS);
    asking for bars before it is a client error, not a server fault.

    Args:
        instrument: Instrument whose calendar is used.

    Yields:
        Nothing.

    Raises:
        errors.ValidationFailedError: If the calendar rejected a time.
    """
    try:
        yield
    except ValueError as err:
        calendar = sessions.for_instrument(instrument).calendar_id.value
        raise errors.ValidationFailedError(
            f"{instrument}: {err} (calendar {calendar})"
        ) from err
