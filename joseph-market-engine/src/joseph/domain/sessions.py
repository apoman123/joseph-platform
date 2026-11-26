# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Trading-session calendars and session-aware bar boundaries.

Crypto venues trade around the clock, so their bars are aligned to the UTC
epoch (``Timeframe`` arithmetic). Exchange-listed instruments trade in
sessions with holidays, early closes and time zones:

* Intraday bars (seconds to 12h) start at the session open and repeat
  every ``timeframe`` until the close; the last bar of a session is cut
  short at the close (a 1h bar on NYSE opens at 09:30, 10:30, ... 15:30).
* A daily bar is one session ``[open, close)``.
* A weekly bar spans the sessions of one ISO week, and a monthly bar the
  sessions of one calendar month, in the exchange's time zone.

Time between sessions belongs to no bar and is never a gap. All boundary
arithmetic for an instrument must go through its calendar
(``for_instrument``): ``floor``, ``end_of``, ``shift``, ``bars_between`` and
``last_closed_start`` mirror the ``Timeframe`` methods of the same names.

``floor`` of a time between sessions returns the last bar that started
before it, so "the bar at or before t" stays well defined.
"""

from __future__ import annotations

import abc
import dataclasses
import datetime
import enum
import functools
import json
import math
import pathlib
import zoneinfo
from collections.abc import Callable
from collections.abc import Iterable
from typing import TypeAlias
from typing import override

from joseph.domain import instruments
from joseph.domain import timeframes

Timeframe = timeframes.Timeframe

_DATA_DIR = pathlib.Path(__file__).with_name("calendar_data")
# Days searched for a neighbouring session before giving up; far longer than
# any real run of closed days (holidays plus weekends).
_MAX_SCAN_DAYS = 60
_DAY = datetime.timedelta(days=1)
_PERIODIC = (Timeframe.D1, Timeframe.W1, Timeframe.MN1)


class CalendarId(enum.StrEnum):
    """Session calendar identifiers (contract ``CalendarId``)."""

    CONTINUOUS = "24x7"
    """Always open; UTC-aligned bars (crypto venues, DEMO)."""
    XTAI = "XTAI"
    """Taiwan Stock Exchange regular session, 09:00-13:30 Asia/Taipei."""
    XNYS = "XNYS"
    """NYSE and Nasdaq regular session, 09:30-16:00 America/New_York."""
    USBOND = "USBOND"
    """US Treasury market business days, 08:00-17:00 America/New_York."""


@dataclasses.dataclass(frozen=True, slots=True)
class Session:
    """One trading session (contract ``TradingSession``).

    Attributes:
        day: Exchange-local trading date.
        open_ms: Session open, UTC epoch milliseconds.
        close_ms: Exclusive session close, UTC epoch milliseconds.
    """

    day: datetime.date
    open_ms: int
    close_ms: int

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``TradingSession`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "day": self.day.isoformat(),
            "open_ms": self.open_ms,
            "close_ms": self.close_ms,
        }


class BarCalendar(abc.ABC):
    """Bar-boundary arithmetic for one session calendar.

    Attributes:
        calendar_id: Identifier.
        close_grace_ms: Prints up to this long after a session close belong
            to the session's last bar (0 for continuous markets). Bar
            builders use it to accept or drop late prints; the bar
            arithmetic does not (``floor`` maps every time after a close to
            the session's last bar).
    """

    calendar_id: CalendarId
    close_grace_ms: int = 0

    @property
    def continuous(self) -> bool:
        """Whether the market never closes."""
        return self.calendar_id is CalendarId.CONTINUOUS

    def ceil(self, tf: Timeframe, ts_ms: int) -> int:
        """Returns the first bar start at or after ``ts_ms``.

        Args:
            tf: Timeframe.
            ts_ms: UTC epoch milliseconds (any time, also before the
                calendar's first session).

        Returns:
            Bar start.
        """
        return self.shift(tf, ts_ms, 0)

    @abc.abstractmethod
    def floor(self, tf: Timeframe, ts_ms: int) -> int:
        """Returns the start of the bar at or before ``ts_ms``.

        Args:
            tf: Timeframe.
            ts_ms: UTC epoch milliseconds.

        Returns:
            Bar start in UTC epoch milliseconds.

        Raises:
            ValueError: If no session opens at or before ``ts_ms`` (exchange
                calendars only).
        """

    @abc.abstractmethod
    def end_of(self, tf: Timeframe, start_ms: int) -> int:
        """Returns the exclusive end of the bar starting at ``start_ms``.

        Args:
            tf: Timeframe.
            start_ms: Aligned bar start.

        Returns:
            Exclusive bar end.

        Raises:
            ValueError: If no session opens at or before ``start_ms``
                (exchange calendars only).
        """

    @abc.abstractmethod
    def shift(self, tf: Timeframe, start_ms: int, bars: int) -> int:
        """Moves a bar start by a number of bars.

        Args:
            tf: Timeframe.
            start_ms: Bar start; any other time counts from the first bar
                start at or after it (so a bar end works too).
            bars: Bars to move; negative moves back.

        Returns:
            The start of the bar ``bars`` bars away.

        Raises:
            ValueError: If the move leaves the calendar's coverage (exchange
                calendars only).
        """

    @abc.abstractmethod
    def bars_between(self, tf: Timeframe, start_ms: int, end_ms: int) -> int:
        """Counts bar starts in ``[start_ms, end_ms)``.

        Args:
            tf: Timeframe.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Number of bars starting in the interval.
        """

    @abc.abstractmethod
    def is_open(self, ts_ms: int) -> bool:
        """Checks whether the market is in session at ``ts_ms``.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            True during a session.
        """

    @abc.abstractmethod
    def sessions(self, from_ms: int, to_ms: int) -> list[Session]:
        """Lists sessions overlapping ``[from_ms, to_ms)``.

        Args:
            from_ms: Inclusive start.
            to_ms: Exclusive end.

        Returns:
            Sessions in time order (empty for a continuous market).
        """

    @abc.abstractmethod
    def contains(self, big: Timeframe, small: Timeframe) -> bool:
        """Checks whether bars of ``small`` nest exactly in ``big`` bars.

        Args:
            big: Higher timeframe.
            small: Candidate lower timeframe.

        Returns:
            True if every ``small`` bar lies inside one ``big`` bar and they
            tile it.
        """

    def is_aligned(self, tf: Timeframe, ts_ms: int) -> bool:
        """Checks whether ``ts_ms`` is a bar start.

        Args:
            tf: Timeframe.
            ts_ms: UTC epoch milliseconds.

        Returns:
            True when ``ts_ms`` is exactly a bar start.
        """
        try:
            return self.floor(tf, ts_ms) == ts_ms
        except ValueError:
            return False

    def last_closed_start(self, tf: Timeframe, as_of_ms: int) -> int | None:
        """Returns the start of the newest bar fully closed at ``as_of_ms``.

        This is the higher-timeframe visibility rule: a bar is usable only
        once ``as_of_ms >= end``.

        Args:
            tf: Timeframe.
            as_of_ms: Evaluation time.

        Returns:
            Start of the last closed bar, or ``None`` when there is none.
        """
        try:
            start = self.floor(tf, as_of_ms)
            if self.end_of(tf, start) <= as_of_ms:
                return start
            previous = self.shift(tf, start, -1)
        except ValueError:
            return None
        return previous if previous >= 0 else None


class ContinuousCalendar(BarCalendar):
    """Always-open market with UTC-aligned bars (``Timeframe`` rules).

    Attributes:
        calendar_id: Always ``CalendarId.CONTINUOUS``.
    """

    calendar_id = CalendarId.CONTINUOUS

    @override
    def floor(self, tf: Timeframe, ts_ms: int) -> int:
        """See base class."""
        return tf.floor(ts_ms)

    @override
    def end_of(self, tf: Timeframe, start_ms: int) -> int:
        """See base class."""
        return tf.end_of(start_ms)

    @override
    def shift(self, tf: Timeframe, start_ms: int, bars: int) -> int:
        """See base class."""
        return tf.shift(start_ms, bars)

    @override
    def bars_between(self, tf: Timeframe, start_ms: int, end_ms: int) -> int:
        """See base class."""
        return tf.bars_between(start_ms, end_ms)

    @override
    def ceil(self, tf: Timeframe, ts_ms: int) -> int:
        """See base class."""
        start = tf.floor(ts_ms)
        return start if start == ts_ms else tf.end_of(start)

    @override
    def is_open(self, ts_ms: int) -> bool:
        """See base class."""
        del ts_ms  # Unused.
        return True

    @override
    def sessions(self, from_ms: int, to_ms: int) -> list[Session]:
        """See base class."""
        del from_ms, to_ms  # Unused.
        return []

    @override
    def contains(self, big: Timeframe, small: Timeframe) -> bool:
        """See base class."""
        return big.contains(small)

    @override
    def last_closed_start(self, tf: Timeframe, as_of_ms: int) -> int | None:
        """See base class."""
        return timeframes.last_closed_start(tf, as_of_ms)


ClosedDay: TypeAlias = Callable[[datetime.date], bool]
"""Predicate that is True for a weekday without a session."""

EarlyClose: TypeAlias = Callable[[datetime.date], datetime.time | None]
"""Local close time of a shortened session, or ``None`` for a full one."""


def _no_early_close(day: datetime.date) -> datetime.time | None:
    """Reports that no session closes early (the default ``EarlyClose``).

    Args:
        day: Session date.

    Returns:
        Always ``None``.
    """
    del day  # Unused.
    return None


def _bar_count(tf: Timeframe, session: Session) -> int:
    """Counts the intraday bars of a session.

    Args:
        tf: Intraday timeframe.
        session: Session.

    Returns:
        Bar count (the last one may be short).
    """
    length = session.close_ms - session.open_ms
    return -(-length // tf.duration_ms)


class ExchangeCalendar(BarCalendar):
    """Session calendar of an exchange with holidays and early closes.

    Sessions are computed on demand and cached per local date.

    Attributes:
        calendar_id: Identifier.
        tz: Exchange time zone.
        open_time: Regular local open.
        close_time: Regular local close (early closes come from the
            ``early_close`` rule).
        first_day: First local date the calendar covers; earlier dates have
            no session.
        close_grace_ms: Prints up to this long after a session close belong
            to the session's last bar.
    """

    def __init__(
        self,
        calendar_id: CalendarId,
        tz_name: str,
        open_time: datetime.time,
        close_time: datetime.time,
        *,
        is_closed: ClosedDay,
        early_close: EarlyClose | None = None,
        weekend_sessions: Iterable[datetime.date] = (),
        first_day: datetime.date,
        close_grace_ms: int = 0,
    ) -> None:
        """Initializes the calendar.

        Args:
            calendar_id: Identifier.
            tz_name: IANA time zone of the exchange.
            open_time: Regular open (local).
            close_time: Regular close (local).
            is_closed: Returns True for a weekday without a session.
            early_close: Returns the local close of a shortened session.
            weekend_sessions: Saturdays or Sundays that traded.
            first_day: First date the calendar covers.
            close_grace_ms: Prints up to this long after the close (a
                closing auction stamped at the close) belong to the last
                bar of the session.
        """
        self.calendar_id = calendar_id
        self.tz = zoneinfo.ZoneInfo(tz_name)
        self.open_time = open_time
        self.close_time = close_time
        self._is_closed = is_closed
        self._early_close = early_close or _no_early_close
        self._weekend = frozenset(weekend_sessions)
        self.first_day = first_day
        self.close_grace_ms = close_grace_ms
        self._extra_closures: set[datetime.date] = set()
        self._cache: dict[datetime.date, Session | None] = {}

    # Sessions -----------------------------------------------------------

    def add_closures(self, days: Iterable[datetime.date]) -> None:
        """Adds closures announced at runtime (e.g. a typhoon day).

        Args:
            days: Dates without a session.
        """
        self._extra_closures.update(days)
        self._cache.clear()

    def session(self, day: datetime.date) -> Session | None:
        """Returns the session on a local date.

        Args:
            day: Exchange-local date.

        Returns:
            The session, or ``None`` when the market is closed.
        """
        if day in self._cache:
            return self._cache[day]
        result: Session | None = None
        trades = (
            day in self._weekend
            if day.weekday() >= 5
            else not self._is_closed(day)
        )
        if day >= self.first_day and trades and day not in self._extra_closures:
            close_time = self._early_close(day) or self.close_time
            result = Session(
                day,
                self._local_ms(day, self.open_time),
                self._local_ms(day, close_time),
            )
        self._cache[day] = result
        return result

    def _local_ms(self, day: datetime.date, at: datetime.time) -> int:
        """Converts a local wall time to UTC epoch milliseconds.

        Args:
            day: Local date.
            at: Local time.

        Returns:
            UTC epoch milliseconds.
        """
        moment = datetime.datetime.combine(day, at, tzinfo=self.tz)
        return int(moment.timestamp() * 1000)

    def local_day(self, ts_ms: int) -> datetime.date:
        """Returns the exchange-local date of an instant.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            Local date.
        """
        return datetime.datetime.fromtimestamp(ts_ms / 1000, self.tz).date()

    def session_at_or_before(self, ts_ms: int) -> Session:
        """Returns the latest session that opened at or before ``ts_ms``.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            The session.

        Raises:
            ValueError: If ``ts_ms`` precedes the calendar's first session.
        """
        day = self.local_day(ts_ms)
        for _ in range(_MAX_SCAN_DAYS):
            if day < self.first_day:
                break
            found = self.session(day)
            if found is not None and found.open_ms <= ts_ms:
                return found
            day -= _DAY
        raise ValueError(
            f"{self.calendar_id} has no session at or before {ts_ms}"
        )

    def next_session(self, current: Session) -> Session:
        """Returns the session after ``current``.

        Args:
            current: A session.

        Returns:
            The next session.

        Raises:
            ValueError: If no session follows within the scan window.
        """
        day = current.day
        for _ in range(_MAX_SCAN_DAYS):
            day += _DAY
            found = self.session(day)
            if found is not None:
                return found
        raise ValueError(f"{self.calendar_id} has no session after {day}")

    def previous_session(self, current: Session) -> Session:
        """Returns the session before ``current``.

        Args:
            current: A session.

        Returns:
            The previous session.

        Raises:
            ValueError: Before the calendar's first session.
        """
        day = current.day
        for _ in range(_MAX_SCAN_DAYS):
            day -= _DAY
            if day < self.first_day:
                break
            found = self.session(day)
            if found is not None:
                return found
        raise ValueError(f"{self.calendar_id} has no session before {day}")

    def _first_session_from(self, ts_ms: int) -> Session:
        """Returns the session containing ``ts_ms`` or the next one.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            A session with ``close_ms > ts_ms``.

        Raises:
            ValueError: If no session follows within the scan window.
        """
        try:
            found = self.session_at_or_before(ts_ms)
        except ValueError:
            day = max(self.local_day(ts_ms), self.first_day)
            for _ in range(_MAX_SCAN_DAYS):
                candidate = self.session(day)
                if candidate is not None:
                    return candidate
                day += _DAY
            raise
        return found if ts_ms < found.close_ms else self.next_session(found)

    @override
    def is_open(self, ts_ms: int) -> bool:
        """See base class."""
        try:
            found = self.session_at_or_before(ts_ms)
        except ValueError:
            return False
        return ts_ms < found.close_ms

    @override
    def sessions(self, from_ms: int, to_ms: int) -> list[Session]:
        """See base class."""
        result: list[Session] = []
        if to_ms <= from_ms:
            return result
        current = self._first_session_from(from_ms)
        while current.open_ms < to_ms:
            result.append(current)
            current = self.next_session(current)
        return result

    # Bars ---------------------------------------------------------------

    @override
    def contains(self, big: Timeframe, small: Timeframe) -> bool:
        """See base class."""
        if small.duration_ms >= big.duration_ms:
            return False
        if big is Timeframe.MN1:
            return small is not Timeframe.W1
        if big in _PERIODIC:
            return True
        return big.duration_ms % small.duration_ms == 0

    def _period_key(self, tf: Timeframe, day: datetime.date) -> object:
        """Returns the grouping key of a periodic bar.

        Args:
            tf: ``1d``, ``1w`` or ``1M``.
            day: Session date.

        Returns:
            Key shared by all sessions of one bar.
        """
        if tf is Timeframe.D1:
            return day
        if tf is Timeframe.W1:
            return day.isocalendar()[:2]
        return (day.year, day.month)

    def _period_first(self, tf: Timeframe, session: Session) -> Session:
        """Returns the first session of the periodic bar of ``session``.

        Args:
            tf: Periodic timeframe.
            session: A session in the bar.

        Returns:
            First session of the bar.
        """
        key = self._period_key(tf, session.day)
        current = session
        while tf is not Timeframe.D1:
            try:
                previous = self.previous_session(current)
            except ValueError:
                break
            if self._period_key(tf, previous.day) != key:
                break
            current = previous
        return current

    def _period_last(self, tf: Timeframe, session: Session) -> Session:
        """Returns the last session of the periodic bar of ``session``.

        Args:
            tf: Periodic timeframe.
            session: A session in the bar.

        Returns:
            Last session of the bar.
        """
        key = self._period_key(tf, session.day)
        current = session
        while tf is not Timeframe.D1:
            following = self.next_session(current)
            if self._period_key(tf, following.day) != key:
                break
            current = following
        return current

    @override
    def floor(self, tf: Timeframe, ts_ms: int) -> int:
        """See base class."""
        found = self.session_at_or_before(ts_ms)
        if tf in _PERIODIC:
            return self._period_first(tf, found).open_ms
        # Any time from the close until the next open (closing-auction
        # prints included) falls in the session's last bar.
        index = min(
            (ts_ms - found.open_ms) // tf.duration_ms, _bar_count(tf, found) - 1
        )
        return found.open_ms + index * tf.duration_ms

    def _ceil(self, tf: Timeframe, ts_ms: int) -> tuple[Session, int]:
        """Returns the first bar start at or after ``ts_ms``.

        Args:
            tf: Timeframe.
            ts_ms: UTC epoch milliseconds.

        Returns:
            ``(session, index)`` of that bar; for periodic timeframes the
            session is the bar's first session and the index is 0.
        """
        found = self._first_session_from(ts_ms)
        if tf in _PERIODIC:
            first = self._period_first(tf, found)
            if first.open_ms >= ts_ms:
                return first, 0
            last = self._period_last(tf, found)
            return self.next_session(last), 0
        if ts_ms <= found.open_ms:
            return found, 0
        index = math.ceil((ts_ms - found.open_ms) / tf.duration_ms)
        if index < _bar_count(tf, found):
            return found, index
        return self.next_session(found), 0

    @override
    def end_of(self, tf: Timeframe, start_ms: int) -> int:
        """See base class."""
        found = self.session_at_or_before(start_ms)
        if tf in _PERIODIC:
            return self._period_last(tf, found).close_ms
        return min(start_ms + tf.duration_ms, found.close_ms)

    @override
    def shift(self, tf: Timeframe, start_ms: int, bars: int) -> int:
        """See base class."""
        current, index = self._ceil(tf, start_ms)
        if tf in _PERIODIC:
            for _ in range(bars):
                current = self.next_session(self._period_last(tf, current))
            for _ in range(-bars):
                current = self._period_first(tf, self.previous_session(current))
            return current.open_ms
        index += bars
        while index >= (count := _bar_count(tf, current)):
            index -= count
            current = self.next_session(current)
        while index < 0:
            current = self.previous_session(current)
            index += _bar_count(tf, current)
        return current.open_ms + index * tf.duration_ms

    @override
    def bars_between(self, tf: Timeframe, start_ms: int, end_ms: int) -> int:
        """See base class."""
        if end_ms <= start_ms:
            return 0
        total = 0
        if tf in _PERIODIC:
            current, _ = self._ceil(tf, start_ms)
            while current.open_ms < end_ms:
                total += 1
                current = self.next_session(self._period_last(tf, current))
            return total
        current = self._first_session_from(start_ms)
        step = tf.duration_ms
        while current.open_ms < end_ms:
            count = _bar_count(tf, current)
            low = max(start_ms, current.open_ms) - current.open_ms
            high = min(end_ms - current.open_ms, count * step)
            first = -(-low // step)
            last = -(-high // step) - 1
            total += max(0, min(last, count - 1) - first + 1)
            current = self.next_session(current)
        return total


# NYSE / Nasdaq ----------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    """Returns the n-th weekday of a month (``n=-1`` for the last).

    Args:
        year: Year.
        month: Month.
        weekday: 0=Monday.
        n: Occurrence (1-based) or -1.

    Returns:
        The date.
    """
    if n > 0:
        first = datetime.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=offset + 7 * (n - 1))
    following = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last = following - _DAY
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> datetime.date:
    """Returns Western Easter Sunday (anonymous Gregorian algorithm).

    The single-letter names are the algorithm's published notation
    (Meeus/Jones/Butcher); ``el`` stands for its ``l``.

    Args:
        year: Year.

    Returns:
        Easter Sunday.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def _observed(
    day: datetime.date, *, saturday_to_friday: bool = True
) -> datetime.date | None:
    """Applies the weekend observance rule to a fixed-date holiday.

    Args:
        day: The holiday's calendar date.
        saturday_to_friday: Whether a Saturday holiday closes the Friday.

    Returns:
        The weekday the market closes, or ``None`` when none.
    """
    if day.weekday() == 5:
        return day - _DAY if saturday_to_friday else None
    if day.weekday() == 6:
        return day + _DAY
    return day


_NYSE_SPECIAL_CLOSURES = frozenset(
    datetime.date.fromisoformat(text)
    for text in (
        "1994-04-27",  # President Nixon's funeral.
        "2001-09-11",
        "2001-09-12",
        "2001-09-13",
        "2001-09-14",  # September 11 attacks.
        "2004-06-11",  # President Reagan's funeral.
        "2007-01-02",  # President Ford's funeral.
        "2012-10-29",
        "2012-10-30",  # Hurricane Sandy.
        "2018-12-05",  # President G. H. W. Bush's funeral.
        "2025-01-09",  # President Carter's funeral.
    )
)


@functools.cache
def _nyse_holidays(year: int) -> frozenset[datetime.date]:
    """Returns NYSE full-day closures of a year (weekdays only).

    Args:
        year: Year.

    Returns:
        Closure dates.
    """
    days = {
        # New Year's Day on a Saturday is not moved to the prior Friday.
        _observed(datetime.date(year, 1, 1), saturday_to_friday=False),
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday.
        _easter(year) - datetime.timedelta(days=2),  # Good Friday.
        _nth_weekday(year, 5, 0, -1),  # Memorial Day.
        _observed(datetime.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day.
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving.
        _observed(datetime.date(year, 12, 25)),
    }
    if year >= 1998:
        days.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day.
    if year >= 2022:
        days.add(_observed(datetime.date(year, 6, 19)))  # Juneteenth.
    # A Saturday New Year's Day of the next year is not observed on Dec 31.
    days.discard(None)
    return frozenset(day for day in days if day is not None)


def _nyse_closed(day: datetime.date) -> bool:
    """Checks NYSE weekday closures.

    Args:
        day: Weekday.

    Returns:
        True when the exchange is closed.
    """
    return day in _nyse_holidays(day.year) or day in _NYSE_SPECIAL_CLOSURES


def _nyse_early_close(day: datetime.date) -> datetime.time | None:
    """Returns the 13:00 early close, if ``day`` has one.

    The day after Thanksgiving closes early, and so do July 3 and
    December 24 when they fall on Monday to Thursday.

    Args:
        day: Session date.

    Returns:
        Local close time or ``None``.
    """
    one_pm = datetime.time(13, 0)
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    if day == thanksgiving + _DAY:
        return one_pm
    if (day.month, day.day) in ((7, 3), (12, 24)) and day.weekday() <= 3:
        return one_pm
    return None


# US bond market ----------------------------------------------------------


@functools.cache
def _bond_holidays(year: int) -> frozenset[datetime.date]:
    """Returns rule-based US Treasury market closures of a year.

    Used only after the observed publication table (``usbond.json``).
    Follows the NYSE holidays plus Columbus Day and Veterans Day (the SIFMA
    recommendation), without Good Friday: Treasury published on some Good
    Fridays (2015, 2021, 2026) and not on others, and whether it will is
    known only from SIFMA's yearly announcement, so a future Good Friday is
    a session. The NYSE special closures are not bond holidays.

    Args:
        year: Year.

    Returns:
        Closure dates.
    """
    good_friday = _easter(year) - datetime.timedelta(days=2)
    days = set(_nyse_holidays(year)) - {good_friday}
    days.add(_nth_weekday(year, 10, 0, 2))  # Columbus Day.
    veterans = _observed(datetime.date(year, 11, 11), saturday_to_friday=False)
    if veterans is not None:
        days.add(veterans)
    return frozenset(days)


# Generated calendar tables (TWSE, US Treasury) ---------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _TableData:
    """Closures and weekend sessions loaded from a generated table.

    Attributes:
        first_day: First date of the record (``observed_from``).
        closures: Weekdays without a session.
        weekend_sessions: Saturdays and Sundays that traded.
        covered_through: Last date the table covers, observed or scheduled.
    """

    first_day: datetime.date
    closures: frozenset[datetime.date]
    weekend_sessions: frozenset[datetime.date]
    covered_through: datetime.date


def _load_table(name: str) -> _TableData:
    """Loads a generated calendar table.

    Args:
        name: File stem in ``calendar_data``.

    Returns:
        The table.
    """
    raw = json.loads((_DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    parse = datetime.date.fromisoformat
    return _TableData(
        first_day=parse(raw["observed_from"]),
        closures=frozenset(parse(day) for day in raw["closures"]),
        weekend_sessions=frozenset(
            parse(day) for day in raw["weekend_sessions"]
        ),
        covered_through=max(
            parse(raw["observed_through"]), parse(raw["scheduled_through"])
        ),
    )


def _build_xtai() -> ExchangeCalendar:
    """Builds the TWSE calendar from the generated trading-day table.

    Returns:
        The calendar. Dates after the table's coverage follow the weekday
        rule until the TWSE adapter adds newly published closures.
    """
    table = _load_table("xtai")
    return ExchangeCalendar(
        CalendarId.XTAI,
        "Asia/Taipei",
        datetime.time(9, 0),
        datetime.time(13, 30),
        is_closed=table.closures.__contains__,
        weekend_sessions=table.weekend_sessions,
        first_day=table.first_day,
        close_grace_ms=60_000,
    )


def _build_usbond() -> ExchangeCalendar:
    """Builds the US Treasury market calendar from its publication table.

    ``usbond.json`` (``scripts/generate_usbond_calendar.py``) lists every
    weekday on which Treasury published no par yield curve, so through its
    ``observed_through`` date the sessions are exactly the publication
    days (Good Friday in some years only, Martin Luther King Jr. Day closed
    before 1998, NYSE special closures only where Treasury did not
    publish).

    Returns:
        The calendar. Later dates follow ``_bond_holidays``, under which
        Good Friday is a session.
    """
    table = _load_table("usbond")

    def closed(day: datetime.date) -> bool:
        """Checks a weekday closure.

        Args:
            day: Weekday.

        Returns:
            True when the bond market is (or was observed) closed.
        """
        if day <= table.covered_through:
            return day in table.closures
        return day in _bond_holidays(day.year)

    return ExchangeCalendar(
        CalendarId.USBOND,
        "America/New_York",
        datetime.time(8, 0),
        datetime.time(17, 0),
        is_closed=closed,
        weekend_sessions=table.weekend_sessions,
        first_day=table.first_day,
    )


CONTINUOUS: BarCalendar = ContinuousCalendar()
"""Calendar of crypto venues and DEMO."""

XNYS = ExchangeCalendar(
    CalendarId.XNYS,
    "America/New_York",
    datetime.time(9, 30),
    datetime.time(16, 0),
    is_closed=_nyse_closed,
    early_close=_nyse_early_close,
    first_day=datetime.date(1990, 1, 2),
)
"""NYSE and Nasdaq regular trading hours."""

USBOND = _build_usbond()
"""US Treasury market: the days Treasury publishes its par yield curve."""

XTAI = _build_xtai()
"""Taiwan Stock Exchange regular session."""

_BY_ID: dict[CalendarId, BarCalendar] = {
    CalendarId.CONTINUOUS: CONTINUOUS,
    CalendarId.XTAI: XTAI,
    CalendarId.XNYS: XNYS,
    CalendarId.USBOND: USBOND,
}


def lookback_start(
    calendar: BarCalendar,
    timeframe: Timeframe,
    end_ms: int,
    limit: int,
) -> int:
    """Bounds an inferred history lookback to the exchange calendar.

    Args:
        calendar: Instrument calendar, including its coverage boundary.
        timeframe: Requested bar resolution.
        end_ms: Exclusive range end in UTC epoch milliseconds.
        limit: Positive maximum number of bars to request.

    Returns:
        Inclusive UTC bar start, clamped to the first supported exchange
        session when fewer than limit bars exist in the calendar.

    Raises:
        ValueError: If end_ms precedes the calendar, the limit is not
            positive, or a continuous calendar cannot represent the range.
    """
    if limit <= 0:
        raise ValueError("lookback limit must be positive")
    last_start = calendar.floor(timeframe, end_ms - 1)
    try:
        return calendar.shift(timeframe, last_start, -(limit - 1))
    except ValueError:
        if not isinstance(calendar, ExchangeCalendar):
            raise
        first = datetime.datetime.combine(
            calendar.first_day, calendar.open_time, tzinfo=calendar.tz
        )
        return calendar.ceil(timeframe, int(first.timestamp() * 1000))


def get(calendar_id: CalendarId | str) -> BarCalendar:
    """Returns a calendar by id.

    Args:
        calendar_id: Identifier.

    Returns:
        The calendar.

    Raises:
        ValueError: For an unknown id.
    """
    return _BY_ID[CalendarId(calendar_id)]


def for_venue(venue: instruments.Venue) -> BarCalendar:
    """Returns the calendar of a venue.

    Args:
        venue: Venue.

    Returns:
        Its calendar (continuous for crypto venues).
    """
    return get(instruments.calendar_code(venue))


def for_instrument(instrument_id: instruments.InstrumentId) -> BarCalendar:
    """Returns the calendar of an instrument.

    Args:
        instrument_id: Instrument.

    Returns:
        Its calendar.
    """
    return get(
        instruments.calendar_code(
            instrument_id.venue, instrument_id.market_type
        )
    )
