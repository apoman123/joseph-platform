# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the reconnecting WebSocket candle stream."""

from __future__ import annotations

import contextlib
import decimal
import json
from collections.abc import AsyncIterator

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import ws
from joseph.market_data.domain import health

_ID = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_TF = timeframes.Timeframe.M1
_T0 = 1_795_277_340_000
_D = decimal.Decimal


def _candle(
    start: int, close: str, volume: str, final: bool, received: int
) -> market.Candle:
    """Builds a flat 1m candle whose open, high, low and close are equal.

    Args:
        start: Bar start, epoch milliseconds.
        close: Price as a decimal string.
        volume: Base volume as a decimal string.
        final: Whether the bar is final.
        received: Receipt time, epoch milliseconds.

    Returns:
        The candle.
    """
    price = _D(close)
    return market.Candle(
        instrument_id=_ID,
        timeframe=_TF,
        start_ms=start,
        end_ms=_TF.end_of(start),
        open=price,
        high=price,
        low=price,
        close=price,
        base_volume=_D(volume),
        is_final=final,
        source="t",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=received,
    )


class FakeConnection:
    """Scripted connection yielding frames then raising to disconnect.

    Attributes:
        frames: Frames left to deliver.
        fail_after: Whether the script ends in a disconnect; ``recv`` raises
            ``OSError`` after the last frame either way.
        sent: Every message sent, in order.
    """

    def __init__(self, frames: list[str], fail_after: bool) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
            fail_after: Whether the script ends in a disconnect; ``recv``
                raises ``OSError`` after the last frame either way.
        """
        self.frames = list(frames)
        self.fail_after = fail_after
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent message in ``sent``.

        Args:
            message: Message text.
        """
        self.sent.append(message)

    async def recv(self) -> str:
        """Returns the next frame or disconnects.

        Returns:
            The next frame text.

        Raises:
            OSError: No frame is left.
        """
        if self.frames:
            return self.frames.pop(0)
        raise OSError("disconnected")


class FakeConnector:
    """Hands out scripted connections; ``None`` entries fail to connect.

    Attributes:
        scripts: Frame lists left, one per connection attempt; ``None``
            fails that attempt.
        connections: Every connection opened, in order.
    """

    def __init__(self, scripts: list[list[str] | None]) -> None:
        """Initializes the connector.

        Args:
            scripts: One frame list per connection attempt; ``None`` fails
                that attempt.
        """
        self.scripts = list(scripts)
        self.connections: list[FakeConnection] = []

    @contextlib.asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[FakeConnection]:
        """Opens the next scripted connection and records it.

        Args:
            url: Stream URL (ignored).

        Yields:
            The connection.

        Raises:
            OSError: The attempt is scripted to fail or no script is left.
        """
        del url
        if not self.scripts:
            raise OSError("no more scripts")
        script = self.scripts.pop(0)
        if script is None:
            raise OSError("connect refused")
        connection = FakeConnection(script, fail_after=True)
        self.connections.append(connection)
        yield connection


def _frame(start: int, close: str, volume: str) -> str:
    """Builds a JSON frame for the fake parser.

    Args:
        start: Bar start, epoch milliseconds.
        close: Price as a decimal string.
        volume: Base volume as a decimal string.

    Returns:
        The frame text, without a ``final`` flag.
    """
    return json.dumps({"t": start, "c": close, "v": volume})


def _spec(marks_final: bool, clock: clock_lib.ManualClock) -> ws.WsCandleSpec:
    """Builds a spec with a simple JSON parser.

    Args:
        marks_final: Whether the venue's frames carry finality (``final``
            key); without it, ``final`` in a frame is ignored.
        clock: Clock the parser advances by 1 ms per candle frame.

    Returns:
        The spec.
    """

    def parse(raw: str | bytes, now_ms: int) -> list[market.Candle]:
        """Parses a test frame and advances ``clock`` by 1 ms per candle.

        Args:
            raw: JSON frame text.
            now_ms: Receipt time, stamped on the candle.

        Returns:
            One candle, or none for a frame without ``t``.
        """
        message = json.loads(raw)
        if "t" not in message:
            return []
        clock.advance(1)
        return [
            _candle(
                message["t"],
                message["c"],
                message["v"],
                bool(message.get("final")) and marks_final,
                now_ms,
            )
        ]

    return ws.WsCandleSpec(
        url="wss://test",
        subscribe=('{"sub":1}',),
        parse=parse,
        timeframe=_TF,
        venue_marks_final=marks_final,
    )


async def _no_sleep(_: float) -> None:
    """Sleep replacement that returns at once, whatever the delay."""


async def _no_backfill(start: int, end: int) -> list[market.Candle]:
    """REST backfill that finds no bars.

    Args:
        start: Requested inclusive start (ignored).
        end: Requested exclusive end (ignored).

    Returns:
        An empty list.
    """
    del start, end
    return []


async def _collect(
    stream: AsyncIterator[market.Candle], limit: int
) -> list[market.Candle]:
    """Collects up to ``limit`` candles or until the stream gives up.

    Args:
        stream: Candle stream; its final ``VenueUnavailableError`` is
            swallowed.
        limit: Number of candles after which collecting stops.

    Returns:
        The candles in stream order.
    """
    out: list[market.Candle] = []
    with contextlib.suppress(errors.VenueUnavailableError):
        async for candle in stream:
            out.append(candle)
            if len(out) >= limit:
                break
    return out


