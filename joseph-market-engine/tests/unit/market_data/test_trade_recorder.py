# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the public-trade recorder (live, backfill, retention)."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import pathlib
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import trade_archive_sql
from joseph.market_data.adapters import ws
from joseph.market_data.app import trade_archive
from joseph.market_data.app import trade_recorder
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trades as trades_lib
from joseph.platform import db

_TF = timeframes.Timeframe
_D = decimal.Decimal
_HL = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_BG = instruments.InstrumentId.parse("bitget:perp:BTCUSDT")
_BN = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_T0 = 1_795_349_040_000
"""A minute boundary (2026-11-22 08:04 UTC)."""
_DAY = 86_400_000


async def _fast_sleep(seconds: float) -> None:
    """Sleeps at most a millisecond so recorder loops turn quickly.

    Args:
        seconds: Requested delay; capped at 1 ms.
    """
    await asyncio.sleep(min(seconds, 0.001))


def _trade(
    instrument: instruments.InstrumentId,
    ts: int,
    qty: str = "1",
    side: market.AggressorSide = market.AggressorSide.BUY,
) -> market.Trade:
    """Builds a trade at price 100.

    Args:
        instrument: Instrument traded.
        ts: Venue event time in epoch milliseconds, also the receive time.
        qty: Base quantity as a decimal string.
        side: Aggressor side.

    Returns:
        The trade; its id is unique per instrument, time and quantity.
    """
    return market.Trade(
        instrument_id=instrument,
        trade_id=f"{instrument.venue_market_id}-{ts}-{qty}",
        venue_event_ts_ms=ts,
        received_at_ms=ts,
        price=_D(100),
        base_quantity=_D(qty),
        aggressor_side=side,
        is_aggregated=False,
    )


class FakePort:
    """Venue adapter double with a scripted trade stream and history.

    Attributes:
        venue: Venue the double serves.
        history: Whether the venue advertises a REST trade history.
        taker: Whether the venue's klines carry taker volume.
        native: Whether the venue has a native trade stream.
        streams: Event scripts left, one per stream connection; each
            connection takes the first.
        history_fn: ``(start, end) -> coverage`` of one REST call, or
            ``None`` when every call fails.
        primes: ``prime_from_ms`` of every stream connection, in order.
        calls: ``(start_ms, end_ms)`` of every REST call, in order.
    """

    def __init__(
        self,
        venue: instruments.Venue,
        *,
        history: bool = False,
        taker: bool = False,
        native: bool = True,
        streams: Sequence[Sequence[ws.TradeStreamEvent]] = (),
        history_fn: Callable[[int, int], trades_lib.TradeCoverage]
        | None = None,
    ) -> None:
        """Initializes the double.

        Args:
            venue: Venue the double serves.
            history: Whether the venue advertises a REST trade history.
            taker: Whether the venue's klines carry taker volume.
            native: Whether the venue has a native trade stream.
            streams: Event scripts, one per stream connection.
            history_fn: ``(start, end) -> coverage`` of one REST call, or
                ``None`` to fail every call.
        """
        self.venue = venue
        self.history = history
        self.taker = taker
        self.native = native
        self.streams = [list(s) for s in streams]
        self.history_fn = history_fn
        self.primes: list[int | None] = []
        self.calls: list[tuple[int, int]] = []

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns the capabilities of the double.

        Returns:
            PERP capabilities with S1 built from trades, and the trade
            stream, trade history and taker volume as configured.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="fake",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_trades=self.native,
                trades_history=self.history,
                taker_volume=self.taker,
            ),
            timeframes=(_TF.S1, _TF.M1),
            trade_built_timeframes=(_TF.S1,),
            notes=(),
            docs_url="x",
        )

    async def stream_trade_events(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
        *,
        prime_from_ms: int | None = None,
    ) -> AsyncGenerator[ws.TradeStreamEvent, None]:
        """Replays the next script, then fails or idles.

        Records ``prime_from_ms`` in ``primes``.

        Args:
            instrument: Instrument to stream (ignored).
            tracker: Health tracker (ignored).
            prime_from_ms: Where the recorder asks the stream to resume.

        Yields:
            The events of the next script, or none when no script is left.

        Raises:
            errors.VenueUnavailableError: The script ended and more scripts
                remain; after the last one the stream idles for an hour.
        """
        del instrument, tracker
        self.primes.append(prime_from_ms)
        script = self.streams.pop(0) if self.streams else []
        for event in script:
            yield event
        if self.streams:
            raise errors.VenueUnavailableError("stream dropped")
        await asyncio.sleep(3_600)

    async def fetch_trade_coverage(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int | None = None,
    ) -> trades_lib.TradeCoverage:
        """Records one REST call in ``calls`` and serves it from the script.

        Args:
            instrument: Instrument to fetch (ignored).
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            max_pages: Page budget (ignored).

        Returns:
            What ``history_fn`` returns for the span.

        Raises:
            errors.VenueUnavailableError: No ``history_fn`` is scripted.
        """
        del instrument, max_pages
        self.calls.append((start_ms, end_ms))
        if self.history_fn is None:
            raise errors.VenueUnavailableError("no history")
        return self.history_fn(start_ms, end_ms)


