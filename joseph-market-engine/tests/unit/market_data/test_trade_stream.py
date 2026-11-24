# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for reconnecting trade streams and trade-built candles."""

from __future__ import annotations

import contextlib
import decimal
import json
from collections.abc import AsyncIterator
from typing import NoReturn
from typing import TypeAlias

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import ws
from joseph.market_data.domain import health
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_T0 = 1_795_349_070_000
_D = decimal.Decimal

Frame: TypeAlias = tuple[int, str]
"""Milliseconds to advance the clock by, then the frame text to deliver."""


def _trade(trade_id: int, ts: int, price: str = "100") -> market.Trade:
    """Builds a buyer-initiated trade of one ``_ID`` contract.

    Args:
        trade_id: Venue trade id, converted with ``str``.
        ts: Venue event time in epoch milliseconds, also the receive time.
        price: Price as a decimal string.

    Returns:
        The trade.
    """
    return market.Trade(
        instrument_id=_ID,
        trade_id=str(trade_id),
        venue_event_ts_ms=ts,
        received_at_ms=ts,
        price=_D(price),
        base_quantity=_D(1),
        aggressor_side=market.AggressorSide.BUY,
        is_aggregated=False,
    )


def _trades_frame(*rows: tuple[int, int]) -> str:
    """Encodes ``(id, ts)`` trades as a test frame.

    Args:
        *rows: Trade id and venue time in epoch milliseconds of each trade.

    Returns:
        The JSON frame text that ``_parse`` reads.
    """
    return json.dumps({"trades": [{"id": i, "ts": t} for i, t in rows]})


def _parse(raw: str | bytes, now_ms: int) -> list[market.Trade]:
    """Parses a test frame (``pong`` and acks carry no trades).

    Args:
        raw: JSON frame text.
        now_ms: Receipt time (ignored).

    Returns:
        The frame's trades; none for a frame without ``trades``.

    Raises:
        ValueError: The frame has ``"broken"`` set.
    """
    del now_ms
    message = json.loads(raw)
    if message.get("broken"):
        raise ValueError("broken frame")
    return [_trade(r["id"], r["ts"]) for r in message.get("trades", [])]


class FakeConnection:
    """WebSocket double that delivers scripted frames, then disconnects.

    Attributes:
        frames: Frames left to deliver, each after its clock advance.
        clock: Clock advanced as frames arrive.
        sent: Every message sent, in order.
    """

    def __init__(
        self, frames: list[Frame], clock: clock_lib.ManualClock
    ) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver, each after its clock advance.
            clock: Clock advanced as frames arrive.
        """
        self.frames = list(frames)
        self.clock = clock
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent message in ``sent``.

        Args:
            message: Message text.
        """
        self.sent.append(message)

    async def recv(self) -> str:
        """Advances the clock and returns the next frame.

        Returns:
            The next frame text.

        Raises:
            OSError: No frame is left (the connection drops).
        """
        if not self.frames:
            raise OSError("disconnected")
        advance, frame = self.frames.pop(0)
        self.clock.advance(advance)
        return frame


class FakeConnector:
    """Connect function double that hands out scripted connections.

    Attributes:
        scripts: Frame scripts left, one per connection attempt; ``None``
            fails that attempt.
        clock: Clock the connections advance.
        connections: Every connection opened, in order.
    """

    def __init__(
        self, scripts: list[list[Frame] | None], clock: clock_lib.ManualClock
    ) -> None:
        """Initializes the connector.

        Args:
            scripts: One frame script per connection attempt; ``None`` fails
                that attempt.
            clock: Clock the connections advance.
        """
        self.scripts = list(scripts)
        self.clock = clock
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
            raise OSError("refused")
        connection = FakeConnection(script, self.clock)
        self.connections.append(connection)
        yield connection


async def _no_sleep(_: float) -> None:
    """Returns at once, whatever the requested delay."""


def _spec(replays: bool = False) -> ws.WsTradeSpec:
    """Builds a test stream spec that parses frames with ``_parse``.

    Args:
        replays: Whether the venue replays recent trades on subscribe.

    Returns:
        The spec.
    """
    return ws.WsTradeSpec(
        url="wss://test",
        subscribe=('{"sub":1}',),
        parse=_parse,
        replays_recent=replays,
    )


