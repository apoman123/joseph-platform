# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fetch and live-session coverage: quiet intervals of sparse-bar venues."""

from __future__ import annotations

import asyncio
import datetime
import decimal
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Sequence

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import ports
from joseph.market_data.app import registry
from joseph.market_data.app import service as service_lib
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

_M1 = timeframes.Timeframe.M1
_MIN = 60_000
_D = decimal.Decimal
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _SESSION is not None
_OPEN = _SESSION.open_ms
_T0 = 1_795_277_340_000


def _bar(
    instrument: instruments.InstrumentId,
    start: int,
    *,
    final: bool = True,
    now: int = 0,
) -> market.Candle:
    """Builds a flat 1m bar.

    Args:
        instrument: Instrument.
        start: Bar start (aligned to the instrument's calendar).
        final: Whether the bar is final.
        now: Receipt time, epoch milliseconds.

    Returns:
        The candle, all prices and the volume 1.
    """
    return market.Candle(
        instrument_id=instrument,
        timeframe=_M1,
        start_ms=start,
        end_ms=start + _MIN,
        open=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        is_final=final,
        source="fake.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=now,
    )


class _Venue:
    """Minute bars of a venue: which minutes traded, how far it settled.

    Attributes:
        traded: Minute offsets (from ``origin``) that have a bar.
        origin: Time of offset 0.
        history_from: Oldest time the venue serves (older ranges return
            nothing and prove nothing).
        settle_ms: How long after its close a bar becomes final.
        clock: Clock.
        fetches: ``(start, end)`` of every fetch.
    """

    def __init__(
        self,
        clock: clock_lib.ManualClock,
        origin: int,
        traded: Callable[[int], bool],
        history_from: int | None = None,
        settle_ms: int = _MIN,
    ) -> None:
        """Initializes the venue.

        Args:
            clock: Clock.
            origin: Time of minute offset 0.
            traded: Whether a minute offset has trades.
            history_from: Oldest time served.
            settle_ms: How long after its close a bar becomes final.
        """
        self.clock = clock
        self.origin = origin
        self.traded = traded
        self.history_from = origin if history_from is None else history_from
        self.settle_ms = settle_ms
        self.fetches: list[tuple[int, int]] = []

    def serve(
        self, instrument: instruments.InstrumentId, start: int, end: int
    ) -> tuple[list[market.Candle], int, int]:
        """Returns the bars of a range and the span they prove.

        Records the fetch in ``fetches``.

        Args:
            instrument: Instrument.
            start: Inclusive start, epoch milliseconds.
            end: Exclusive end, epoch milliseconds.

        Returns:
            ``(bars, proven_from, proven_to)``; bars settle ``settle_ms``
            after they close.
        """
        self.fetches.append((start, end))
        now = self.clock.now_ms()
        low = max(start, self.history_from)
        settled = now - self.settle_ms
        rows = []
        minute = -(-(low - self.origin) // _MIN)
        while (begin := self.origin + minute * _MIN) < min(end, now):
            if self.traded(minute):
                rows.append(
                    _bar(
                        instrument,
                        begin,
                        final=begin + _MIN <= settled,
                        now=now,
                    )
                )
            minute += 1
        return rows, low, min(end, settled)


class SparseAdapter:
    """``SparseBarsPort`` double (a stock venue without bars when quiet).

    Attributes:
        SPARSE_BARS: Declares sparse bars.
        venue: Venue the double serves (Nasdaq).
        data: Venue whose bars are served.
        native: Whether a native candle stream is advertised.
        updates: Scripted stream updates.
        down: While True, fetches raise ``VenueUnavailableError``.
    """

    SPARSE_BARS = True

    def __init__(
        self,
        venue_data: _Venue,
        *,
        native: bool = False,
        updates: Sequence[candle_rules.CandleCoverage] = (),
    ) -> None:
        """Initializes the double.

        Args:
            venue_data: Venue whose bars are served.
            native: Whether native candles are advertised.
            updates: Stream updates, delivered once, then the stream idles.
        """
        self.venue = instruments.Venue.NASDAQ
        self.data = venue_data
        self.native = native
        self.updates = list(updates)
        self.down = False

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns fake capabilities.

        Returns:
            REST history of 1m bars; native candles only if advertised.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="sparse",
            market_types=(instruments.MarketType.STOCK,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True, native_ws_candles=self.native
            ),
            timeframes=(_M1,),
            notes=(),
            docs_url="x",
        )

    async def fetch_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> candle_rules.CandleCoverage:
        """Serves bars with their coverage.

        Args:
            instrument: Instrument.
            timeframe: Ignored; the venue serves 1m bars.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            limit: Ignored.

        Returns:
            The served bars and the span the venue proved, marked complete.

        Raises:
            errors.VenueUnavailableError: While the venue is down.
        """
        del timeframe, limit
        if self.down:
            raise errors.VenueUnavailableError("venue down")
        rows, low, high = self.data.serve(instrument, start_ms, end_ms)
        return candle_rules.CandleCoverage(
            tuple(rows), ((low, high),), complete=True
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Serves bars.

        Args:
            instrument: Instrument.
            timeframe: Ignored; the venue serves 1m bars.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            limit: Ignored.

        Returns:
            The served bars.
        """
        fetched = await self.fetch_candle_coverage(
            instrument, timeframe, start_ms, end_ms, limit
        )
        return list(fetched.candles)

    async def _script(
        self,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Delivers the scripted updates, then idles.

        Yields:
            Each scripted update once.
        """
        for update in self.updates:
            yield update
        await asyncio.sleep(3600)

    def stream_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[candle_rules.CandleCoverage]:
        """Streams the scripted updates.

        Args:
            instrument: Ignored.
            timeframe: Ignored.
            tracker: Ignored.

        Returns:
            The scripted updates; the stream then idles for an hour.
        """
        del instrument, timeframe, tracker
        return self._script()

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams the scripted candles.

        Args:
            instrument: Ignored.
            timeframe: Ignored.
            tracker: Ignored.

        Returns:
            The candles of the scripted updates.
        """
        return base.candles_of(
            self.stream_candle_coverage(instrument, timeframe, tracker)
        )


class DenseAdapter(SparseAdapter):
    """A crypto venue double: same bars, but dense (no coverage claims).

    Attributes:
        SPARSE_BARS: Declares dense bars.
        venue: Venue the double serves (Binance).
    """

    SPARSE_BARS = False

    def __init__(self, venue_data: _Venue, *, native: bool = False) -> None:
        """Initializes the double.

        Args:
            venue_data: Venue whose bars are served.
            native: Whether native candles are advertised.
        """
        super().__init__(venue_data, native=native)
        self.venue = instruments.Venue.BINANCE


async def _no_sleep(_: float) -> None:
    """Sleeps 5 ms whatever the delay, so retry loops yield to the test."""
    await asyncio.sleep(0.005)


def _service(
    adapter: SparseAdapter, clock: clock_lib.Clock
) -> tuple[service_lib.MarketDataService, candle_store.CandleStore]:
    """Builds the service around one adapter double.

    The router retries native streams only after an hour, so a session
    stays on what the double first offers.

    Args:
        adapter: Adapter double.
        clock: Clock shared by the router and the service.

    Returns:
        The service and its candle store.
    """
    adapters = {adapter.venue: adapter}
    store = candle_store.CandleStore()
    router = feed_router.FeedRouter(
        adapters,  # type: ignore[arg-type]  # implements only the MarketDataPort methods these tests reach
        store,
        clock,
        sleep=_no_sleep,
        native_retry_s=3600,
    )
    svc = service_lib.MarketDataService(
        adapters,  # type: ignore[arg-type]  # implements only the MarketDataPort methods these tests reach
        registry.InstrumentRegistry(adapters),  # type: ignore[arg-type]  # implements only the MarketDataPort methods these tests reach
        store,
        router,
        clock,
    )
    return svc, store


def _quiet_every_third(minute: int) -> bool:
    """Checks whether a minute traded: all do except every third one.

    Args:
        minute: Minute offset.

    Returns:
        Whether the minute traded.
    """
    return minute % 3 != 2


async def _wait_for(predicate: Callable[[], bool]) -> None:
    """Waits until a background task made ``predicate()`` true.

    Args:
        predicate: Condition a background task makes true.

    Raises:
        TimeoutError: The condition stayed false for 2 seconds.
    """

    async def loop() -> None:
        """Polls the condition."""
        while not predicate():  # noqa: ASYNC110 - polls state that background tasks change; there is no event to await
            await asyncio.sleep(0.005)

    await asyncio.wait_for(loop(), 2.0)


# Ports ------------------------------------------------------------------


def test_only_adapters_declaring_sparse_bars_are_sparse() -> None:
    """``SPARSE_BARS`` decides; wrappers and doubles without it are dense."""
    data = _Venue(clock_lib.ManualClock(_OPEN), _OPEN, _quiet_every_third)
    assert ports.sparse_bars(SparseAdapter(data))  # type: ignore[arg-type]  # implements only the MarketDataPort methods these tests reach
    assert not ports.sparse_bars(DenseAdapter(data))  # type: ignore[arg-type]  # implements only the MarketDataPort methods these tests reach
    assert not ports.sparse_bars(object())  # type: ignore[arg-type]  # an object without SPARSE_BARS must count as dense


# Store ------------------------------------------------------------------


def test_store_keeps_coverage_and_never_excuses_open_bars() -> None:
    """Covered quiet slots are not gaps; a non-final bar still is."""
    store = candle_store.CandleStore()
    store.upsert_many([_bar(_AAPL, _OPEN + m * _MIN) for m in (0, 1, 3)])
    end = _OPEN + 6 * _MIN
    assert store.final_gaps(_AAPL, _M1, _OPEN, end) == [
        (_OPEN + 2 * _MIN, _OPEN + 3 * _MIN),
        (_OPEN + 4 * _MIN, end),
    ]
    store.add_coverage(_AAPL, _M1, [])
    store.add_coverage(_AAPL, _M1, [(_OPEN, _OPEN + 3 * _MIN)])
    store.add_coverage(_AAPL, _M1, [(_OPEN + 3 * _MIN, end)])
    assert store.coverage(_AAPL, _M1, _OPEN, end) == [(_OPEN, end)]
    assert store.final_gaps(_AAPL, _M1, _OPEN, end) == []
    store.upsert(_bar(_AAPL, _OPEN + 4 * _MIN, final=False))
    assert store.final_gaps(_AAPL, _M1, _OPEN, end) == [
        (_OPEN + 4 * _MIN, _OPEN + 5 * _MIN)
    ]
    assert store.coverage(_BTC, _M1, _OPEN, end) == []


def test_store_eviction_drops_the_coverage_of_evicted_bars() -> None:
    """An evicted bar is missing again instead of passing for quiet."""
    store = candle_store.CandleStore(max_bars_per_series=3)
    end = _OPEN + 7 * _MIN
    store.add_coverage(_AAPL, _M1, [(_OPEN, end)])
    store.upsert_many([_bar(_AAPL, _OPEN + m * _MIN) for m in (0, 2, 4)])
    assert store.final_gaps(_AAPL, _M1, _OPEN, end) == []
    store.upsert(_bar(_AAPL, _OPEN + 6 * _MIN))  # Evicts minute 0.
    assert store.final_gaps(_AAPL, _M1, _OPEN, end) == [
        (_OPEN, _OPEN + 2 * _MIN)
    ]
    store.add_coverage(_AAPL, _M1, [(_OPEN - _MIN, end)])
    assert store.coverage(_AAPL, _M1, 0, end)[0][0] == _OPEN + 2 * _MIN


def test_store_bounds_the_number_of_coverage_spans() -> None:
    """The oldest disjoint spans are dropped beyond the bound."""
    store = candle_store.CandleStore()
    store.add_coverage(
        _AAPL,
        _M1,
        [
            (i * 10, i * 10 + 5)
            for i in range(candle_store.MAX_COVERAGE_SPANS + 5)
        ],
    )
    kept = store.coverage(_AAPL, _M1, 0, 2**62)
    assert len(kept) == candle_store.MAX_COVERAGE_SPANS
    assert kept[0] == (50, 55)


# Service ----------------------------------------------------------------


async def test_quiet_minute_in_a_fetched_window_is_neither_gap_nor_fetch() -> (
    None
):
    """Quiet minutes of a fetched window are not gaps and not re-fetched."""
    clock = clock_lib.ManualClock(_OPEN + 30 * _MIN)
    data = _Venue(clock, _OPEN, _quiet_every_third)
    svc, _ = _service(SparseAdapter(data), clock)
    end = _OPEN + 20 * _MIN
    batch = await svc.get_batch(_AAPL, _M1, from_ms=_OPEN, to_ms=end)
    starts = {c.start_ms for c in batch.candles}
    assert _OPEN + 2 * _MIN not in starts  # Minute 2 was quiet.
    assert len(batch.candles) == 14
    assert batch.gaps == () and batch.complete
    assert data.fetches == [(_OPEN, end)]
    for _ in range(5):
        again = await svc.get_batch(_AAPL, _M1, from_ms=_OPEN, to_ms=end)
        assert again.complete
        await svc.get_candles(_AAPL, _M1, _OPEN + 3 * _MIN, end)
    assert data.fetches == [(_OPEN, end)]


async def test_coverage_survives_calls_and_only_the_tail_is_fetched() -> None:
    """Later calls request only what no earlier call covered."""
    clock = clock_lib.ManualClock(_OPEN + 10 * _MIN + 30_000)
    data = _Venue(clock, _OPEN, _quiet_every_third)
    svc, store = _service(SparseAdapter(data), clock)
    await svc.get_candles(_AAPL, _M1, _OPEN, _OPEN + 10 * _MIN)
    assert data.fetches == [(_OPEN, _OPEN + 10 * _MIN)]
    for minute in range(11, 21):
        clock.advance(_OPEN + minute * _MIN + 30_000 - clock.now_ms())
        rows = await svc.get_candles(
            _AAPL, _M1, _OPEN, _OPEN + (minute - 1) * _MIN
        )
        assert rows[-1].end_ms <= _OPEN + (minute - 1) * _MIN
        # Never back to the first quiet minute (minute 2).
        assert data.fetches[-1][0] >= _OPEN + (minute - 3) * _MIN
    assert len(data.fetches) <= 11
    assert store.final_gaps(_AAPL, _M1, _OPEN, _OPEN + 19 * _MIN) == []
    batch = await svc.get_batch(
        _AAPL, _M1, from_ms=_OPEN, to_ms=_OPEN + 19 * _MIN
    )
    assert batch.complete


async def test_uncovered_outage_is_a_gap_and_gets_repaired() -> None:
    """Minutes no fetch or session covered are gaps until a fetch repairs."""
    clock = clock_lib.ManualClock(_OPEN + 30 * _MIN)
    data = _Venue(clock, _OPEN, _quiet_every_third)
    adapter = SparseAdapter(data)
    svc, store = _service(adapter, clock)
    # A live session saw minutes 0-5 and 12-19, then lost its connection.
    seen = [m for m in (*range(6), *range(12, 20)) if _quiet_every_third(m)]
    store.upsert_many([_bar(_AAPL, _OPEN + m * _MIN) for m in seen])
    store.add_coverage(
        _AAPL,
        _M1,
        [(_OPEN, _OPEN + 6 * _MIN), (_OPEN + 12 * _MIN, _OPEN + 20 * _MIN)],
    )
    outage = (_OPEN + 6 * _MIN, _OPEN + 12 * _MIN)
    assert store.final_gaps(_AAPL, _M1, _OPEN, _OPEN + 20 * _MIN) == [outage]
    adapter.down = True
    with pytest.raises(errors.VenueUnavailableError):
        await svc.get_batch(_AAPL, _M1, from_ms=_OPEN, to_ms=_OPEN + 20 * _MIN)
    adapter.down = False
    batch = await svc.get_batch(
        _AAPL, _M1, from_ms=_OPEN, to_ms=_OPEN + 20 * _MIN
    )
    assert data.fetches == [(outage[0], _OPEN + 20 * _MIN)]
    assert batch.complete
    repaired = {c.start_ms for c in batch.candles}
    assert {_OPEN + 6 * _MIN, _OPEN + 7 * _MIN, _OPEN + 9 * _MIN} <= repaired
    assert store.final_gaps(_AAPL, _M1, _OPEN, _OPEN + 20 * _MIN) == []


async def test_outage_the_venue_cannot_serve_stays_a_reported_gap() -> None:
    """Without history before ``history_from`` the hole stays a gap."""
    clock = clock_lib.ManualClock(_OPEN + 30 * _MIN)
    data = _Venue(
        clock, _OPEN, _quiet_every_third, history_from=_OPEN + 12 * _MIN
    )
    svc, _ = _service(SparseAdapter(data), clock)
    end = _OPEN + 20 * _MIN
    batch = await svc.get_batch(_AAPL, _M1, from_ms=_OPEN, to_ms=end)
    assert batch.gaps == ((_OPEN, _OPEN + 12 * _MIN),)
    again = await svc.get_batch(_AAPL, _M1, from_ms=_OPEN, to_ms=end)
    assert again.gaps == batch.gaps
    assert len(data.fetches) == 2  # One bounded attempt per call.


async def test_dense_venue_gaps_are_unchanged() -> None:
    """Crypto bars are dense: a missing minute is a gap, fetched each time."""
    clock = clock_lib.ManualClock(_T0 + 30 * _MIN)
    data = _Venue(clock, _T0, _quiet_every_third)
    svc, store = _service(DenseAdapter(data), clock)
    end = _T0 + 9 * _MIN
    batch = await svc.get_batch(_BTC, _M1, from_ms=_T0, to_ms=end)
    assert batch.gaps == (
        (_T0 + 2 * _MIN, _T0 + 3 * _MIN),
        (_T0 + 5 * _MIN, _T0 + 6 * _MIN),
        (_T0 + 8 * _MIN, end),
    )
    calls = len(data.fetches)
    await svc.get_batch(_BTC, _M1, from_ms=_T0, to_ms=end)
    assert len(data.fetches) > calls
    assert data.fetches[calls][0] == _T0 + 2 * _MIN
    assert store.coverage(_BTC, _M1, _T0, end) == []


# Live sessions ----------------------------------------------------------


def _aapl_minute(m: int) -> market.Candle:
    """Builds a streamed final AAPL bar.

    Args:
        m: Minutes after the 2026-11-20 open.

    Returns:
        The candle.
    """
    return _bar(_AAPL, _OPEN + m * _MIN)


async def test_polling_session_records_coverage() -> None:
    """A REST polling session of a sparse venue covers what it polled."""
    clock = clock_lib.ManualClock(_OPEN + 10 * _MIN + 30_000)
    data = _Venue(clock, _OPEN, lambda minute: minute != 9, settle_ms=0)
    svc, store = _service(SparseAdapter(data), clock)
    await svc.router.acquire(_AAPL, _M1)
    await _wait_for(lambda: bool(store.coverage(_AAPL, _M1, 0, 2**62)))
    await svc.router.close()
    low, high = store.coverage(_AAPL, _M1, 0, 2**62)[0]
    assert low <= _OPEN + 9 * _MIN and high >= _OPEN + 10 * _MIN
    quiet = (_OPEN + 9 * _MIN, _OPEN + 10 * _MIN)
    assert store.final_gaps(_AAPL, _M1, *quiet) == []
    assert store.latest(_AAPL, _M1) is not None


async def test_dense_polling_session_records_no_coverage() -> None:
    """Crypto polling sessions prove nothing about missing bars."""
    clock = clock_lib.ManualClock(_T0 + 10 * _MIN + 30_000)
    data = _Venue(clock, _T0, lambda minute: minute != 9, settle_ms=0)
    svc, store = _service(DenseAdapter(data), clock)
    await svc.router.acquire(_BTC, _M1)
    await _wait_for(lambda: bool(data.fetches))
    await _wait_for(lambda: store.latest(_BTC, _M1) is not None)
    await svc.router.close()
    assert store.coverage(_BTC, _M1, 0, 2**62) == []


async def test_native_session_records_the_coverage_its_stream_proved() -> None:
    """Streamed coverage is stored; an outage between chains stays a gap."""
    clock = clock_lib.ManualClock(_OPEN + 30 * _MIN)
    data = _Venue(clock, _OPEN, _quiet_every_third)
    updates = [
        candle_rules.CandleCoverage((_aapl_minute(0),)),
        candle_rules.CandleCoverage(
            (_aapl_minute(1),), ((_OPEN + _MIN, _OPEN + 2 * _MIN),)
        ),
        candle_rules.CandleCoverage(
            (_aapl_minute(3),), ((_OPEN + 2 * _MIN, _OPEN + 4 * _MIN),)
        ),
        # Reconnect: the next chain starts over at minute 9.
        candle_rules.CandleCoverage((_aapl_minute(9),)),
        candle_rules.CandleCoverage((), ()),
        candle_rules.CandleCoverage(
            (_aapl_minute(10),), ((_OPEN + 10 * _MIN, _OPEN + 11 * _MIN),)
        ),
    ]
    adapter = SparseAdapter(data, native=True, updates=updates)
    svc, store = _service(adapter, clock)
    session = await svc.router.acquire(_AAPL, _M1)
    await _wait_for(lambda: store.latest(_AAPL, _M1) == _aapl_minute(10))
    assert session.state is feed_router.SessionState.LIVE
    await svc.router.close()
    assert store.final_gaps(_AAPL, _M1, _OPEN, _OPEN + 11 * _MIN) == [
        (_OPEN + 4 * _MIN, _OPEN + 9 * _MIN)
    ]


async def test_dense_native_session_records_no_coverage() -> None:
    """A crypto stream stores bars but no coverage."""
    clock = clock_lib.ManualClock(_T0 + 30 * _MIN)
    data = _Venue(clock, _T0, _quiet_every_third)
    adapter = DenseAdapter(data, native=True)
    adapter.updates = [candle_rules.CandleCoverage((_bar(_BTC, _T0),))]
    svc, store = _service(adapter, clock)
    await svc.router.acquire(_BTC, _M1)
    await _wait_for(lambda: store.latest(_BTC, _M1) is not None)
    await svc.router.close()
    assert store.coverage(_BTC, _M1, 0, 2**62) == []


# Adapter helpers --------------------------------------------------------


class _StreamingVenue(base.BaseVenueAdapter):
    """Dense base adapter streaming two scripted candles.

    Attributes:
        VENUE: Venue the adapter serves (Binance).
    """

    VENUE = instruments.Venue.BINANCE

    async def _bars(self) -> AsyncGenerator[market.Candle, None]:
        """Yields two consecutive final BTCUSDT minutes.

        Yields:
            The bars at ``_T0`` and one minute later.
        """
        yield _bar(_BTC, _T0)
        yield _bar(_BTC, _T0 + _MIN)

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams the scripted bars.

        Args:
            instrument: Ignored.
            timeframe: Ignored.
            tracker: Ignored.

        Returns:
            The two scripted bars.
        """
        del instrument, timeframe, tracker
        return self._bars()


class _Plain:
    """Async iterator without ``aclose`` over coverage updates.

    Attributes:
        items: Remaining updates.
    """

    def __init__(self, items: list[candle_rules.CandleCoverage]) -> None:
        """Initializes the iterator.

        Args:
            items: Updates to deliver in order; consumed.
        """
        self.items = items

    def __aiter__(self) -> _Plain:
        """Returns the iterator.

        Returns:
            This iterator.
        """
        return self

    async def __anext__(self) -> candle_rules.CandleCoverage:
        """Returns the next update.

        Returns:
            The oldest remaining update.

        Raises:
            StopAsyncIteration: When exhausted.
        """
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


async def test_base_streams_prove_no_coverage_by_default() -> None:
    """A dense adapter's stream becomes updates without coverage."""
    adapter = _StreamingVenue(
        None,  # type: ignore[arg-type]  # these paths never use the HTTP client
        clock_lib.ManualClock(_T0),
    )
    assert not adapter.SPARSE_BARS
    updates = [u async for u in adapter.stream_candle_coverage(_BTC, _M1)]
    assert [len(u.candles) for u in updates] == [1, 1]
    assert all(u.covered == () and not u.complete for u in updates)
    plain = _Plain([candle_rules.CandleCoverage((_bar(_BTC, _T0),), ((0, 1),))])
    assert [c.start_ms async for c in base.candles_of(plain)] == [_T0]


def test_candle_coverage_follows_the_returned_candles() -> None:
    """Coverage is clipped to the request and to bars a limit kept."""
    rows = [_bar(_AAPL, _OPEN + m * _MIN) for m in (0, 1, 3, 4)]
    window = (_OPEN, _OPEN + 5 * _MIN)
    covered = [(_OPEN - _MIN, _OPEN + 9 * _MIN)]
    full = base.candle_coverage(rows, *window, None, covered, complete=True)
    assert full.covered == (window,) and len(full.candles) == 4
    newest = base.candle_coverage(rows, *window, 2, covered, complete=True)
    assert [c.start_ms for c in newest.candles] == [
        _OPEN + 3 * _MIN,
        _OPEN + 4 * _MIN,
    ]
    assert newest.covered == ((_OPEN + 3 * _MIN, window[1]),)
    roomy = base.candle_coverage(rows, *window, 9, covered, complete=False)
    assert roomy.covered == (window,) and not roomy.complete


async def test_polling_outage_records_nothing_until_the_venue_recovers() -> (
    None
):
    """A failing poll covers nothing; after recovery coverage resumes."""
    clock = clock_lib.ManualClock(_OPEN + 10 * _MIN + 30_000)
    data = _Venue(clock, _OPEN, lambda minute: minute != 9, settle_ms=0)
    adapter = SparseAdapter(data)
    adapter.down = True
    svc, store = _service(adapter, clock)
    session = await svc.router.acquire(_AAPL, _M1)
    await _wait_for(
        lambda: session.state is feed_router.SessionState.UNAVAILABLE
    )
    assert store.coverage(_AAPL, _M1, 0, 2**62) == []
    adapter.down = False
    await _wait_for(lambda: bool(store.coverage(_AAPL, _M1, 0, 2**62)))
    await _wait_for(lambda: session.state is feed_router.SessionState.POLLING)
    batch = await svc.get_batch(
        _AAPL, _M1, from_ms=_OPEN + 9 * _MIN, to_ms=_OPEN + 10 * _MIN
    )
    assert batch.feed_state is quality.FeedState.POLL
    assert batch.source == "nasdaq.poll" and batch.complete
    with pytest.raises(errors.MissingDataError):
        await svc.get_batch(_AAPL, _M1, feed_mode=quality.FeedMode.REPLAY)
    await svc.router.close()


async def test_base_adapter_defaults_refuse_what_venues_must_provide() -> None:
    """Unimplemented hooks fail loudly instead of fabricating data."""
    adapter = _StreamingVenue(
        None,  # type: ignore[arg-type]  # these paths never use the HTTP client
        clock_lib.ManualClock(_T0),
    )
    assert adapter.venue is instruments.Venue.BINANCE
    with pytest.raises(NotImplementedError):
        adapter.capabilities()
    with pytest.raises(NotImplementedError):
        await adapter.list_instruments()
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_snapshots()
    with pytest.raises(errors.NotFoundError):
        await adapter._require(_AAPL)
    with pytest.raises(errors.UnsupportedError):
        adapter._trade_spec(_BTC)
    with pytest.raises(errors.UnsupportedError):
        await adapter._trade_history(_BTC, _T0, _T0 + _MIN, 1)
