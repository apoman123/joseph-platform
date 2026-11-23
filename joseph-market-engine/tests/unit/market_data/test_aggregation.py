"""Unit tests for bars aggregated from a shorter native timeframe."""

from __future__ import annotations

import dataclasses
import decimal
import json
import pathlib

import pytest

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import mexc
from joseph.market_data.domain import aggregation

_TF = timeframes.Timeframe
_D = decimal.Decimal
_UTC = sessions.get(sessions.CalendarId.CONTINUOUS)
_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_HL = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_DAY = 86_400_000
_NOV = 1_793_491_200_000
"""2026-11-01 00:00 UTC."""
_CAPTURED = 1_795_400_000_000
"""Shortly after the 1d/1M fixtures were captured (2026-11-23)."""
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures"


def _rows(name: str) -> list[list[object]]:
    """Loads a Binance kline fixture.

    Args:
        name: File name without extension.

    Returns:
        Kline rows.
    """
    path = _FIXTURES / "venues" / "binance" / f"{name}.json"
    rows: list[list[object]] = json.loads(path.read_text())
    return rows


def _klines(name: str, tf: timeframes.Timeframe) -> list[market.Candle]:
    """Parses a Binance kline fixture.

    Args:
        name: File name without extension.
        tf: Timeframe of the rows.

    Returns:
        Candles.
    """
    return [
        binance.parse_kline_row(row, _BTC, tf, _CAPTURED) for row in _rows(name)
    ]


def _day(
    start: int,
    *,
    final: bool = True,
    taker: str | None = "1",
    method: market.BuyVolumeMethod | None = None,
    price: str = "10",
    instrument: instruments.InstrumentId = _HL,
    tf: timeframes.Timeframe = _TF.D1,
    received: int = 0,
) -> market.Candle:
    """Builds a child bar.

    Args:
        start: Bar start.
        final: Finality.
        taker: Taker buy volume.
        method: Buy-volume method.
        price: Open/close price.
        instrument: Instrument.
        tf: Child timeframe.
        received: Receipt time.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=instrument,
        timeframe=tf,
        start_ms=start,
        end_ms=tf.end_of(start),
        open=_D(price),
        high=_D(price) + 1,
        low=_D(price) - 1,
        close=_D(price),
        base_volume=_D(2),
        is_final=final,
        source="hyperliquid.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=received,
        quote_turnover=_D(20),
        trade_count=3,
        event_ts_ms=start + 5,
        taker_buy_base_volume=None if taker is None else _D(taker),
        taker_buy_method=method,
    )


def test_aggregated_1m_equals_binance_native_1m_on_fixture() -> None:
    """Folding Binance 1d klines reproduces its native 1M klines exactly."""
    days = _klines("klines_1d_since_2026_06", _TF.D1)
    native = _klines("klines_1M_since_2026_06", _TF.MN1)
    folded = {
        c.start_ms: c
        for c in aggregation.aggregate(
            days, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=_CAPTURED
        )
    }
    closed = [c for c in native if c.is_final]
    assert len(closed) == 5
    for month in closed:
        agg = folded[month.start_ms]
        assert agg.is_final
        assert (agg.start_ms, agg.end_ms) == (month.start_ms, month.end_ms)
        assert (agg.open, agg.high, agg.low, agg.close) == (
            month.open,
            month.high,
            month.low,
            month.close,
        )
        assert agg.base_volume == month.base_volume
        assert agg.quote_turnover == month.quote_turnover
        assert agg.trade_count == month.trade_count
        assert agg.taker_buy_base_volume == month.taker_buy_base_volume
        assert agg.source == "binance.rest.agg1d"
    live = folded[_NOV]
    assert not live.is_final
    assert live.start_ms == _NOV


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        (
            hyperliquid._KLINE_TF,
            {_TF.H6: _TF.H2, _TF.W1: _TF.D1, _TF.MN1: _TF.D1},
        ),
        (
            lighter._KLINE_TF,
            {
                _TF.M3: _TF.M1,
                _TF.H2: _TF.H1,
                _TF.H6: _TF.H1,
                _TF.W1: _TF.D1,
                _TF.MN1: _TF.D1,
            },
        ),
        (
            tuple(mexc._INTERVAL),
            {
                _TF.M3: _TF.M1,
                _TF.H2: _TF.H1,
                _TF.H6: _TF.H1,
                _TF.H12: _TF.H4,
            },
        ),
        (timeframes.MINUTE_AND_ABOVE, {}),
    ],
    ids=["hyperliquid", "lighter", "mexc", "complete"],
)
def test_derived_timeframes_use_the_longest_tiling_child(
    native: tuple[timeframes.Timeframe, ...],
    expected: dict[timeframes.Timeframe, timeframes.Timeframe],
) -> None:
    """Each missing timeframe maps to the longest native child tiling it."""
    assert aggregation.derived_timeframes(_UTC, native) == expected


def test_source_timeframe_ignores_seconds_and_non_tiling() -> None:
    """Seconds and non-tiling timeframes are never aggregation sources."""
    assert aggregation.source_timeframe(_UTC, _TF.MN1, [_TF.W1, _TF.S1]) is None
    assert aggregation.source_timeframe(_UTC, _TF.M5, [_TF.M3]) is None


def test_missing_closed_child_leaves_the_period_a_gap() -> None:
    """A month lacking one closed day is not built (no guess)."""
    days = [_day(_NOV + i * _DAY) for i in range(30) if i != 12]
    now = _NOV + 40 * _DAY
    assert (
        aggregation.aggregate(
            days, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=now
        )
        == []
    )


def test_live_period_is_closed_children_plus_current_child() -> None:
    """The current month folds closed days and today's (missing) bar."""
    now = _NOV + 5 * _DAY + 3_600_000
    closed = [_day(_NOV + i * _DAY) for i in range(5)]
    month = aggregation.aggregate(
        closed, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=now
    )[0]
    assert not month.is_final
    assert month.base_volume == _D(10)
    today = _day(_NOV + 5 * _DAY, final=False, price="12")
    month = aggregation.aggregate(
        [*closed, today],
        calendar=_UTC,
        target=_TF.MN1,
        child=_TF.D1,
        now_ms=now,
    )[0]
    assert month.close == _D(12)
    assert month.high == _D(13)
    assert month.base_volume == _D(12)
    assert month.trade_count == 18
    assert month.taker_buy_base_volume == _D(6)
    assert month.event_ts_ms == today.event_ts_ms