async def test_rollover_confirms_closed_bar_over_rest() -> None:
    """Venues without final flags get closed bars confirmed by REST."""
    clock = clock_lib.ManualClock(_T0 + 30_000)
    confirmed: list[tuple[int, int]] = []

    async def backfill(start: int, end: int) -> list[market.Candle]:
        """Records the request in ``confirmed`` and serves the closed bar.

        Args:
            start: Requested inclusive start, epoch milliseconds.
            end: Requested exclusive end, epoch milliseconds.

        Returns:
            The authoritative final bar at ``_T0``.
        """
        confirmed.append((start, end))
        return [_candle(_T0, "101.5", "9", True, clock.now_ms())]

    connector = FakeConnector(
        [
            [
                _frame(_T0, "100", "1"),
                _frame(_T0, "101", "2"),
                _frame(_T0 + 60_000, "102", "1"),
            ]
        ]
    )
    rows = await _collect(
        ws.stream_with_recovery(
            _spec(False, clock),
            backfill=backfill,
            clock=clock,
            connect=connector,
            max_failures=1,
            sleep=_no_sleep,
        ),
        10,
    )
    assert confirmed == [(_T0, _T0 + 60_000)]
    finals = [c for c in rows if c.is_final]
    assert [c.close for c in finals] == [_D("101.5")]
    assert rows[-1].start_ms == _T0 + 60_000
    assert not rows[-1].is_final
    assert connector.connections[0].sent == ['{"sub":1}']


async def test_websocket_update_alone_never_becomes_final() -> None:
    """A frame claiming finality is ignored for non-flagging venues."""
    clock = clock_lib.ManualClock(_T0)

    frame = json.dumps({"t": _T0, "c": "100", "v": "1", "final": True})
    rows = await _collect(
        ws.stream_with_recovery(
            _spec(False, clock),
            backfill=_no_backfill,
            clock=clock,
            connect=FakeConnector([[frame]]),
            max_failures=1,
            sleep=_no_sleep,
        ),
        5,
    )
    assert all(not c.is_final for c in rows)


async def test_duplicates_and_out_of_order_frames_are_dropped() -> None:
    """Repeated and older frames never reach consumers."""
    clock = clock_lib.ManualClock(_T0)

    frames = [
        _frame(_T0, "100", "2"),
        _frame(_T0, "100", "2"),
        _frame(_T0, "99", "1"),
    ]
    rows = await _collect(
        ws.stream_with_recovery(
            _spec(True, clock),
            backfill=_no_backfill,
            clock=clock,
            connect=FakeConnector([frames]),
            max_failures=1,
            sleep=_no_sleep,
        ),
        5,
    )
    assert len(rows) == 1


async def test_reconnect_backfills_missed_bars() -> None:
    """After a drop the missed range is fetched over REST."""
    clock = clock_lib.ManualClock(_T0)
    requests: list[tuple[int, int]] = []

    async def backfill(start: int, end: int) -> list[market.Candle]:
        """Records the request in ``requests`` and serves the missed bars.

        Args:
            start: Requested inclusive start, epoch milliseconds.
            end: Requested exclusive end, epoch milliseconds.

        Returns:
            Final bars at ``_T0`` and ``_T0 + 60_000``.
        """
        requests.append((start, end))
        return [
            _candle(_T0, "100", "5", True, clock.now_ms()),
            _candle(_T0 + 60_000, "101", "5", True, clock.now_ms()),
        ]

    tracker = health.FeedHealthTracker(instruments.Venue.BINANCE)
    connector = FakeConnector(
        [
            [json.dumps({"t": _T0, "c": "100", "v": "1"})],
            None,
            [json.dumps({"t": _T0 + 120_000, "c": "102", "v": "1"})],
        ]
    )
    rows = await _collect(
        ws.stream_with_recovery(
            _spec(True, clock),
            backfill=backfill,
            clock=clock,
            connect=connector,
            max_failures=3,
            sleep=_no_sleep,
            tracker=tracker,
        ),
        4,
    )
    assert requests, "a reconnect must trigger a backfill"
    assert [c.start_ms for c in rows if c.is_final] == [_T0, _T0 + 60_000]
    assert tracker.reconnects >= 2


async def test_stream_gives_up_after_max_failures() -> None:
    """Consecutive connect failures raise VenueUnavailableError."""
    clock = clock_lib.ManualClock(_T0)

    stream = ws.stream_with_recovery(
        _spec(True, clock),
        backfill=_no_backfill,
        clock=clock,
        connect=FakeConnector([None, None, None]),
        max_failures=3,
        sleep=_no_sleep,
    )
    with pytest.raises(errors.VenueUnavailableError):
        async for _ in stream:
            pass


async def test_unparseable_frames_are_recorded_not_fatal() -> None:
    """A malformed frame is skipped and recorded on the tracker."""
    clock = clock_lib.ManualClock(_T0)

    tracker = health.FeedHealthTracker(instruments.Venue.BINANCE)
    frames = [
        '{"t": "not-a-number", "c": "1", "v": "1"}',
        _frame(_T0, "100", "1"),
    ]
    rows = await _collect(
        ws.stream_with_recovery(
            _spec(True, clock),
            backfill=_no_backfill,
            clock=clock,
            connect=FakeConnector([frames]),
            max_failures=1,
            sleep=_no_sleep,
            tracker=tracker,
        ),
        1,
    )
    assert len(rows) == 1
    assert tracker.last_error is not None
