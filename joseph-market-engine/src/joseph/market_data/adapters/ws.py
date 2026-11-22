"""Reconnecting native WebSocket candle streams with REST recovery.

One implementation serves every venue:

* subscribe messages are re-sent on each connection;
* an application-level ping keeps venues that require it alive;
* reconnects use exponential backoff with full jitter and give up after
  ``max_failures`` consecutive failures (so ``AUTO`` can fall back);
* after a reconnect the missed range is backfilled over REST;
* venues that do not flag closed bars get each closed bar confirmed over
  REST at rollover (a WebSocket update alone never becomes ``is_final``);
* duplicates and out-of-order updates are dropped via merge semantics.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import random
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Protocol

import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import health as health_lib

_LOG = logging.getLogger(__name__)
_GUARD_BARS = 6


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


ConnectFn = Callable[
    [str], contextlib.AbstractAsyncContextManager[WsConnection]
]
Backfill = Callable[[int, int], Awaitable[list[market.Candle]]]


def default_connect(
    url: str,
) -> contextlib.AbstractAsyncContextManager[WsConnection]:
    """Opens a WebSocket with protocol pings enabled.

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
        parse: Converts a frame to candle updates (empty for acks).
        timeframe: Stream resolution.
        venue_marks_final: True when frames flag closed bars reliably.
        ping_message: Optional application-level ping frame.
        ping_interval_s: Interval between application pings.
        idle_timeout_s: Reconnect when no frame arrives for this long.
    """

    url: str
    subscribe: tuple[str, ...]
    parse: Callable[[str | bytes, int], list[market.Candle]]
    timeframe: timeframes.Timeframe
    venue_marks_final: bool
    ping_message: str | None = None
    ping_interval_s: float = 20.0
    idle_timeout_s: float = 60.0


class _Guard:
    """Drops duplicate and out-of-order updates per bar."""

    def __init__(self) -> None:
        """Initializes an empty guard."""
        self._bars: dict[int, market.Candle] = {}

    def accept(self, candle: market.Candle) -> market.Candle | None:
        """Merges a candle, returning it when it changed state.

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
) -> AsyncIterator[market.Candle]:
    """Yields candle updates from a native stream, recovering from drops.

    Args:
        spec: Venue stream description.
        backfill: REST fetch ``(start_ms, end_ms) -> candles``.
        clock: Time source.
        connect: WebSocket connector (injectable for tests).
        max_failures: Consecutive failures before giving up.
        base_delay_s: First backoff delay.
        max_delay_s: Backoff cap.
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
    duration = spec.timeframe.duration_ms
    while True:
        try:
            async with connect(spec.url) as connection:
                for frame in spec.subscribe:
                    await connection.send(frame)
                if connected_before:
                    resume_from = guard.last_final_start()
                    start = (
                        resume_from + duration
                        if resume_from is not None
                        else spec.timeframe.floor(clock.now_ms()) - duration
                    )
                    for candle in await _safe_backfill(
                        backfill,
                        start,
                        clock.now_ms() + duration,
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
                        if updates:
                            failures = 0
                            if tracker is not None:
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
                                yield candle
                finally:
                    if pinger is not None:
                        pinger.cancel()
        except (OSError, TimeoutError, websockets.WebSocketException) as err:
            failures += 1
            now = clock.now_ms()
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
            delay = min(max_delay_s, base_delay_s * 2 ** (failures - 1))
            await sleep(delay * (0.5 + random.random() / 2))  # noqa: S311


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
        last_final = guard.last_final_start()
        confirm_from = (
            open_start
            if last_final is None
            else min(open_start, last_final + spec.timeframe.duration_ms)
        )
        expected = (update.start_ms - confirm_from) // (
            spec.timeframe.duration_ms
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
        start_ms: Inclusive start.
        end_ms: Exclusive end.
        tracker: Optional health tracker.
        clock: Time source.

    Returns:
        Candles, or an empty list when the REST call failed.
    """
    if end_ms <= start_ms:
        return []
    try:
        return await backfill(start_ms, end_ms)
    except errors.JosephError as err:
        if tracker is not None:
            tracker.record_error(clock.now_ms(), f"backfill failed: {err}")
        return []


async def _ping_loop(connection: WsConnection, spec: WsCandleSpec) -> None:
    """Sends application-level pings until cancelled.

    Args:
        connection: Open connection.
        spec: Stream description with the ping frame.
    """
    assert spec.ping_message is not None
    while True:
        await asyncio.sleep(spec.ping_interval_s)
        try:
            await connection.send(spec.ping_message)
        except (OSError, websockets.WebSocketException):
            return
