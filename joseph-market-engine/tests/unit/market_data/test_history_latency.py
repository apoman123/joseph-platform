# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Chart-history latency guarantees of the market-data service.

Identical concurrent refreshes share one venue fetch (a cancelled waiter
does not cancel it for the others), a just-fetched live edge is served
from the store, adjusted history survives adjustment epochs while the
provider's corporate-action factors are unchanged, and the venue token
bucket lets concurrent callers wait in parallel at an unchanged rate.
"""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import Mapping

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import http
from joseph.market_data.adapters import mock
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service

_NOW = 1_795_278_000_000
_BTC = instruments.InstrumentId.parse("mock:perp:BTCUSD")
_M1 = timeframes.Timeframe.M1
_D = decimal.Decimal


class _SlowVenue(mock.MockAdapter):
    """DEMO venue whose history calls take a while and are counted.

    Attributes:
        fetches: ``(start_ms, end_ms)`` of every history call.
        gate: Released to let pending history calls finish.
        factors: Corporate-action factors ``price_basis`` reports.
        basis_reads: Number of ``price_basis`` calls.
        basis_error: Raised by ``price_basis`` when set.
    """

    def __init__(self, clock: clock_lib.Clock) -> None:
        """Initializes the venue with an open gate.

        Args:
            clock: Clock of the venue.
        """
        super().__init__(clock)
        self.fetches: list[tuple[int, int]] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.factors: dict[int, decimal.Decimal] = {}
        self.basis_reads = 0
        self.basis_error: errors.JosephError | None = None

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Records the call, waits for the gate, then serves DEMO bars.

        Args:
            instrument: Mock instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars.

        Returns:
            DEMO candles.
        """
        self.fetches.append((start_ms, end_ms))
        await self.gate.wait()
        return await super().fetch_candles(
            instrument, timeframe, start_ms, end_ms, limit
        )


class _AdjustedVenue(_SlowVenue):
    """Slow venue that reports split-adjusted history and its factors."""

    def price_adjustment(
        self, timeframe: timeframes.Timeframe
    ) -> market.PriceAdjustment:
        """Reports split-adjusted history at every timeframe.

        Args:
            timeframe: Requested resolution.

        Returns:
            SPLIT.
        """
        del timeframe
        return market.PriceAdjustment.SPLIT

    async def price_basis(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
    ) -> Mapping[int, decimal.Decimal]:
        """Reports the configured factors.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            The factors of ``factors`` in the window.

        Raises:
            errors.JosephError: ``basis_error`` when set.
        """
        del instrument
        self.basis_reads += 1
        if self.basis_error is not None:
            raise self.basis_error
        return {s: f for s, f in self.factors.items() if start_ms <= s < end_ms}


def _service(
    venue: _SlowVenue, clock: clock_lib.Clock
) -> service.MarketDataService:
    """Builds a service over one venue.

    Args:
        venue: Venue adapter.
        clock: Clock shared with the venue.

    Returns:
        The service.
    """
    adapters = {instruments.Venue.MOCK: venue}
    store = candle_store.CandleStore()
    return service.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )


async def test_identical_concurrent_batches_share_one_fetch() -> None:
    """Four identical cold batches send one venue request."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _SlowVenue(clock)
    venue.gate.clear()
    svc = _service(venue, clock)
    batches = [
        asyncio.ensure_future(svc.get_batch(_BTC, _M1, limit=30))
        for _ in range(4)
    ]
    await asyncio.sleep(0.01)
    venue.gate.set()
    results = await asyncio.gather(*batches)
    assert len(venue.fetches) == 1
    assert all(r.candles == results[0].candles for r in results)
    assert len(results[0].candles) == 30


async def test_cancelled_waiter_does_not_cancel_the_shared_fetch() -> None:
    """A client that goes away leaves the fetch running for the others."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _SlowVenue(clock)
    venue.gate.clear()
    svc = _service(venue, clock)
    first = asyncio.ensure_future(svc.get_batch(_BTC, _M1, limit=30))
    second = asyncio.ensure_future(svc.get_batch(_BTC, _M1, limit=30))
    await asyncio.sleep(0.01)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    venue.gate.set()
    batch = await second
    assert len(batch.candles) == 30
    assert len(venue.fetches) == 1


async def test_shared_fetch_errors_reach_every_waiter() -> None:
    """A failed shared fetch fails each request, and the next one retries."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _SlowVenue(clock)
    svc = _service(venue, clock)
    original = venue.fetch_candles
    calls: list[int] = []

    async def failing(*args: object, **kwargs: object) -> list[market.Candle]:
        """Fails the first call after letting the second request join.

        Args:
            *args: Positional arguments of ``fetch_candles``.
            **kwargs: Keyword arguments of ``fetch_candles``.

        Returns:
            DEMO candles from the second call on.

        Raises:
            errors.VenueUnavailableError: On the first call.
        """
        calls.append(len(calls))
        if len(calls) == 1:
            await asyncio.sleep(0.01)
            raise errors.VenueUnavailableError("down")
        return await original(*args, **kwargs)  # type: ignore[arg-type]  # forwards the original signature

    venue.fetch_candles = failing  # type: ignore[method-assign]  # instrumented for the test
    results = await asyncio.gather(
        svc.get_batch(_BTC, _M1, limit=30),
        svc.get_batch(_BTC, _M1, limit=30),
        return_exceptions=True,
    )
    assert all(isinstance(r, errors.VenueUnavailableError) for r in results)
    assert len(calls) == 1
    assert len((await svc.get_batch(_BTC, _M1, limit=30)).candles) == 30


async def test_fresh_live_edge_is_served_from_the_store() -> None:
    """A reload within the edge TTL sends nothing; a later one refreshes."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _SlowVenue(clock)
    svc = _service(venue, clock)
    await svc.get_batch(_BTC, _M1, limit=30)
    assert len(venue.fetches) == 1
    clock.advance(1_000)
    warm = await svc.get_batch(_BTC, _M1, limit=30)
    assert len(venue.fetches) == 1
    assert not warm.candles[-1].is_final
    clock.advance(service.edge_ttl_ms(_M1))
    await svc.get_batch(_BTC, _M1, limit=30)
    assert len(venue.fetches) == 2
    assert service.edge_ttl_ms(timeframes.Timeframe.S1) == 1_000
    assert service.edge_ttl_ms(timeframes.Timeframe.H1) == 10_000