def _recorder(
    ports: dict[instruments.Venue, FakePort],
    clock: clock_lib.ManualClock,
    archive: trade_archive.TradeArchive | None = None,
    **settings: object,
) -> trade_recorder.TradeRecorder:
    """Builds a recorder over fake ports that flushes at once.

    Args:
        ports: Fake port per venue.
        clock: Time source.
        archive: Archive to record into; a new memory archive when ``None``.
        **settings: ``RecorderSettings`` overrides; by default flushes are
            immediate and supervision runs every millisecond.

    Returns:
        The recorder, not started.
    """
    values: dict[str, Any] = {"flush_interval_ms": 0, "supervise_s": 0.001}
    values.update(settings)
    return trade_recorder.TradeRecorder(
        ports,  # type: ignore[arg-type]  # FakePort stands in for MarketDataPort
        archive or trade_archive.MemoryTradeArchive(),
        clock,
        trade_recorder.RecorderSettings(**values),
        sleep=_fast_sleep,
    )


async def _settle(rounds: int = 30) -> None:
    """Lets the recorder's background tasks run.

    Args:
        rounds: Number of 1 ms sleeps.
    """
    for _ in range(rounds):
        await asyncio.sleep(0.001)


def _live_script(
    instrument: instruments.InstrumentId, start: int | None
) -> list[ws.TradeStreamEvent]:
    """Builds a stream script: coverage start, three trades, delivery proof.

    The trades fall in seconds 0 and 2 after ``_T0``; the last frame proves
    delivery up to ``_T0 + 5_600``.

    Args:
        instrument: Instrument traded.
        start: Proven coverage start, or ``None`` when the stream cannot
            prove one.

    Returns:
        The events in stream order.
    """
    trades = (
        _trade(instrument, _T0 + 200, "1"),
        _trade(instrument, _T0 + 800, "2", market.AggressorSide.SELL),
        _trade(instrument, _T0 + 2_100, "3"),
    )
    return [
        ws.CoverageStart(start),
        ws.TradesReceived(trades, _T0 + 2_200),
        ws.FrameReceived(_T0 + 5_600),
    ]


async def test_live_recording_archives_complete_seconds() -> None:
    """Live trades land in the archive as proven seconds with coverage."""
    clock = clock_lib.ManualClock(_T0 + 6_000)
    port = FakePort(
        instruments.Venue.HYPERLIQUID, streams=[_live_script(_HL, _T0)]
    )
    recorder = _recorder({port.venue: port}, clock)
    assert not recorder.touch(_HL)
    recorder.start()
    recorder.start()
    assert recorder.touch(_HL)
    assert recorder.recording(_HL)
    await _settle()
    archive = recorder.archive
    assert archive.coverage(_HL, 0, _T0 + 10_000) == [(_T0, _T0 + 4_000)]
    rows = archive.seconds(_HL, 0, _T0 + 10_000)
    assert [(r.start_ms - _T0, r.base_volume) for r in rows] == [
        (0, _D(3)),
        (2_000, _D(3)),
    ]
    assert rows[0].taker_buy_base_volume == _D(1)
    assert recorder.proven_until(_HL) == _T0 + 4_000
    assert port.primes == [None]
    assert recorder.health()[0].venue is instruments.Venue.HYPERLIQUID
    bars = await recorder.seconds_bars(_HL, _TF.S1, _T0, _T0 + 4_000)
    assert [b.base_volume for b in bars] == [_D(3), 0, _D(3), 0]
    await recorder.stop()
    assert not recorder.recording(_HL)


