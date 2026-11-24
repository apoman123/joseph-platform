# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for session-aligned candles, aggregation and US tape bars."""

from __future__ import annotations

import dataclasses
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


def _trading_session(
    calendar: sessions.ExchangeCalendar, day: datetime.date
) -> sessions.Session:
    """Returns the session on a day the calendar trades.

    Args:
        calendar: Exchange calendar.
        day: Local trading day.

    Returns:
        The session; the day must not be a closure.
    """
    session = calendar.session(day)
    assert session is not None
    return session


_SESSION = _trading_session(sessions.XNYS, datetime.date(2026, 11, 20))
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
        close: High and close as a decimal string.
        final: Whether the bar is final.
        count: Trade count, or None when unknown.
        taker: Classified buy volume, or None when unknown.
        instrument: Instrument.

    Returns:
        A bar that opens at 10 with low 9, volume 2 and turnover 20.
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
        trade_id: Trade id.
        offset_ms: Milliseconds after the open.
        price: Price as a decimal string.
        size: Shares.
        conditions: Sale condition codes.
        side: Classified side.

    Returns:
        The tape-C trade.
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


# Closing print -------------------------------------------------------------

_CLOSE = _SESSION.close_ms
_EARLY = _trading_session(sessions.XNYS, datetime.date(2026, 11, 27))
_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")


def _print(
    trade_id: str,
    ts_ms: int,
    price: str,
    size: int,
    conditions: tuple[str, ...],
) -> us_trade_bars.TapeTrade:
    """Builds a tape-C trade at an absolute time.

    Args:
        trade_id: Trade id.
        ts_ms: Trade time in epoch milliseconds.
        price: Price as a decimal string.
        size: Shares.
        conditions: Sale condition codes.

    Returns:
        The tape-C trade, classified as a buy.
    """
    return us_trade_bars.TapeTrade(
        trade_id=trade_id,
        ts_ms=ts_ms,
        price=_D(price),
        size=_D(size),
        conditions=conditions,
        tape="C",
        side=_BUY,
    )


def test_closing_print_condition_is_the_tables_closing_trade() -> None:
    """Condition 6 is the market center closing trade, updating everything."""
    assert us_trade_bars.CLOSING_TRADE == "6"
    rule = us_trade_bars.update_rule((us_trade_bars.CLOSING_TRADE,), "A")
    assert (rule.open_close, rule.high_low, rule.volume) == (True, True, True)
    assert us_trade_bars.CLOSING_WINDOW_MS == 60_000


def test_closing_print_joins_the_last_seconds_bar() -> None:
    """The closing cross after 16:00 sets the last bar; Form T does not."""
    builder = us_trade_bars.SecondsBarBuilder(_AAPL, _TF.S1, source="test")
    builder.start_segment(_CLOSE - 5_000)
    last = _CLOSE - 1_000
    out = builder.add(
        [
            _print("r", _CLOSE - 500, "10.00", 100, ("@",)),
            _print("x", _CLOSE + 400, "10.05", 5_000, ("@", "6")),
            _print("t", _CLOSE + 5_000, "10.20", 50, ("@", "T")),
            _print("late", _CLOSE + 61_000, "10.30", 70, ("6",)),
        ],
        _CLOSE + 61_000,
    )
    assert builder.outside_session == 2  # Form T and the late print.
    bar = out[-1]
    assert bar.start_ms == last and bar.end_ms == _CLOSE
    assert (bar.open, bar.high, bar.low, bar.close) == (
        _D("10.00"),
        _D("10.05"),
        _D("10.00"),
        _D("10.05"),
    )
    assert bar.base_volume == _D(5_100) and bar.trade_count == 2
    assert not bar.is_final
    # The last bar settles only once its closing window was delivered.
    assert builder.finalize_through(_CLOSE + 59_999, _CLOSE + 60_000) == []
    final = builder.finalize_through(_CLOSE + 60_000, _CLOSE + 60_000)
    assert [b.start_ms for b in final] == [last] and final[0].is_final
    assert builder.coverage(_CLOSE + 30_000) == (_CLOSE - 5_000, last)
    assert builder.coverage(_CLOSE + 60_000) == (
        _CLOSE - 5_000,
        _CLOSE + 60_000,
    )


def test_closing_print_on_an_early_close_day() -> None:
    """On a 13:00 close the window is 13:00-13:01 and joins the 12:59 bar."""
    builder = us_trade_bars.SecondsBarBuilder(_AAPL, _TF.S5, source="test")
    close = _EARLY.close_ms
    builder.start_segment(close - 10_000)
    bars = builder.add(
        [
            _print("r", close - 3_000, "20.00", 10, ("@",)),
            _print("x", close + 300, "20.40", 900, ("6",)),
            _print("t", close + 1_000, "21.00", 10, ("T",)),
        ],
        close + 2_000,
    )
    assert [b.start_ms for b in bars] == [close - 5_000]
    assert bars[0].close == _D("20.40") and bars[0].base_volume == _D(910)
    assert us_trade_bars.closing_window_end(_AAPL, close) == close + 60_000
    assert (
        us_trade_bars.settled_until(_AAPL, _TF.S5, close + 1) == close - 5_000
    )


