"""Unit tests for session-aligned candles, aggregation and US tape bars."""

from __future__ import annotations

import datetime
import decimal

import pytest

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import session_bars
from joseph.market_data.domain import us_trade_bars

_D = decimal.Decimal
_TF = timeframes.Timeframe
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_MSFT = instruments.InstrumentId.parse("nasdaq:stock:MSFT")
_SESSION = sessions.XNYS.session(datetime.date(2026, 11, 20))
assert _SESSION is not None
_OPEN = _SESSION.open_ms
_BUY = market.AggressorSide.BUY
_SELL = market.AggressorSide.SELL


def _minute(
    offset: int,
    close: str,
    *,
    final: bool = True,
    count: int | None = 5,
    taker: str | None = "1",
    instrument: instruments.InstrumentId = _AAPL,
) -> market.Candle:
    """Builds a regular-session 1m bar.

    Args:
        offset: Minutes after the open.
        close: Close.
        final: Finality.
        count: Trade count.
        taker: Buy volume.
        instrument: Instrument.

    Returns:
        Candle.
    """
    return session_bars.candle(
        instrument_id=instrument,
        timeframe=_TF.M1,
        start_ms=_OPEN + offset * 60_000,
        open_=_D("10"),
        high=_D(close),
        low=_D("9"),
        close=_D(close),
        base_volume=_D(2),
        now_ms=_OPEN + 3_600_000,
        source="test",
        is_final=final,
        trade_count=count,
        quote_turnover=_D(20),
        taker_buy_base_volume=None if taker is None else _D(taker),
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
    )


def test_candle_widens_clamps_and_uses_the_calendar() -> None:
    """Session candles end where the calendar says and stay consistent."""
    bar = session_bars.candle(
        instrument_id=_AAPL,
        timeframe=_TF.H1,
        start_ms=_OPEN + 6 * 3_600_000,
        open_=_D(5),
        high=_D(4),
        low=_D(6),
        close=_D(5),
        base_volume=_D(-1),
        now_ms=_OPEN,
        source="test",
        taker_buy_base_volume=_D(3),
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
    )
    assert bar.end_ms == _SESSION.close_ms
    assert (bar.high, bar.low, bar.base_volume) == (_D(5), _D(5), _D(0))
    assert bar.taker_buy_base_volume == _D(0)
    assert not bar.is_final
    plain = session_bars.candle(
        instrument_id=_AAPL,
        timeframe=_TF.D1,
        start_ms=_OPEN,
        open_=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        now_ms=_SESSION.close_ms,
        source="test",
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
    )
    assert plain.is_final and plain.taker_buy_method is None


def test_candle_rejects_unaligned_starts() -> None:
    """A start off the session grid fails validation."""
    with pytest.raises(ValueError, match="aligned"):
        session_bars.candle(
            instrument_id=_AAPL,
            timeframe=_TF.H1,
            start_ms=_OPEN + 60_000,
            open_=_D(1),
            high=_D(1),
            low=_D(1),
            close=_D(1),
            base_volume=_D(1),
            now_ms=_OPEN,
            source="test",
        )


def test_aggregate_sums_onto_the_session_grid() -> None:
    """Five minutes make one 5m bar; the next is open and not final."""
    bars = [_minute(i, str(11 + i)) for i in range(7)]
    rows = session_bars.aggregate(
        bars, _TF.M5, now_ms=_OPEN + 540_000, covered_to_ms=_OPEN + 540_000
    )
    assert [r.start_ms for r in rows] == [_OPEN, _OPEN + 300_000]
    first, second = rows
    assert (first.open, first.high, first.close) == (_D(10), _D(15), _D(15))
    assert first.base_volume == _D(10)
    assert first.trade_count == 25
    assert first.quote_turnover == _D(100)
    assert first.taker_buy_base_volume == _D(5)
    assert first.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
    assert first.is_final
    assert not second.is_final


def test_aggregate_finality_needs_coverage_and_final_parts() -> None:
    """Uncovered or non-final constituents keep the aggregate open."""
    bars = [_minute(i, "11", final=i != 4) for i in range(5)]
    later = _OPEN + 3_600_000
    open_part = session_bars.aggregate(
        bars, _TF.M5, now_ms=later, covered_to_ms=later
    )
    assert not open_part[0].is_final
    bars = [_minute(i, "11") for i in range(5)]
    uncovered = session_bars.aggregate(
        bars, _TF.M5, now_ms=later, covered_to_ms=_OPEN
    )
    assert not uncovered[0].is_final


def test_aggregate_drops_partial_optional_sums() -> None:
    """Missing counts or buy volumes make the aggregate sum unknown."""
    bars = [_minute(0, "11", count=None, taker=None), _minute(1, "12")]
    row = session_bars.aggregate(
        bars, _TF.M5, now_ms=_OPEN, covered_to_ms=_OPEN
    )[0]
    assert row.trade_count is None
    assert row.taker_buy_base_volume is None


