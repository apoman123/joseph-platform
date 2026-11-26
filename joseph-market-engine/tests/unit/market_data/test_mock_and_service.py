# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the DEMO venue, registry and market-data service."""

from __future__ import annotations

import asyncio
from typing import Any

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import mock
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service

_NOW = 1_795_278_000_000
_BTC = instruments.InstrumentId.parse("mock:perp:BTCUSD")


def _service(
    clock: clock_lib.ManualClock,
) -> tuple[service.MarketDataService, mock.MockAdapter]:
    """Builds a service over the mock venue.

    Args:
        clock: Clock shared by the venue, router and service.

    Returns:
        The service and the mock adapter behind it.
    """
    adapter = mock.MockAdapter(clock)
    adapters = {instruments.Venue.MOCK: adapter}
    store = candle_store.CandleStore()
    router = feed_router.FeedRouter(adapters, store, clock)
    svc = service.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        router,
        clock,
    )
    return svc, adapter


async def test_mock_candles_are_deterministic() -> None:
    """Identical requests yield identical bars."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    tf = timeframes.Timeframe.M5
    first = await adapter.fetch_candles(_BTC, tf, _NOW - 3_600_000, _NOW)
    second = await mock.MockAdapter(clock_lib.ManualClock(_NOW)).fetch_candles(
        _BTC, tf, _NOW - 3_600_000, _NOW
    )
    assert first == second
    assert all(c.feed_state is quality.FeedState.DEMO for c in first)


async def test_mock_in_progress_bar_is_not_final() -> None:
    """Only bars that have ended are final."""
    clock = clock_lib.ManualClock(_NOW + 90_000)
    adapter = mock.MockAdapter(clock)
    rows = await adapter.fetch_candles(
        _BTC, timeframes.Timeframe.M5, _NOW - 900_000, _NOW + 300_000
    )
    assert rows[-1].start_ms == timeframes.Timeframe.M5.floor(_NOW + 90_000)
    assert not rows[-1].is_final
    assert all(c.is_final for c in rows[:-1])


@hypothesis.settings(max_examples=40, deadline=None)
@hypothesis.given(
    strategies.integers(min_value=0, max_value=50_000),
    strategies.sampled_from(list(timeframes.Timeframe)),
)
def test_mock_bars_respect_ohlc_and_tick(
    offset_minutes: int, timeframe: timeframes.Timeframe
) -> None:
    """Every synthetic bar satisfies OHLC invariants and tick alignment."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    start = timeframe.floor(
        _NOW - offset_minutes * 60_000 - (timeframe.duration_ms)
    )
    bar = adapter.bar(_BTC, timeframe, start, _NOW)
    assert bar is not None
    assert bar.low <= min(bar.open, bar.close)
    assert bar.high >= max(bar.open, bar.close)
    assert (bar.close / mock.SPECS[0].tick) % 1 == 0


async def test_mock_lower_timeframes_aggregate_consistently() -> None:
    """A 1h bar equals the aggregate of its twelve 5m bars."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    hour = timeframes.Timeframe.H1.floor(_NOW) - 3_600_000
    one_hour = adapter.bar(_BTC, timeframes.Timeframe.H1, hour, _NOW)
    fives = await adapter.fetch_candles(
        _BTC, timeframes.Timeframe.M5, hour, hour + 3_600_000
    )
    assert one_hour is not None
    assert one_hour.open == fives[0].open
    assert one_hour.close == fives[-1].close
    assert one_hour.high == max(c.high for c in fives)
    assert one_hour.low == min(c.low for c in fives)


async def test_mock_stream_finalizes_bars_on_the_clock() -> None:
    """The DEMO stream emits the live bar, then the final bar."""
    clock = clock_lib.ManualClock(_NOW + 30_000)

    async def tick(_: float) -> None:
        """Advances the clock one minute per sleep, whatever the delay."""
        clock.advance(60_000)

    adapter = mock.MockAdapter(clock, sleep=tick)
    seen = []
    async for candle in adapter.stream_candles(_BTC, timeframes.Timeframe.M1):
        seen.append(candle)
        if len(seen) >= 3:
            break
    assert not seen[0].is_final
    assert seen[1].is_final
    assert seen[1].start_ms == seen[0].start_ms


async def test_mock_unknown_instrument_is_not_found() -> None:
    """Unknown DEMO symbols are rejected."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    with pytest.raises(errors.NotFoundError):
        await adapter.fetch_snapshot(
            instruments.InstrumentId.parse("mock:perp:DOGEUSD")
        )


async def test_service_get_candles_returns_finals_only() -> None:
    """The CandleSource port never returns the in-progress bar."""
    clock = clock_lib.ManualClock(_NOW + 90_000)
    svc, _ = _service(clock)
    rows = await svc.get_candles(
        _BTC, timeframes.Timeframe.M5, _NOW - 1_800_000, _NOW + 300_000
    )
    assert rows
    assert all(c.is_final for c in rows)
    assert rows[-1].end_ms <= clock.now_ms()


async def test_service_accepts_string_arguments() -> None:
    """Strings are normalized to domain identifiers."""
    svc, _ = _service(clock_lib.ManualClock(_NOW))
    rows = await svc.get_candles(
        "mock:perp:BTCUSD", "15m", _NOW - 3_600_000, _NOW
    )
    assert len(rows) == 3
    assert all(c.is_final for c in rows)


