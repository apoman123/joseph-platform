# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the engine-derived bars wrapper (aggregation, delta)."""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from typing import TypeAlias

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import ws
from joseph.market_data.app import derived_bars
from joseph.market_data.app import trade_archive
from joseph.market_data.app import trade_recorder
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import trades as trades_lib

_TF = timeframes.Timeframe
_D = decimal.Decimal
_HL = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_DAY = 86_400_000
_NOV = 1_793_491_200_000
"""2026-11-01 00:00 UTC."""
_DEC = _NOV + 30 * _DAY
_HL_TF = tuple(
    tf
    for tf in (*timeframes.SECONDS, *timeframes.MINUTE_AND_ABOVE)
    if tf not in (_TF.H6, _TF.W1, _TF.MN1)
)

Script: TypeAlias = list[tuple[int, market.Candle]]
"""Stream script: ``(clock time, update)`` pairs, delivered in order."""


def _bar(
    start: int,
    tf: timeframes.Timeframe = _TF.D1,
    *,
    final: bool = True,
    price: str = "10",
    volume: str = "2",
    received: int = 0,
) -> market.Candle:
    """Builds a venue bar without taker volume.

    Args:
        start: Bar start, epoch milliseconds.
        tf: Timeframe.
        final: Whether the bar is final; final bars come from REST, others
            from the WebSocket.
        price: Close price.
        volume: Base volume.
        received: Receipt time, epoch milliseconds.

    Returns:
        The candle: open 10, low 9, high one above the larger of open and
        close, 4 trades.
    """
    return market.Candle(
        instrument_id=_HL,
        timeframe=tf,
        start_ms=start,
        end_ms=tf.end_of(start),
        open=_D(10),
        high=max(_D(10), _D(price)) + 1,
        low=_D(9),
        close=_D(price),
        base_volume=_D(volume),
        is_final=final,
        source="hyperliquid.ws" if not final else "hyperliquid.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=received,
        trade_count=4,
    )


