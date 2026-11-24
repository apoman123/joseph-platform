# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for calendar-month boundaries and shared adapter helpers."""

from __future__ import annotations

import datetime
import decimal

import pytest

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import ws
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import codec
from joseph.market_data.domain import trades as trades_lib

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_MN1 = timeframes.Timeframe.MN1
_D = decimal.Decimal


def _ms(year: int, month: int, day: int = 1) -> int:
    """Returns UTC midnight of a date in epoch milliseconds.

    Args:
        year: Year.
        month: Month (1-12).
        day: Day of the month.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, tzinfo=datetime.UTC)
    return int(moment.timestamp()) * 1000


def _month_row(start_ms: int) -> list[object]:
    """Builds a Binance 1M kline row.

    Args:
        start_ms: Month start in epoch milliseconds.

    Returns:
        Row with taker buy volume at index 9.
    """
    return [
        start_ms,
        "100",
        "110",
        "90",
        "105",
        "1000",
        _MN1.end_of(start_ms) - 1,
        "100000",
        42,
        "600",
        "60000",
        "0",
    ]


@pytest.mark.parametrize(
    ("year", "month", "days"),
    [(2028, 2, 29), (2026, 2, 28), (2026, 4, 30), (2026, 12, 31)],
)
def test_monthly_kline_spans_its_calendar_month(
    year: int, month: int, days: int
) -> None:
    """Leap Februaries, short months and year ends get exact bounds."""
    start = _ms(year, month)
    candle = binance.parse_kline_row(_month_row(start), _ID, _MN1, start)
    assert candle.end_ms - candle.start_ms == days * 86_400_000
    assert candle.taker_buy_base_volume == _D(600)
    assert not candle.is_final


def test_unaligned_month_is_refused() -> None:
    """A "month" not starting on the 1st 00:00 UTC is never relabelled."""
    with pytest.raises(errors.UnsupportedError):
        base.make_candle(
            instrument_id=_ID,
            timeframe=_MN1,
            start_ms=_ms(2026, 11, 3),
            open_=_D(1),
            high=_D(1),
            low=_D(1),
            close=_D(1),
            base_volume=_D(1),
            now_ms=0,
            source="x",
        )


def test_make_candle_clamps_taker_volume_into_range() -> None:
    """Inconsistent venue taker volume is clamped, like high/low."""
    candle = base.make_candle(
        instrument_id=_ID,
        timeframe=timeframes.Timeframe.M1,
        start_ms=60_000,
        open_=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(2),
        now_ms=200_000,
        source="x",
        taker_buy_base_volume=_D(3),
    )
    assert candle.taker_buy_base_volume == _D(2)


def test_window_helpers_use_calendar_math() -> None:
    """Newest-window starts and first starts step by real months."""
    end = _ms(2026, 11, 22)
    assert base.newest_window_start(_MN1, 0, end, 3) == _ms(2026, 9)
    assert base.newest_window_start(_MN1, _ms(2026, 10, 5), end, 3) == _ms(
        2026, 10
    )
    assert base.first_start(_MN1, _ms(2026, 2, 15)) == _ms(2026, 3)
    assert base.first_start(_MN1, _ms(2026, 3)) == _ms(2026, 3)


def test_find_gaps_counts_calendar_months() -> None:
    """Gap detection steps month by month across a leap year."""
    present = [_ms(2028, 1), _ms(2028, 4)]
    gaps = candle_rules.find_gaps(present, _MN1, _ms(2028, 1), _ms(2028, 6))
    assert gaps == [(_ms(2028, 2), _ms(2028, 4)), (_ms(2028, 5), _ms(2028, 6))]


def test_find_gaps_is_linear_in_present_bars() -> None:
    """A decade of 1s slots is checked without walking every slot."""
    tf = timeframes.Timeframe.S1
    start = 1_500_000_000_000
    gaps = candle_rules.find_gaps(
        [start + 1_000], tf, start, start + 10 * 365 * 86_400_000
    )
    assert gaps[0] == (start, start + 1_000)
    assert gaps[1][0] == start + 2_000


def _trade(ts: int) -> market.Trade:
    """Builds a one-unit taker-buy trade whose id is its timestamp.

    Args:
        ts: Venue and receive time in epoch milliseconds.

    Returns:
        The trade.
    """
    return market.Trade(
        _ID,
        str(ts),
        ts,
        ts,
        _D(1),
        _D(1),
        market.AggressorSide.BUY,
        False,
    )


async def test_suffix_chunks_keep_the_newest_complete_trades() -> None:
    """Forward-paging venues return a complete suffix under a page cap."""
    end = 100_000
    calls: list[tuple[int, int]] = []

    async def page(start: int, stop: int) -> trades_lib.TradeCoverage:
        """Returns one trade per 1,000 ms, at most three per page.

        Args:
            start: Window start in epoch milliseconds, inclusive.
            stop: Window end in epoch milliseconds, exclusive.

        Returns:
            Coverage of the window's first three trades, complete only when
            the window holds no more than three.
        """
        calls.append((start, stop))
        rows = [_trade(t) for t in range(start - start % 1_000, stop, 1_000)]
        rows = [t for t in rows if t.venue_event_ts_ms >= start]
        return trades_lib.coverage_from_prefix(
            rows[:3], start, stop, complete=len(rows) <= 3
        )

    coverage = await base.suffix_by_chunks(
        page, 0, end, max_pages=4, first_window_ms=2_000
    )
    assert coverage.to_ms == end
    assert coverage.from_ms > 0
    times = [t.venue_event_ts_ms for t in coverage.trades]
    assert times == list(range(coverage.from_ms, end, 1_000))
    assert len(calls) == 4


def test_ws_trade_spec_defaults_prove_delivery_often() -> None:
    """Trade streams ping often enough for quiet bars to finalize."""
    spec = ws.WsTradeSpec(url="wss://x", subscribe=(), parse=lambda r, n: [])
    assert spec.ping_interval_s <= 5.0
    assert not spec.replays_recent


def test_month_candle_contract_round_trip() -> None:
    """A month candle with taker volume survives the contract codec."""
    start = _ms(2026, 11)
    candle = binance.parse_kline_row(_month_row(start), _ID, _MN1, start)
    decoded = codec.candle_from_contract(
        candle.to_contract(), feed_state=quality.FeedState.LIVE
    )
    assert decoded.end_ms == _ms(2026, 12)
    assert decoded.taker_buy_base_volume == _D(600)
