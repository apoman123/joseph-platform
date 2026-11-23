"""Bounded in-memory candle store with merge semantics and fetch coverage.

Besides bars, each series keeps its *coverage*: spans in which a fetch or a
live session delivered every bar (``candle_rules.CandleCoverage``). It is
recorded only for venues with sparse bars (``ports.sparse_bars``), where a
slot without a bar inside coverage had no trades and is not a gap. Coverage
lives with the bars, in memory: evicting bars also drops the coverage over
them, so an evicted bar is missing again (and repaired) instead of being
taken for a quiet interval.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Iterable

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules

SeriesKey = tuple[instruments.InstrumentId, timeframes.Timeframe]
MAX_COVERAGE_SPANS = 1_000
"""Disjoint coverage spans kept per series (the oldest are dropped)."""
_FOREVER_MS = 2**63


class _Series:
    """Ordered bars and coverage of one series.

    Attributes:
        starts: Ascending bar starts.
        bars: Bars by start.
        coverage: Disjoint ascending coverage spans.
        floor_ms: Coverage never reaches below this (the oldest bar kept
            after an eviction).
    """

    def __init__(self) -> None:
        """Initializes an empty series."""
        self.starts: list[int] = []
        self.bars: dict[int, market.Candle] = {}
        self.coverage: list[candle_rules.Span] = []
        self.floor_ms: int | None = None

    def clip_coverage(self) -> None:
        """Drops coverage below the eviction floor and beyond the bound."""
        if self.floor_ms is not None:
            self.coverage = candle_rules.clip_spans(
                self.coverage, self.floor_ms, _FOREVER_MS
            )
        if len(self.coverage) > MAX_COVERAGE_SPANS:
            del self.coverage[: len(self.coverage) - MAX_COVERAGE_SPANS]


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
                    series.floor_ms = series.starts[0]
                    series.clip_coverage()
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

    def add_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        spans: Iterable[candle_rules.Span],
    ) -> None:
        """Records spans in which every bar of a series was delivered.

        Call after the bars of the spans were upserted. Only venues with
        sparse bars record coverage.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            spans: Covered spans.
        """
        added = list(spans)
        if not added:
            return
        with self._lock:
            series = self._series.setdefault((instrument, timeframe), _Series())
            series.coverage = candle_rules.merge_spans(
                [*series.coverage, *added]
            )
            series.clip_coverage()

    def coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[candle_rules.Span]:
        """Returns the recorded coverage inside ``[start, end)``.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Disjoint ascending spans, minus the slots of non-final bars
            (an open bar is not proven, whatever the coverage says).
        """
        with self._lock:
            series = self._series.get((instrument, timeframe))
            if series is None or not series.coverage:
                return []
            spans = candle_rules.clip_spans(series.coverage, start_ms, end_ms)
            lo = bisect.bisect_left(series.starts, start_ms)
            hi = bisect.bisect_left(series.starts, end_ms)
            open_bars = [
                (bar.start_ms, bar.end_ms)
                for bar in (series.bars[s] for s in series.starts[lo:hi])
                if not bar.is_final
            ]
        return candle_rules.subtract_spans(spans, open_bars)

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

        Slots follow the instrument's session calendar, so the time between
        sessions is never reported missing; slots wholly inside recorded
        coverage without a bar had no trades and are not missing either.
        This drives repair fetches.

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
        return candle_rules.find_gaps(
            finals,
            timeframe,
            start_ms,
            end_ms,
            calendar=sessions.for_instrument(instrument),
            covered=self.coverage(instrument, timeframe, start_ms, end_ms),
        )