async def test_service_batch_reports_demo_provenance_and_no_gaps() -> None:
    """DEMO batches are labelled DEMO and complete."""
    svc, _ = _service(clock_lib.ManualClock(_NOW + 1))
    batch = await svc.get_batch(_BTC, timeframes.Timeframe.M1, limit=30)
    assert batch.feed_state is quality.FeedState.DEMO
    assert len(batch.candles) == 30
    assert batch.complete


async def test_service_validates_inputs() -> None:
    """Bad ids, timeframes, limits and ranges are validation errors."""
    svc, _ = _service(clock_lib.ManualClock(_NOW))
    with pytest.raises(errors.ValidationFailedError):
        await svc.get_batch("BTCUSDT", "5m")
    with pytest.raises(errors.ValidationFailedError):
        await svc.get_batch(_BTC, "7m")
    with pytest.raises(errors.ValidationFailedError):
        await svc.get_batch(_BTC, "5m", limit=0)
    with pytest.raises(errors.ValidationFailedError):
        await svc.get_batch(_BTC, "5m", from_ms=_NOW, to_ms=_NOW - 1)
    with pytest.raises(errors.NotFoundError):
        await svc.get_batch("binance:perp:BTCUSDT", "5m")
    with pytest.raises(errors.ValidationFailedError):
        await svc.get_order_book(_BTC, depth=0)


async def test_service_serves_cached_closed_bars_without_refetch() -> None:
    """Closed bars already stored are not fetched again."""
    clock = clock_lib.ManualClock(_NOW)
    svc, adapter = _service(clock)
    calls: list[tuple[Any, ...]] = []
    original = adapter.fetch_candles

    async def counting(*args: Any, **kwargs: Any) -> list[market.Candle]:
        """Records the call, then delegates to the real fetch.

        Args:
            *args: Positional arguments of ``fetch_candles``.
            **kwargs: Keyword arguments of ``fetch_candles``.

        Returns:
            The candles the mock venue returns.
        """
        calls.append(args)
        return await original(*args, **kwargs)

    adapter.fetch_candles = counting  # type: ignore[method-assign]  # spy replaces the bound method to count fetches
    start = _NOW - 3_600_000
    end = timeframes.Timeframe.M5.floor(_NOW)
    await svc.get_candles(_BTC, timeframes.Timeframe.M5, start, end)
    await svc.get_candles(_BTC, timeframes.Timeframe.M5, start, end)
    assert len(calls) == 1


async def test_snapshot_is_cached_for_one_second() -> None:
    """UI polling cannot hammer the venue."""
    clock = clock_lib.ManualClock(_NOW)
    svc, _ = _service(clock)
    first = await svc.get_snapshot(_BTC)
    assert await svc.get_snapshot(_BTC) is first
    clock.advance(1_500)
    assert await svc.get_snapshot(_BTC) is not first


async def test_registry_search_keeps_venues_separate() -> None:
    """Registry search finds symbols; a venue with no adapter is not found."""
    clock = clock_lib.ManualClock(_NOW)
    adapters = {instruments.Venue.MOCK: mock.MockAdapter(clock)}
    reg = registry.InstrumentRegistry(adapters)
    rows = await reg.search("eth")
    assert [str(r.instrument_id) for r in rows] == ["mock:perp:ETHUSD"]
    # Three DEMO perpetuals and the DEMO stock mock:stock:ACME.
    assert len(await reg.search(None)) == 4
    with pytest.raises(errors.NotFoundError):
        await reg.all_instruments(instruments.Venue.BINANCE)


class _Down:
    """Venue adapter whose venue is unreachable."""

    async def list_instruments(self) -> list[object]:
        """Fails like an unreachable venue.

        Returns:
            Never returns.

        Raises:
            errors.VenueUnavailableError: Always.
        """
        raise errors.VenueUnavailableError("down")


async def test_registry_reports_unreachable_venue_without_failing() -> None:
    """One failing venue does not break global search."""
    clock = clock_lib.ManualClock(_NOW)
    adapters = {
        instruments.Venue.MOCK: mock.MockAdapter(clock),
        instruments.Venue.BITGET: _Down(),
    }
    reg = registry.InstrumentRegistry(adapters)  # type: ignore[arg-type]  # _Down fakes only list_instruments of the adapter port
    assert len(await reg.search("btc")) == 1
    assert instruments.Venue.BITGET in reg.errors
    with pytest.raises(errors.VenueUnavailableError):
        await reg.all_instruments(instruments.Venue.BITGET)


async def test_concurrent_queries_are_consistent() -> None:
    """Concurrent batch queries do not corrupt the store."""
    svc, _ = _service(clock_lib.ManualClock(_NOW))
    batches = await asyncio.gather(
        *(
            svc.get_batch(_BTC, timeframes.Timeframe.M5, limit=50)
            for _ in range(5)
        )
    )
    assert len({tuple(b.candles) for b in batches}) == 1


async def test_registry_warmup_shares_inflight_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the selector during warmup shares one provider request.

    Args:
        monkeypatch: Scoped patch helper for the delayed listing request.
    """
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[bool] = []

    async def listing() -> list[instruments.Instrument]:
        """Waits for a deterministic metadata response.

        Returns:
            An empty fixture listing.
        """
        calls.append(True)
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(adapter, "list_instruments", listing)
    catalog = registry.InstrumentRegistry({instruments.Venue.MOCK: adapter})
    catalog.start_warmup()
    await entered.wait()
    search = asyncio.create_task(catalog.search("BTC"))
    await asyncio.sleep(0)
    release.set()
    assert await search == []
    assert len(calls) == 1
    await catalog.close()
