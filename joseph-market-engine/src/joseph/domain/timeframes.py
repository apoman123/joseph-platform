"""Candle timeframes and UTC-aligned bar boundaries.

All bars are half-open intervals ``[start_ms, end_ms)`` aligned to the UTC
epoch. Weekly bars start on Monday 00:00 UTC, matching Binance and most
perpetual venues.
"""

from __future__ import annotations

import enum

_MINUTE_MS = 60_000
_HOUR_MS = 60 * _MINUTE_MS
_DAY_MS = 24 * _HOUR_MS
# 1970-01-01 was a Thursday; Monday 1970-01-05 00:00 UTC anchors weeks.
_WEEK_ANCHOR_MS = 4 * _DAY_MS


class Timeframe(enum.StrEnum):
    """Supported candle resolutions (contract ``Timeframe``)."""

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

    @property
    def duration_ms(self) -> int:
        """Returns the bar length in milliseconds."""
        return _DURATIONS_MS[self]

    def floor(self, ts_ms: int) -> int:
        """Returns the start of the bar containing ``ts_ms``.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            Bar start in UTC epoch milliseconds.
        """
        if self is Timeframe.W1:
            offset = (ts_ms - _WEEK_ANCHOR_MS) % self.duration_ms
            return ts_ms - offset
        return ts_ms - (ts_ms % self.duration_ms)

    def end_of(self, start_ms: int) -> int:
        """Returns the exclusive end of the bar starting at ``start_ms``.

        Args:
            start_ms: Aligned bar start.

        Returns:
            Exclusive bar end in UTC epoch milliseconds.
        """
        return start_ms + self.duration_ms

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
            True if ``other`` is shorter and divides this duration.
        """
        if other.duration_ms >= self.duration_ms:
            return False
        if self is Timeframe.W1:
            return _DAY_MS % other.duration_ms == 0
        return self.duration_ms % other.duration_ms == 0


_DURATIONS_MS: dict[Timeframe, int] = {
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
}


def parse(value: str) -> Timeframe:
    """Parses a contract timeframe string.

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
    current_start = tf.floor(as_of_ms)
    previous_start = current_start - tf.duration_ms
    if tf is Timeframe.W1:
        previous_start = tf.floor(current_start - 1)
    if previous_start < 0:
        return None
    return previous_start