async def _events(
    stream: AsyncIterator[ws.TradeStreamEvent],
) -> list[ws.TradeStreamEvent]:
    """Collects events until the stream gives up.

    Args:
        stream: Event stream; its final ``VenueUnavailableError`` is
            swallowed.

    Returns:
        The events in stream order.
    """
    out: list[ws.TradeStreamEvent] = []
    with contextlib.suppress(errors.VenueUnavailableError):
        async for event in stream:
            out.append(event)
    return out


def _ids(events: list[ws.TradeStreamEvent]) -> list[str]:
    """Lists delivered trade ids.

    Args:
        events: Stream events.

    Returns:
        Ids of the trades in ``TradesReceived`` events, in order.
    """
    ids: list[str] = []
    for event in events:
        if isinstance(event, ws.TradesReceived):
            ids.extend(trade.trade_id for trade in event.trades)
    return ids


def _starts(events: list[ws.TradeStreamEvent]) -> list[int | None]:
    """Lists coverage starts.

    Args:
        events: Stream events.

    Returns:
        ``complete_from_ms`` of every ``CoverageStart``.
    """
    return [
        e.complete_from_ms for e in events if isinstance(e, ws.CoverageStart)
    ]


async def test_first_connection_without_backfill_has_unknown_start() -> None:
    """Without backfill the start is unknown; every frame proves delivery."""
    clock = clock_lib.ManualClock(_T0)
    connector = FakeConnector(
        [[(5, _trades_frame((1, _T0 + 1))), (5, '{"pong":1}')]], clock
    )
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=connector,
            max_failures=1,
            sleep=_no_sleep,
        )
    )
    assert _starts(events) == [None]
    assert _ids(events) == ["1"]
    assert sum(isinstance(e, ws.FrameReceived) for e in events) == 2
    assert connector.connections[0].sent == ['{"sub":1}']


async def test_prime_backfill_starts_known_coverage() -> None:
    """With a REST history the first connection is primed and complete."""
    clock = clock_lib.ManualClock(_T0 + 1_500)
    calls: list[tuple[int, int]] = []

    async def backfill(start: int, end: int) -> trades_lib.TradeCoverage:
        """Records the request in ``calls`` and serves one trade.

        Args:
            start: Requested inclusive start, epoch milliseconds.
            end: Requested exclusive end, epoch milliseconds.

        Returns:
            Complete coverage of the span with one trade at ``_T0 + 1_200``.
        """
        calls.append((start, end))
        return trades_lib.TradeCoverage((_trade(7, _T0 + 1_200),), start, end)

    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [[(1, _trades_frame((8, _T0 + 1_600)))]], clock
            ),
            backfill=backfill,
            prime=lambda now: now - now % 1_000,
            max_failures=1,
            sleep=_no_sleep,
        )
    )
    assert calls[0][0] == _T0 + 1_000
    assert calls[0][1] > clock.now_ms()
    assert _starts(events) == [_T0 + 1_000]
    assert _ids(events) == ["7", "8"]


async def test_reconnect_backfills_missed_trades_and_dedupes() -> None:
    """After a drop missed trades come from REST; repeats are dropped."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.BINANCE)
    requests: list[tuple[int, int]] = []

    async def backfill(start: int, end: int) -> trades_lib.TradeCoverage:
        """Records the request in ``requests`` and serves the outage.

        Args:
            start: Requested inclusive start, epoch milliseconds.
            end: Requested exclusive end, epoch milliseconds.

        Returns:
            Complete coverage of the span: one already seen trade and one
            missed trade.
        """
        requests.append((start, end))
        return trades_lib.TradeCoverage(
            (_trade(2, _T0 + 20), _trade(3, _T0 + 30)), start, end
        )

    connector = FakeConnector(
        [
            [(1, _trades_frame((1, _T0 + 10), (2, _T0 + 20)))],
            None,
            [(1, _trades_frame((3, _T0 + 30), (4, _T0 + 40)))],
        ],
        clock,
    )
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=connector,
            backfill=backfill,
            max_failures=3,
            sleep=_no_sleep,
            tracker=tracker,
        )
    )
    assert requests[0][0] == _T0 + 20
    assert _ids(events) == ["1", "2", "3", "4"]
    assert _starts(events) == [None]
    assert tracker.gaps == 0
    assert tracker.reconnects >= 2


async def test_reconnect_without_backfill_records_gap() -> None:
    """Venues without trade history break coverage on reconnect."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.MEXC)
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [
                    [(1, _trades_frame((1, _T0 + 10)))],
                    [(1, _trades_frame((2, _T0 + 50)))],
                ],
                clock,
            ),
            max_failures=2,
            sleep=_no_sleep,
            tracker=tracker,
        )
    )
    assert _starts(events) == [None, None]
    assert tracker.gaps == 1


