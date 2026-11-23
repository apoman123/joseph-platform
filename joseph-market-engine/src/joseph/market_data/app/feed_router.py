"""Feed router: the ``FeedMode`` policy as an explicit state machine.

``AUTO``
    Native WebSocket when the venue supports it. After the stream gives up
    the session falls back to REST polling, *visibly*: ``feed_state=POLL``,
    ``transport=REST_POLL`` and ``FeedHealth.state=DEGRADED``. Native is
    retried every ``native_retry_s``.
``NATIVE_WS``
    Native only. Unsupported venues raise ``UnsupportedError`` at acquire;
    an outage leaves the session ``STALE`` and retrying, never polling.
``REST_POLL``
    Bounded REST polling (display-grade ``POLL`` data).
``REPLAY``
    Recorded candles only; never tradable.

Seconds timeframes are built from the venue's public trades
(``VenueCapabilities.trade_built_timeframes``): "native" then means the
native *trade* stream, and REST polling means polling the REST trade
history. A venue without a time-range trade history cannot poll seconds
bars: ``REST_POLL`` is rejected at acquire and ``AUTO`` goes ``STALE`` and
retries the trade stream instead of emulating bars from anything else.

Sessions of venues with sparse bars (``ports.sparse_bars``) also record
what they covered: each poll the spans its fetch delivered completely, each
native update the spans its connection proved. The store keeps them, so a
quiet interval a live session saw is not a gap later.

Session transitions::

    CONNECTING -> LIVE -> (failure) -> POLLING [AUTO] | STALE [NATIVE_WS]
    POLLING -> CONNECTING (native retry, AUTO only)
    POLLING -> UNAVAILABLE (repeated REST failures) -> POLLING (recovery)
    REPLAYING -> STOPPED ; any -> STOPPED (release)
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import enum
import logging
import math
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import replay as replay_lib
from joseph.market_data.app import candle_store
from joseph.market_data.app import ports
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

_LOG = logging.getLogger(__name__)

CandleHandler = Callable[
    ["FeedSession", market.Candle, candle_rules.MergeAction], Awaitable[None]
]
HealthHandler = Callable[[health.FeedHealth], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


class SessionState(enum.StrEnum):
    """Lifecycle of a feed session."""

    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    POLLING = "POLLING"
    REPLAYING = "REPLAYING"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    STOPPED = "STOPPED"


_ALLOWED: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CONNECTING: frozenset(
        {
            SessionState.LIVE,
            SessionState.POLLING,
            SessionState.STALE,
            SessionState.STOPPED,
        }
    ),
    SessionState.LIVE: frozenset(
        {
            SessionState.POLLING,
            SessionState.STALE,
            SessionState.CONNECTING,
            SessionState.STOPPED,
        }
    ),
    SessionState.POLLING: frozenset(
        {
            SessionState.CONNECTING,
            SessionState.UNAVAILABLE,
            SessionState.STOPPED,
            SessionState.POLLING,
        }
    ),
    SessionState.UNAVAILABLE: frozenset(
        {
            SessionState.POLLING,
            SessionState.STOPPED,
            SessionState.CONNECTING,
        }
    ),
    SessionState.STALE: frozenset(
        {
            SessionState.CONNECTING,
            SessionState.STOPPED,
        }
    ),
    SessionState.REPLAYING: frozenset({SessionState.STOPPED}),
    SessionState.STOPPED: frozenset(),
}

SessionKey = tuple[
    instruments.InstrumentId, timeframes.Timeframe, quality.FeedMode
]


@dataclasses.dataclass
class FeedSession:
    """Mutable state of one running series feed.

    Attributes:
        instrument: Instrument.
        timeframe: Resolution.
        mode: Requested policy.
        tracker: Health tracker of this session.
        state: Current state.
        refs: Number of acquirers.
        task: Background task.
        history: Ordered states visited (for observability and tests).
    """

    instrument: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    mode: quality.FeedMode
    tracker: health.FeedHealthTracker
    state: SessionState = SessionState.CONNECTING
    refs: int = 0
    task: asyncio.Task[None] | None = None
    history: list[SessionState] = dataclasses.field(default_factory=list)

    @property
    def key(self) -> SessionKey:
        """Returns the session key."""
        return (self.instrument, self.timeframe, self.mode)

    def transition(self, new_state: SessionState) -> None:
        """Moves to a new state, enforcing the transition table.

        Args:
            new_state: Target state.

        Raises:
            ValueError: On an illegal transition.
        """
        if new_state is self.state and new_state is not SessionState.POLLING:
            return
        if new_state not in _ALLOWED[self.state]:
            raise ValueError(
                f"illegal feed transition {self.state}->{new_state}"
            )
        self.state = new_state
        self.history.append(new_state)


class FeedRouter:
    """Starts, shares and stops series feeds according to ``FeedMode``."""

    def __init__(
        self,
        adapters: Mapping[instruments.Venue, ports.MarketDataPort],
        store: candle_store.CandleStore,
        clock: clock_lib.Clock,
        *,
        replay: replay_lib.ReplaySource | None = None,
        on_candle: CandleHandler | None = None,
        on_health: HealthHandler | None = None,
        poll_interval_s: float = 5.0,
        native_retry_s: float = 60.0,
        max_poll_failures: int = 5,
        sleep: Sleep = asyncio.sleep,
        stale_after_ms: int = 30_000,
    ) -> None:
        """Initializes the router.

        Args:
            adapters: Adapter per venue.
            store: Candle store receiving every update.
            clock: Time source.
            replay: Optional replay source for ``REPLAY`` mode.
            on_candle: Awaited for every changed candle.
            on_health: Awaited when a session changes state.
            poll_interval_s: REST polling interval (minimum 1s).
            native_retry_s: Delay before retrying native after fallback.
            max_poll_failures: Failures before a polling session is
                ``UNAVAILABLE``.
            sleep: Sleep function (injectable for tests).
            stale_after_ms: Silence budget for health.
        """
        self._adapters = dict(adapters)
        self._store = store
        self._clock = clock
        self._replay = replay
        self._on_candle = on_candle
        self._on_health = on_health
        self._poll_interval_s = max(1.0, poll_interval_s)
        self._native_retry_s = native_retry_s
        self._max_poll_failures = max_poll_failures
        self._sleep = sleep
        self._stale_after_ms = stale_after_ms
        self._sessions: dict[SessionKey, FeedSession] = {}
        self._rest: dict[instruments.Venue, health.FeedHealthTracker] = {}

    def _adapter(self, venue: instruments.Venue) -> ports.MarketDataPort:
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

    def validate(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        mode: quality.FeedMode,
    ) -> None:
        """Checks that a mode can serve a series, without starting it.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            mode: Requested policy.

        Raises:
            errors.UnsupportedError: For unsupported native/vendor requests.
            errors.MissingDataError: For ``REPLAY`` without a recording.
            errors.NotFoundError: If the venue is not enabled.
        """
        if mode is quality.FeedMode.EXTERNAL_VENDOR:
            raise errors.UnsupportedError(
                "no external vendor feed is configured"
            )
        if mode is quality.FeedMode.REPLAY:
            if self._replay is None or not self._replay.has(
                instrument, timeframe
            ):
                raise errors.MissingDataError(
                    f"no replay recording for {instrument} {timeframe.value}"
                )
            return
        adapter = self._adapter(instrument.venue)
        caps = adapter.capabilities()
        venue = instrument.venue.value
        if not caps.supports_timeframe(timeframe):
            raise errors.UnsupportedError(
                f"{venue} does not provide {timeframe.value}"
            )
        if caps.is_trade_built(timeframe):
            if mode is quality.FeedMode.NATIVE_WS and (
                not caps.market_data.native_ws_trades
            ):
                raise errors.UnsupportedError(
                    f"{venue} has no native trade stream for "
                    f"{timeframe.value} bars"
                )
            if mode is quality.FeedMode.REST_POLL and (
                not caps.market_data.trades_history
            ):
                raise errors.UnsupportedError(
                    f"{venue} publishes no time-range trade history, so "
                    f"{timeframe.value} bars cannot be polled; choose AUTO "
                    "or NATIVE_WS (built from the trade stream)"
                )
            return
        if mode is quality.FeedMode.NATIVE_WS and (
            not caps.market_data.native_ws_candles
        ):
            raise errors.UnsupportedError(
                f"{venue} has no native WebSocket candles; "
                "choose AUTO or REST_POLL"
            )

    async def acquire(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        mode: quality.FeedMode = quality.FeedMode.AUTO,
    ) -> FeedSession:
        """Starts (or joins) a series feed.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            mode: Requested policy.

        Returns:
            The shared session.
        """
        self.validate(instrument, timeframe, mode)
        key = (instrument, timeframe, mode)
        session = self._sessions.get(key)
        if session is None:
            session = FeedSession(
                instrument=instrument,
                timeframe=timeframe,
                mode=mode,
                tracker=health.FeedHealthTracker(
                    instrument.venue, mode, self._stale_after_ms
                ),
            )
            self._sessions[key] = session
            session.task = asyncio.create_task(self._run(session))
        session.refs += 1
        return session

    async def release(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        mode: quality.FeedMode = quality.FeedMode.AUTO,
    ) -> None:
        """Releases one reference, stopping the feed at zero.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            mode: Requested policy.
        """
        session = self._sessions.get((instrument, timeframe, mode))
        if session is None:
            return
        session.refs -= 1
        if session.refs <= 0:
            await self._stop(session)

    async def _stop(self, session: FeedSession) -> None:
        """Cancels a session task.

        Args:
            session: Session to stop.
        """
        self._sessions.pop(session.key, None)
        if session.task is not None:
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.task
        if session.state is not SessionState.STOPPED:
            session.state = SessionState.STOPPED
            session.history.append(SessionState.STOPPED)

    async def close(self) -> None:
        """Stops every session."""
        for session in list(self._sessions.values()):
            await self._stop(session)

    def session(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        mode: quality.FeedMode | None = None,
    ) -> FeedSession | None:
        """Finds a running session for a series.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            mode: Optional mode filter.

        Returns:
            Session or ``None``.
        """
        for session in self._sessions.values():
            if session.instrument == instrument and (
                session.timeframe == timeframe
                and (mode is None or session.mode is mode)
            ):
                return session
        return None

    def rest_tracker(
        self, venue: instruments.Venue
    ) -> health.FeedHealthTracker:
        """Returns the REST health tracker of a venue.

        Args:
            venue: Venue.

        Returns:
            Tracker (created on first use).
        """
        tracker = self._rest.get(venue)
        if tracker is None:
            tracker = health.FeedHealthTracker(
                venue, quality.FeedMode.REST_POLL, self._stale_after_ms * 20
            )
            self._rest[venue] = tracker
        return tracker

    def venue_health(self, venue: instruments.Venue) -> health.FeedHealth:
        """Summarizes a venue's health across sessions and REST calls.

        The worst session wins; with no session the REST tracker reports.

        Args:
            venue: Venue.

        Returns:
            Feed health.
        """
        now = self._clock.now_ms()
        order = {
            quality.HealthState.UNAVAILABLE: 0,
            quality.HealthState.STALE: 1,
            quality.HealthState.DEGRADED: 2,
            quality.HealthState.HEALTHY: 3,
        }
        snapshots = [
            s.tracker.snapshot(now)
            for s in self._sessions.values()
            if s.instrument.venue is venue
        ]
        if snapshots:
            return min(snapshots, key=lambda h: order[h.state])
        rest = self.rest_tracker(venue)
        if venue is instruments.Venue.MOCK:
            rest.set_transport(quality.Transport.MOCK, quality.FeedState.DEMO)
            rest.record_message(now)
        return rest.snapshot(now)

    async def _set_state(
        self, session: FeedSession, state: SessionState
    ) -> None:
        """Transitions a session and publishes its health.

        Args:
            session: Session.
            state: New state.
        """
        previous = session.state
        session.transition(state)
        if self._on_health is not None and previous is not state:
            await self._on_health(
                session.tracker.snapshot(self._clock.now_ms())
            )

    async def _emit(self, session: FeedSession, candle: market.Candle) -> None:
        """Stores a candle and forwards it when it changed.

        Args:
            session: Producing session.
            candle: Candle.
        """
        result = self._store.upsert(candle)
        if result.changed and self._on_candle is not None:
            await self._on_candle(session, result.candle, result.action)

    async def _run(self, session: FeedSession) -> None:
        """Runs a session until cancelled.

        Args:
            session: Session.
        """
        try:
            if session.mode is quality.FeedMode.REPLAY:
                await self._run_replay(session)
            elif session.mode is quality.FeedMode.REST_POLL:
                await self._set_state(session, SessionState.POLLING)
                session.tracker.set_transport(
                    quality.Transport.REST_POLL, quality.FeedState.POLL
                )
                await self._poll(session, None)
            elif session.mode is quality.FeedMode.NATIVE_WS:
                await self._run_native_only(session)
            else:
                await self._run_auto(session)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOG.exception("feed session crashed: %s", type(err).__name__)
            session.tracker.set_unavailable(
                self._clock.now_ms(), type(err).__name__
            )

    async def _run_replay(self, session: FeedSession) -> None:
        """Replays a recording.

        Args:
            session: Session.
        """
        assert self._replay is not None
        session.state = SessionState.REPLAYING
        session.history.append(SessionState.REPLAYING)
        session.tracker.set_transport(
            quality.Transport.REPLAY, quality.FeedState.REPLAY
        )
        async for candle in self._replay.stream_candles(
            session.instrument, session.timeframe
        ):
            session.tracker.record_message(self._clock.now_ms())
            await self._emit(session, candle)
        await self._set_state(session, SessionState.STOPPED)

    async def _stream_native(self, session: FeedSession) -> None:
        """Consumes the native stream until it gives up.

        Args:
            session: Session.

        Raises:
            errors.VenueUnavailableError: When the stream gives up or ends.
        """
        adapter = self._adapter(session.instrument.venue)
        transport = (
            quality.Transport.MOCK
            if session.instrument.venue is instruments.Venue.MOCK
            else quality.Transport.NATIVE_WS
        )
        feed_state = (
            quality.FeedState.DEMO
            if session.instrument.venue is instruments.Venue.MOCK
            else quality.FeedState.LIVE
        )
        await self._set_state(session, SessionState.CONNECTING)
        updates = (
            adapter.stream_candle_coverage(
                session.instrument, session.timeframe, tracker=session.tracker
            )
            if ports.sparse_bars(adapter)
            else _uncovered(
                adapter.stream_candles(
                    session.instrument,
                    session.timeframe,
                    tracker=session.tracker,
                )
            )
        )
        async for update in updates:
            for candle in update.candles:
                if session.state is not SessionState.LIVE:
                    session.tracker.set_transport(transport, feed_state)
                    await self._set_state(session, SessionState.LIVE)
                session.tracker.record_message(self._clock.now_ms())
                await self._emit(session, candle)
            self._store.add_coverage(
                session.instrument, session.timeframe, update.covered
            )
        raise errors.VenueUnavailableError("native stream ended")

    async def _run_native_only(self, session: FeedSession) -> None:
        """Runs ``NATIVE_WS``: retry forever, never poll.

        Args:
            session: Session.
        """
        while True:
            try:
                await self._stream_native(session)
            except errors.JosephError as err:
                session.tracker.set_unavailable(
                    self._clock.now_ms(), err.message
                )
                await self._set_state(session, SessionState.STALE)
                await self._sleep(self._native_retry_s)

    async def _run_auto(self, session: FeedSession) -> None:
        """Runs ``AUTO``: native first, visible REST fallback.

        Args:
            session: Session.
        """
        caps = self._adapter(session.instrument.venue).capabilities()
        native = _native(caps, session.timeframe)
        if native and not _pollable(caps, session.timeframe):
            await self._run_native_only(session)
            return
        while True:
            if native:
                try:
                    await self._stream_native(session)
                except errors.JosephError as err:
                    session.tracker.record_error(
                        self._clock.now_ms(), f"native failed: {err.message}"
                    )
            await self._set_state(session, SessionState.POLLING)
            session.tracker.set_transport(
                quality.Transport.REST_POLL,
                quality.FeedState.POLL,
                fallback=True,
            )
            until = (
                self._clock.now_ms() + int(self._native_retry_s * 1000)
                if native
                else None
            )
            await self._poll(session, until)

    async def _poll(self, session: FeedSession, until_ms: int | None) -> None:
        """Polls recent bars over REST.

        Args:
            session: Session.
            until_ms: Return once the clock passes this time (``None``
                polls forever).
        """
        adapter = self._adapter(session.instrument.venue)
        failures = 0
        tf = session.timeframe
        calendar = sessions.for_instrument(session.instrument)
        # Look back far enough that consecutive polls overlap; window
        # sizing may use the nominal duration, boundaries the calendar.
        back = 1 + math.ceil(self._poll_interval_s * 1000 / tf.duration_ms)
        if not tf.is_seconds:
            back = 1
        while until_ms is None or self._clock.now_ms() < until_ms:
            now = self._clock.now_ms()
            try:
                current = calendar.floor(tf, now)
                window = (
                    calendar.shift(tf, current, -back),
                    calendar.end_of(tf, current),
                )
                if ports.sparse_bars(adapter):
                    fetched = await adapter.fetch_candle_coverage(
                        session.instrument, tf, *window
                    )
                else:
                    fetched = candle_rules.CandleCoverage(
                        tuple(
                            await adapter.fetch_candles(
                                session.instrument, tf, *window
                            )
                        )
                    )
            except errors.JosephError as err:
                failures += 1
                session.tracker.record_error(now, err.message)
                if failures >= self._max_poll_failures and (
                    session.state is not SessionState.UNAVAILABLE
                ):
                    session.tracker.set_unavailable(now, err.message)
                    await self._set_state(session, SessionState.UNAVAILABLE)
            else:
                if session.state is SessionState.UNAVAILABLE:
                    session.tracker.set_transport(
                        quality.Transport.REST_POLL,
                        quality.FeedState.POLL,
                        fallback=session.mode is quality.FeedMode.AUTO,
                    )
                    await self._set_state(session, SessionState.POLLING)
                failures = 0
                session.tracker.record_message(now)
                for candle in fetched.candles:
                    await self._emit(
                        session,
                        dataclasses.replace(
                            candle,
                            feed_state=(
                                quality.FeedState.DEMO
                                if candle.feed_state is quality.FeedState.DEMO
                                else quality.FeedState.POLL
                            ),
                            source=candle.source.replace(".rest", ".poll"),
                        ),
                    )
                self._store.add_coverage(
                    session.instrument, tf, fetched.covered
                )
            await self._sleep(self._poll_interval_s)


async def _uncovered(
    candles: AsyncIterator[market.Candle],
) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
    """Wraps a dense venue's candle stream as updates without coverage.

    Args:
        candles: Native candle stream.

    Yields:
        One update per candle.
    """
    try:
        async for candle in candles:
            yield candle_rules.CandleCoverage((candle,))
    finally:
        close = getattr(candles, "aclose", None)
        if close is not None:
            await close()


def _native(
    caps: capabilities.VenueCapabilities, timeframe: timeframes.Timeframe
) -> bool:
    """Checks whether a series has a native stream.

    Args:
        caps: Venue capabilities.
        timeframe: Resolution.

    Returns:
        True for native klines, or a native trade stream for seconds bars.
    """
    if caps.is_trade_built(timeframe):
        return caps.market_data.native_ws_trades
    return caps.market_data.native_ws_candles


def _pollable(
    caps: capabilities.VenueCapabilities, timeframe: timeframes.Timeframe
) -> bool:
    """Checks whether a series can be polled over REST.

    Args:
        caps: Venue capabilities.
        timeframe: Resolution.

    Returns:
        True for REST klines, or a REST trade history for seconds bars.
    """
    if caps.is_trade_built(timeframe):
        return caps.market_data.trades_history
    return caps.market_data.rest_history