async def test_unprimed_stream_starts_coverage_after_the_first_trade() -> None:
    """Without a proven start the first trade's second is not covered."""
    clock = clock_lib.ManualClock(_T0 + 6_000)
    port = FakePort(
        instruments.Venue.HYPERLIQUID, streams=[_live_script(_HL, None)]
    )
    recorder = _recorder({port.venue: port}, clock)
    recorder.start()
    recorder.touch(_HL)
    await _settle()
    assert recorder.archive.coverage(_HL, 0, _T0 + 9_000) == [
        (_T0 + 1_000, _T0 + 4_000)
    ]
    assert [
        r.start_ms for r in recorder.archive.seconds(_HL, 0, _T0 + 9_000)
    ] == [_T0 + 2_000]
    await recorder.stop()


async def test_outage_breaks_coverage_and_the_stream_restarts() -> None:
    """A failed stream ends the segment; the next one starts a new one."""
    clock = clock_lib.ManualClock(_T0 + 30_000)
    later = [
        ws.CoverageStart(None),
        ws.TradesReceived(
            (_trade(_HL, _T0 + 20_500), _trade(_HL, _T0 + 21_400, "2")),
            _T0 + 21_500,
        ),
        ws.FrameReceived(_T0 + 25_000),
    ]
    port = FakePort(
        instruments.Venue.HYPERLIQUID,
        streams=[_live_script(_HL, _T0), later],
    )
    recorder = _recorder({port.venue: port}, clock, retry_s=0.001)
    recorder.start()
    recorder.touch(_HL)
    await _settle(80)
    assert recorder.archive.coverage(_HL, 0, _T0 + 60_000) == [
        (_T0, _T0 + 4_000),
        (_T0 + 21_000, _T0 + 23_000),
    ]
    assert recorder.health()[0].state is quality.HealthState.UNAVAILABLE
    assert len(port.primes) == 2
    await recorder.stop()


async def test_leases_capacity_and_recordable_venues() -> None:
    """Touches lease recording; capacity and venue support are enforced."""
    clock = clock_lib.ManualClock(_T0)
    hl = FakePort(instruments.Venue.HYPERLIQUID)
    bg = FakePort(instruments.Venue.BITGET)
    quiet = FakePort(instruments.Venue.LIGHTER, native=False)
    mock = FakePort(instruments.Venue.MOCK)
    recorder = _recorder(
        {p.venue: p for p in (hl, bg, quiet, mock)},
        clock,
        max_instruments=1,
        lease_ms=1_000,
    )
    assert recorder.delta_venues == frozenset(
        {instruments.Venue.HYPERLIQUID, instruments.Venue.BITGET}
    )
    assert not recorder.records_venue(instruments.Venue.LIGHTER)
    assert not recorder.records_venue(instruments.Venue.MOCK)
    recorder.start()
    assert recorder.touch(_HL)
    assert not recorder.touch(_BG)
    assert not recorder.touch(
        instruments.InstrumentId.parse("lighter:perp:BTC")
    )
    clock.advance(500)
    assert recorder.expire() == []
    clock.advance(1_000)
    assert recorder.expire() == [_HL]
    assert recorder.touch(_BG)
    await recorder.stop()


async def test_wait_recorded_flushes_once_the_bar_is_proven() -> None:
    """Waiting flushes and succeeds once delivery is proven past the bar."""
    clock = clock_lib.ManualClock(_T0 + 6_000)
    port = FakePort(
        instruments.Venue.HYPERLIQUID, streams=[_live_script(_HL, _T0)]
    )
    recorder = _recorder({port.venue: port}, clock, flush_interval_ms=10**9)
    assert not await recorder.wait_recorded(_HL, _T0, 0.01)
    recorder.start()
    recorder.touch(_HL)
    await _settle()
    assert recorder.archive.coverage(_HL, 0, _T0 + 9_000) == []
    assert await recorder.wait_recorded(_HL, _T0 + 3_000, 0.01)
    assert recorder.archive.coverage(_HL, 0, _T0 + 9_000) == [
        (_T0, _T0 + 4_000)
    ]
    assert not await recorder.wait_recorded(_HL, _T0 + 9_000, 0.01)
    assert not await recorder.wait_recorded(_HL, _T0 - 1, 0.01)
    await recorder.stop()