class FakeInner:
    """Venue adapter double (Hyperliquid-like timeframes).

    Attributes:
        clock: Clock advanced by the stream script.
        rows: Bars served per timeframe.
        script: ``(clock time, update)`` pairs streamed in order.
        native_ws: Whether native candle streams exist.
        tfs: Native timeframes.
        venue: Venue the double serves.
        taker: Whether klines carry taker volume.
        fetches: ``(timeframe, start, end)`` of every candle fetch.
        calls: Names of the delegated calls received, in order.
    """

    def __init__(
        self,
        clock: clock_lib.ManualClock,
        *,
        rows: dict[timeframes.Timeframe, list[market.Candle]] | None = None,
        script: Script | None = None,
        native_ws: bool = True,
        timeframes_: tuple[timeframes.Timeframe, ...] = _HL_TF,
        venue: instruments.Venue = instruments.Venue.HYPERLIQUID,
        taker: bool = False,
    ) -> None:
        """Initializes the double.

        Args:
            clock: Clock advanced by the stream script.
            rows: Bars served per timeframe.
            script: ``(clock time, update)`` pairs streamed in order.
            native_ws: Whether native candle streams exist.
            timeframes_: Native timeframes.
            venue: Venue.
            taker: Whether klines carry taker volume.
        """
        self.clock = clock
        self.rows = rows or {}
        self.script = script or []
        self.native_ws = native_ws
        self.tfs = timeframes_
        self.venue = venue
        self.taker = taker
        self.fetches: list[tuple[timeframes.Timeframe, int, int]] = []
        self.calls: list[str] = []

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns capabilities of the double.

        Returns:
            REST history and native trades; candles and taker volume as
            configured.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="fake",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=self.native_ws,
                native_ws_trades=True,
                taker_volume=self.taker,
            ),
            timeframes=self.tfs,
            trade_built_timeframes=timeframes.SECONDS,
            notes=("venue note",),
            docs_url="x",
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Serves stored bars of a range and records the fetch.

        Args:
            instrument: Ignored.
            timeframe: Resolution.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            limit: Keeps only the newest ``limit`` bars; None keeps all.

        Returns:
            The stored bars starting in ``[start_ms, end_ms)``.
        """
        del instrument
        self.fetches.append((timeframe, start_ms, end_ms))
        rows = [
            c
            for c in self.rows.get(timeframe, [])
            if start_ms <= c.start_ms < end_ms
        ]
        return rows[-limit:] if limit else rows

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams the script.

        Args:
            instrument: Ignored.
            timeframe: Ignored.
            tracker: Ignored.

        Returns:
            The scripted updates.

        Raises:
            errors.UnsupportedError: Without native streams.
        """
        if not self.native_ws:
            raise errors.UnsupportedError("no native candle stream")
        return self._stream()

    async def _stream(self) -> AsyncGenerator[market.Candle, None]:
        """Yields scripted updates, advancing the clock.

        Yields:
            Each scripted update, once the clock is at its time.
        """
        for at, update in self.script:
            self.clock.advance(at - self.clock.now_ms())
            yield update

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Records the call.

        Returns:
            Empty list.
        """
        self.calls.append("list_instruments")
        return []

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Records the call.

        Args:
            instrument: Instrument.

        Returns:
            A placeholder live snapshot.
        """
        self.calls.append("fetch_snapshot")
        return market.MarketSnapshot(instrument, quality.FeedState.LIVE, "x", 0)

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Records the call.

        Returns:
            Empty list.
        """
        self.calls.append("fetch_snapshots")
        return []

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Records the call.

        Args:
            instrument: Instrument.
            depth: Depth.

        Returns:
            Empty book.
        """
        self.calls.append(f"fetch_order_book:{depth}")
        return market.OrderBook(instrument, (), (), 0, 0, "x")

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Records the call.

        Args:
            instrument: Instrument.

        Returns:
            A placeholder open interest of 1.
        """
        self.calls.append("fetch_open_interest")
        return market.OpenInterest(instrument, _D(1), _D(1), "x", 0)

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Records the call.

        Args:
            instrument: Instrument.

        Returns:
            A placeholder predicted funding rate of 0.
        """
        self.calls.append("fetch_funding")
        return market.Funding(
            instrument, _D(0), 1.0, market.FundingKind.PREDICTED, 0, "x", 0
        )

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Records the call.

        Args:
            instrument: Instrument.
            tracker: Health tracker.

        Returns:
            Empty stream.
        """
        self.calls.append("stream_trades")
        return self._no_trades()

    async def _no_trades(self) -> AsyncGenerator[market.Trade, None]:
        """Ends at once without a trade.

        Yields:
            Nothing; the stream is empty.
        """
        empty: tuple[market.Trade, ...] = ()
        for trade in empty:
            yield trade

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Records the call.

        Args:
            instrument: Ignored.
            start_ms: Ignored.
            end_ms: Ignored.
            limit: Ignored.

        Returns:
            Empty list.
        """
        self.calls.append("fetch_trades")
        return []

    async def close(self) -> None:
        """Records the call."""
        self.calls.append("close")

    def stream_trade_events(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
        *,
        prime_from_ms: int | None = None,
    ) -> AsyncIterator[ws.TradeStreamEvent]:
        """Streams no trade events (recorder capability).

        Args:
            instrument: Ignored.
            tracker: Ignored.
            prime_from_ms: Ignored.

        Returns:
            Idle stream.
        """
        return self._idle()

    async def _idle(self) -> AsyncGenerator[ws.TradeStreamEvent, None]:
        """Idles for an hour, longer than any test runs.

        Yields:
            A frame event, only after the hour.
        """
        await asyncio.sleep(3_600)
        yield ws.FrameReceived(0)

    async def fetch_trade_coverage(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int | None = None,
    ) -> trades_lib.TradeCoverage:
        """Returns empty coverage.

        Args:
            instrument: Ignored.
            start_ms: Ignored.
            end_ms: End of the request, epoch milliseconds.
            max_pages: Ignored.

        Returns:
            Empty coverage of the empty span at ``end_ms``.
        """
        return trades_lib.TradeCoverage((), end_ms, end_ms)


def _days(
    first: int, count: int, *, live_last: bool = False
) -> list[market.Candle]:
    """Builds consecutive daily bars.

    Args:
        first: First day start, epoch milliseconds.
        count: Number of days.
        live_last: Whether the last day is in progress.

    Returns:
        The daily bars, all final unless ``live_last``.
    """
    return [
        _bar(first + i * _DAY, final=not (live_last and i == count - 1))
        for i in range(count)
    ]


