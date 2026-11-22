"""Bounded in-memory candle store with merge semantics."""

from __future__ import annotations

import bisect
import threading

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules

SeriesKey = tuple[instruments.InstrumentId, timeframes.Timeframe]


class _Series:
    """Ordered bars of one series."""

    def __init__(self) -> None:
        """Initializes an empty series."""
        self.starts: list[int] = []
        self.bars: dict[int, market.Candle] = {}


class CandleStore:
    """Thread-safe bounded store keyed by ``(instrument, timeframe)``.

    Seconds series grow by up to 86,400 bars a day, so they have their own,
    smaller bound (two hours of 1s bars by default, which still holds a
    full 5,000-bar batch).
    """

    def __init__(
        self,
        max_bars_per_series: int = 20_000,
        max_seconds_bars_per_series: int = 7_200,
    ) -> None:
        """Initializes the store.

        Args:
            max_bars_per_series: Oldest bars beyond this are evicted.
            max_seconds_bars_per_series: Bound for seconds timeframes.
        """
        self._max = max_bars_per_series
        self._max_seconds = max_seconds_bars_per_series
        self._series: dict[SeriesKey, _Series] = {}
        self._lock = threading.Lock()
        self.revisions = 0

    def upsert(self, candle: market.Candle) -> candle_rules.MergeResult:
        """Merges one candle.

        Args:
            candle: Candle to store.

        Returns:
            Merge result.
        """
        key = (candle.instrument_id, candle.timeframe)
        with self._lock:
            series = self._series.setdefault(key, _Series())
            result = candle_rules.merge_candle(
                series.bars.get(candle.start_ms), candle
            )
            if result.changed:
                if candle.start_ms not in series.bars:
                    bisect.insort(series.starts, candle.start_ms)
                series.bars[candle.start_ms] = result.candle
                if result.action is candle_rules.MergeAction.REVISED:
                    self.revisions += 1
                bound = (
                    self._max_seconds
                    if candle.timeframe.is_seconds
                    else self._max
                )
                excess = len(series.starts) - bound
                if excess > 0:
                    for oldest in series.starts[:excess]:
                        del series.bars[oldest]
                    del series.starts[:excess]
            return result

    def upsert_many(
        self, rows: list[market.Candle]
    ) -> list[candle_rules.MergeResult]:
        """Merges many candles in time order.

        Args:
            rows: Candles.

        Returns:
            Merge results in input order after sorting by start.
        """
        return [
            self.upsert(c)
            for c in sorted(rows, key=lambda c: (c.start_ms, c.received_at_ms))
        ]

    def range(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns stored bars starting in [start, end).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending candles.
        """
        with self._lock:
            series = self._series.get((instrument, timeframe))
            if series is None:
                return []
            lo = bisect.bisect_left(series.starts, start_ms)
            hi = bisect.bisect_left(series.starts, end_ms)
            return [series.bars[s] for s in series.starts[lo:hi]]

    def latest(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> market.Candle | None:
        """Returns the newest stored bar.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Returns:
            Candle or ``None``.
        """
        with self._lock:
            series = self._series.get((instrument, timeframe))
            if series is None or not series.starts:
                return None
            return series.bars[series.starts[-1]]

    def final_gaps(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[int, int]]:
        """Finds closed-bar slots in range that hold no final bar.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end (typically the current bar start).

        Returns:
            Missing ranges.
        """
        finals = [
            c.start_ms
            for c in self.range(instrument, timeframe, start_ms, end_ms)
            if c.is_final
        ]
        return candle_rules.find_gaps(finals, timeframe, start_ms, end_ms)
