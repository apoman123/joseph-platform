# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for recorded-trade coverage, archived seconds and true delta."""

from __future__ import annotations

import decimal
import json
import pathlib

import pytest

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import bitget
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_TF = timeframes.Timeframe
_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("bitget:perp:BTCUSDT")
_T0 = 1_795_349_040_000
"""A minute boundary (2026-11-22)."""
_BUY = market.AggressorSide.BUY
_SELL = market.AggressorSide.SELL


def _trade(
    ts: int,
    price: str,
    qty: str,
    side: market.AggressorSide = _BUY,
    trade_id: str | None = None,
) -> market.Trade:
    """Builds a trade.

    Args:
        ts: Venue time.
        price: Price.
        qty: Quantity.
        side: Aggressor side.
        trade_id: Optional id (defaults to the time).

    Returns:
        Trade.
    """
    return market.Trade(
        instrument_id=_ID,
        trade_id=trade_id or f"{ts}-{price}-{qty}",
        venue_event_ts_ms=ts,
        received_at_ms=ts,
        price=_D(price),
        base_quantity=_D(qty),
        aggressor_side=side,
        is_aggregated=False,
    )


def _second(
    start: int, price: str, qty: str = "1", taker: str | None = "1"
) -> market.Candle:
    """Builds an archived 1s aggregate.

    Args:
        start: Second start.
        price: Single price.
        qty: Volume.
        taker: Taker buy volume.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=_TF.S1,
        start_ms=start,
        end_ms=start + 1000,
        open=_D(price),
        high=_D(price),
        low=_D(price),
        close=_D(price),
        base_volume=_D(qty),
        is_final=True,
        source="bitget.trades",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=start + 1000,
        quote_turnover=_D(price) * _D(qty),
        trade_count=1,
        taker_buy_base_volume=None if taker is None else _D(taker),
    )


def _minute(volume: str, final: bool = True) -> market.Candle:
    """Builds a 1m kline without taker volume.

    Args:
        volume: Base volume.
        final: Finality.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=_TF.M1,
        start_ms=_T0,
        end_ms=_T0 + 60_000,
        open=_D(10),
        high=_D(11),
        low=_D(9),
        close=_D(10),
        base_volume=_D(volume),
        is_final=final,
        source="bitget.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=_T0 + 60_000,
    )


def test_interval_helpers() -> None:
    """Rounding, union, containment, holes and clipping of coverage."""
    assert recorded_trades.ceil_to(1_001, 1_000) == 2_000
    assert recorded_trades.floor_to(1_999, 1_000) == 1_000
    assert recorded_trades.whole_seconds(1_001, 3_500) == (2_000, 3_000)
    assert recorded_trades.whole_seconds(1_001, 2_500) is None
    merged = recorded_trades.merge([(5, 7), (0, 2), (2, 3), (4, 4), (6, 9)])
    assert merged == [(0, 3), (5, 9)]
    assert recorded_trades.covering(merged, 0, 3)
    assert recorded_trades.covering(merged, 6, 9)
    assert not recorded_trades.covering(merged, 2, 6)
    assert not recorded_trades.covering(merged, -1, 1)
    assert recorded_trades.uncovered(merged, -2, 12) == [
        (-2, 0),
        (3, 5),
        (9, 12),
    ]
    assert recorded_trades.uncovered(merged, 0, 3) == []
    assert recorded_trades.uncovered([(0, 1)], 5, 6) == [(5, 6)]
    assert recorded_trades.uncovered([(10, 20)], 0, 5) == [(0, 5)]
    assert recorded_trades.clip(merged, 6) == [(6, 9)]


def test_rebuild_seconds_fills_only_inside_coverage() -> None:
    """Empty seconds after a recorded trade are flat; gaps stay gaps."""
    seconds = [_second(_T0, "10"), _second(_T0 + 3_000, "12")]
    coverage = [(_T0, _T0 + 5_000), (_T0 + 9_000, _T0 + 11_000)]
    bars = recorded_trades.rebuild_seconds(
        seconds,
        coverage,
        _TF.S1,
        _T0 - 2_000,
        _T0 + 12_000,
        previous=None,
        now_ms=0,
    )
    assert [b.start_ms - _T0 for b in bars] == [0, 1_000, 2_000, 3_000, 4_000]
    flat = bars[1]
    assert (flat.open, flat.base_volume, flat.trade_count) == (_D(10), 0, 0)
    assert flat.taker_buy_base_volume == 0
    assert bars[4].close == _D(12)
    assert all(b.is_final for b in bars)