def test_bars_from_trades_fold_the_closing_print_when_read() -> None:
    """A REST window read through the closing window gets the print."""
    trades = [
        _print("r", _CLOSE - 1_500, "10.00", 100, ("@",)),
        _print("x", _CLOSE + 800, "10.10", 4_000, ("@", "6")),
        _print("t", _CLOSE + 2_000, "10.50", 10, ("@", "T")),
    ]
    to_ms = us_trade_bars.closing_window_end(_AAPL, _CLOSE)
    bars = us_trade_bars.bars_from_trades(
        trades,
        _AAPL,
        _TF.S1,
        from_ms=_CLOSE - 2_000,
        to_ms=to_ms,
        source="test",
        now_ms=to_ms,
    )
    assert [b.start_ms for b in bars] == [_CLOSE - 2_000, _CLOSE - 1_000]
    assert bars[-1].close == _D("10.10") and bars[-1].is_final
    assert bars[-1].base_volume == _D(4_000)
    # Read only up to the close, the print is missing and nothing settles.
    early = us_trade_bars.bars_from_trades(
        trades,
        _AAPL,
        _TF.S1,
        from_ms=_CLOSE - 2_000,
        to_ms=_CLOSE,
        source="test",
        now_ms=_CLOSE,
    )
    assert early[-1].start_ms == _CLOSE - 2_000 and early[-1].is_final


def test_closing_window_helpers_leave_other_times_alone() -> None:
    """Outside closing windows and on 24x7 venues nothing moves."""
    window = us_trade_bars.closing_window_end
    settled = us_trade_bars.settled_until
    assert window(_AAPL, _CLOSE - 1) == _CLOSE - 1
    assert window(_AAPL, _CLOSE + 60_000) == _CLOSE + 60_000
    assert window(_AAPL, _CLOSE + 59_999) == _CLOSE + 60_000
    assert window(_BTC, _CLOSE) == _CLOSE
    assert window(_AAPL, 0) == 0  # Before the calendar's first session.
    assert settled(_AAPL, _TF.S1, _CLOSE + 10_000) == _CLOSE - 1_000
    assert settled(_AAPL, _TF.S1, _CLOSE + 60_000) == _CLOSE + 60_000
    assert settled(_BTC, _TF.S1, _CLOSE + 10_000) == _CLOSE + 10_000


def test_seconds_builder_coverage_starts_after_the_partial_bar() -> None:
    """Nothing is covered before the first trade, nor its partial bar."""
    builder = us_trade_bars.SecondsBarBuilder(_AAPL, _TF.S1, source="test")
    builder.start_segment(None)
    assert builder.coverage(_OPEN + 10_000) is None
    builder.add([_tape("1", 2_500, "10", 1)], _OPEN + 3_000)
    assert builder.coverage(_OPEN + 10_000) == (_OPEN + 3_000, _OPEN + 10_000)
    assert builder.coverage(_OPEN + 3_000) is None
    builder.start_segment(_OPEN + 20_000)
    assert builder.coverage(_OPEN + 25_000) == (_OPEN + 20_000, _OPEN + 25_000)


# Closing prints of minute bars ---------------------------------------------

_LAST_MINUTE = _CLOSE - 60_000


def _last_minute(*, final: bool = True) -> market.Candle:
    """Builds the session's last 1m bar as Alpaca reports it.

    Args:
        final: Whether the bar is final.

    Returns:
        The 15:59 bar without the closing print.
    """
    return dataclasses.replace(
        _minute(389, "10.00", final=final, count=4),
        open=_D("10.02"),
        high=_D("10.05"),
        low=_D("9.98"),
        base_volume=_D(300),
    )


def test_closing_prints_keep_condition_6_inside_the_window() -> None:
    """Only condition-6 trades of [close, close + 60 s) count, in order."""
    prints = us_trade_bars.closing_prints(
        [
            _print("late", _CLOSE + 1_500, "10.10", 70, ("@", "6")),
            _print("cross", _CLOSE + 4, "10.10", 9_000, ("@", "6")),
            _print("t", _CLOSE + 300, "10.40", 50, ("@", "T")),
            _print("before", _CLOSE - 1, "10.00", 10, ("@", "6")),
            _print("after", _CLOSE + 60_000, "10.20", 10, ("@", "6")),
        ],
        _CLOSE,
    )
    assert [p.trade_id for p in prints] == ["cross", "late"]
    assert us_trade_bars.closing_slot(_AAPL, _CLOSE) == (_LAST_MINUTE, _CLOSE)
    early = _EARLY.close_ms
    assert us_trade_bars.closing_slot(_AAPL, early) == (early - 60_000, early)


