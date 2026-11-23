"""Market-data application service (read side used by the API and engines).

Implements ``ports.CandleSource`` (``get_candles`` returns final candles
only) for the indicator engine, plus richer queries for the API: candle
batches with provenance and gaps, snapshots, books and venue status.

Seconds bars are built from public trades. Where the venue has a REST
trade history they are fetched like klines (only the fully covered part);
where it has none they exist only from a live trade-stream session, so
batches serve what the store holds and report the rest as gaps.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
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

DEFAULT_LIMIT = 500
MAX_LIMIT = 5_000
_SNAPSHOT_TTL_MS = 1_000


@dataclasses.dataclass(frozen=True, slots=True)
class CandleBatchResult:
    """Candles plus provenance (contract ``CandleBatch``).

    Attributes:
        instrument_id: Instrument.
        timeframe: Resolution.
        candles: Ascending candles (the last may be non-final).
        feed_state: Provenance of the batch.
        source: Origin label.
        as_of_ms: Query time.
        gaps: Missing closed-bar ranges.
    """

    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    candles: tuple[market.Candle, ...]
    feed_state: quality.FeedState
    source: str
    as_of_ms: int
    gaps: tuple[tuple[int, int], ...]

    @property
    def complete(self) -> bool:
        """Whether no closed bar is missing."""
        return not self.gaps

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``CandleBatch``.

        Returns:
            JSON-compatible dict.
        """
        return {
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


class MarketDataService:
    """Read-side facade over adapters, store, registry and feeds."""

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
        recorded: trade_archive.RecordedTrades | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            adapters: Adapter per venue.
            instrument_registry: Registry.
            store: Candle store.
            router: Feed router.
            clock: Time source.
            replay: Optional replay source.
            stale_after_ms: Snapshot staleness budget.
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
        self._recorded = recorded
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
        """Returns the feed router."""
        return self._router

    def adapter(self, venue: instruments.Venue) -> ports.MarketDataPort:
        """Returns the adapter for a venue.

        Args:
            venue: Venue.

        Returns:
            Adapter.

        Raises:
            errors.NotFoundError: If the venue is not enabled.
        """
        adapter = self._adapters.get(venue)
        if adapter is None:
            raise errors.NotFoundError(f"venue {venue.value} is not enabled")
        return adapter

    async def _call[T](self, venue: instruments.Venue, call: Awaitable[T]) -> T:
        """Awaits a venue call, recording REST health.

        Args:
            venue: Venue.
            call: Awaitable result of an adapter method.

        Returns:
            The awaited value.
        """
        tracker = self._router.rest_tracker(venue)
        try:
            result = await call
        except errors.JosephError as err:
            if isinstance(err, errors.VenueUnavailableError):
                tracker.record_error(self._clock.now_ms(), err.message)
            raise
        tracker.set_transport(
            quality.Transport.MOCK
            if venue is instruments.Venue.MOCK
            else quality.Transport.REST_POLL,
            quality.FeedState.DEMO
            if venue is instruments.Venue.MOCK
            else quality.FeedState.LIVE,
        )
        tracker.record_message(self._clock.now_ms())
        return result

    async def _refresh(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> None:
        """Fetches missing or recent bars from the venue into the store.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
        """
        now = self._clock.now_ms()
        current = timeframe.floor(now)
        closed_end = min(end_ms, current)
        if self._recorded is not None and timeframe.is_seconds:
            # Recorded seconds (earlier sessions, restarts, archives) first.
            self._store.upsert_many(
                await self._recorded.seconds_bars(
                    instrument, timeframe, start_ms, closed_end
                )
            )
        gaps = self._store.final_gaps(
            instrument, timeframe, start_ms, closed_end
        )
        caps = self.adapter(instrument.venue).capabilities()
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
                tail = timeframe.end_of(finals[-1])
                gaps = [gap for gap in gaps if gap[0] >= tail]
        if gaps:
            fetch_from = gaps[0][0]
        elif end_ms > current:
            fetch_from = max(start_ms, timeframe.shift(current, -1))
        else:
            return
        adapter = self.adapter(instrument.venue)
        if caps.is_trade_built(timeframe) and (
            not caps.market_data.trades_history
        ):
            return
        rows = await self._call(
            instrument.venue,
            adapter.fetch_candles(instrument, timeframe, fetch_from, end_ms),
        )
        self._store.upsert_many(list(rows))

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId | str,
        timeframe: timeframes.Timeframe | str,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns final candles with start in [start, end) (CandleSource).

        Args:
            instrument_id: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending final candles.
        """
        instrument = as_instrument_id(instrument_id)
        tf = as_timeframe(timeframe)
        await self._refresh(instrument, tf, start_ms, end_ms)
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
            instrument_id: Instrument.
            timeframe: Resolution.
            from_ms: Inclusive start; defaults to ``limit`` bars back.
            to_ms: Exclusive end; defaults to just after the live bar.
            limit: Maximum bars (default 500, max 5000).
            feed_mode: ``REPLAY`` serves recordings; others use the venue.

        Returns:
            Candle batch.

        Raises:
            errors.ValidationFailedError: On an invalid range or limit.
        """
        instrument = as_instrument_id(instrument_id)
        tf = as_timeframe(timeframe)
        bars = DEFAULT_LIMIT if limit is None else limit
        if not 1 <= bars <= MAX_LIMIT:
            raise errors.ValidationFailedError(
                f"limit must be between 1 and {MAX_LIMIT}"
            )
        now = self._clock.now_ms()
        end = to_ms if to_ms is not None else tf.end_of(tf.floor(now))
        start = (
            from_ms
            if from_ms is not None
            else tf.shift(tf.floor(end - 1), -(bars - 1))
        )
        if end <= start:
            raise errors.ValidationFailedError("to_ms must be after from_ms")
        if feed_mode is quality.FeedMode.REPLAY:
            if self._replay is None:
                raise errors.MissingDataError("no replay source configured")
            rows = await self._replay.fetch_candles(
                instrument, tf, start, end, bars
            )
            state, source = quality.FeedState.REPLAY, "replay"
        else:
            self.adapter(instrument.venue)
            await self._refresh(instrument, tf, start, end)
            rows = self._enrich(
                self._store.range(instrument, tf, start, end)[-bars:]
            )
            state, source = self._provenance(instrument, tf)
        closed_end = min(end, tf.floor(now))
        window_start = rows[0].start_ms if rows and from_ms is None else start
        gaps = candle_rules.find_gaps(
            [c.start_ms for c in rows if c.is_final],
            tf,
            window_start,
            closed_end,
        )
        return CandleBatchResult(
            instrument_id=instrument,
            timeframe=tf,
            candles=tuple(rows),
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
            Candles in the same order.
        """
        return rows if self._recorded is None else self._recorded.enrich(rows)

    def _provenance(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> tuple[quality.FeedState, str]:
        """Derives batch provenance from the venue and any live session.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Returns:
            ``(feed_state, source)``.
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
        return quality.FeedState.LIVE, f"{venue}.rest"

    async def get_snapshot(
        self, instrument_id: instruments.InstrumentId | str
    ) -> market.MarketSnapshot:
        """Returns the latest snapshot (cached for one second).

        Args:
            instrument_id: Instrument.

        Returns:
            Snapshot.
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

    async def get_order_book(
        self, instrument_id: instruments.InstrumentId | str, depth: int = 50
    ) -> market.OrderBook:
        """Returns an order-book snapshot.

        Args:
            instrument_id: Instrument.
            depth: Levels per side (1..1000).

        Returns:
            Order book.

        Raises:
            errors.ValidationFailedError: On an invalid depth.
        """
        if not 1 <= depth <= 1_000:
            raise errors.ValidationFailedError("depth must be 1..1000")
        instrument = as_instrument_id(instrument_id)
        adapter = self.adapter(instrument.venue)
        book: market.OrderBook = await self._call(
            instrument.venue, adapter.fetch_order_book(instrument, depth)
        )
        return book

    def venue_status(
        self,
    ) -> list[tuple[capabilities.VenueCapabilities, health.FeedHealth]]:
        """Returns capabilities and health of every enabled venue.

        Returns:
            ``(capabilities, health)`` pairs.
        """
        return [
            (adapter.capabilities(), self._router.venue_health(venue))
            for venue, adapter in self._adapters.items()
        ]