def _recorder(
    inner: FakeInner, clock: clock_lib.Clock
) -> trade_recorder.TradeRecorder:
    """Builds a recorder over the inner double.

    Args:
        inner: Inner adapter.
        clock: Clock.

    Returns:
        Recorder (not started).
    """
    return trade_recorder.TradeRecorder(
        {inner.venue: inner},
        trade_archive.MemoryTradeArchive(),
        clock,
    )


def test_capabilities_declare_aggregated_timeframes_and_delta() -> None:
    """Missing timeframes are declared aggregated; delta needs recording."""
    clock = clock_lib.ManualClock(_NOV)
    inner = FakeInner(clock)
    plain = derived_bars.DerivedBarsAdapter(inner, clock)
    caps = plain.capabilities()
    assert caps.aggregated_timeframes == (_TF.H6, _TF.W1, _TF.MN1)
    assert set(caps.timeframes) == set(timeframes.Timeframe)
    assert not caps.market_data.trade_delta
    assert any("1M from 1d" in note for note in caps.notes)
    assert plain.venue is instruments.Venue.HYPERLIQUID
    recorded = derived_bars.DerivedBarsAdapter(
        inner, clock, recorder=_recorder(inner, clock)
    ).capabilities()
    assert recorded.market_data.trade_delta
    assert any("true delta" in note for note in recorded.notes)
    assert any("recorded" in note for note in recorded.notes)
    full = FakeInner(
        clock,
        timeframes_=tuple(timeframes.Timeframe),
        venue=instruments.Venue.BINANCE,
        taker=True,
    )
    native = full.capabilities()
    assert derived_bars.DerivedBarsAdapter(full, clock).capabilities() == native


async def test_fetch_aggregates_monthly_bars_from_days() -> None:
    """A 1M request folds the venue's 1d bars of each month."""
    now = _NOV + 4 * _DAY + 3_600_000
    clock = clock_lib.ManualClock(now)
    october = _NOV - 31 * _DAY
    inner = FakeInner(
        clock,
        rows={_TF.D1: [*_days(october, 31), *_days(_NOV, 5, live_last=True)]},
    )
    adapter = derived_bars.DerivedBarsAdapter(inner, clock)
    months = await adapter.fetch_candles(_HL, _TF.MN1, october, now + 1)
    assert [c.start_ms for c in months] == [october, _NOV]
    assert months[0].is_final and not months[1].is_final
    assert months[0].base_volume == _D(62)
    assert months[0].source == "hyperliquid.rest.agg1d"
    assert inner.fetches[-1] == (_TF.D1, october, _DEC)
    newest = await adapter.fetch_candles(_HL, _TF.MN1, 0, now + 1, limit=1)
    assert [c.start_ms for c in newest] == [_NOV]
    assert inner.fetches[-1][1] == _NOV
    hours = await adapter.fetch_candles(_HL, _TF.H1, 0, now)
    assert hours == []
    assert inner.fetches[-1] == (_TF.H1, 0, now)


def test_clip_with_limit_zero_keeps_no_bars() -> None:
    """``limit=0`` keeps nothing; a positive limit keeps the newest bars."""
    rows = _days(_NOV, 3)
    assert derived_bars._clip(rows, _NOV, _DEC, 0) == []
    assert derived_bars._clip(rows, _NOV, _DEC, 1) == rows[-1:]
    assert derived_bars._clip(rows, _NOV, _DEC, None) == rows


