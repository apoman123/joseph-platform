# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the FeedMode state machine."""

from __future__ import annotations

import asyncio
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import replay as replay_lib
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

_TF = timeframes.Timeframe.M1
_T0 = 1_795_277_340_000
_D = decimal.Decimal


def _candle(
    instrument: instruments.InstrumentId, start: int, final: bool, now: int
) -> market.Candle:
    """Builds a flat 1m candle.

    Args:
        instrument: Instrument.
        start: Bar start, epoch milliseconds.
        final: Whether the bar is final.
        now: Receipt time, epoch milliseconds.

    Returns:
        The candle, all prices and the volume 1.
    """
    return market.Candle(
        instrument_id=instrument,
        timeframe=_TF,
        start_ms=start,
        end_ms=_TF.end_of(start),
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


class FakeAdapter:
    """Controllable ``MarketDataPort`` double.

    Attributes:
        venue: Venue the double serves.
        native: Whether native candles are advertised.
        stream_fails: Whether the stream raises immediately.
        clock: Clock stamping receipts; the stream advances it.
        fetches: Number of candle fetches.
        streams: Number of candle streams opened.
    """

    def __init__(
        self,
        venue: instruments.Venue,
        native: bool,
        stream_fails: bool,
        clock: clock_lib.ManualClock,
    ) -> None:
        """Initializes the fake.

        Args:
            venue: Venue.
            native: Whether native candles are advertised.
            stream_fails: Whether the stream raises immediately.
            clock: Clock.
        """
        self.venue = venue
        self.native = native
        self.stream_fails = stream_fails
        self.clock = clock
        self.fetches = 0
        self.streams = 0

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns fake capabilities.

        Returns:
            REST history of 1m bars; native candles only if advertised.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="fake",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True, native_ws_candles=self.native
            ),
            timeframes=(_TF,),
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
        """Returns one live candle per call and counts the fetch.

        Args:
            instrument: Instrument of the candle.
            timeframe: Ignored.
            start_ms: Ignored.
            end_ms: Ignored.
            limit: Ignored.

        Returns:
            A live candle at ``_T0``, received now.
        """
        del timeframe, start_ms, end_ms, limit
        self.fetches += 1
        return [_candle(instrument, _T0, False, self.clock.now_ms())]

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Yields candles or fails.

        Counts the stream when it starts.

        Args:
            instrument: Instrument of the candles.
            timeframe: Ignored.
            tracker: Ignored.

        Yields:
            A live candle at ``_T0`` every 10 ms; each advances the clock by
            1 ms.

        Raises:
            errors.VenueUnavailableError: When configured to fail.
        """
        del timeframe, tracker
        self.streams += 1
        if self.stream_fails:
            raise errors.VenueUnavailableError("ws down")
        while True:
            yield _candle(instrument, _T0, False, self.clock.now_ms())
            self.clock.advance(1)
            await asyncio.sleep(0.01)


async def _sleep(_: float) -> None:
    """Sleeps 5 ms whatever the delay, so retry loops yield to the test."""
    await asyncio.sleep(0.005)


def _router(
    adapter: FakeAdapter,
    clock: clock_lib.ManualClock,
    store: candle_store.CandleStore | None = None,
    replay: replay_lib.ReplaySource | None = None,
    emitted: list[tuple[str, market.Candle]] | None = None,
) -> feed_router.FeedRouter:
    """Builds a router around a fake adapter.

    The router retries native streams only after an hour, so a session
    stays on the transport it first falls back to.

    Args:
        adapter: Fake adapter.
        clock: Clock.
        store: Store; None uses a new one.
        replay: Replay source; None configures none.
        emitted: Receives ``(mode, candle)`` per emitted candle; None drops
            them.

    Returns:
        The router.
    """

    async def on_candle(
        session: feed_router.FeedSession,
        candle: market.Candle,
        action: candle_rules.MergeAction,
    ) -> None:
        """Records emitted candles.

        Args:
            session: Session.
            candle: Candle.
            action: Merge action.
        """
        del action
        if emitted is not None:
            emitted.append((session.mode.value, candle))

    return feed_router.FeedRouter(
        {adapter.venue: adapter},  # type: ignore[dict-item]  # implements only the MarketDataPort methods the router uses
        store or candle_store.CandleStore(),
        clock,
        replay=replay,
        on_candle=on_candle,
        sleep=_sleep,
        native_retry_s=3600,
    )


async def _wait_for(
    predicate: Callable[[], object], limit_s: float = 2.0
) -> None:
    """Waits until ``predicate()`` is truthy.

    Args:
        predicate: Zero-argument callable.
        limit_s: Seconds before giving up.

    Raises:
        TimeoutError: ``predicate()`` stayed falsy for ``limit_s`` seconds.
    """

    async def loop() -> None:
        """Polls the predicate (state lives in the router under test)."""
        while not predicate():  # noqa: ASYNC110 - polls state that the router's tasks change; there is no event to await
            await asyncio.sleep(0.005)

    await asyncio.wait_for(loop(), limit_s)


async def test_auto_uses_native_stream_when_healthy() -> None:
    """AUTO runs LIVE on the native stream."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    router = _router(adapter, clock)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    session = await router.acquire(instrument, _TF)
    await _wait_for(lambda: session.state is feed_router.SessionState.LIVE)
    health_state = router.venue_health(instruments.Venue.BINANCE)
    assert health_state.transport is quality.Transport.NATIVE_WS
    assert health_state.feed_state is quality.FeedState.LIVE
    assert adapter.fetches == 0
    await router.close()
    assert session.state is feed_router.SessionState.STOPPED