def test_rebuild_seconds_folds_wider_buckets_and_uses_previous() -> None:
    """5s buckets fold 1s aggregates; a prior aggregate seeds flat bars."""
    seconds = [
        _second(_T0 + 6_000, "11", "2", "1"),
        _second(_T0 + 7_000, "13", "1", None),
    ]
    coverage = [(_T0 - 5_000, _T0 + 13_000)]
    previous = _second(_T0 - 4_000, "9")
    bars = recorded_trades.rebuild_seconds(
        seconds,
        coverage,
        _TF.S5,
        _T0 + 1,
        _T0 + 13_000,
        previous=previous,
        now_ms=7,
    )
    assert [b.start_ms - _T0 for b in bars] == [5_000]
    folded = bars[0]
    assert (folded.open, folded.close, folded.high, folded.low) == (
        _D(11),
        _D(13),
        _D(13),
        _D(11),
    )
    assert folded.base_volume == _D(3)
    assert folded.taker_buy_base_volume is None
    early = recorded_trades.rebuild_seconds(
        [],
        coverage,
        _TF.S5,
        _T0,
        _T0 + 5_000,
        previous=previous,
        now_ms=7,
    )
    assert [(b.start_ms, b.close) for b in early] == [(_T0, _D(9))]
    stale = recorded_trades.rebuild_seconds(
        [],
        [(_T0, _T0 + 5_000)],
        _TF.S5,
        _T0,
        _T0 + 5_000,
        previous=previous,
        now_ms=7,
    )
    assert stale == []
    with pytest.raises(ValueError, match="seconds"):
        recorded_trades.rebuild_seconds(
            [], [], _TF.M1, 0, 1, previous=None, now_ms=0
        )


def test_true_delta_needs_full_coverage_and_matching_volume() -> None:
    """Only a fully covered bar whose volume matches gets a true delta."""
    seconds = [
        _second(_T0 + 1_000, "10", "2", "2"),
        _second(_T0 + 30_000, "10", "3", "1"),
    ]
    flows = recorded_trades.minute_flows(seconds)
    assert flows == [recorded_trades.MinuteFlow(_T0, _D(5), _D(3))]
    full = [(_T0 - 1_000, _T0 + 61_000)]
    flow = recorded_trades.span_flow(flows, full, _T0, _T0 + 60_000)
    assert flow == recorded_trades.RecordedFlow(_D(5), _D(3))
    enriched = recorded_trades.with_true_delta(_minute("5"), flow)
    assert enriched.taker_buy_base_volume == _D(3)
    assert enriched.taker_buy_method is market.BuyVolumeMethod.TAKER
    partial = [(_T0 + 1_000, _T0 + 61_000)]
    assert recorded_trades.span_flow(flows, partial, _T0, _T0 + 60_000) is None
    bar = _minute("5")
    assert recorded_trades.with_true_delta(bar, None) is bar
    assert (
        recorded_trades.with_true_delta(
            _minute("6"), flow
        ).taker_buy_base_volume
        is None
    )
    live = _minute("5", final=False)
    assert recorded_trades.with_true_delta(live, flow) is live
    assert recorded_trades.span_flow(flows, full, _T0 + 1, _T0 + 60_000) is None


def test_unknown_aggressor_side_blocks_the_delta() -> None:
    """A recorded trade without a published side voids the span's delta."""
    flows = recorded_trades.minute_flows(
        [_second(_T0, "10"), _second(_T0 + 1_000, "10", taker=None)]
    )
    assert flows[0].taker_buy_base_volume is None
    span = recorded_trades.span_flow(
        flows, [(_T0, _T0 + 60_000)], _T0, _T0 + 60_000
    )
    assert span is None


def test_volumes_agree_within_float_rounding() -> None:
    """Venue float artifacts pass; a missing trade does not."""
    assert recorded_trades.volumes_agree(_D("10.92266"), _D("10.922659999999"))
    assert not recorded_trades.volumes_agree(_D("10.9"), _D("10.92266"))
    assert recorded_trades.volumes_agree(_D(0), _D(0))


def test_archivable_seconds_from_recorded_bitget_fills() -> None:
    """Recorded fills fold into complete 1s aggregates with true sides."""
    path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "fixtures"
        / "venues"
        / "bitget"
        / "fills_history.json"
    )
    rows = json.loads(path.read_text())["data"]
    trades = bitget.sort_fills([bitget.parse_fill(r, _ID, 0) for r in rows])
    first = trades[0].venue_event_ts_ms
    last = trades[-1].venue_event_ts_ms
    coverage = trades_lib.TradeCoverage(tuple(trades), first, last + 2_000)
    bars, window = recorded_trades.archivable_seconds(
        coverage, _ID, source="bitget.trades.rest", now_ms=1
    )
    assert window == recorded_trades.whole_seconds(first, last + 2_000)
    assert bars
    assert all(recorded_trades.archivable(b) for b in bars)
    assert all(window[0] <= b.start_ms < window[1] for b in bars)
    inside = [t for t in trades if t.venue_event_ts_ms >= window[0]]
    assert sum(b.base_volume for b in bars) == sum(
        t.base_quantity for t in inside
    )
    assert sum(b.taker_buy_base_volume or 0 for b in bars) == sum(
        t.base_quantity for t in inside if t.aggressor_side is _BUY
    )
    empty = trades_lib.TradeCoverage((), first, first + 10)
    assert recorded_trades.archivable_seconds(
        empty, _ID, source="x", now_ms=0
    ) == ([], None)


def test_flat_and_live_bars_are_not_archived() -> None:
    """Only final traded seconds are stored as aggregates."""
    builder = trade_bars.TradeBarBuilder(_ID, _TF.S1, source="x")
    builder.start_segment(_T0)
    bars = builder.add(
        [
            _trade(_T0 + 100, "10", "1"),
            _trade(_T0 + 3_100, "11", "2", _SELL),
        ],
        _T0 + 4_000,
    )
    stored = [b for b in bars if recorded_trades.archivable(b)]
    assert [b.start_ms - _T0 for b in stored] == [0]
    assert any(b.base_volume == 0 for b in bars)