def test_fold_closing_prints_follows_the_update_rules() -> None:
    """The cross sets the close; an odd-lot print adds volume only."""
    bar = _last_minute()
    folded = us_trade_bars.fold_closing_prints(
        bar,
        [
            _print("cross", _CLOSE + 4, "10.10", 9_000, ("@", "6")),
            _print("odd", _CLOSE + 900, "10.90", 30, ("@", "6", "I")),
        ],
        instrument_id=_AAPL,
        timeframe=_TF.M1,
        start_ms=_LAST_MINUTE,
        now_ms=_CLOSE + 70_000,
        source="other",
        is_final=True,
    )
    assert folded is not None and folded.is_final
    assert (folded.open, folded.high, folded.low, folded.close) == (
        _D("10.02"),
        _D("10.10"),
        _D("9.98"),
        _D("10.10"),
    )
    assert folded.base_volume == _D(9_330) and folded.trade_count == 6
    assert folded.event_ts_ms == _CLOSE + 900
    assert folded.source == "test" and folded.end_ms == _CLOSE
    # Unclassified prints leave no buy volume or turnover to report.
    assert folded.taker_buy_base_volume is None
    assert folded.quote_turnover is None


def _fold(
    bar: market.Candle | None, prints: list[us_trade_bars.TapeTrade]
) -> market.Candle | None:
    """Folds closing prints into the regular session's last minute.

    Args:
        bar: The last minute's bar, or None for a quiet minute.
        prints: Closing prints to fold.

    Returns:
        The folded bar, not final; None when nothing sets a price.
    """
    return us_trade_bars.fold_closing_prints(
        bar,
        prints,
        instrument_id=_AAPL,
        timeframe=_TF.M1,
        start_ms=_LAST_MINUTE,
        now_ms=_CLOSE + 70_000,
        source="alpaca.rest",
        is_final=False,
    )


def test_fold_closing_prints_without_a_bar_or_prints() -> None:
    """A quiet last minute gets a bar from a price print, else nothing."""
    cross = _print("cross", _CLOSE + 4, "10.10", 9_000, ("@", "6"))
    odd = _print("odd", _CLOSE + 900, "10.90", 30, ("@", "6", "I"))
    created = _fold(None, [cross, odd])
    assert created is not None and not created.is_final
    assert created.open == created.high == created.low == created.close
    assert created.close == _D("10.10") and created.base_volume == _D(9_030)
    assert created.source == "alpaca.rest" and created.trade_count == 2
    assert _fold(None, [odd]) is None
    assert _fold(None, []) is None
    bar = _last_minute()
    unchanged = _fold(bar, [])
    assert unchanged == dataclasses.replace(bar, is_final=False)


def test_with_closing_prints_folds_known_and_opens_pending() -> None:
    """Known prints fold into last minutes; pending ones stay open."""
    early_bar = session_bars.candle(
        instrument_id=_AAPL,
        timeframe=_TF.M1,
        start_ms=_EARLY.close_ms - 60_000,
        open_=_D("20"),
        high=_D("20"),
        low=_D("20"),
        close=_D("20"),
        base_volume=_D(5),
        now_ms=_EARLY.close_ms + 3_600_000,
        source="test",
    )
    other = _minute(10, "10.00")
    rows = us_trade_bars.with_closing_prints(
        [early_bar, _last_minute(), other],
        _AAPL,
        prints={
            _CLOSE: (_print("x", _CLOSE + 4, "10.10", 9_000, ("@", "6")),),
        },
        pending=[_EARLY.close_ms],
        now_ms=_CLOSE + 70_000,
        settled_before_ms=_CLOSE + 10_000,
        source="test",
    )
    assert [r.start_ms for r in rows] == [
        other.start_ms,
        _LAST_MINUTE,
        early_bar.start_ms,
    ]
    assert rows[0] == other
    assert rows[1].close == _D("10.10") and rows[1].is_final
    assert not rows[2].is_final and rows[2].close == _D("20")
    # A quiet last minute is created final once its close has settled.
    created = us_trade_bars.with_closing_prints(
        [],
        _AAPL,
        prints={_CLOSE: (_print("x", _CLOSE + 4, "10.10", 1, ("6",)),)},
        pending=(),
        now_ms=_CLOSE + 70_000,
        settled_before_ms=_CLOSE,
        source="test",
    )
    assert [(c.start_ms, c.is_final) for c in created] == [(_LAST_MINUTE, True)]