def test_closed_period_with_unconfirmed_child_is_not_final() -> None:
    """A month whose last day is not yet confirmed stays non-final."""
    days = [_day(_NOV + i * _DAY, final=i < 29) for i in range(30)]
    month = aggregation.aggregate(
        days,
        calendar=_UTC,
        target=_TF.MN1,
        child=_TF.D1,
        now_ms=_NOV + 31 * _DAY,
    )[0]
    assert not month.is_final
    final = aggregation.aggregate(
        [*days[:29], dataclasses.replace(days[29], is_final=True)],
        calendar=_UTC,
        target=_TF.MN1,
        child=_TF.D1,
        now_ms=_NOV + 31 * _DAY,
    )[0]
    assert final.is_final
    assert final.end_ms == _NOV + 30 * _DAY


def test_optional_sums_need_every_child() -> None:
    """Taker volume is dropped when one child lacks it or methods differ."""
    now = _NOV + 3 * _DAY
    missing = [_day(_NOV), _day(_NOV + _DAY, taker=None), _day(_NOV + 2 * _DAY)]
    month = aggregation.aggregate(
        missing, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=now
    )[0]
    assert month.taker_buy_base_volume is None
    assert month.taker_buy_method is None
    mixed = [
        _day(_NOV),
        _day(_NOV + _DAY, method=market.BuyVolumeMethod.CLASSIFIED),
        _day(_NOV + 2 * _DAY),
    ]
    month = aggregation.aggregate(
        mixed, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=now
    )[0]
    assert month.taker_buy_base_volume is None
    labelled = [
        _day(_NOV + i * _DAY, method=market.BuyVolumeMethod.TAKER)
        for i in range(2)
    ] + [_day(_NOV + 2 * _DAY)]
    month = aggregation.aggregate(
        labelled, calendar=_UTC, target=_TF.MN1, child=_TF.D1, now_ms=now
    )[0]
    assert month.taker_buy_base_volume == _D(3)
    assert month.taker_buy_method is market.BuyVolumeMethod.TAKER
    no_count = [dataclasses.replace(_day(_NOV), trade_count=None)]
    month = aggregation.aggregate(
        no_count,
        calendar=_UTC,
        target=_TF.MN1,
        child=_TF.D1,
        now_ms=_NOV + 3_600_000,
    )[0]
    assert month.trade_count is None
    assert month.quote_turnover == _D(20)


