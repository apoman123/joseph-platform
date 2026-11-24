# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for DEMO taker volume, synthetic trades, seconds and months."""

from __future__ import annotations

import datetime
from collections.abc import Callable

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import mock
from joseph.market_data.domain import trades as trades_lib

_NOW = 1_795_278_000_000 + 37_500
_BTC = instruments.InstrumentId.parse("mock:perp:BTCUSD")
_ETH = instruments.InstrumentId.parse("mock:perp:ETHUSD")
_M1 = timeframes.Timeframe.M1
Validate = Callable[[str, str, object], None]


@hypothesis.settings(max_examples=60, deadline=None)
@hypothesis.given(
    strategies.integers(min_value=1, max_value=50_000),
    strategies.sampled_from(list(timeframes.MINUTE_AND_ABOVE)),
)
def test_every_demo_bar_has_plausible_taker_volume(
    offset_minutes: int, timeframe: timeframes.Timeframe
) -> None:
    """Taker buy volume is set and lies inside ``[0, base_volume]``."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    start = timeframe.floor(_NOW - offset_minutes * 60_000)
    bar = adapter.bar(_BTC, timeframe, start, _NOW)
    assert bar is not None
    assert bar.taker_buy_base_volume is not None
    assert 0 <= bar.taker_buy_base_volume <= bar.base_volume
    if bar.base_volume > 0 and timeframe is not timeframes.Timeframe.M1:
        share = bar.taker_buy_base_volume / bar.base_volume
        assert 0.05 <= share <= 0.95


@pytest.mark.parametrize("timeframe", timeframes.SECONDS)
async def test_seconds_bars_aggregate_exactly_to_the_minute_bar(
    timeframe: timeframes.Timeframe,
) -> None:
    """Seconds bars tile the 1m bar: same OHLC, volume and taker volume."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    minute = _M1.floor(_NOW) - 3 * 60_000
    one = adapter.bar(_ETH, _M1, minute, _NOW)
    assert one is not None
    bars = await adapter.fetch_candles(_ETH, timeframe, minute, minute + 60_000)
    assert len(bars) == 60_000 // timeframe.duration_ms
    assert bars[0].open == one.open and bars[-1].close == one.close
    assert max(c.high for c in bars) == one.high
    assert min(c.low for c in bars) == one.low
    assert sum(c.base_volume for c in bars) == one.base_volume
    assert (
        sum(c.taker_buy_base_volume or 0 for c in bars)
        == one.taker_buy_base_volume
    )
    assert all(
        c.is_final and c.feed_state is quality.FeedState.DEMO for c in bars
    )
    assert {c.source for c in bars} == {"mock.trades"}


async def test_synthetic_trades_are_deterministic_and_contract_valid(
    contract_validate: Validate,
) -> None:
    """Identical requests return identical, schema-valid trades."""
    minute = _M1.floor(_NOW) - 60_000
    first = await mock.MockAdapter(clock_lib.ManualClock(_NOW)).fetch_trades(
        _BTC, minute, minute + 60_000
    )
    second = await mock.MockAdapter(clock_lib.ManualClock(_NOW)).fetch_trades(
        _BTC, minute, minute + 60_000
    )
    assert first == second
    assert first[0].venue_event_ts_ms == minute
    for trade in first:
        contract_validate(
            "market.schema.json", "Trade", trades_lib.to_contract(trade)
        )
    sides = {t.aggressor_side for t in first}
    assert sides == {market.AggressorSide.BUY, market.AggressorSide.SELL}
    latest = await mock.MockAdapter(clock_lib.ManualClock(_NOW)).fetch_trades(
        _BTC, minute, minute + 60_000, limit=3
    )
    assert latest == first[-3:]


async def test_trades_never_come_from_the_future() -> None:
    """Only trades that have happened by the clock are served."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    trades = await adapter.fetch_trades(_BTC, _NOW - 30_000, _NOW + 60_000)
    assert trades and max(t.venue_event_ts_ms for t in trades) <= _NOW
    bars = await adapter.fetch_candles(
        _BTC, timeframes.Timeframe.S1, _NOW - 10_000, _NOW + 5_000
    )
    assert all(c.start_ms <= _NOW for c in bars)
    assert all(c.is_final == (c.end_ms <= _NOW) for c in bars)


async def test_monthly_demo_bars_are_calendar_months() -> None:
    """1M bars start on the 1st 00:00 UTC and span the real month."""
    adapter = mock.MockAdapter(clock_lib.ManualClock(_NOW))
    tf = timeframes.Timeframe.MN1
    rows = await adapter.fetch_candles(_BTC, tf, 0, _NOW + 1, limit=14)
    assert len(rows) == 14
    for candle in rows:
        start = datetime.datetime.fromtimestamp(
            candle.start_ms / 1000, datetime.UTC
        )
        assert (start.day, start.hour) == (1, 0)
        assert candle.end_ms == tf.end_of(candle.start_ms)
    assert [c.end_ms for c in rows[:-1]] == [c.start_ms for c in rows[1:]]
    assert not rows[-1].is_final and rows[-2].is_final


async def test_demo_trade_stream_follows_the_clock() -> None:
    """The DEMO trade stream yields trades as time passes them."""
    clock = clock_lib.ManualClock(_NOW)

    async def tick(_: float) -> None:
        """Advances the clock five seconds per sleep.

        Args:
            _: Ignored delay.
        """
        clock.advance(5_000)

    adapter = mock.MockAdapter(clock, sleep=tick)
    seen: list[market.Trade] = []
    async for trade in adapter.stream_trades(_BTC):
        seen.append(trade)
        if len(seen) >= 5:
            break
    assert all(t.venue_event_ts_ms > _NOW for t in seen)
    times = [t.venue_event_ts_ms for t in seen]
    assert times == sorted(times)


async def test_demo_seconds_stream_emits_final_and_flat_bars() -> None:
    """The DEMO seconds stream behaves like a venue trade stream."""
    clock = clock_lib.ManualClock(_NOW)

    async def tick(_: float) -> None:
        """Advances the clock one second per sleep.

        Args:
            _: Ignored delay.
        """
        clock.advance(1_000)

    adapter = mock.MockAdapter(clock, sleep=tick)
    finals: list[market.Candle] = []
    async for candle in adapter.stream_candles(_BTC, timeframes.Timeframe.S1):
        if candle.is_final:
            finals.append(candle)
        if len(finals) >= 40:
            break
    starts = [c.start_ms for c in finals]
    assert starts == list(range(starts[0], starts[0] + 40_000, 1_000))
    assert any(c.base_volume == 0 for c in finals)


def test_demo_capabilities_cover_seconds_and_months() -> None:
    """The DEMO venue declares every timeframe and trade features."""
    caps = mock.MockAdapter(clock_lib.ManualClock(_NOW)).capabilities()
    assert caps.timeframes == tuple(timeframes.Timeframe)
    assert caps.trade_built_timeframes == timeframes.SECONDS
    assert caps.market_data.taker_volume
    assert caps.market_data.native_ws_trades and caps.market_data.trades_history