async def _failing_backfill(start: int, end: int) -> NoReturn:
    """Fails every REST backfill.

    Args:
        start: Requested inclusive start (ignored).
        end: Requested exclusive end (ignored).

    Raises:
        errors.VenueUnavailableError: Always.
    """
    del start, end
    raise errors.VenueUnavailableError("down")


async def test_failed_backfill_records_gap_and_breaks_coverage() -> None:
    """A REST failure during recovery is a gap, never silently bridged."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.BITGET)
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [
                    [(1, _trades_frame((1, _T0 + 10)))],
                    [(1, _trades_frame((2, _T0 + 50)))],
                ],
                clock,
            ),
            backfill=_failing_backfill,
            max_failures=2,
            sleep=_no_sleep,
            tracker=tracker,
        )
    )
    assert _starts(events) == [None, None]
    assert tracker.gaps == 1
    assert tracker.last_error is not None


async def _tail_backfill(start: int, end: int) -> trades_lib.TradeCoverage:
    """Serves a page-capped backfill that covers only the outage's tail.

    Args:
        start: Requested inclusive start, epoch milliseconds.
        end: Requested exclusive end, epoch milliseconds.

    Returns:
        Coverage without trades that starts 5 ms after ``start``.
    """
    return trades_lib.TradeCoverage((), start + 5, end)


async def test_incomplete_backfill_is_not_recovery() -> None:
    """A page-capped backfill that misses the resume point breaks coverage."""
    clock = clock_lib.ManualClock(_T0)
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [
                    [(1, _trades_frame((1, _T0 + 10)))],
                    [(1, _trades_frame((2, _T0 + 50)))],
                ],
                clock,
            ),
            backfill=_tail_backfill,
            max_failures=2,
            sleep=_no_sleep,
        )
    )
    assert _starts(events) == [None, None]


@pytest.mark.parametrize(
    ("replay", "expected_starts"),
    [
        (_trades_frame((1, _T0 + 10), (2, _T0 + 20), (3, _T0 + 30)), [None]),
        (_trades_frame((5, _T0 + 50), (6, _T0 + 60)), [None, None]),
    ],
)
async def test_replay_overlap_proves_continuity(
    replay: str, expected_starts: list[int | None]
) -> None:
    """A replay overlapping the last seen trade bridges a reconnect."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.HYPERLIQUID)
    events = await _events(
        ws.trade_stream_events(
            _spec(replays=True),
            clock=clock,
            connect=FakeConnector(
                [
                    [(1, _trades_frame((1, _T0 + 10), (2, _T0 + 20)))],
                    [(1, '{"ack":1}'), (1, replay)],
                ],
                clock,
            ),
            max_failures=2,
            sleep=_no_sleep,
            tracker=tracker,
        )
    )
    assert _starts(events) == expected_starts
    assert tracker.gaps == len(expected_starts) - 1
    assert "1" not in _ids(events)[2:]