async def test_auto_falls_back_to_visible_rest_polling() -> None:
    """A failing native stream degrades visibly to POLL."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, True, clock)
    emitted: list[tuple[str, market.Candle]] = []
    router = _router(adapter, clock, emitted=emitted)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    session = await router.acquire(instrument, _TF)
    await _wait_for(lambda: emitted)
    assert session.state is feed_router.SessionState.POLLING
    snapshot = router.venue_health(instruments.Venue.BINANCE)
    assert snapshot.state is quality.HealthState.DEGRADED
    assert snapshot.transport is quality.Transport.REST_POLL
    assert emitted[0][1].feed_state is quality.FeedState.POLL
    assert emitted[0][1].source == "fake.poll"
    await router.close()


async def test_native_ws_mode_rejects_unsupported_venue() -> None:
    """NATIVE_WS on a REST-only venue is UNSUPPORTED, never polled."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.LIGHTER, False, False, clock)
    router = _router(adapter, clock)
    instrument = instruments.InstrumentId.parse("lighter:perp:BTC")
    with pytest.raises(errors.UnsupportedError):
        await router.acquire(instrument, _TF, quality.FeedMode.NATIVE_WS)
    assert adapter.fetches == 0


async def test_native_ws_outage_goes_stale_without_polling() -> None:
    """NATIVE_WS never silently polls during an outage."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, True, clock)
    router = _router(adapter, clock)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    session = await router.acquire(instrument, _TF, quality.FeedMode.NATIVE_WS)
    await _wait_for(lambda: session.state is feed_router.SessionState.STALE)
    await asyncio.sleep(0.05)
    assert adapter.fetches == 0
    assert (
        router.venue_health(instruments.Venue.BINANCE).state
        is quality.HealthState.UNAVAILABLE
    )
    await router.close()


async def test_rest_poll_mode_polls_and_labels_poll() -> None:
    """REST_POLL emits POLL-labelled candles."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    emitted: list[tuple[str, market.Candle]] = []
    router = _router(adapter, clock, emitted=emitted)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    await router.acquire(instrument, _TF, quality.FeedMode.REST_POLL)
    await _wait_for(lambda: emitted)
    assert emitted[0][0] == "REST_POLL"
    assert adapter.streams == 0
    await router.close()