def _history(
    instrument: instruments.InstrumentId, window_ms: int
) -> Callable[[int, int], trades_lib.TradeCoverage]:
    """Builds a REST history that covers ``window_ms`` per call.

    Args:
        instrument: Instrument traded.
        window_ms: Span each call covers, ending at the requested end.

    Returns:
        A ``(start, end) -> coverage`` function for ``FakePort``.
    """

    def page(start: int, end: int) -> trades_lib.TradeCoverage:
        """Serves the newest ``window_ms`` of a request.

        Args:
            start: Requested inclusive start, epoch milliseconds.
            end: Requested exclusive end, epoch milliseconds.

        Returns:
            Coverage of the newest window, with a trade 300 ms into each
            second.
        """
        begin = max(start, end - window_ms) + 1
        trades = tuple(
            _trade(instrument, ts)
            for ts in range(begin + 300 - begin % 1_000, end, 1_000)
            if ts >= begin
        )
        return trades_lib.TradeCoverage(trades, begin, end)

    return page


async def test_backfill_walks_back_within_the_budget_and_primes() -> None:
    """The gap is filled newest first; the stream continues from there."""
    clock = clock_lib.ManualClock(_T0 + 60_000)
    port = FakePort(
        instruments.Venue.BITGET,
        history=True,
        history_fn=_history(_BG, 10_000),
        streams=[[]],
    )
    archive = trade_archive.MemoryTradeArchive()
    archive.put(_BG, [], [(_T0, _T0 + 1_000)])
    recorder = _recorder(
        {port.venue: port}, clock, archive, backfill_max_calls=3
    )
    prime = await recorder.backfill(_BG)
    end = _T0 + 59_000
    assert prime == end
    assert len(port.calls) == 3
    assert port.calls[0] == (_T0 + 1_000, end)
    coverage = archive.coverage(_BG, 0, end)
    assert coverage[-1][1] == end
    assert coverage[-1][0] == end - 27_000
    assert recorded_trades.uncovered(coverage, _T0, end) == [
        (_T0 + 1_000, end - 27_000)
    ]
    rows = archive.seconds(_BG, end - 27_000, end)
    assert len(rows) == 27
    assert all(r.source == "bitget.trades.rest" for r in rows)
    recorder.start()
    recorder.touch(_BG)
    await _settle()
    assert port.primes[-1] is not None
    await recorder.stop()


def _stuck_history(start: int, end: int) -> trades_lib.TradeCoverage:
    """Serves a REST call that makes no progress.

    Args:
        start: Requested inclusive start (ignored).
        end: Requested exclusive end, epoch milliseconds.

    Returns:
        Empty coverage at ``end``.
    """
    del start
    return trades_lib.TradeCoverage((), end, end)


async def test_backfill_stops_on_no_progress_and_failures() -> None:
    """A call that covers nothing ends the walk; errors are tolerated."""
    clock = clock_lib.ManualClock(_T0 + 10_000)
    port = FakePort(
        instruments.Venue.BITGET, history=True, history_fn=_stuck_history
    )
    recorder = _recorder({port.venue: port}, clock)
    assert await recorder.backfill(_BG) is None
    assert len(port.calls) == 1
    failing = FakePort(instruments.Venue.LIGHTER, history=True)
    other = _recorder({failing.venue: failing}, clock)
    lighter = instruments.InstrumentId.parse("lighter:perp:BTC")
    assert await other._safe_backfill(lighter) is None
    no_history = FakePort(instruments.Venue.MEXC)
    third = _recorder({no_history.venue: no_history}, clock)
    assert (
        await third.backfill(
            instruments.InstrumentId.parse("mexc:perp:BTC_USDT")
        )
        is None
    )