async def test_unparseable_trade_frame_breaks_coverage() -> None:
    """A malformed trade frame means lost trades: gap and new segment."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.LIGHTER)
    events = await _events(
        ws.trade_stream_events(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [
                    [
                        (1, _trades_frame((1, _T0 + 10))),
                        (1, '{"broken": true}'),
                        (1, _trades_frame((2, _T0 + 20))),
                    ]
                ],
                clock,
            ),
            max_failures=1,
            sleep=_no_sleep,
            tracker=tracker,
        )
    )
    assert _starts(events) == [None, None]
    assert _ids(events) == ["1", "2"]
    assert tracker.gaps == 1


async def test_trade_stream_gives_up_after_max_failures() -> None:
    """Consecutive connect failures raise VenueUnavailableError."""
    clock = clock_lib.ManualClock(_T0)
    stream = ws.stream_trades_with_recovery(
        _spec(),
        clock=clock,
        connect=FakeConnector([None, None], clock),
        max_failures=2,
        sleep=_no_sleep,
    )
    with pytest.raises(errors.VenueUnavailableError):
        async for _ in stream:
            pass


async def test_stream_trades_yields_trades_only() -> None:
    """The plain trade stream yields de-duplicated trades only."""
    clock = clock_lib.ManualClock(_T0)
    out: list[str] = []
    with contextlib.suppress(errors.VenueUnavailableError):
        async for trade in ws.stream_trades_with_recovery(
            _spec(),
            clock=clock,
            connect=FakeConnector(
                [[(1, _trades_frame((1, _T0), (1, _T0), (2, _T0 + 1)))]], clock
            ),
            max_failures=1,
            sleep=_no_sleep,
        ):
            out.append(trade.trade_id)
    assert out == ["1", "2"]


async def _primed_backfill(start: int, end: int) -> trades_lib.TradeCoverage:
    """Serves the history that primes the stream.

    Args:
        start: Requested inclusive start, epoch milliseconds.
        end: Requested exclusive end, epoch milliseconds.

    Returns:
        Complete coverage of the span with one trade at ``_T0 + 1_100``, in
        the bar that is live when the stream starts.
    """
    return trades_lib.TradeCoverage(
        (_trade(1, _T0 + 1_100, "100"),), start, end
    )


async def test_trade_candles_finalize_on_heartbeat_after_grace() -> None:
    """A quiet bar turns final once a frame proves delivery past the grace."""
    clock = clock_lib.ManualClock(_T0 + 1_500)
    connector = FakeConnector(
        [
            [
                (0, '{"ack":1}'),
                (1_000, '{"pong":1}'),
                (1_100, '{"pong":1}'),
                (500, _trades_frame((2, _T0 + 4_200))),
            ]
        ],
        clock,
    )
    builder = trade_bars.TradeBarBuilder(
        _ID, timeframes.Timeframe.S1, source="binance.trades"
    )
    rows: list[market.Candle] = []
    with contextlib.suppress(errors.VenueUnavailableError):
        async for candle in ws.stream_trade_candles(
            _spec(),
            builder,
            clock=clock,
            connect=connector,
            backfill=_primed_backfill,
            grace_ms=1_500,
            max_failures=1,
            sleep=_no_sleep,
        ):
            rows.append(candle)
    first = [c for c in rows if c.start_ms == _T0 + 1_000]
    assert [c.is_final for c in first] == [False, True]
    flats = [c for c in rows if c.start_ms in (_T0 + 2_000, _T0 + 3_000)]
    assert [c.base_volume for c in flats] == [0, 0]
    assert rows[-1].start_ms == _T0 + 4_000 and not rows[-1].is_final


async def test_trade_candles_record_gap_for_discarded_open_bar() -> None:
    """An unrecoverable outage drops the open bar and records a gap."""
    clock = clock_lib.ManualClock(_T0)
    tracker = health.FeedHealthTracker(instruments.Venue.MEXC)
    builder = trade_bars.TradeBarBuilder(
        _ID, timeframes.Timeframe.S1, source="mexc.trades"
    )
    rows: list[market.Candle] = []
    with contextlib.suppress(errors.VenueUnavailableError):
        async for candle in ws.stream_trade_candles(
            _spec(),
            builder,
            clock=clock,
            connect=FakeConnector(
                [
                    [
                        (1, _trades_frame((1, _T0 + 100))),
                        (1, _trades_frame((2, _T0 + 1_100))),
                    ],
                    [(1, _trades_frame((3, _T0 + 9_100)))],
                ],
                clock,
            ),
            max_failures=2,
            sleep=_no_sleep,
            tracker=tracker,
        ):
            rows.append(candle)
    assert {c.start_ms for c in rows} == {_T0 + 1_000}
    assert all(not c.is_final for c in rows)
    assert tracker.gaps == 2
