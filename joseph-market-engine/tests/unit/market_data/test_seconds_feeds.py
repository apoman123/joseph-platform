# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for seconds (trade-built) feed sessions, batches and storage."""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import AsyncIterator

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

_S1 = timeframes.Timeframe.S1
_T0 = 1_795_349_070_000
_ID = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_D = decimal.Decimal


def _candle(start: int, final: bool, now: int) -> market.Candle:
    """Builds a 1s candle.

    Args:
        start: Bar start.
        final: Finality.
        now: Receipt time.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=_S1,
        start_ms=start,
        end_ms=_S1.end_of(start),
        open=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        is_final=final,
        source="hyperliquid.trades",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=now,
    )


class SecondsAdapter:
    """``MarketDataPort`` double building seconds bars from trades."""

    venue = instruments.Venue.HYPERLIQUID

    def __init__(
        self,
        clock: clock_lib.ManualClock,
        *,
        history: bool,
        native_trades: bool = True,
        stream_fails: bool = True,
    ) -> None:
        """Initializes the double.

        Args:
            clock: Clock.
            history: Whether a REST trade history exists.
            native_trades: Whether a native trade stream exists.
            stream_fails: Whether the trade stream fails immediately.
        """
        self.clock = clock
        self.history = history
        self.native_trades = native_trades
        self.stream_fails = stream_fails
        self.fetches: list[tuple[int, int]] = []
        self.streams = 0

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns seconds-capable capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="fake",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                native_ws_trades=self.native_trades,
                trades_history=self.history,
            ),
            timeframes=(_S1, timeframes.Timeframe.M1),
            trade_built_timeframes=(_S1,),
            notes=(),
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
        """Records the range and returns one closed bar.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Start.
            end_ms: End.
            limit: Ignored.

        Returns:
            Candles.
        """
        del instrument, timeframe, limit
        self.fetches.append((start_ms, end_ms))
        start = _S1.shift(_S1.floor(self.clock.now_ms()), -1)
        return [_candle(start, True, self.clock.now_ms())]

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Fails or yields live bars.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Ignored.

        Yields:
            Candles.

        Raises:
            errors.VenueUnavailableError: When configured to fail.
        """
        del instrument, timeframe, tracker
        self.streams += 1
        if self.stream_fails:
            raise errors.VenueUnavailableError("trade stream down")
        while True:
            now = self.clock.now_ms()
            yield _candle(_S1.floor(now), False, now)
            self.clock.advance(1)
            await asyncio.sleep(0.01)


async def _sleep(_: float) -> None:
    """Yields control briefly.

    Args:
        _: Ignored delay.
    """
    await asyncio.sleep(0.005)


async def _wait_for(predicate: object, limit_s: float = 2.0) -> None:
    """Waits until ``predicate()`` is truthy.

    Args:
        predicate: Zero-argument callable.
        limit_s: Seconds before giving up.
    """

    async def loop() -> None:
        """Polls the predicate."""
        while not predicate():  # type: ignore[operator]  # noqa: ASYNC110
            await asyncio.sleep(0.005)

    await asyncio.wait_for(loop(), limit_s)


def _router(
    adapter: SecondsAdapter, clock: clock_lib.ManualClock
) -> feed_router.FeedRouter:
    """Builds a router around the double.

    Args:
        adapter: Adapter double.
        clock: Clock.

    Returns:
        Router.
    """
    return feed_router.FeedRouter(
        {adapter.venue: adapter},  # type: ignore[dict-item]
        candle_store.CandleStore(),
        clock,
        sleep=_sleep,
        native_retry_s=3600,
        poll_interval_s=5.0,
    )


async def test_rest_poll_of_seconds_without_trade_history_is_rejected() -> None:
    """Seconds bars cannot be polled from a venue without trade history."""
    clock = clock_lib.ManualClock(_T0)
    router = _router(SecondsAdapter(clock, history=False), clock)
    with pytest.raises(errors.UnsupportedError, match="trade history"):
        await router.acquire(_ID, _S1, quality.FeedMode.REST_POLL)


async def test_native_ws_seconds_require_a_trade_stream() -> None:
    """NATIVE_WS seconds sessions need the native trade stream."""
    clock = clock_lib.ManualClock(_T0)
    adapter = SecondsAdapter(clock, history=True, native_trades=False)
    with pytest.raises(errors.UnsupportedError, match="trade stream"):
        await _router(adapter, clock).acquire(
            _ID, _S1, quality.FeedMode.NATIVE_WS
        )