async def test_unchanged_factors_keep_adjusted_history_across_epochs() -> None:
    """Only a changed factor drops cached adjusted history and its epoch."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _AdjustedVenue(clock)
    yesterday = _NOW - 86_400_000
    venue.factors = {yesterday - 86_400_000: _D(1), yesterday: _D(1)}
    svc = _service(venue, clock)
    first = (await svc.get_batch(_BTC, _M1, limit=30)).to_contract()
    await asyncio.sleep(0)
    assert venue.basis_reads == 1
    fetched = len(venue.fetches)
    clock.advance(service.ADJUSTMENT_REFRESH_MS)
    kept = await svc.get_batch(_BTC, _M1, limit=30)
    same = kept.to_contract()
    assert venue.basis_reads == 2
    # Only the bars closed since were read; history and basis epoch stayed.
    assert all(
        start > kept.candles[0].start_ms for start, _ in venue.fetches[fetched:]
    )
    assert same["price_adjustment"] == "SPLIT"
    assert (
        same["price_adjustment_epoch_ms"] == first["price_adjustment_epoch_ms"]
    )
    refresh_after = first["price_adjustment_refresh_after_ms"]
    assert isinstance(refresh_after, int)
    assert same["price_adjustment_refresh_after_ms"] == (
        refresh_after + service.ADJUSTMENT_REFRESH_MS
    )
    # A split: every earlier factor changes, so history is read again.
    venue.factors = {s: _D("0.25") for s in venue.factors}
    clock.advance(service.ADJUSTMENT_REFRESH_MS)
    fetched = len(venue.fetches)
    reloaded = await svc.get_batch(_BTC, _M1, limit=30)
    rebased = reloaded.to_contract()
    assert venue.fetches[fetched][0] == reloaded.candles[0].start_ms
    assert rebased["price_adjustment_epoch_ms"] == (
        clock.now_ms()
        // service.ADJUSTMENT_REFRESH_MS
        * service.ADJUSTMENT_REFRESH_MS
    )
    assert (
        rebased["price_adjustment_epoch_ms"]
        != first["price_adjustment_epoch_ms"]
    )


async def test_unreadable_factors_drop_adjusted_history() -> None:
    """Without evidence the cached adjusted history is not trusted."""
    clock = clock_lib.ManualClock(_NOW + 1)
    venue = _AdjustedVenue(clock)
    svc = _service(venue, clock)
    await svc.get_batch(_BTC, _M1, limit=30)
    venue.basis_error = errors.VenueUnavailableError("down")
    clock.advance(service.ADJUSTMENT_REFRESH_MS)
    fetched = len(venue.fetches)
    reloaded = await svc.get_batch(_BTC, _M1, limit=30)
    assert venue.fetches[fetched][0] == reloaded.candles[0].start_ms


def test_newly_historical_session_must_keep_unit_factor() -> None:
    """The session that was current at the last check was native then."""
    today = 1_000_000
    assert not service.basis_changed({}, today, {today - 10: _D(2)})
    assert not service.basis_changed({}, today, {today: _D(1)})
    assert service.basis_changed({}, today, {today: _D("0.5")})
    assert service.basis_changed({5: _D(1)}, today, {5: _D("0.5")})


async def test_rate_limiter_waits_in_parallel_at_the_same_rate() -> None:
    """Concurrent waiters sleep at once; a cancelled one returns its token."""
    now = [0.0]
    sleeps: list[float] = []
    release = asyncio.Event()

    async def sleep(seconds: float) -> None:
        """Records the wait and blocks until released.

        Args:
            seconds: Requested wait.
        """
        sleeps.append(seconds)
        await release.wait()

    limiter = http.RateLimiter(2.0, 1, monotonic=lambda: now[0], sleep=sleep)
    await limiter.acquire()
    waiters = [asyncio.ensure_future(limiter.acquire()) for _ in range(3)]
    await asyncio.sleep(0)
    # All three wait at the same time, queued 0.5 s apart.
    assert sleeps == [0.5, 1.0, 1.5]
    waiters[2].cancel()
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*waiters[:2])
    now[0] = 1.0
    sleeps.clear()
    release.clear()
    late = asyncio.ensure_future(limiter.acquire())
    await asyncio.sleep(0)
    # The cancelled reservation was returned: 1 s refilled two tokens.
    assert sleeps == [0.5]
    release.set()
    await late
