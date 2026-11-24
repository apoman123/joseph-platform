# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit and property tests for seconds bars folded from public trades."""

from __future__ import annotations

import decimal
import random

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_S1 = timeframes.Timeframe.S1
_T0 = 1_795_349_070_000
_D = decimal.Decimal
_BUY = market.AggressorSide.BUY
_SELL = market.AggressorSide.SELL


def _trade(
    trade_id: object,
    ts: int,
    price: str,
    qty: str,
    side: market.AggressorSide = _BUY,
    *,
    quote: str | None = None,
    aggregated: bool = False,
) -> market.Trade:
    """Builds a trade of ``_ID``.

    Args:
        trade_id: Venue trade id, converted with ``str``.
        ts: Venue event time in epoch milliseconds, also the receive time.
        price: Price as a decimal string.
        qty: Base quantity as a decimal string.
        side: Aggressor side.
        quote: Quote quantity as a decimal string, or ``None`` when the venue
            sends none.
        aggregated: Whether the venue aggregates fills into this trade.

    Returns:
        The trade.
    """
    return market.Trade(
        instrument_id=_ID,
        trade_id=str(trade_id),
        venue_event_ts_ms=ts,
        received_at_ms=ts,
        price=_D(price),
        base_quantity=_D(qty),
        aggressor_side=side,
        is_aggregated=aggregated,
        quote_quantity=None if quote is None else _D(quote),
    )


def _builder(
    timeframe: timeframes.Timeframe = _S1, retention: int = 300
) -> trade_bars.TradeBarBuilder:
    """Builds a bar builder whose first segment starts at ``_T0``.

    Args:
        timeframe: Seconds timeframe of the bars.
        retention: Bars kept for late trades behind the newest one.

    Returns:
        The builder.
    """
    builder = trade_bars.TradeBarBuilder(
        _ID, timeframe, source="binance.trades", retention_bars=retention
    )
    builder.start_segment(_T0)
    return builder


def _latest(candles: list[market.Candle]) -> dict[int, market.Candle]:
    """Keeps the last emitted observation of each bar.

    Args:
        candles: Emitted candles in order.

    Returns:
        The last observation of each bar, keyed by bar start.
    """
    out: dict[int, market.Candle] = {}
    for candle in candles:
        out[candle.start_ms] = candle
    return out


def test_bar_aggregates_ohlcv_quote_taker_and_count() -> None:
    """One bucket folds OHLC, sums, taker volume and the trade count."""
    builder = _builder()
    rows = builder.add(
        [
            _trade(1, _T0 + 10, "100", "1", _BUY),
            _trade(2, _T0 + 200, "103", "2", _SELL),
            _trade(3, _T0 + 300, "99", "0.5", _BUY, quote="49"),
            _trade(4, _T0 + 900, "101", "1", _SELL),
        ],
        _T0 + 950,
    )
    bar = rows[-1]
    assert (bar.open, bar.high, bar.low, bar.close) == (
        _D(100),
        _D(103),
        _D(99),
        _D(101),
    )
    assert bar.base_volume == _D("4.5")
    assert bar.quote_turnover == _D(100) + _D(206) + _D(49) + _D(101)
    assert bar.taker_buy_base_volume == _D("1.5")
    assert bar.trade_count == 4
    assert bar.event_ts_ms == _T0 + 900
    assert not bar.is_final
    assert bar.source == "binance.trades"


def test_unknown_aggressor_or_aggregation_nulls_fields() -> None:
    """Unknown sides null the taker volume; aggregated fills null the count."""
    builder = _builder()
    bar = builder.add(
        [
            _trade(1, _T0 + 1, "100", "1", _BUY, aggregated=True),
            _trade(2, _T0 + 2, "100", "1", market.AggressorSide.UNKNOWN),
        ],
        _T0 + 3,
    )[-1]
    assert bar.taker_buy_base_volume is None
    assert bar.trade_count is None