def _utc_day_of(ts_ms: int) -> datetime.date:
    """Returns the UTC date of a time.

    Args:
        ts_ms: Epoch milliseconds.

    Returns:
        The UTC calendar date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).date()


def _utc_day_bounds(day: datetime.date) -> recorded_trades.Interval:
    """Returns the UTC bounds of a date.

    Args:
        day: Calendar date.

    Returns:
        ``(start, end)`` in epoch milliseconds, end exclusive.
    """
    moment = datetime.datetime(
        day.year, day.month, day.day, tzinfo=datetime.UTC
    )
    start = int(moment.timestamp()) * 1000
    return start, start + _DAY


class FakeDays:
    """Day-archive double on UTC days with a scripted outcome per day.

    Attributes:
        venue: Venue whose archive the double fakes.
        note: History note the recorder shows for the venue.
        outcomes: Scripted outcome per day: ``True`` published, ``False``
            unpublished, or an error to raise.
        requests: Every day asked for, in order.
        pruned: Every ``before_ms`` passed to ``prune``, in order.
        closed: Whether ``close`` ran.
    """

    venue = instruments.Venue.BINANCE
    note = "fake day archive"

    def __init__(self, outcomes: dict[datetime.date, bool | Exception]) -> None:
        """Initializes the double.

        Args:
            outcomes: Scripted outcome per day: ``True`` published,
                ``False`` unpublished (also for days not listed), or an
                error to raise.
        """
        self.outcomes = outcomes
        self.requests: list[datetime.date] = []
        self.pruned: list[int] = []
        self.closed = False

    def day_of(self, ts_ms: int) -> datetime.date:
        """Returns the UTC date of a time.

        Args:
            ts_ms: Epoch milliseconds.

        Returns:
            The UTC calendar date.
        """
        return _utc_day_of(ts_ms)

    def day_bounds(self, day: datetime.date) -> recorded_trades.Interval:
        """Returns the UTC bounds of a date.

        Args:
            day: Calendar date.

        Returns:
            ``(start, end)`` in epoch milliseconds, end exclusive.
        """
        return _utc_day_bounds(day)

    async def import_day(
        self,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: trade_recorder.Sink,
        received_ms: int,
    ) -> bool:
        """Records the request and imports the scripted day.

        A published day writes no aggregates and covers the whole day.

        Args:
            instrument: Instrument to import (ignored).
            day: UTC date to import.
            sink: Archive writer that takes ``(aggregates, coverage)``.
            received_ms: Receipt time to stamp (ignored).

        Returns:
            Whether the day was published.

        Raises:
            Exception: The error scripted for ``day``.
        """
        del instrument, received_ms
        self.requests.append(day)
        outcome = self.outcomes.get(day, False)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome:
            start = _utc_day_bounds(day)[0]
            sink([], [(start, start + _DAY)])
        return outcome

    def prune(self, before_ms: int) -> int:
        """Records a prune in ``pruned``.

        Args:
            before_ms: Cut-off in epoch milliseconds.

        Returns:
            Zero: the double holds nothing to delete.
        """
        self.pruned.append(before_ms)
        return 0

    async def close(self) -> None:
        """Records the close in ``closed``."""
        self.closed = True


async def test_day_archive_backfill_and_on_demand_imports() -> None:
    """Newest published days first; unpublished yesterday is skipped."""
    now = _T0
    today = _utc_day_of(now)
    one = datetime.timedelta(days=1)
    days = FakeDays(
        {
            today - one: False,
            today - 2 * one: True,
            today - 3 * one: errors.VenueUnavailableError("checksum"),
            today - 4 * one: True,
        }
    )
    clock = clock_lib.ManualClock(now)
    port = FakePort(instruments.Venue.BINANCE, taker=True)
    recorder = trade_recorder.TradeRecorder(
        {port.venue: port},  # type: ignore[dict-item]  # FakePort stands in for MarketDataPort
        trade_archive.MemoryTradeArchive(),
        clock,
        trade_recorder.RecorderSettings(
            archive_backfill_days=1, archive_request_days=2
        ),
        day_archives=[days],
        sleep=_fast_sleep,
    )
    assert recorder.delta_venues == frozenset()
    assert await recorder.seconds_bars(_BN, _TF.S1, now, now) == []
    assert days.requests == []
    assert await recorder.backfill(_BN) is None
    assert days.requests == [today - one, today - 2 * one]
    start = _utc_day_bounds(today - 4 * one)[0]
    bars = await recorder.seconds_bars(
        _BN, _TF.S1, start, _utc_day_bounds(today - one)[0]
    )
    assert bars == []
    assert days.requests[2:] == [today - 3 * one, today - 4 * one]
    covered = recorder.archive.coverage(_BN, 0, now)
    assert covered == [
        (start, start + _DAY),
        (start + 2 * _DAY, start + 3 * _DAY),
    ]
    # The failed day is not retried right away; unpublished older days stop.
    await recorder.seconds_bars(_BN, _TF.S1, start - 5 * _DAY, start)
    assert today - 3 * one not in days.requests[4:]
    assert days.requests[-1] == today - 5 * one
    notes = recorder.history_notes(instruments.Venue.BINANCE)
    assert "fake day archive" in notes
    assert recorder.history_notes(instruments.Venue.MEXC) == ()
    recorder.prune_now()
    assert days.pruned
    await recorder.stop()
    assert days.closed


class SlowDays(FakeDays):
    """Day archive double whose imports yield to the event loop first."""

    async def import_day(
        self,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: trade_recorder.Sink,
        received_ms: int,
    ) -> bool:
        """Yields to the event loop five times, then imports the scripted day.

        Args:
            instrument: Instrument to import (ignored).
            day: UTC date to import.
            sink: Archive writer that takes ``(aggregates, coverage)``.
            received_ms: Receipt time to stamp (ignored).

        Returns:
            Whether the day was published.

        Raises:
            Exception: The error scripted for ``day``, propagated from
                ``FakeDays.import_day``.
        """
        for _ in range(5):
            await asyncio.sleep(0)
        return await super().import_day(instrument, day, sink, received_ms)


async def test_concurrent_requests_import_a_day_once() -> None:
    """A day being imported is awaited, then found covered, not refetched."""
    now = _T0
    yesterday = _utc_day_of(now) - datetime.timedelta(days=1)
    days = SlowDays({yesterday: True})
    port = FakePort(instruments.Venue.BINANCE, taker=True)
    recorder = trade_recorder.TradeRecorder(
        {port.venue: port},  # type: ignore[dict-item]  # FakePort stands in for MarketDataPort
        trade_archive.MemoryTradeArchive(),
        clock_lib.ManualClock(now),
        trade_recorder.RecorderSettings(archive_request_days=1),
        day_archives=[days],
        sleep=_fast_sleep,
    )
    start, end = _utc_day_bounds(yesterday)
    await asyncio.gather(
        *(recorder.seconds_bars(_BN, _TF.S1, start, end) for _ in range(3))
    )
    assert days.requests == [yesterday]
    assert recorder.archive.coverage(_BN, start, end) == [(start, end)]
    assert recorder._day_locks
    recorder.prune_now()
    assert recorder._day_locks == {}


async def test_startup_backfills_archived_instruments_and_always_on(
    tmp_path: pathlib.Path,
) -> None:
    """A restart sees earlier recordings, backfills them and keeps them."""
    url = f"sqlite:///{tmp_path / 'market.db'}"
    clock = clock_lib.ManualClock(_T0 + 6_000)
    hl = FakePort(
        instruments.Venue.HYPERLIQUID, streams=[_live_script(_HL, _T0)]
    )
    first = _recorder(
        {hl.venue: hl},
        clock,
        trade_archive_sql.SqlTradeArchive(db.create_engine(url)),
    )
    first.start()
    first.touch(_HL)
    await _settle()
    await first.stop()
    clock.advance(60_000)
    bg = FakePort(
        instruments.Venue.BITGET,
        history=True,
        history_fn=_history(_BG, 5_000),
        streams=[[]],
    )
    archive = trade_archive_sql.SqlTradeArchive(db.create_engine(url))
    archive.put(_BG, [], [(_T0, _T0 + 1_000)])
    second = _recorder(
        {hl.venue: hl, bg.venue: bg},
        clock,
        archive,
        always=(_HL,),
        backfill_max_calls=1,
    )
    bars = await second.seconds_bars(_HL, _TF.S1, _T0, _T0 + 4_000)
    assert [b.base_volume for b in bars] == [_D(3), 0, _D(3), 0]
    second.start()
    await _settle(60)
    assert second.recording(_HL)
    assert bg.calls and bg.calls[0][0] == _T0 + 1_000
    assert second.expire() == []
    await second.stop()


async def test_retention_prunes_the_archive() -> None:
    """``prune_now`` drops recorded data older than the retention."""
    clock = clock_lib.ManualClock(_T0 + 10 * _DAY)
    port = FakePort(instruments.Venue.HYPERLIQUID)
    archive = trade_archive.MemoryTradeArchive()
    row = market.Candle(
        instrument_id=_HL,
        timeframe=_TF.S1,
        start_ms=_T0,
        end_ms=_T0 + 1_000,
        open=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        is_final=True,
        source="x",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=0,
    )
    archive.put(_HL, [row], [(_T0, _T0 + 1_000)])
    recorder = _recorder(
        {port.venue: port}, clock, archive, retention_ms=5 * _DAY
    )
    assert recorder.prune_now() == 1
    assert archive.instruments() == []
    assert trade_recorder.recording_settings_note(recorder.settings).startswith(
        "Public trades are recorded"
    )
    assert recorder.enrich([row]) == [row]


@pytest.mark.parametrize("history", [True, False])
async def test_history_notes_describe_the_source(history: bool) -> None:
    """Notes say whether recording backfills over REST or is live only."""
    port = FakePort(instruments.Venue.LIGHTER, history=history)
    recorder = _recorder({port.venue: port}, clock_lib.ManualClock(_T0))
    (note,) = recorder.history_notes(instruments.Venue.LIGHTER)
    assert ("REST" in note) is history