async def test_external_vendor_is_unsupported() -> None:
    """No vendor feed is configured, so the mode is rejected."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    router = _router(adapter, clock)
    with pytest.raises(errors.UnsupportedError):
        router.validate(
            instruments.InstrumentId.parse("binance:perp:BTCUSDT"),
            _TF,
            quality.FeedMode.EXTERNAL_VENDOR,
        )


async def test_replay_mode_replays_recording_then_stops(
    tmp_path: pathlib.Path,
) -> None:
    """REPLAY serves recorded bars labelled REPLAY and then stops."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    folder = tmp_path / "binance" / "perp" / "BTCUSDT"
    folder.mkdir(parents=True)
    lines = [
        json.dumps(_candle(instrument, _T0 + i * 60_000, True, 0).to_contract())
        for i in range(3)
    ]
    (folder / "1m.jsonl").write_text("\n".join(lines) + "\n")
    emitted: list[tuple[str, market.Candle]] = []
    replay = replay_lib.ReplaySource(tmp_path, sleep=_sleep)
    router = _router(adapter, clock, replay=replay, emitted=emitted)
    with pytest.raises(errors.MissingDataError):
        router.validate(
            instrument, timeframes.Timeframe.M5, quality.FeedMode.REPLAY
        )
    session = await router.acquire(instrument, _TF, quality.FeedMode.REPLAY)
    await _wait_for(lambda: session.state is feed_router.SessionState.STOPPED)
    assert len(emitted) == 3
    assert {c.feed_state for _, c in emitted} == {quality.FeedState.REPLAY}


async def test_replay_fetch_with_limit_zero_returns_no_candles(
    tmp_path: pathlib.Path,
) -> None:
    """A recording asked for ``limit=0`` bars returns none, 1 the newest."""
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    folder = tmp_path / "binance" / "perp" / "BTCUSDT"
    folder.mkdir(parents=True)
    lines = [
        json.dumps(_candle(instrument, _T0 + i * 60_000, True, 0).to_contract())
        for i in range(3)
    ]
    (folder / "1m.jsonl").write_text("\n".join(lines) + "\n")
    replay = replay_lib.ReplaySource(tmp_path, sleep=_sleep)
    window = (_T0, _T0 + 3 * 60_000)
    assert await replay.fetch_candles(instrument, _TF, *window, 0) == []
    newest = await replay.fetch_candles(instrument, _TF, *window, 1)
    assert [c.start_ms for c in newest] == [_T0 + 2 * 60_000]


async def test_sessions_are_reference_counted() -> None:
    """Two acquirers share one session; the last release stops it."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    router = _router(adapter, clock)
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    first = await router.acquire(instrument, _TF)
    second = await router.acquire(instrument, _TF)
    assert first is second
    await router.release(instrument, _TF)
    assert router.session(instrument, _TF) is first
    await router.release(instrument, _TF)
    assert router.session(instrument, _TF) is None
    assert first.state is feed_router.SessionState.STOPPED


def test_illegal_transition_raises() -> None:
    """A stopped session cannot go LIVE: the transition table is enforced."""
    session = feed_router.FeedSession(
        instrument=instruments.InstrumentId.parse("binance:perp:BTCUSDT"),
        timeframe=_TF,
        mode=quality.FeedMode.AUTO,
        tracker=health.FeedHealthTracker(instruments.Venue.BINANCE),
    )
    session.transition(feed_router.SessionState.STOPPED)
    with pytest.raises(ValueError):
        session.transition(feed_router.SessionState.LIVE)


def test_unknown_venue_is_not_found() -> None:
    """A venue without an adapter raises NotFoundError instead of guessing."""
    clock = clock_lib.ManualClock(_T0)
    adapter = FakeAdapter(instruments.Venue.BINANCE, True, False, clock)
    router = _router(adapter, clock)
    with pytest.raises(errors.NotFoundError):
        router.validate(
            instruments.InstrumentId.parse("mexc:perp:BTC_USDT"),
            _TF,
            quality.FeedMode.AUTO,
        )
