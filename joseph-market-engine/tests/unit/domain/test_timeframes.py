# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for timeframe boundaries: seconds, fixed and month bars."""

from __future__ import annotations

import datetime
import decimal

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes

TF = timeframes.Timeframe
_UTC = datetime.UTC


def _ms(*args: int) -> int:
    """Returns UTC epoch milliseconds for a date/time.

    Args:
        *args: ``datetime`` positional fields (year, month, day, ...).

    Returns:
        Epoch milliseconds.
    """
    return int(datetime.datetime(*args, tzinfo=_UTC).timestamp()) * 1000


def test_contract_values_are_case_sensitive() -> None:
    """``1m`` is a minute and ``1M`` a calendar month."""
    assert timeframes.parse("1m") is TF.M1
    assert timeframes.parse("1M") is TF.MN1
    assert timeframes.parse("15s") is TF.S15
    with pytest.raises(ValueError, match="unsupported"):
        timeframes.parse("1S")


def test_seconds_and_minutes_partition_the_enum() -> None:
    """Seconds are flagged separately from kline-capable timeframes."""
    assert timeframes.SECONDS == (TF.S1, TF.S5, TF.S15, TF.S30)
    assert set(timeframes.SECONDS).isdisjoint(timeframes.MINUTE_AND_ABOVE)
    assert len(timeframes.SECONDS) + len(timeframes.MINUTE_AND_ABOVE) == len(TF)
    assert all(tf.is_seconds for tf in timeframes.SECONDS)
    assert not TF.M1.is_seconds


def test_seconds_floor_and_end() -> None:
    """Seconds bars align to whole multiples of their length."""
    ts = _ms(2026, 11, 22, 10, 0, 17) + 450
    assert TF.S1.floor(ts) == ts - 450
    assert TF.S15.floor(ts) == _ms(2026, 11, 22, 10, 0, 15)
    assert TF.S30.end_of(TF.S30.floor(ts)) == _ms(2026, 11, 22, 10, 0, 30)
    assert TF.M1.contains(TF.S5)
    assert TF.S30.contains(TF.S5)
    assert not TF.S15.contains(TF.S30)


@pytest.mark.parametrize(
    ("moment", "start", "end"),
    [
        (_ms(2024, 2, 29, 23, 59, 59), _ms(2024, 2, 1), _ms(2024, 3, 1)),
        (_ms(2023, 2, 14), _ms(2023, 2, 1), _ms(2023, 3, 1)),
        (_ms(2026, 12, 31, 12), _ms(2026, 12, 1), _ms(2027, 1, 1)),
        (_ms(2026, 1, 1), _ms(2026, 1, 1), _ms(2026, 2, 1)),
        (0, 0, _ms(1970, 2, 1)),
    ],
)
def test_month_floor_and_end(moment: int, start: int, end: int) -> None:
    """Months start on the 1st 00:00 UTC and last 28-31 days."""
    assert TF.MN1.floor(moment) == start
    assert TF.MN1.end_of(start) == end
    assert TF.MN1.is_aligned(start)
    assert not TF.MN1.is_aligned(start + 1)


def test_month_shift_and_count_cross_years() -> None:
    """Shifting and counting follow the calendar, not 31-day steps."""
    nov = _ms(2026, 11, 1)
    assert TF.MN1.shift(nov, 2) == _ms(2027, 1, 1)
    assert TF.MN1.shift(nov, -11) == _ms(2025, 12, 1)
    assert TF.MN1.bars_between(_ms(2025, 12, 1), nov) == 11
    assert TF.MN1.bars_between(nov, nov + 1) == 1
    assert TF.MN1.bars_between(nov, nov) == 0
    assert TF.MN1.is_calendar
    assert not TF.W1.is_calendar


def test_month_nests_days_but_not_weeks() -> None:
    """Days tile a month exactly; weeks do not."""
    assert TF.MN1.contains(TF.D1)
    assert TF.MN1.contains(TF.H4)
    assert not TF.MN1.contains(TF.W1)
    assert TF.MN1.duration_ms > TF.W1.duration_ms


def test_last_closed_month() -> None:
    """The previous month becomes visible exactly at the month boundary."""
    assert timeframes.last_closed_start(TF.MN1, _ms(2026, 3, 1)) == (
        _ms(2026, 2, 1)
    )
    assert timeframes.last_closed_start(TF.MN1, _ms(2026, 3, 1) - 1) == (
        _ms(2026, 1, 1)
    )
    assert timeframes.last_closed_start(TF.MN1, 0) is None


@hypothesis.given(
    tf=strategies.sampled_from(list(TF)),
    ts=strategies.integers(min_value=0, max_value=_ms(2100, 1, 1)),
    bars=strategies.integers(min_value=-50, max_value=50),
)
def test_boundary_identities(
    tf: timeframes.Timeframe, ts: int, bars: int
) -> None:
    """floor/shift/end_of/bars_between agree for every timeframe."""
    start = tf.floor(ts)
    assert start <= ts < tf.end_of(start)
    assert tf.is_aligned(start)
    moved = tf.shift(start, bars)
    assert tf.is_aligned(moved)
    assert tf.shift(moved, -bars) == start
    if bars >= 0:
        assert tf.bars_between(start, moved) == bars
    assert tf.end_of(start) - start <= tf.duration_ms


def _candle(
    timeframe: timeframes.Timeframe,
    start_ms: int,
    end_ms: int,
    taker: str | None = None,
) -> market.Candle:
    """Builds a flat final candle with volume 3.

    Args:
        timeframe: Bar resolution.
        start_ms: Bar start, epoch ms.
        end_ms: Bar end, epoch ms.
        taker: Taker buy base volume as a decimal string, or None for none.

    Returns:
        The candle.

    Raises:
        ValueError: The bounds or the taker volume are invalid.
    """
    return market.Candle(
        instrument_id=instruments.InstrumentId.parse("mock:perp:BTCUSD"),
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=decimal.Decimal(1),
        high=decimal.Decimal(1),
        low=decimal.Decimal(1),
        close=decimal.Decimal(1),
        base_volume=decimal.Decimal(3),
        is_final=True,
        source="test",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=0,
        taker_buy_base_volume=None if taker is None else decimal.Decimal(taker),
    )


def test_monthly_candle_validates_calendar_bounds() -> None:
    """A 1M candle must span exactly one calendar month."""
    candle = _candle(TF.MN1, _ms(2024, 2, 1), _ms(2024, 3, 1))
    assert candle.to_contract()["timeframe"] == "1M"
    with pytest.raises(ValueError, match="end_ms"):
        _candle(TF.MN1, _ms(2024, 2, 1), _ms(2024, 3, 2))


def test_taker_buy_volume_is_bounded_by_volume() -> None:
    """Taker buy volume must lie within [0, base_volume]."""
    candle = _candle(TF.S5, 5_000, 10_000, taker="2")
    assert candle.to_contract()["taker_buy_base_volume"] == "2"
    assert (
        _candle(TF.S5, 5_000, 10_000).to_contract()["taker_buy_base_volume"]
        is None
    )
    with pytest.raises(ValueError, match="taker"):
        _candle(TF.S5, 5_000, 10_000, taker="4")