async def test_stream_builds_the_live_month_and_rolls_over() -> None:
    """Seeded closed days plus live day updates; the month then closes."""
    start = _NOV + 29 * _DAY + 3_600_000
    clock = clock_lib.ManualClock(start)
    last = _NOV + 29 * _DAY
    script: Script = [
        (start + 1, _bar(last, final=False, price="12", received=start + 1)),
        (_DEC + 1, _bar(last, price="12", received=_DEC + 1)),
        (_DEC + 2, _bar(_DEC, final=False, received=_DEC + 2)),
        (_DEC + 2 * _DAY + 5, _bar(_DEC + 2 * _DAY, final=False, received=9)),
    ]
    inner = FakeInner(
        clock,
        rows={_TF.D1: [*_days(_NOV, 30, live_last=True), *_days(_DEC, 2)]},
        script=script,
    )
    adapter = derived_bars.DerivedBarsAdapter(inner, clock)
    updates = [u async for u in adapter.stream_candles(_HL, _TF.MN1)]
    assert [(u.start_ms, u.is_final, u.close) for u in updates] == [
        (_NOV, False, _D(10)),
        (_NOV, False, _D(12)),
        (_NOV, True, _D(12)),
        (_DEC, False, _D(10)),
        (_DEC, False, _D(10)),
        (_DEC, False, _D(10)),
    ]
    assert [u.base_volume for u in updates[3:]] == [_D(2), _D(4), _D(6)]
    assert inner.fetches[-1] == (_TF.D1, _DEC + _DAY, _DEC + 2 * _DAY)


async def _fail_backfills(
    instrument: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    end_ms: int,
    limit: int | None = None,
) -> list[market.Candle]:
    """Serves the December seed request empty and fails every other fetch.

    Args:
        instrument: Ignored.
        timeframe: Ignored.
        start_ms: Inclusive start, epoch milliseconds.
        end_ms: Exclusive end, epoch milliseconds.
        limit: Ignored.

    Returns:
        No bars, for the seed request of December's days.

    Raises:
        errors.VenueUnavailableError: For any other request (the backfills).
    """
    if start_ms == _DEC and end_ms == _DEC + 31 * _DAY:
        return []
    raise errors.VenueUnavailableError("down")


async def test_stream_tolerates_a_failed_child_backfill() -> None:
    """A child backfill error is recorded and the stream continues."""
    clock = clock_lib.ManualClock(_DEC + 3 * _DAY)
    inner = FakeInner(
        clock,
        script=[(_DEC + 3 * _DAY + 1, _bar(_DEC + 3 * _DAY, final=False))],
    )
    inner.fetch_candles = _fail_backfills  # type: ignore[method-assign]  # swaps in a fetch that fails every child backfill
    tracker = health.FeedHealthTracker(instruments.Venue.HYPERLIQUID)
    adapter = derived_bars.DerivedBarsAdapter(inner, clock)
    updates = [u async for u in adapter.stream_candles(_HL, _TF.MN1, tracker)]
    assert updates == []
    assert "child backfill failed" in (tracker.snapshot(0).last_error or "")


def test_streams_need_native_child_candles() -> None:
    """Without native candle streams aggregated streaming is refused."""
    clock = clock_lib.ManualClock(_NOV)
    adapter = derived_bars.DerivedBarsAdapter(
        FakeInner(clock, native_ws=False), clock
    )
    with pytest.raises(errors.UnsupportedError, match="aggregate"):
        adapter.stream_candles(_HL, _TF.MN1)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(_HL, _TF.M1)


def _covered_minute(
    recorder: trade_recorder.TradeRecorder, start: int, volume: str
) -> None:
    """Archives a fully covered minute with half its volume bought.

    Args:
        recorder: Recorder.
        start: Minute start.
        volume: Recorded volume.
    """
    second = market.Candle(
        instrument_id=_HL,
        timeframe=_TF.S1,
        start_ms=start + 7_000,
        end_ms=start + 8_000,
        open=_D(10),
        high=_D(10),
        low=_D(10),
        close=_D(10),
        base_volume=_D(volume),
        is_final=True,
        source="hyperliquid.trades",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=0,
        taker_buy_base_volume=_D(volume) / 2,
    )
    recorder.archive.put(_HL, [second], [(start, start + 60_000)])


