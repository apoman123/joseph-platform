"""Candle timeframes and UTC-aligned bar boundaries.

All bars are half-open intervals ``[start_ms, end_ms)`` aligned to the UTC
epoch. Weekly bars start on Monday 00:00 UTC, matching Binance and most
perpetual venues. Monthly bars (``1M``) are calendar months starting on the
1st at 00:00 UTC, so their length varies (28-31 days).

Boundary arithmetic must go through ``floor``, ``end_of``, ``shift`` and
``bars_between``: ``duration_ms`` is exact for fixed-length timeframes but
only an upper bound for calendar ones, suitable for ordering timeframes and
sizing look-back windows.
"""

from __future__ import annotations

import datetime
import enum

_SECOND_MS = 1_000
_MINUTE_MS = 60 * _SECOND_MS
_HOUR_MS = 60 * _MINUTE_MS
_DAY_MS = 24 * _HOUR_MS
# 1970-01-01 was a Thursday; Monday 1970-01-05 00:00 UTC anchors weeks.
_WEEK_ANCHOR_MS = 4 * _DAY_MS
_MONTH_MAX_MS = 31 * _DAY_MS


class Timeframe(enum.StrEnum):
    """Supported candle resolutions (contract ``Timeframe``)."""

    S1 = "1s"
    S5 = "5s"
    S15 = "15s"
    S30 = "30s"
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H12 = "12h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

    @property
    def duration_ms(self) -> int:
        """Returns the bar length in milliseconds.

        Exact for fixed-length timeframes. For ``1M`` it is the longest
        possible month (31 days): use it to order timeframes or to size
        look-back windows, never to step between bar boundaries.
        """
        return _DURATIONS_MS[self]

    @property
    def is_calendar(self) -> bool:
        """Returns True when bar length varies with the calendar (``1M``)."""
        return self is Timeframe.MN1

    @property
    def is_seconds(self) -> bool:
        """Returns True for sub-minute timeframes (built from trades)."""
        return self.duration_ms < _MINUTE_MS

    def floor(self, ts_ms: int) -> int:
        """Returns the start of the bar containing ``ts_ms``.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            Bar start in UTC epoch milliseconds.
        """
        if self is Timeframe.MN1:
            return _month_start(_month_index(ts_ms))
        if self is Timeframe.W1:
            offset = (ts_ms - _WEEK_ANCHOR_MS) % self.duration_ms
            return ts_ms - offset
        return ts_ms - (ts_ms % self.duration_ms)

    def shift(self, start_ms: int, bars: int) -> int:
        """Moves an aligned bar start by a number of bars.

        Args:
            start_ms: Aligned bar start (or bar end, which is also a start).
            bars: Bars to move; negative moves back in time.

        Returns:
            The start of the bar ``bars`` bars away.
        """
        if self is Timeframe.MN1:
            return _month_start(_month_index(start_ms) + bars)
        return start_ms + bars * self.duration_ms

    def end_of(self, start_ms: int) -> int:
        """Returns the exclusive end of the bar starting at ``start_ms``.

        Args:
            start_ms: Aligned bar start.

        Returns:
            Exclusive bar end in UTC epoch milliseconds.
        """
        return self.shift(start_ms, 1)

    def bars_between(self, start_ms: int, end_ms: int) -> int:
        """Counts bar starts in ``[start_ms, end_ms)`` for an aligned start.

        Args:
            start_ms: Aligned bar start.
            end_ms: Exclusive end (any time).

        Returns:
            Number of bars that start in the interval (0 when empty).
        """
        if end_ms <= start_ms:
            return 0
        if self is Timeframe.MN1:
            last = _month_index(end_ms - 1)
            return last - _month_index(start_ms) + 1
        return (end_ms - start_ms + self.duration_ms - 1) // self.duration_ms

    def is_aligned(self, ts_ms: int) -> bool:
        """Checks whether ``ts_ms`` is a valid bar start.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            True when ``ts_ms`` is exactly a bar boundary.
        """
        return self.floor(ts_ms) == ts_ms

    def contains(self, other: Timeframe) -> bool:
        """Checks whether bars of ``other`` nest exactly inside this one.

        Args:
            other: Candidate lower timeframe.

        Returns:
            True if ``other`` is shorter and its bars tile this one exactly.
        """
        if other.duration_ms >= self.duration_ms:
            return False
        if self in (Timeframe.W1, Timeframe.MN1):
            return _DAY_MS % other.duration_ms == 0
        return self.duration_ms % other.duration_ms == 0


_DURATIONS_MS: dict[Timeframe, int] = {
    Timeframe.S1: _SECOND_MS,
    Timeframe.S5: 5 * _SECOND_MS,
    Timeframe.S15: 15 * _SECOND_MS,
    Timeframe.S30: 30 * _SECOND_MS,
    Timeframe.M1: _MINUTE_MS,
    Timeframe.M3: 3 * _MINUTE_MS,
    Timeframe.M5: 5 * _MINUTE_MS,
    Timeframe.M15: 15 * _MINUTE_MS,
    Timeframe.M30: 30 * _MINUTE_MS,
    Timeframe.H1: _HOUR_MS,
    Timeframe.H2: 2 * _HOUR_MS,
    Timeframe.H4: 4 * _HOUR_MS,
    Timeframe.H6: 6 * _HOUR_MS,
    Timeframe.H12: 12 * _HOUR_MS,
    Timeframe.D1: _DAY_MS,
    Timeframe.W1: 7 * _DAY_MS,
    Timeframe.MN1: _MONTH_MAX_MS,
}

SECONDS: tuple[Timeframe, ...] = tuple(tf for tf in Timeframe if tf.is_seconds)
"""Sub-minute timeframes, which venues do not publish as perp klines."""

MINUTE_AND_ABOVE: tuple[Timeframe, ...] = tuple(
    tf for tf in Timeframe if not tf.is_seconds
)
"""Timeframes venues may serve as native klines."""


def _month_index(ts_ms: int) -> int:
    """Returns months since January 1970 for the month containing ``ts_ms``.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Zero-based month index (negative before 1970).
    """
    moment = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC)
    return (moment.year - 1970) * 12 + moment.month - 1


def _month_start(index: int) -> int:
    """Returns the first instant of a month index.

    Args:
        index: Months since January 1970.

    Returns:
        UTC epoch milliseconds of the 1st at 00:00.
    """
    year, month = divmod(index, 12)
    start = datetime.datetime(1970 + year, month + 1, 1, tzinfo=datetime.UTC)
    return int(start.timestamp()) * 1000


def parse(value: str) -> Timeframe:
    """Parses a contract timeframe string.

    Case matters: ``1m`` is one minute and ``1M`` is one month.

    Args:
        value: Text such as ``"5m"``.

    Returns:
        The matching ``Timeframe``.

    Raises:
        ValueError: If the text is not a supported timeframe.
    """
    try:
        return Timeframe(value)
    except ValueError as err:
        raise ValueError(f"unsupported timeframe: {value!r}") from err


def last_closed_start(tf: Timeframe, as_of_ms: int) -> int | None:
    """Returns the start of the most recent bar fully closed at ``as_of_ms``.

    This is the visibility rule that prevents higher-timeframe look-ahead: a
    4h bar is usable only once ``as_of_ms >= end_ms``.

    Args:
        tf: Timeframe of interest.
        as_of_ms: Evaluation time (usually a lower-timeframe bar close).

    Returns:
        Start of the last closed bar, or ``None`` before the epoch's first bar.
    """
    previous_start = tf.shift(tf.floor(as_of_ms), -1)
    if previous_start < 0:
        return None
    return previous_start
