# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Reconnecting native WebSocket candle and trade streams with recovery.

One implementation serves every venue:

* subscribe messages are re-sent on each connection;
* an application-level ping keeps venues that require it alive;
* reconnects use exponential backoff, each delay jittered to 50-100 % of
  its nominal value, and give up after ``max_failures`` consecutive
  failures (so ``AUTO`` can fall back);
* after a reconnect the missed range is backfilled over REST;
* venues that do not flag closed bars get each closed bar confirmed over
  REST at rollover (a WebSocket update alone never becomes ``is_final``);
* duplicates and out-of-order updates are dropped via merge semantics.

Trade streams (``trade_stream_events``) additionally track *coverage*:
after a reconnect the missed trades are backfilled over REST where the
venue has a time-range trade history, or proven by a venue replay of
recent trades that overlaps the last trade seen; otherwise the stream
reports a coverage break (and a gap on the health tracker) so seconds bars
are never bridged across the outage. Every received frame (including
pongs) is a delivery proof that lets quiet seconds bars finalize.

Backfill and confirmation windows follow the instrument's session calendar
(taken from the candles themselves, else ``WsCandleSpec.calendar``), so the
time between exchange sessions is never confirmed or counted as a gap.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import random
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Protocol
from typing import TypeAlias

import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import health as health_lib
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_LOG = logging.getLogger(__name__)
_GUARD_BARS = 6
"""Newest bars the merge guard keeps per stream; older updates are dropped."""
HEALTHY_CONNECTION_MS = 30_000
"""Uptime after which a connection's end no longer counts as a failure.

A venue that accepts a subscription and drops it at once, or sends an error
frame and closes, must back off and give up after ``max_failures``
attempts; resetting the count on every subscribe or frame would retry it
forever at the shortest backoff. So a connection proves the venue healthy
only by delivering market data or by staying up this long. 30 s is far
longer than a venue takes to reject a subscription and close, and shorter
than the 60 s idle timeouts, so a quiet market's idle reconnect never
counts.
"""


def failures_after(
    failures: int,
    connected_at_ms: int | None,
    delivered_data: bool,
    now_ms: int,
) -> int:
    """Returns the consecutive-failure count after a connection ended.

    A connection that delivered market data (candles, trades, statuses;
    not acks, pongs or error messages), or that stayed up for
    ``HEALTHY_CONNECTION_MS`` (a quiet market), was healthy: the count
    starts again at 0 and its end does not count. Every other attempt
    (refused, failed before subscribing, dropped soon after subscribing,
    closed after an error frame) adds one.

    Args:
        failures: Consecutive failures before this connection.
        connected_at_ms: When the connection had subscribed, epoch
            milliseconds; ``None`` if it never got that far.
        delivered_data: Whether the connection delivered market data.
        now_ms: When the connection ended, epoch milliseconds.

    Returns:
        The new consecutive-failure count.
    """
    healthy = delivered_data or (
        connected_at_ms is not None
        and now_ms - connected_at_ms >= HEALTHY_CONNECTION_MS
    )
    return 0 if healthy else failures + 1


class WsConnection(Protocol):
    """Minimal WebSocket connection surface used by the stream."""

    async def send(self, message: str) -> None:
        """Sends a text frame.

        Args:
            message: Frame payload.
        """
        ...

    async def recv(self) -> str | bytes:
        """Receives one frame.

        Returns:
            Frame payload.
        """
        ...


ConnectFn: TypeAlias = Callable[
    [str], contextlib.AbstractAsyncContextManager[WsConnection]
]
"""Opens a connection to a URL: ``url -> async context manager``."""
Backfill: TypeAlias = Callable[[int, int], Awaitable[list[market.Candle]]]
"""REST candle fetch: ``(start_ms, end_ms) -> candles``."""
TradeBackfill: TypeAlias = Callable[
    [int, int], Awaitable[trades_lib.TradeCoverage]
]
"""REST trade history: ``(start_ms, end_ms) -> coverage``."""