def test_aggregate_validates_its_input() -> None:
    """Mixed series and non-tiling timeframes are rejected."""
    assert session_bars.aggregate([], _TF.M5, now_ms=0, covered_to_ms=0) == []
    with pytest.raises(ValueError, match="one series"):
        session_bars.aggregate(
            [_minute(0, "11"), _minute(1, "11", instrument=_MSFT)],
            _TF.M5,
            now_ms=_OPEN,
            covered_to_ms=_OPEN,
        )
    three = session_bars.candle(
        instrument_id=_AAPL,
        timeframe=_TF.M3,
        start_ms=_OPEN,
        open_=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        now_ms=_OPEN,
        source="test",
    )
    with pytest.raises(ValueError, match="tile"):
        session_bars.aggregate(
            [three], _TF.M5, now_ms=_OPEN, covered_to_ms=_OPEN
        )


def _tape(
    trade_id: str,
    offset_ms: int,
    price: str,
    size: int,
    conditions: tuple[str, ...] = ("@",),
    side: market.AggressorSide = _BUY,
) -> us_trade_bars.TapeTrade:
    """Builds a tape-C trade relative to the open.

    Args:
        trade_id: Id.
        offset_ms: Milliseconds after the open.
        price: Price.
        size: Shares.
        conditions: Sale conditions.
        side: Classified side.

    Returns:
        Trade.
    """
    return us_trade_bars.TapeTrade(
        trade_id=trade_id,
        ts_ms=_OPEN + offset_ms,
        price=_D(price),
        size=_D(size),
        conditions=conditions,
        tape="C",
        side=side,
    )


@pytest.mark.parametrize(
    ("conditions", "tape", "expected"),
    [
        (("@",), "C", (True, True, True)),
        (("@", "I"), "C", (False, False, True)),
        (("B",), "A", (False, False, True)),
        (("B",), "C", (True, True, True)),
        (("M",), "C", (False, False, False)),
        (("@", "4", "I"), "C", (False, False, True)),
        (("?",), "C", (True, True, True)),
        ((), None, (True, True, True)),
    ],
)
def test_update_rules_follow_the_condition_table(
    conditions: tuple[str, ...],
    tape: str | None,
    expected: tuple[bool, bool, bool],
) -> None:
    """The strictest documented rule applies; unknown codes are regular."""
    rule = us_trade_bars.update_rule(conditions, tape)
    assert (rule.open_close, rule.high_low, rule.volume) == expected


def test_seconds_bars_apply_conditions_and_classified_volume() -> None:
    """Odd lots add volume only; buy volume is classified."""
    trades = [
        _tape("1", 0, "10.00", 100),
        _tape("2", 400, "10.50", 5, ("@", "I")),
        _tape("3", 1_100, "10.20", 200),
        _tape("4", 1_900, "10.10", 100, side=_SELL),
        _tape("5", 2_300, "10.10", 50, ("@", "I"), side=_SELL),
        _tape("6", 3_200, "10.30", 10, ("@",), market.AggressorSide.UNKNOWN),
        _tape("7", 3_300, "10.40", 10, ("M",)),
    ]
    bars = us_trade_bars.bars_from_trades(
        trades,
        _AAPL,
        _TF.S1,
        from_ms=_OPEN,
        to_ms=_OPEN + 5_000,
        source="test",
        now_ms=_OPEN + 5_000,
    )
    by_start = {(b.start_ms - _OPEN) // 1000: b for b in bars}
    assert sorted(by_start) == [0, 1, 3]
    zero = by_start[0]
    assert (zero.open, zero.high, zero.close) == (_D(10), _D(10), _D(10))
    assert zero.base_volume == _D(105)
    assert zero.taker_buy_base_volume == _D(105)
    assert zero.trade_count == 2
    one = by_start[1]
    assert (one.open, one.high, one.low, one.close) == (
        _D("10.2"),
        _D("10.2"),
        _D("10.1"),
        _D("10.1"),
    )
    assert one.taker_buy_base_volume == _D(200)
    assert by_start[3].taker_buy_base_volume is None
    assert all(b.is_final for b in bars)
    assert all(
        b.buy_volume_method is market.BuyVolumeMethod.CLASSIFIED
        for b in bars
        if b.taker_buy_base_volume is not None
    )


def test_seconds_builder_segments_sessions_and_duplicates() -> None:
    """Outside-session, duplicate and pre-coverage trades are skipped."""
    builder = us_trade_bars.SecondsBarBuilder(
        _AAPL, _TF.S5, source="test", retention_bars=2
    )
    builder.start_segment(None)
    pre = _tape("0", -60_000, "9.00", 100)
    first = _tape("1", 2_000, "10.00", 100)
    assert builder.add([pre, first], _OPEN) == []
    assert builder.outside_session == 1
    out = builder.add([_tape("2", 6_000, "10.10", 100), first], _OPEN)
    assert builder.duplicates == 1
    assert [c.start_ms for c in out] == [_OPEN + 5_000]
    assert not out[0].is_final
    finals = builder.finalize_through(_OPEN + 10_000, _OPEN + 10_000)
    assert [c.is_final for c in finals] == [True]
    builder.add(
        [_tape(str(i), 10_000 + i * 5_000, "10.2", 1) for i in range(3, 8)],
        _OPEN,
    )
    assert len(builder._starts) == 2
    builder.start_segment(_OPEN + 61_500)
    assert builder.add([_tape("x", 61_000, "11", 1)], _OPEN) == []
    with pytest.raises(ValueError, match="seconds"):
        us_trade_bars.SecondsBarBuilder(_AAPL, _TF.M1, source="test")
