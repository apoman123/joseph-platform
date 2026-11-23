"""Unit tests for session calendars and session-aware bar boundaries."""

from __future__ import annotations

import datetime
import decimal
import importlib.util
import json
import pathlib
import zoneinfo
from typing import Any

import hypothesis
import pytest
from hypothesis import strategies as st

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes

TF = timeframes.Timeframe
NY = zoneinfo.ZoneInfo("America/New_York")
TPE = zoneinfo.ZoneInfo("Asia/Taipei")
D = datetime.date


def _ny(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _tpe(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> int:
    """Returns UTC epoch ms of a Taipei wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=TPE)
    return int(moment.timestamp() * 1000)


XNYS = sessions.XNYS
XTAI = sessions.XTAI


@pytest.mark.parametrize(
    "day",
    [
        D(2026, 1, 1),
        D(2026, 1, 19),
        D(2026, 2, 16),
        D(2026, 4, 3),
        D(2026, 5, 25),
        D(2026, 6, 19),
        D(2026, 7, 3),
        D(2026, 9, 7),
        D(2026, 11, 26),
        D(2026, 12, 25),
        D(2027, 3, 26),
        D(2027, 6, 18),
        D(2027, 7, 5),
        D(2027, 12, 24),
        D(2025, 1, 9),
        D(2012, 10, 29),
    ],
)
def test_nyse_holidays_have_no_session(day: datetime.date) -> None:
    """Published NYSE closures (2026/2027 schedules, ad hoc days)."""
    assert XNYS.session(day) is None


def test_saturday_new_year_is_not_observed_on_friday() -> None:
    """NYSE traded on Friday 2021-12-31 (New Year's Day was a Saturday)."""
    assert XNYS.session(D(2021, 12, 31)) is not None


@pytest.mark.parametrize(
    ("day", "close_hour"),
    [
        (D(2026, 11, 27), 13),
        (D(2026, 12, 24), 13),
        (D(2025, 7, 3), 13),
        (D(2026, 11, 25), 16),
    ],
)
def test_early_closes(day: datetime.date, close_hour: int) -> None:
    """Day after Thanksgiving, Christmas Eve and July 3 close at 13:00."""
    found = XNYS.session(day)
    assert found is not None
    assert found.close_ms == _ny(day.year, day.month, day.day, close_hour)


def test_daylight_saving_moves_the_utc_open() -> None:
    """The 09:30 open is 14:30 UTC in winter and 13:30 UTC in summer."""
    winter = XNYS.session(D(2026, 3, 6))
    summer = XNYS.session(D(2026, 3, 9))
    assert winter is not None
    assert summer is not None
    hour = 3_600_000
    assert winter.open_ms % (24 * hour) == 14 * hour + 30 * 60_000
    assert summer.open_ms % (24 * hour) == 13 * hour + 30 * 60_000


def test_hourly_bars_start_at_the_open_and_end_at_the_close() -> None:
    """1h bars run 09:30, 10:30 ... 15:30-16:00 (TradingView RTH)."""
    assert XNYS.floor(TF.H1, _ny(2026, 11, 23, 10, 15)) == _ny(
        2026, 11, 23, 9, 30
    )
    last = _ny(2026, 11, 23, 15, 30)
    assert XNYS.end_of(TF.H1, last) == _ny(2026, 11, 23, 16)
    assert (
        XNYS.bars_between(
            TF.H1, _ny(2026, 11, 23, 9, 30), _ny(2026, 11, 23, 16)
        )
        == 7
    )
    assert (
        XNYS.bars_between(
            TF.H1, _ny(2026, 11, 27, 9, 30), _ny(2026, 11, 27, 16)
        )
        == 4
    )


def test_four_hour_bars_split_the_session() -> None:
    """4h bars are 09:30-13:30 and 13:30-16:00."""
    assert XNYS.floor(TF.H4, _ny(2026, 11, 23, 14)) == _ny(2026, 11, 23, 13, 30)
    assert XNYS.end_of(TF.H4, _ny(2026, 11, 23, 13, 30)) == _ny(
        2026, 11, 23, 16
    )


def test_floor_between_sessions_is_the_last_bar_before() -> None:
    """Saturday floors to Friday's last bar; no bar spans the weekend."""
    saturday = _ny(2026, 11, 21, 12)
    assert XNYS.floor(TF.H1, saturday) == _ny(2026, 11, 20, 15, 30)
    assert XNYS.floor(TF.D1, saturday) == _ny(2026, 11, 20, 9, 30)
    assert not XNYS.is_open(saturday)
    assert XNYS.is_open(_ny(2026, 11, 20, 15, 59))


def test_shift_skips_weekends_and_holidays() -> None:
    """Friday 15:30 + 1 bar is Tuesday 09:30 when Monday is MLK Day."""
    friday_last = _ny(2026, 1, 16, 15, 30)
    tuesday_open = _ny(2026, 1, 20, 9, 30)
    assert XNYS.shift(TF.H1, friday_last, 1) == tuesday_open
    assert XNYS.shift(TF.H1, tuesday_open, -1) == friday_last
    # A bar end (the close) counts from the next bar start.
    assert XNYS.shift(TF.H1, _ny(2026, 1, 16, 16), 0) == tuesday_open


def test_periodic_bars() -> None:
    """Daily = session, weekly = ISO week, monthly = calendar month."""
    tuesday = _ny(2026, 11, 24, 11)
    assert XNYS.floor(TF.D1, tuesday) == _ny(2026, 11, 24, 9, 30)
    assert XNYS.end_of(TF.D1, _ny(2026, 11, 24, 9, 30)) == _ny(2026, 11, 24, 16)
    week = XNYS.floor(TF.W1, tuesday)
    assert week == _ny(2026, 11, 23, 9, 30)
    # Thanksgiving week ends with Friday's early close.
    assert XNYS.end_of(TF.W1, week) == _ny(2026, 11, 27, 13)
    month = XNYS.floor(TF.MN1, tuesday)
    assert month == _ny(2026, 11, 2, 9, 30)
    assert XNYS.end_of(TF.MN1, month) == _ny(2026, 11, 30, 16)
    assert XNYS.shift(TF.MN1, month, 1) == _ny(2026, 12, 1, 9, 30)
    # January 2027 starts on Monday the 4th (New Year's Day is a Friday).
    assert XNYS.shift(TF.MN1, month, 2) == _ny(2027, 1, 4, 9, 30)
    assert XNYS.bars_between(TF.D1, month, XNYS.end_of(TF.MN1, month)) == 20


def test_last_closed_start_respects_sessions() -> None:
    """Monday morning sees Friday's daily bar; Friday's close sees it too."""
    friday_open = _ny(2026, 11, 20, 9, 30)
    assert XNYS.last_closed_start(TF.D1, _ny(2026, 11, 23, 10)) == friday_open
    assert XNYS.last_closed_start(TF.D1, _ny(2026, 11, 20, 16)) == friday_open
    assert XNYS.last_closed_start(TF.D1, _ny(2026, 11, 20, 15)) == _ny(
        2026, 11, 19, 9, 30
    )


def test_nesting_rules() -> None:
    """Intraday bars nest by divisibility; anything intraday nests in 1d."""
    assert XNYS.contains(TF.H4, TF.H1)
    assert XNYS.contains(TF.D1, TF.H4)
    assert XNYS.contains(TF.D1, TF.H12)
    assert XNYS.contains(TF.W1, TF.D1)
    assert XNYS.contains(TF.MN1, TF.D1)
    assert not XNYS.contains(TF.MN1, TF.W1)
    assert not XNYS.contains(TF.H1, TF.H4)
    assert not sessions.CONTINUOUS.contains(TF.D1, TF.W1)


def test_taiwan_session_and_closing_auction() -> None:
    """TWSE bars start at 09:00; the 13:30 auction print joins the last bar."""
    assert XTAI.floor(TF.H1, _tpe(2026, 11, 23, 13, 10)) == _tpe(
        2026, 11, 23, 13
    )
    assert XTAI.end_of(TF.H1, _tpe(2026, 11, 23, 13)) == _tpe(
        2026, 11, 23, 13, 30
    )
    auction = _tpe(2026, 11, 23, 13, 30)
    assert XTAI.floor(TF.M1, auction) == _tpe(2026, 11, 23, 13, 29)
    assert (
        XTAI.bars_between(TF.H1, _tpe(2026, 11, 23, 9), _tpe(2026, 11, 23, 14))
        == 5
    )


def test_taiwan_closures_come_from_twse_data() -> None:
    """Observed and published TWSE closures (2024 typhoon, 2027 new year)."""
    assert XTAI.session(D(2024, 10, 31)) is None  # Typhoon Kong-rey.
    assert XTAI.session(D(2024, 10, 30)) is not None
    for day in (2, 3, 4, 5, 8, 9, 10):
        assert XTAI.session(D(2027, 2, day)) is None
    assert XTAI.session(D(2027, 2, 1)) is not None


def test_runtime_closures_are_applied() -> None:
    """An announced closure removes the session."""
    calendar = sessions.ExchangeCalendar(
        sessions.CalendarId.XTAI,
        "Asia/Taipei",
        datetime.time(9),
        datetime.time(13, 30),
        is_closed=lambda day: False,
        first_day=D(2026, 1, 1),
    )
    assert calendar.session(D(2026, 11, 24)) is not None
    calendar.add_closures([D(2026, 11, 24)])
    assert calendar.session(D(2026, 11, 24)) is None


def _table(name: str) -> dict[str, Any]:
    """Reads a generated calendar table.

    Args:
        name: File stem in ``calendar_data``.

    Returns:
        Parsed table.
    """
    path = pathlib.Path(sessions.__file__).with_name("calendar_data")
    table: dict[str, Any] = json.loads(
        (path / f"{name}.json").read_text(encoding="utf-8")
    )
    return table


@pytest.mark.parametrize(
    ("day", "published"),
    [
        (D(2026, 4, 3), True),
        (D(2021, 4, 2), True),
        (D(2015, 4, 3), True),
        (D(2024, 3, 29), False),
        (D(2025, 4, 18), False),
    ],
)
def test_bond_good_fridays_follow_treasury_publications(
    day: datetime.date, published: bool
) -> None:
    """Good Friday is a session only in years Treasury published yields."""
    assert (sessions.USBOND.session(day) is not None) is published
    assert (day.isoformat() not in _table("usbond")["closures"]) is published


@pytest.mark.parametrize(
    "day",
    [
        D(2026, 10, 12),
        D(2026, 11, 11),
        D(2025, 10, 13),
        D(2025, 11, 11),
        D(2010, 10, 11),  # Listed by Treasury with blank values only.
        D(2027, 10, 11),
        D(2027, 11, 11),
    ],
)
def test_bond_market_closes_columbus_and_veterans_day(
    day: datetime.date,
) -> None:
    """Columbus and Veterans Day close the bond market, not NYSE."""
    assert sessions.USBOND.session(day) is None
    assert XNYS.session(day) is not None


def test_bond_calendar_is_the_observed_publication_days() -> None:
    """MLK Day closed before 1998; NYSE special closures only if unpublished."""
    bond = sessions.USBOND
    assert bond.session(D(1997, 1, 20)) is None  # MLK, NYSE was open.
    assert XNYS.session(D(1997, 1, 20)) is not None
    assert bond.session(D(2001, 9, 11)) is None
    assert bond.session(D(2012, 10, 30)) is None
    for published in (D(2001, 9, 13), D(2012, 10, 29), D(2025, 1, 9)):
        assert bond.session(published) is not None
        assert XNYS.session(published) is None
    assert bond.session(D(2023, 11, 10)) is not None  # Veterans on a Sat.
    assert bond.session(D(2026, 11, 20)) is not None
    assert bond.first_day == D(1990, 1, 1)
    assert bond.session(D(1990, 1, 1)) is None
    assert bond.session(D(1990, 1, 2)) is not None


def test_bond_table_has_the_xtai_layout() -> None:
    """The USBOND table carries the XTAI keys and only weekday closures."""
    table = _table("usbond")
    assert set(table) == set(_table("xtai"))
    assert table["weekend_sessions"] == []
    closures = [D.fromisoformat(day) for day in table["closures"]]
    assert closures == sorted(closures)
    assert all(day.weekday() < 5 for day in closures)
    through = D.fromisoformat(str(table["observed_through"]))
    assert all(day <= through for day in closures)


def test_bond_calendar_falls_back_to_rules_after_the_table() -> None:
    """After the observed range rule holidays apply; Good Friday trades."""
    bond = sessions.USBOND
    good_friday = D(2027, 3, 26)
    assert XNYS.session(good_friday) is None
    assert bond.session(good_friday) is not None
    for holiday in (D(2027, 1, 18), D(2027, 7, 5), D(2027, 12, 24)):
        assert bond.session(holiday) is None
    assert bond.session(D(2027, 11, 12)) is not None


def test_usbond_generator_counts_only_rows_with_values() -> None:
    """A Treasury row of blank or N/A cells is not a publication day."""
    script = (
        pathlib.Path(__file__).resolve().parents[3]
        / "scripts"
        / "generate_usbond_calendar.py"
    )
    spec = importlib.util.spec_from_file_location("usbond_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = (
        '\ufeffDate,"3 Mo","10 Yr"\n'
        "10/12/2010,0.14,2.44\n"
        "10/11/2010,,\n"
        "10/08/2010,N/A,2.41\n"
        "10/07/2010,N/A,N/A\n"
        "\n"
    )
    assert module.parse_dates(body) == {D(2010, 10, 12), D(2010, 10, 8)}
    with pytest.raises(ValueError, match="header"):
        module.parse_dates("<html>403</html>")


def test_calendar_lookup_by_instrument() -> None:
    """Exchanges map to their calendars; crypto stays continuous."""
    parse = instruments.InstrumentId.parse
    assert sessions.for_instrument(parse("twse:stock:2330")) is XTAI
    assert sessions.for_instrument(parse("nasdaq:etf:TLT")) is XNYS
    assert sessions.for_instrument(parse("nyse:stock:IBM")) is XNYS
    assert sessions.for_instrument(parse("cboe:etf:GOVT")) is XNYS
    assert (
        sessions.for_instrument(parse("treasury:rate:UST10Y"))
        is sessions.USBOND
    )
    assert (
        sessions.for_instrument(parse("binance:perp:BTCUSDT"))
        is sessions.CONTINUOUS
    )
    assert sessions.get("XNYS") is XNYS
    # DEMO stocks follow NYSE so session paths run without network access.
    assert sessions.for_instrument(parse("mock:stock:ACME")) is XNYS
    assert (
        sessions.for_instrument(parse("mock:perp:BTCUSDT"))
        is sessions.CONTINUOUS
    )


def test_sessions_listing() -> None:
    """Sessions overlapping a window, in order."""
    listed = XNYS.sessions(_ny(2026, 11, 20, 12), _ny(2026, 11, 24))
    assert [s.day for s in listed] == [D(2026, 11, 20), D(2026, 11, 23)]
    assert listed[0].to_contract()["day"] == "2026-11-20"


def test_candles_validate_against_the_calendar() -> None:
    """An equity 1h bar starts at the session grid and ends at the close."""
    iid = instruments.InstrumentId.parse("nasdaq:stock:AAPL")

    def candle(start_ms: int, end_ms: int) -> market.Candle:
        """Builds a flat equity bar.

        Args:
            start_ms: Bar start.
            end_ms: Bar end.

        Returns:
            The candle.
        """
        price = decimal.Decimal(1)
        return market.Candle(
            instrument_id=iid,
            timeframe=TF.H1,
            start_ms=start_ms,
            end_ms=end_ms,
            open=price,
            high=price,
            low=price,
            close=price,
            base_volume=price,
            is_final=True,
            source="test",
            feed_state=quality.FeedState.LIVE,
            received_at_ms=end_ms,
        )

    candle(_ny(2026, 11, 23, 15, 30), _ny(2026, 11, 23, 16))
    with pytest.raises(ValueError, match="aligned"):
        candle(_ny(2026, 11, 23, 15), _ny(2026, 11, 23, 16))
    with pytest.raises(ValueError, match="bar end"):
        candle(_ny(2026, 11, 23, 15, 30), _ny(2026, 11, 23, 16, 30))


_RANGE = st.integers(min_value=_ny(2015, 1, 1), max_value=_ny(2026, 12, 1))
_INTRADAY = st.sampled_from(
    [TF.M1, TF.M5, TF.M30, TF.H1, TF.H4, TF.D1, TF.W1, TF.MN1]
)
_CALENDARS = st.sampled_from([XNYS, XTAI, sessions.USBOND])


@hypothesis.settings(max_examples=300, deadline=None)
@hypothesis.given(_CALENDARS, _INTRADAY, _RANGE, st.integers(-40, 40))
def test_boundary_arithmetic_is_consistent(
    calendar: sessions.ExchangeCalendar, tf: TF, ts: int, bars: int
) -> None:
    """floor/shift/end_of/bars_between agree on every calendar."""
    start = calendar.floor(tf, ts)
    assert start <= ts
    assert calendar.is_aligned(tf, start)
    end = calendar.end_of(tf, start)
    assert end > start
    following = calendar.shift(tf, start, 1)
    assert following >= end
    assert calendar.floor(tf, following) == following
    moved = calendar.shift(tf, start, bars)
    assert calendar.shift(tf, moved, -bars) == start
    if bars >= 0:
        assert calendar.bars_between(tf, start, moved) == bars
    else:
        assert calendar.bars_between(tf, moved, start) == -bars


def test_times_before_a_calendar_have_no_bar() -> None:
    """Before the first session nothing is aligned, closed or open."""
    for calendar in (XNYS, sessions.USBOND):
        assert not calendar.is_aligned(TF.M1, 0)
        assert calendar.last_closed_start(TF.D1, 0) is None
        assert not calendar.is_open(0)
    assert sessions.CONTINUOUS.is_open(0)