def default_connect(
    url: str,
) -> contextlib.AbstractAsyncContextManager[WsConnection]:
    """Opens a WebSocket with protocol pings enabled.

    The opening handshake times out after 10 s, a protocol ping is sent
    every 20 s (and must be answered within 20 s), and frames up to 4 MiB
    are accepted.

    Args:
        url: WebSocket URL.

    Returns:
        Async context manager yielding the connection.
    """
    return websockets.connect(
        url,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_size=2**22,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class WsCandleSpec:
    """Venue-specific stream description.

    Attributes:
        url: WebSocket URL.
        subscribe: Frames sent after each connect.
        parse: ``(frame, received_ms) -> candles``, empty for acks; raises
            ``KeyError``/``ValueError``/``TypeError`` for a malformed frame
            (recorded on the tracker and skipped).
        timeframe: Stream resolution.
        venue_marks_final: True when frames flag closed bars reliably.
        ping_message: Optional application-level ping frame.
        ping_interval_s: Seconds between application pings.
        idle_timeout_s: Seconds without any frame before reconnecting.
        calendar: Session calendar of the streamed instrument, used before
            the first candle arrives (UTC-aligned bars by default).
    """

    url: str
    subscribe: tuple[str, ...]
    parse: Callable[[str | bytes, int], list[market.Candle]]
    timeframe: timeframes.Timeframe
    venue_marks_final: bool
    ping_message: str | None = None
    ping_interval_s: float = 20.0
    idle_timeout_s: float = 60.0
    calendar: sessions.BarCalendar = sessions.CONTINUOUS


class _Guard:
    """Drops duplicate and out-of-order updates per bar."""

    def __init__(self) -> None:
        """Initializes an empty guard."""
        self._bars: dict[int, market.Candle] = {}

    def accept(self, candle: market.Candle) -> market.Candle | None:
        """Merges a candle, returning it when it changed state.

        An update older than every remembered bar is dropped.

        Args:
            candle: Incoming update.

        Returns:
            The stored candle when changed, else ``None``.
        """
        if self._bars and candle.start_ms < min(self._bars):
            return None
        result = candle_rules.merge_candle(
            self._bars.get(candle.start_ms), candle
        )
        if not result.changed:
            return None
        self._bars[candle.start_ms] = result.candle
        while len(self._bars) > _GUARD_BARS:
            del self._bars[min(self._bars)]
        return result.candle

    def last_final_start(self) -> int | None:
        """Returns the newest finalized bar start, if any."""
        finals = [s for s, c in self._bars.items() if c.is_final]
        return max(finals) if finals else None

    def open_start(self) -> int | None:
        """Returns the newest non-final bar start, if any."""
        opens = [s for s, c in self._bars.items() if not c.is_final]
        return max(opens) if opens else None

    def calendar(self, default: sessions.BarCalendar) -> sessions.BarCalendar:
        """Returns the session calendar of the guarded series.

        Args:
            default: Calendar used before any candle was accepted.

        Returns:
            The calendar of the stored candles' instrument, else ``default``.
        """
        if not self._bars:
            return default
        candle = next(iter(self._bars.values()))
        return sessions.for_instrument(candle.instrument_id)


async def stream_with_recovery(  # noqa: PLR0912 - recovery loop.
    spec: WsCandleSpec,
    *,
    backfill: Backfill,
    clock: clock_lib.Clock,
    connect: ConnectFn = default_connect,
    max_failures: int = 5,
    base_delay_s: float = 0.5,
    max_delay_s: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tracker: health_lib.FeedHealthTracker | None = None,
) -> AsyncGenerator[market.Candle, None]:
    """Yields candle updates from a native stream, recovering from drops.

    Args:
        spec: Venue stream description.
        backfill: REST fetch ``(start_ms, end_ms) -> candles``.
        clock: Time source.
        connect: WebSocket connector (injectable for tests).
        max_failures: Consecutive failed connections before giving up
            (see ``failures_after``: a connection that delivered candles or
            stayed up ``HEALTHY_CONNECTION_MS`` resets the count; acks,
            pongs and error frames do not).
        base_delay_s: Nominal first backoff delay in seconds (doubles per
            failure).
        max_delay_s: Cap of the nominal backoff delay in seconds.
        sleep: Sleep function (injectable for tests).
        tracker: Optional health tracker updated as frames arrive.

    Yields:
        Changed candles (live updates and finalized bars).

    Raises:
        errors.VenueUnavailableError: After ``max_failures`` failures.
    """
    guard = _Guard()
    failures = 0
    connected_before = False
    tf = spec.timeframe
    while True:
        connected_at: int | None = None
        delivered = False
        try:
            async with connect(spec.url) as connection:
                for frame in spec.subscribe:
                    await connection.send(frame)
                connected_at = clock.now_ms()
                if connected_before:
                    # Refill from the bar after the last final one (else the
                    # previous bar) through the current bar.
                    calendar = guard.calendar(spec.calendar)
                    resume_from = guard.last_final_start()
                    current = calendar.floor(tf, clock.now_ms())
                    start = (
                        calendar.end_of(tf, resume_from)
                        if resume_from is not None
                        else calendar.shift(tf, current, -1)
                    )
                    for candle in await _safe_backfill(
                        backfill,
                        start,
                        calendar.end_of(tf, current),
                        tracker,
                        clock,
                    ):
                        accepted = guard.accept(candle)
                        if accepted is not None:
                            yield accepted
                connected_before = True
                pinger = (
                    asyncio.create_task(_ping_loop(connection, spec))
                    if spec.ping_message
                    else None
                )
                try:
                    while True:
                        raw = await asyncio.wait_for(
                            connection.recv(), spec.idle_timeout_s
                        )
                        now = clock.now_ms()
                        try:
                            updates = spec.parse(raw, now)
                        except (KeyError, ValueError, TypeError) as err:
                            if tracker is not None:
                                tracker.record_error(
                                    now, f"unparseable frame: {err}"
                                )
                            continue
                        if updates and tracker is not None:
                            tracker.record_message(now)
                        for update in updates:
                            async for candle in _apply(
                                spec,
                                guard,
                                update,
                                backfill=backfill,
                                tracker=tracker,
                                clock=clock,
                            ):
                                # A changed candle is market data; a
                                # repeated snapshot proves nothing new.
                                delivered = True
                                yield candle
                finally:
                    await _stop_pinger(pinger)
        except (OSError, TimeoutError, websockets.WebSocketException) as err:
            now = clock.now_ms()
            failures = failures_after(failures, connected_at, delivered, now)
            if tracker is not None:
                tracker.record_reconnect(now, type(err).__name__)
            _LOG.warning(
                "stream %s failed (%s), attempt %d",
                spec.url,
                type(err).__name__,
                failures,
            )
            if failures >= max_failures:
                raise errors.VenueUnavailableError(
                    f"native stream unavailable after {failures} attempts"
                ) from err
            delay = min(max_delay_s, base_delay_s * 2 ** max(failures - 1, 0))
            await sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - backoff jitter, not cryptography


async def _apply(
    spec: WsCandleSpec,
    guard: _Guard,
    update: market.Candle,
    *,
    backfill: Backfill,
    tracker: health_lib.FeedHealthTracker | None,
    clock: clock_lib.Clock,
) -> AsyncIterator[market.Candle]:
    """Applies one parsed update, confirming closed bars when needed.

    On a venue that does not flag closed bars reliably, the first update of
    a new bar triggers a REST confirmation of the bars it closed; a bar
    that REST does not return final is recorded as a gap.

    Args:
        spec: Stream description.
        guard: Per-stream merge guard.
        update: Parsed candle.
        backfill: REST fetch for confirmation.
        tracker: Optional health tracker.
        clock: Time source.

    Yields:
        Changed candles in time order.
    """
    open_start = guard.open_start()
    if (
        not spec.venue_marks_final
        and open_start is not None
        and update.start_ms > open_start
    ):
        calendar = sessions.for_instrument(update.instrument_id)
        last_final = guard.last_final_start()
        confirm_from = (
            open_start
            if last_final is None
            else min(open_start, calendar.end_of(spec.timeframe, last_final))
        )
        expected = calendar.bars_between(
            spec.timeframe, confirm_from, update.start_ms
        )
        confirmed = await _safe_backfill(
            backfill, confirm_from, update.start_ms, tracker, clock
        )
        finals = [c for c in confirmed if c.is_final]
        if tracker is not None and len(finals) < expected:
            tracker.record_gap(clock.now_ms(), expected - len(finals))
        for candle in finals:
            accepted = guard.accept(candle)
            if accepted is not None:
                yield accepted
    # Such a venue's "closed" flag is not trusted: only REST confirms a bar.
    if update.is_final and not spec.venue_marks_final:
        return
    accepted = guard.accept(update)
    if accepted is not None:
        yield accepted


async def _safe_backfill(
    backfill: Backfill,
    start_ms: int,
    end_ms: int,
    tracker: health_lib.FeedHealthTracker | None,
    clock: clock_lib.Clock,
) -> list[market.Candle]:
    """Runs a REST backfill, recording but tolerating failures.

    Args:
        backfill: REST fetch.
        start_ms: Inclusive start, epoch milliseconds.
        end_ms: Exclusive end, epoch milliseconds.
        tracker: Optional health tracker.
        clock: Time source.

    Returns:
        Candles, or an empty list for an empty range or a failed REST call.
    """
    if end_ms <= start_ms:
        return []
    try:
        return await backfill(start_ms, end_ms)
    except errors.JosephError as err:
        if tracker is not None:
            tracker.record_error(clock.now_ms(), f"backfill failed: {err}")
        return []


async def _stop_pinger(pinger: asyncio.Task[None] | None) -> None:
    """Cancels a ping task and waits for it before the connection closes.

    Closing a connection while a cancelled ping is still inside ``send``
    races the connection teardown, so the task is awaited. Whatever the task
    ended with is discarded: the stream handles the connection's own errors.

    Args:
        pinger: Ping task, if any.
    """
    if pinger is None:
        return
    pinger.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await pinger


async def _ping_loop(connection: WsConnection, spec: WsCandleSpec) -> None:
    """Sends application-level pings until cancelled or a send fails.

    Args:
        connection: Open connection.
        spec: Stream description with the ping frame.
    """
    if spec.ping_message is None:
        raise AssertionError("ping loop started without a ping message")
    while True:
        await asyncio.sleep(spec.ping_interval_s)
        try:
            await connection.send(spec.ping_message)
        except (OSError, websockets.WebSocketException):
            return


@dataclasses.dataclass(frozen=True, slots=True)
class WsTradeSpec:
    """Venue-specific public trade stream description.

    Attributes:
        url: WebSocket URL.
        subscribe: Frames sent after each connect.
        parse: Converts a frame to trades (empty for acks and pongs);
            raises ``KeyError``/``ValueError``/``TypeError`` for a
            malformed trade frame (treated as lost trades).
        ping_message: Optional application-level ping frame. Venues that
            answer it give quiet markets a steady delivery proof.
        ping_interval_s: Seconds between application pings.
        idle_timeout_s: Seconds without any frame before reconnecting.
        replays_recent: True when the venue sends its latest trades right
            after subscribing (contiguous with the live feed), so a replay
            that repeats a trade already seen proves no trade was missed.
    """

    url: str
    subscribe: tuple[str, ...]
    parse: Callable[[str | bytes, int], list[market.Trade]]
    ping_message: str | None = None
    ping_interval_s: float = 5.0
    idle_timeout_s: float = 60.0
    replays_recent: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class CoverageStart:
    """A new coverage segment starts (startup or unrecovered reconnect).

    Attributes:
        complete_from_ms: Venue time from which every trade is delivered,
            or ``None`` when unknown (from the next trade on).
    """

    complete_from_ms: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class TradesReceived:
    """New (de-duplicated) trades.

    Attributes:
        trades: Trades in arrival order.
        received_ms: Receipt time, epoch milliseconds.
    """

    trades: tuple[market.Trade, ...]
    received_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class FrameReceived:
    """Any frame arrived: every trade sent before it has been delivered.

    Attributes:
        received_ms: Receipt time, epoch milliseconds.
    """

    received_ms: int


TradeStreamEvent: TypeAlias = CoverageStart | TradesReceived | FrameReceived
"""One event of ``trade_stream_events``."""

_FIRST_FRAME_WAIT_S = 2.0
"""Seconds to wait for the first frame (subscription live) before backfill."""
_BACKFILL_SKEW_MS = 5_000
"""Milliseconds a backfill asks past local now, for a clock behind the venue."""


async def trade_stream_events(  # noqa: PLR0912, PLR0915 - recovery loop.
    spec: WsTradeSpec,
    *,
    clock: clock_lib.Clock,
    connect: ConnectFn = default_connect,
    backfill: TradeBackfill | None = None,
    prime: Callable[[int], int] | None = None,
    max_failures: int = 5,
    base_delay_s: float = 0.5,
    max_delay_s: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tracker: health_lib.FeedHealthTracker | None = None,
    dedupe_capacity: int = 50_000,
) -> AsyncGenerator[TradeStreamEvent, None]:
    """Yields trade-stream events from a native stream, tracking coverage.

    On every connection the subscribe frames are sent and the first frame
    is awaited briefly (so the subscription is active), then:

    * first connection: with ``prime`` and ``backfill``, trades from
      ``prime(now)`` are fetched and coverage starts there; otherwise
      coverage starts at the first trade received;
    * reconnect: trades since the last one seen are backfilled; failing
      that, a replay that repeats a trade already seen (``replays_recent``)
      proves continuity; failing that, a gap is recorded and a new
      coverage segment starts.

    A backfill counts only when it reaches back to its start. A frame that
    does not parse is treated as lost trades: a gap and a new segment.

    Args:
        spec: Venue stream description.
        clock: Time source.
        connect: WebSocket connector (injectable for tests).
        backfill: Optional REST trade history ``(start, end) -> coverage``.
        prime: Optional ``now -> start`` for the first connection's
            backfill (requires ``backfill``), epoch milliseconds.
        max_failures: Consecutive failed connections before giving up
            (see ``failures_after``: a connection that delivered new trades
            or stayed up ``HEALTHY_CONNECTION_MS`` resets the count; acks,
            pongs, error frames and replayed trades already seen do not).
        base_delay_s: Nominal first backoff delay in seconds (doubles per
            failure).
        max_delay_s: Cap of the nominal backoff delay in seconds.
        sleep: Sleep function (injectable for tests).
        tracker: Optional health tracker updated as frames arrive.
        dedupe_capacity: Trade ids remembered for de-duplication.

    Yields:
        ``CoverageStart``, ``TradesReceived`` and ``FrameReceived`` events.

    Raises:
        errors.VenueUnavailableError: After ``max_failures`` failures.
    """
    seen = trades_lib.RecentIds(dedupe_capacity)
    failures = 0
    connected_before = False
    last_trade_ms: int | None = None
    while True:
        connected_at: int | None = None
        delivered = False
        try:
            async with connect(spec.url) as connection:
                for frame in spec.subscribe:
                    await connection.send(frame)
                connected_at = clock.now_ms()
                pending: list[str | bytes] = []
                with contextlib.suppress(TimeoutError):
                    pending.append(
                        await asyncio.wait_for(
                            connection.recv(), _FIRST_FRAME_WAIT_S
                        )
                    )
                now = clock.now_ms()
                start = (
                    last_trade_ms
                    if connected_before
                    else (prime(now) if prime is not None else None)
                )
                recovered = False
                if start is not None and backfill is not None:
                    # Ask past "now" so a local clock behind the venue's
                    # cannot leave a hole; overlap is de-duplicated by id.
                    # A backfill that does not reach back to ``start`` would
                    # leave a hole, so it does not count as a recovery.
                    coverage = await _safe_trade_backfill(
                        backfill,
                        start,
                        max(now, start) + _BACKFILL_SKEW_MS,
                        tracker,
                        clock,
                    )
                    if coverage is not None and coverage.from_ms <= start:
                        recovered = True
                        if not connected_before:
                            yield CoverageStart(start)
                        fresh = seen.fresh(coverage.trades)
                        if fresh:
                            last_trade_ms = _newest(fresh, last_trade_ms)
                            yield TradesReceived(tuple(fresh), now)
                awaiting_overlap = (
                    not recovered and connected_before and spec.replays_recent
                )
                if not recovered and not awaiting_overlap:
                    if connected_before and tracker is not None:
                        tracker.record_gap(now)
                    yield CoverageStart(None)
                connected_before = True
                pinger = (
                    asyncio.create_task(_trade_ping_loop(connection, spec))
                    if spec.ping_message
                    else None
                )
                try:
                    while True:
                        if pending:
                            raw = pending.pop(0)
                        else:
                            raw = await asyncio.wait_for(
                                connection.recv(), spec.idle_timeout_s
                            )
                        now = clock.now_ms()
                        if tracker is not None:
                            tracker.record_message(now)
                        try:
                            parsed = spec.parse(raw, now)
                        except (KeyError, ValueError, TypeError) as err:
                            if tracker is not None:
                                tracker.record_error(
                                    now, f"unparseable trade frame: {err}"
                                )
                                tracker.record_gap(now)
                            awaiting_overlap = False
                            yield CoverageStart(None)
                            continue
                        if awaiting_overlap and parsed:
                            awaiting_overlap = False
                            if not any(t.trade_id in seen for t in parsed):
                                if tracker is not None:
                                    tracker.record_gap(now)
                                yield CoverageStart(None)
                        fresh = seen.fresh(parsed)
                        if fresh:
                            # New trades are market data; a replay of trades
                            # already seen proves nothing new.
                            delivered = True
                            last_trade_ms = _newest(fresh, last_trade_ms)
                            yield TradesReceived(tuple(fresh), now)
                        # Until a replay proves continuity, a frame proves
                        # nothing about the trades sent before it.
                        if not awaiting_overlap:
                            yield FrameReceived(now)
                finally:
                    await _stop_pinger(pinger)
        except (OSError, TimeoutError, websockets.WebSocketException) as err:
            now = clock.now_ms()
            failures = failures_after(failures, connected_at, delivered, now)
            if tracker is not None:
                tracker.record_reconnect(now, type(err).__name__)
            _LOG.warning(
                "trade stream %s failed (%s), attempt %d",
                spec.url,
                type(err).__name__,
                failures,
            )
            if failures >= max_failures:
                raise errors.VenueUnavailableError(
                    f"native trade stream unavailable after {failures} attempts"
                ) from err
            delay = min(max_delay_s, base_delay_s * 2 ** max(failures - 1, 0))
            await sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - backoff jitter, not cryptography


async def stream_trades_with_recovery(
    spec: WsTradeSpec,
    *,
    clock: clock_lib.Clock,
    connect: ConnectFn = default_connect,
    backfill: TradeBackfill | None = None,
    max_failures: int = 5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tracker: health_lib.FeedHealthTracker | None = None,
) -> AsyncGenerator[market.Trade, None]:
    """Yields de-duplicated public trades, backfilling after reconnects.

    Args:
        spec: Venue stream description.
        clock: Time source.
        connect: WebSocket connector.
        backfill: Optional REST trade history used after reconnects.
        max_failures: Consecutive failures before giving up.
        sleep: Sleep function (injectable for tests).
        tracker: Optional health tracker (gaps recorded when trades may
            have been missed).

    Yields:
        Trades in arrival order.

    Raises:
        errors.VenueUnavailableError: After ``max_failures`` failures
            (propagated from ``trade_stream_events``).
    """
    events = trade_stream_events(
        spec,
        clock=clock,
        connect=connect,
        backfill=backfill,
        max_failures=max_failures,
        sleep=sleep,
        tracker=tracker,
    )
    async with contextlib.aclosing(events):
        async for event in events:
            if isinstance(event, TradesReceived):
                for trade in event.trades:
                    yield trade


async def stream_trade_candles(
    spec: WsTradeSpec,
    builder: trade_bars.TradeBarBuilder,
    *,
    clock: clock_lib.Clock,
    connect: ConnectFn = default_connect,
    backfill: TradeBackfill | None = None,
    grace_ms: int = 1_500,
    max_failures: int = 5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tracker: health_lib.FeedHealthTracker | None = None,
) -> AsyncGenerator[market.Candle, None]:
    """Yields seconds candles built from a native trade stream.

    With a REST trade history the first connection is primed from the
    previous bar start (so the first live bar is complete) and reconnects
    are backfilled; otherwise the first bar after each coverage start is
    partial and dropped, and outages are recorded as gaps.

    Args:
        spec: Venue stream description.
        builder: Seconds-bar builder of the instrument and timeframe.
        clock: Time source.
        connect: WebSocket connector.
        backfill: Optional REST trade history.
        grace_ms: Milliseconds a frame must trail a quiet bar's end before
            that frame finalizes the bar (allows for delivery delay).
        max_failures: Consecutive failed connections before giving up.
        sleep: Sleep function (injectable for tests).
        tracker: Optional health tracker.

    Yields:
        Live and finalized seconds candles.

    Raises:
        errors.VenueUnavailableError: After ``max_failures`` failures
            (propagated from ``trade_stream_events``).
    """
    tf = builder.timeframe
    calendar = builder.calendar

    def prime(now_ms: int) -> int:
        """Returns the aligned start of the bar before the current one.

        Args:
            now_ms: Current time, epoch milliseconds.

        Returns:
            Bar start (on the instrument's session grid).
        """
        return calendar.shift(tf, calendar.floor(tf, now_ms), -1)

    events = trade_stream_events(
        spec,
        clock=clock,
        connect=connect,
        backfill=backfill,
        prime=prime if backfill is not None else None,
        max_failures=max_failures,
        sleep=sleep,
        tracker=tracker,
    )
    async with contextlib.aclosing(events):
        async for event in events:
            if isinstance(event, CoverageStart):
                discarded = builder.start_segment(event.complete_from_ms)
                if discarded and tracker is not None:
                    tracker.record_gap(clock.now_ms(), discarded)
                continue
            if isinstance(event, TradesReceived):
                candles = builder.add(event.trades, event.received_ms)
            else:
                candles = builder.finalize_through(
                    event.received_ms - grace_ms, event.received_ms
                )
            for candle in candles:
                yield candle


def _newest(trades: list[market.Trade], current: int | None) -> int:
    """Returns the newest venue timestamp among trades and ``current``.

    Args:
        trades: Non-empty trades.
        current: Previous newest timestamp.

    Returns:
        Newest timestamp.
    """
    newest = max(t.venue_event_ts_ms for t in trades)
    return newest if current is None else max(newest, current)


async def _safe_trade_backfill(
    backfill: TradeBackfill,
    start_ms: int,
    end_ms: int,
    tracker: health_lib.FeedHealthTracker | None,
    clock: clock_lib.Clock,
) -> trades_lib.TradeCoverage | None:
    """Runs a REST trade backfill, recording but tolerating failures.

    Args:
        backfill: REST trade history.
        start_ms: Inclusive start, epoch milliseconds.
        end_ms: Exclusive end, epoch milliseconds.
        tracker: Optional health tracker.
        clock: Time source.

    Returns:
        Coverage (empty at ``start_ms`` for an empty range), or ``None``
        when the REST call failed.
    """
    if end_ms <= start_ms:
        return trades_lib.TradeCoverage((), start_ms, start_ms)
    try:
        return await backfill(start_ms, end_ms)
    except errors.JosephError as err:
        if tracker is not None:
            tracker.record_error(
                clock.now_ms(), f"trade backfill failed: {err}"
            )
        return None


async def _trade_ping_loop(connection: WsConnection, spec: WsTradeSpec) -> None:
    """Sends application-level pings until cancelled or a send fails.

    Args:
        connection: Open connection.
        spec: Stream description with the ping frame.
    """
    if spec.ping_message is None:
        raise AssertionError("ping loop started without a ping message")
    while True:
        await asyncio.sleep(spec.ping_interval_s)
        try:
            await connection.send(spec.ping_message)
        except (OSError, websockets.WebSocketException):
            return