def test_later_bucket_trade_finalizes_and_fills_flat_bars() -> None:
    """Empty seconds between two trades become flat zero-volume bars."""
    builder = _builder()
    builder.add([_trade(1, _T0 + 500, "100", "1")], _T0 + 600)
    rows = builder.add([_trade(2, _T0 + 3_200, "105", "2")], _T0 + 3_300)
    by_start = _latest(rows)
    assert by_start[_T0].is_final and by_start[_T0].close == _D(100)
    for start in (_T0 + 1_000, _T0 + 2_000):
        flat = by_start[start]
        assert flat.is_final
        assert flat.base_volume == 0 and flat.trade_count == 0
        assert flat.taker_buy_base_volume == 0
        assert flat.open == flat.high == flat.low == flat.close == _D(100)
    assert not by_start[_T0 + 3_000].is_final


def test_no_fill_before_first_or_after_last_trade() -> None:
    """Coverage alone never creates bars without trades on both sides."""
    builder = _builder()
    rows = builder.add([_trade(1, _T0 + 5_500, "100", "1")], _T0 + 5_600)
    assert [c.start_ms for c in rows] == [_T0 + 5_000]
    later = builder.finalize_through(_T0 + 20_000, _T0 + 20_000)
    assert [c.start_ms for c in later] == [_T0 + 5_000]
    assert later[0].is_final


def test_clock_finalization_needs_proven_delivery() -> None:
    """A quiet bar is final only once delivery is proven past its end."""
    builder = _builder()
    builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 200)
    assert builder.finalize_through(_T0 + 999, _T0 + 2_000) == []
    final = builder.finalize_through(_T0 + 1_000, _T0 + 2_000)
    assert len(final) == 1 and final[0].is_final


def test_duplicates_are_ignored() -> None:
    """A repeated trade id changes nothing and is counted as a duplicate."""
    builder = _builder()
    first = builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 200)
    assert builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 300) == []
    assert builder.duplicates == 1
    assert first[0].base_volume == _D(1)


def test_late_trade_revises_final_bar_and_following_flat_bars() -> None:
    """An out-of-order trade re-emits its final bar and the flat run."""
    builder = _builder()
    builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 200)
    builder.add([_trade(2, _T0 + 3_100, "102", "1")], _T0 + 3_200)
    rows = builder.add([_trade(3, _T0 + 900, "101", "1")], _T0 + 3_300)
    by_start = _latest(rows)
    assert by_start[_T0].is_final
    assert by_start[_T0].close == _D(101)
    assert by_start[_T0].base_volume == _D(2)
    assert by_start[_T0 + 1_000].close == _D(101)
    assert by_start[_T0 + 2_000].close == _D(101)


def test_late_trade_inside_flat_run_becomes_real_bar() -> None:
    """A late trade in an empty second replaces the flat bar."""
    builder = _builder()
    builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 200)
    builder.add([_trade(2, _T0 + 3_100, "102", "1")], _T0 + 3_200)
    rows = _latest(
        builder.add([_trade(3, _T0 + 1_500, "99", "3")], _T0 + 3_300)
    )
    assert rows[_T0 + 1_000].base_volume == _D(3)
    assert rows[_T0 + 1_000].is_final
    assert rows[_T0 + 2_000].close == _D(99)


def test_trades_beyond_retention_or_coverage_are_dropped() -> None:
    """Trades behind the retention or before coverage are dropped, counted."""
    builder = _builder(retention=5)
    builder.add([_trade(1, _T0 + 10_000, "100", "1")], _T0 + 10_100)
    assert builder.add([_trade(2, _T0 + 1_000, "99", "1")], _T0 + 10_200) == []
    assert builder.add([_trade(3, _T0 - 1, "99", "1")], _T0 + 10_300) == []
    assert builder.late_dropped == 2


def test_unknown_coverage_start_drops_partial_first_bucket() -> None:
    """Without a known start the first trade's bucket is never emitted."""
    builder = trade_bars.TradeBarBuilder(_ID, _S1, source="x")
    assert builder.add([_trade(1, _T0 + 400, "100", "1")], _T0 + 500) == []
    rows = builder.add([_trade(2, _T0 + 2_100, "101", "1")], _T0 + 2_200)
    by_start = _latest(rows)
    assert _T0 not in by_start
    assert by_start[_T0 + 1_000].close == _D(100)
    assert by_start[_T0 + 1_000].base_volume == 0
    assert not by_start[_T0 + 2_000].is_final