def test_aggregate_rejects_non_tiling_or_foreign_children() -> None:
    """Weeks do not tile months and children must share one timeframe."""
    with pytest.raises(ValueError, match="does not tile"):
        aggregation.aggregate(
            [], calendar=_UTC, target=_TF.MN1, child=_TF.W1, now_ms=0
        )
    with pytest.raises(ValueError, match="another timeframe"):
        aggregation.aggregate(
            [_day(_NOV, tf=_TF.H1)],
            calendar=_UTC,
            target=_TF.MN1,
            child=_TF.D1,
            now_ms=0,
        )


def test_six_hour_bars_fold_two_hour_children() -> None:
    """Intraday aggregation uses the same rules (6h from 2h)."""
    start = _NOV + 6 * 3_600_000
    children = [
        _day(start + i * 7_200_000, tf=_TF.H2, price=str(10 + i))
        for i in range(3)
    ]
    bar = aggregation.aggregate(
        children,
        calendar=_UTC,
        target=_TF.H6,
        child=_TF.H2,
        now_ms=start + _DAY,
    )[0]
    assert (bar.start_ms, bar.end_ms) == (start, start + 6 * 3_600_000)
    assert (bar.open, bar.close, bar.high, bar.low) == (
        _D(10),
        _D(12),
        _D(13),
        _D(9),
    )
    assert bar.is_final


def test_live_aggregator_streams_the_month_and_rolls_over() -> None:
    """Seeded closed days plus live day updates; the month closes once."""
    live = aggregation.LiveAggregator(_UTC, _TF.MN1, _TF.D1)
    last = _NOV + 29 * _DAY
    seed = [_day(_NOV + i * _DAY) for i in range(29)]
    now = last + 3_600_000
    first = live.update(seed, now)
    assert len(first) == 1
    assert not first[0].is_final
    assert live.update(seed, now) == []
    today = _day(last, final=False, price="11", received=now)
    updated = live.update([today], now)
    assert updated[0].close == _D(11)
    assert live.missing_before(last) == []
    december = _NOV + 30 * _DAY
    assert live.missing_before(december + 2 * _DAY) == [
        december,
        december + _DAY,
    ]
    closed = dataclasses.replace(today, is_final=True, received_at_ms=now + 1)
    rolled = live.update(
        [closed, _day(december, final=False, received=december + 1)],
        december + 1,
    )
    assert [c.start_ms for c in rolled] == [_NOV, december]
    assert rolled[0].is_final
    assert not rolled[1].is_final
    # A child of an evicted older parent is ignored.
    live.update([_day(december + 31 * _DAY, final=False)], december + 32 * _DAY)
    assert live.update([_day(_NOV)], december + 32 * _DAY) == []
    assert live.update([_day(_NOV, tf=_TF.H1)], december + 32 * _DAY) == []


def test_live_aggregator_finalizes_on_time_alone() -> None:
    """A complete month becomes final when time passes its end."""
    live = aggregation.LiveAggregator(_UTC, _TF.MN1, _TF.D1)
    days = [_day(_NOV + i * _DAY) for i in range(30)]
    early = live.update(days, _NOV + 29 * _DAY + 1)
    assert not early[0].is_final
    late = live.update([], _NOV + 31 * _DAY)
    assert late[0].is_final