async def test_auto_seconds_without_history_goes_stale_not_polling() -> None:
    """AUTO never emulates seconds bars when the trade stream is down."""
    clock = clock_lib.ManualClock(_T0)
    adapter = SecondsAdapter(clock, history=False)
    router = _router(adapter, clock)
    session = await router.acquire(_ID, _S1)
    await _wait_for(lambda: session.state is feed_router.SessionState.STALE)
    await asyncio.sleep(0.05)
    assert adapter.fetches == []
    assert feed_router.SessionState.POLLING not in session.history
    await router.close()


async def test_auto_seconds_with_history_polls_overlapping_windows() -> None:
    """Polling seconds bars looks back over the whole poll interval."""
    clock = clock_lib.ManualClock(_T0 + 400)
    adapter = SecondsAdapter(clock, history=True)
    router = _router(adapter, clock)
    session = await router.acquire(_ID, _S1)
    await _wait_for(lambda: adapter.fetches)
    assert session.state is feed_router.SessionState.POLLING
    start, end = adapter.fetches[0]
    assert start <= _S1.floor(clock.now_ms()) - 5 * 1_000
    assert end == _S1.end_of(_S1.floor(clock.now_ms()))
    await router.close()


async def test_auto_seconds_streams_live_from_trades() -> None:
    """A healthy trade stream makes the session LIVE."""
    clock = clock_lib.ManualClock(_T0)
    adapter = SecondsAdapter(clock, history=False, stream_fails=False)
    router = _router(adapter, clock)
    session = await router.acquire(_ID, _S1)
    await _wait_for(lambda: session.state is feed_router.SessionState.LIVE)
    assert adapter.fetches == []
    await router.close()


def _service(
    adapter: SecondsAdapter, clock: clock_lib.ManualClock
) -> tuple[service.MarketDataService, candle_store.CandleStore]:
    """Builds a service around the double.

    Args:
        adapter: Adapter double.
        clock: Clock.

    Returns:
        Service and its store.
    """
    adapters = {adapter.venue: adapter}
    store = candle_store.CandleStore()
    router = feed_router.FeedRouter(
        adapters,  # type: ignore[arg-type]
        store,
        clock,
        sleep=_sleep,
    )
    svc = service.MarketDataService(
        adapters,  # type: ignore[arg-type]
        registry.InstrumentRegistry(adapters),  # type: ignore[arg-type]
        store,
        router,
        clock,
    )
    return svc, store


async def test_batch_without_trade_history_serves_store_and_gaps() -> None:
    """No REST call is attempted; stored live bars and gaps are reported."""
    clock = clock_lib.ManualClock(_T0 + 10_500)
    adapter = SecondsAdapter(clock, history=False)
    svc, store = _service(adapter, clock)
    store.upsert(_candle(_T0 + 8_000, True, _T0 + 9_000))
    store.upsert(_candle(_T0 + 9_000, True, _T0 + 10_000))
    batch = await svc.get_batch(_ID, "1s", limit=5)
    assert adapter.fetches == []
    assert [c.start_ms for c in batch.candles] == [_T0 + 8_000, _T0 + 9_000]
    assert batch.source == "hyperliquid.trades"
    assert batch.complete


async def test_batch_with_trade_history_fetches_rest_bars() -> None:
    """Venues with a trade history fetch missing seconds bars over REST."""
    clock = clock_lib.ManualClock(_T0 + 10_500)
    adapter = SecondsAdapter(clock, history=True)
    svc, _ = _service(adapter, clock)
    batch = await svc.get_batch(_ID, "1s", limit=3)
    assert adapter.fetches
    assert batch.candles[-1].start_ms == _T0 + 9_000
    assert batch.source == "hyperliquid.trades.rest"


def test_store_bounds_seconds_series_separately() -> None:
    """Seconds series keep their own, smaller bound."""
    store = candle_store.CandleStore(
        max_bars_per_series=10, max_seconds_bars_per_series=3
    )
    for i in range(6):
        store.upsert(_candle(_T0 + i * 1_000, True, _T0 + 10_000))
    kept = store.range(_ID, _S1, 0, _T0 + 10_000)
    assert [c.start_ms for c in kept] == [_T0 + 3_000, _T0 + 4_000, _T0 + 5_000]