def test_segment_break_never_bridges_and_discards_open_bar() -> None:
    """Nothing is filled across a disconnect; the open bar is dropped."""
    builder = _builder()
    builder.add([_trade(1, _T0 + 100, "100", "1")], _T0 + 200)
    assert builder.start_segment(None) == 1
    rows = builder.add(
        [
            _trade(2, _T0 + 5_100, "101", "1"),
            _trade(3, _T0 + 7_100, "102", "1"),
        ],
        _T0 + 7_200,
    )
    starts = {c.start_ms for c in rows}
    assert starts == {_T0 + 6_000, _T0 + 7_000}


def test_builder_rejects_bad_inputs() -> None:
    """Minute timeframes, zero retention and foreign trades raise ValueError."""
    with pytest.raises(ValueError, match="seconds"):
        trade_bars.TradeBarBuilder(_ID, timeframes.Timeframe.M1, source="x")
    with pytest.raises(ValueError, match="retention"):
        trade_bars.TradeBarBuilder(_ID, _S1, source="x", retention_bars=0)
    other = market.Trade(
        instruments.InstrumentId.parse("bitget:perp:BTCUSDT"),
        "1",
        _T0,
        _T0,
        _D(1),
        _D(1),
        _BUY,
        False,
    )
    with pytest.raises(ValueError, match="instrument"):
        _builder().add([other], _T0)


def test_bars_from_coverage_finality_and_partial_edges() -> None:
    """Covered bars are final, the end bar open, a partial first bar dropped."""
    trades = (
        _trade(1, _T0 + 1_200, "100", "1"),
        _trade(2, _T0 + 2_500, "101", "1"),
        _trade(3, _T0 + 4_100, "102", "1"),
    )
    rows = trade_bars.bars_from_coverage(
        trades_lib.TradeCoverage(trades, _T0 + 1_000, _T0 + 4_500),
        _ID,
        _S1,
        source="binance.trades.rest",
        now_ms=_T0 + 5_000,
    )
    assert [c.start_ms - _T0 for c in rows] == [1_000, 2_000, 3_000, 4_000]
    assert [c.is_final for c in rows] == [True, True, True, False]
    unaligned = trade_bars.bars_from_coverage(
        trades_lib.TradeCoverage(trades, _T0 + 1_100, _T0 + 4_500),
        _ID,
        _S1,
        source="x",
        now_ms=_T0 + 5_000,
    )
    assert unaligned[0].start_ms == _T0 + 2_000


def test_five_second_bars_bucket_correctly() -> None:
    """Coarser seconds timeframes bucket on their own boundaries."""
    tf = timeframes.Timeframe.S5
    rows = trade_bars.bars_from_coverage(
        trades_lib.TradeCoverage(
            (
                _trade(1, _T0 + 100, "100", "1"),
                _trade(2, _T0 + 4_900, "103", "1"),
                _trade(3, _T0 + 5_000, "101", "2"),
            ),
            _T0,
            _T0 + 10_000,
        ),
        _ID,
        tf,
        source="x",
        now_ms=_T0 + 10_000,
    )
    assert [(c.start_ms - _T0, c.base_volume) for c in rows] == [
        (0, _D(2)),
        (5_000, _D(2)),
    ]
    assert all(c.end_ms - c.start_ms == 5_000 for c in rows)


_PRICES = strategies.integers(min_value=90, max_value=110)
_QTYS = strategies.integers(min_value=1, max_value=50)
_SIDES = strategies.sampled_from([_BUY, _SELL])


@strategies.composite
def _trade_lists(draw: strategies.DrawFn) -> list[market.Trade]:
    """Draws trades with distinct times inside 40 seconds of ``_T0``.

    ``strategies.composite`` turns this into a factory: ``_trade_lists()`` is
    the Hypothesis strategy.

    Args:
        draw: Hypothesis draw function.

    Returns:
        Ascending trades with unique ids.
    """
    offsets = draw(
        strategies.lists(
            strategies.integers(min_value=0, max_value=39_999),
            min_size=1,
            max_size=40,
            unique=True,
        )
    )
    return [
        _trade(
            i,
            _T0 + off,
            str(draw(_PRICES)),
            str(draw(_QTYS)),
            draw(_SIDES),
        )
        for i, off in enumerate(sorted(offsets))
    ]