async def test_fetch_attaches_recorded_delta_only_to_covered_bars() -> None:
    """Covered final minutes get true delta; others keep none."""
    clock = clock_lib.ManualClock(_NOV + 600_000)
    minutes = [_bar(_NOV + i * 60_000, _TF.M1, volume="4") for i in range(3)]
    inner = FakeInner(clock, rows={_TF.M1: minutes})
    recorder = _recorder(inner, clock)
    _covered_minute(recorder, _NOV, "4")
    _covered_minute(recorder, _NOV + 60_000, "3")
    adapter = derived_bars.DerivedBarsAdapter(inner, clock, recorder=recorder)
    rows = await adapter.fetch_candles(_HL, _TF.M1, _NOV, _NOV + 180_000)
    assert rows[0].taker_buy_base_volume == _D(2)
    assert rows[0].taker_buy_method is market.BuyVolumeMethod.TAKER
    assert rows[1].taker_buy_base_volume is None  # Volume mismatch.
    assert rows[2].taker_buy_base_volume is None  # Not covered.


async def test_stream_waits_for_the_recorder_at_a_bar_close() -> None:
    """A just-closed streamed bar waits for recording, then has delta."""
    close = _NOV + 60_000
    clock = clock_lib.ManualClock(close + 500)
    inner = FakeInner(
        clock,
        script=[
            (close + 600, _bar(_NOV, _TF.M1, final=False, volume="3")),
            (close + 700, _bar(_NOV, _TF.M1, volume="4")),
        ],
    )
    recorder = _recorder(inner, clock)
    waits: list[int] = []

    async def wait(
        instrument: instruments.InstrumentId, end_ms: int, timeout_s: float
    ) -> bool:
        """Records the wait and archives the minute.

        Args:
            instrument: Ignored.
            end_ms: Bar end, epoch milliseconds.
            timeout_s: Ignored.

        Returns:
            True: the minute is recorded.
        """
        waits.append(end_ms)
        _covered_minute(recorder, _NOV, "4")
        return True

    recorder.wait_recorded = wait  # type: ignore[method-assign]  # stub records the minute instead of waiting
    recorder.recording = lambda instrument: True  # type: ignore[method-assign]  # stub: the instrument is being recorded
    adapter = derived_bars.DerivedBarsAdapter(inner, clock, recorder=recorder)
    updates = [u async for u in adapter.stream_candles(_HL, _TF.M1)]
    assert [u.taker_buy_base_volume for u in updates] == [None, _D(2)]
    assert waits == [close]
    seconds = adapter.stream_candles(_HL, _TF.S1)
    assert isinstance(seconds, AsyncIterator)
    await seconds.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()


async def test_other_calls_are_delegated() -> None:
    """Every non-candle call goes to the venue adapter unchanged."""
    clock = clock_lib.ManualClock(_NOV)
    inner = FakeInner(clock)
    adapter = derived_bars.DerivedBarsAdapter(inner, clock)
    await adapter.list_instruments()
    await adapter.fetch_snapshot(_HL)
    await adapter.fetch_snapshots()
    await adapter.fetch_order_book(_HL, 5)
    await adapter.fetch_open_interest(_HL)
    await adapter.fetch_funding(_HL)
    assert [t async for t in adapter.stream_trades(_HL)] == []
    await adapter.fetch_trades(_HL, 0, 1)
    await adapter.close()
    assert inner.calls == [
        "list_instruments",
        "fetch_snapshot",
        "fetch_snapshots",
        "fetch_order_book:5",
        "fetch_open_interest",
        "fetch_funding",
        "stream_trades",
        "fetch_trades",
        "close",
    ]


def test_wrap_leaves_the_demo_venue_alone() -> None:
    """Real venues are wrapped; DEMO keeps its own adapter."""
    clock = clock_lib.ManualClock(_NOV)
    hl = FakeInner(clock)
    mock = FakeInner(clock, venue=instruments.Venue.MOCK)
    wrapped = derived_bars.wrap(
        {hl.venue: hl, mock.venue: mock},
        clock,
    )
    assert isinstance(
        wrapped[instruments.Venue.HYPERLIQUID], derived_bars.DerivedBarsAdapter
    )
    assert wrapped[instruments.Venue.MOCK] is mock


def test_aggregation_needs_rest_history() -> None:
    """A venue without REST history derives nothing."""
    clock = clock_lib.ManualClock(_NOV)
    inner = FakeInner(clock)
    caps = inner.capabilities()
    no_rest = dataclasses.replace(
        caps,
        market_data=dataclasses.replace(caps.market_data, rest_history=False),
    )
    assert derived_bars.derived(no_rest) == {}