def _fold(
    trades: list[market.Trade],
    timeframe: timeframes.Timeframe = _S1,
) -> dict[int, market.Candle]:
    """Folds trades with coverage from ``_T0`` and proves delivery to +60s.

    Args:
        trades: Trades in arrival order.
        timeframe: Seconds resolution.

    Returns:
        Final state of every emitted bar.
    """
    builder = _builder(timeframe)
    emitted: list[market.Candle] = []
    for trade in trades:
        emitted.extend(builder.add([trade], trade.venue_event_ts_ms))
    emitted.extend(builder.finalize_through(_T0 + 60_000, _T0 + 60_000))
    return _latest(emitted)


@hypothesis.settings(max_examples=120, deadline=None)
@hypothesis.given(
    _trade_lists(), strategies.sampled_from(list(timeframes.SECONDS))
)
def test_property_volume_and_taker_volume_are_conserved(
    trades: list[market.Trade], timeframe: timeframes.Timeframe
) -> None:
    """Bars hold exactly the traded volume and buyer-aggressor volume."""
    bars = _fold(trades, timeframe)
    assert sum(c.base_volume for c in bars.values()) == sum(
        t.base_quantity for t in trades
    )
    assert sum(c.taker_buy_base_volume or 0 for c in bars.values()) == sum(
        t.base_quantity for t in trades if t.aggressor_side is _BUY
    )
    assert all(c.is_final for c in bars.values())


@hypothesis.settings(max_examples=120, deadline=None)
@hypothesis.given(_trade_lists())
def test_property_ohlc_matches_bucket_trades_and_bars_are_contiguous(
    trades: list[market.Trade],
) -> None:
    """OHLC equal the bucket's trades; no hole between first and last."""
    bars = _fold(trades)
    for start, bar in bars.items():
        inside = [t for t in trades if _S1.floor(t.venue_event_ts_ms) == start]
        if not inside:
            assert bar.base_volume == 0
            assert bar.open == bar.close == bar.high == bar.low
            continue
        prices = [t.price for t in inside]
        assert bar.open == prices[0] and bar.close == prices[-1]
        assert bar.high == max(prices) and bar.low == min(prices)
    first = _S1.floor(trades[0].venue_event_ts_ms)
    last = _S1.floor(trades[-1].venue_event_ts_ms)
    assert sorted(bars) == list(range(first, last + 1, 1_000))


@hypothesis.settings(max_examples=100, deadline=None)
@hypothesis.given(_trade_lists(), strategies.randoms(use_true_random=False))
def test_property_duplicates_and_arrival_order_do_not_change_bars(
    trades: list[market.Trade], rng: random.Random
) -> None:
    """Duplicated, shuffled delivery converges to the in-order bars."""
    noisy = trades + [rng.choice(trades) for _ in range(len(trades))]
    rng.shuffle(noisy)
    expected = _fold(trades)
    got = _fold(noisy)
    assert {
        s: (c.open, c.high, c.low, c.close, c.base_volume)
        for s, c in got.items()
    } == {
        s: (c.open, c.high, c.low, c.close, c.base_volume)
        for s, c in expected.items()
    }


@hypothesis.settings(max_examples=100, deadline=None)
@hypothesis.given(
    _trade_lists(), strategies.integers(min_value=1, max_value=39)
)
def test_property_no_bar_is_built_across_a_coverage_break(
    trades: list[market.Trade], cut_index: int
) -> None:
    """Nothing is built between the old segment and the new one."""
    cut = min(cut_index, len(trades))
    before, after = trades[:cut], trades[cut:]
    builder = _builder()
    emitted = builder.add(before, _T0)
    builder.start_segment(None)
    emitted += builder.add(after, _T0 + 50_000)
    emitted += builder.finalize_through(_T0 + 60_000, _T0 + 60_000)
    starts = {c.start_ms for c in emitted}
    if before and after:
        last_old = _S1.floor(before[-1].venue_event_ts_ms)
        first_new = _S1.floor(after[0].venue_event_ts_ms)
        assert not {s for s in starts if last_old < s <= first_new}
