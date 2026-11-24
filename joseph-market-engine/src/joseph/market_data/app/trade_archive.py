# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Archive of recorded trades: port, in-memory store and read side.

The archive holds what the engine records from venues' public trades (see
``domain.recorded_trades``): complete 1s aggregates, per-minute roll-ups of
them and explicit coverage intervals. ``TradeArchive`` is implemented by
``MemoryTradeArchive`` (tests, ephemeral deployments) and by the SQLAlchemy
adapter ``adapters.trade_archive_sql`` (history survives restarts).

``ArchiveReader`` is the read side used by ``MarketDataService``: it
rebuilds seconds bars for any seconds timeframe and attaches a true delta to
final 1m-and-above bars whose span coverage proves complete.
"""

from __future__ import annotations

import bisect
import collections
import threading
from collections.abc import Iterable
from collections.abc import Sequence
from typing import Protocol
from typing import TypeAlias

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import recorded_trades

Interval: TypeAlias = recorded_trades.Interval
_FLOW_CACHE = 20_000
"""Span flows ``ArchiveReader`` keeps cached (least recently used first out)."""


class TradeArchive(Protocol):
    """Durable store of recorded 1s aggregates and their coverage."""

    def put(
        self,
        instrument: instruments.InstrumentId,
        seconds: Sequence[market.Candle],
        covered: Sequence[Interval],
    ) -> None:
        """Stores complete 1s aggregates and merges their coverage.

        Args:
            instrument: Instrument the trades belong to.
            seconds: Final 1s aggregates with traded volume (upserted).
            covered: Whole-second intervals in which every trade is
                recorded (united with the stored coverage).
        """
        ...

    def seconds(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[market.Candle]:
        """Returns stored 1s aggregates starting in ``[start, end)``.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending 1s candles; seconds without trades have none.
        """
        ...

    def previous(
        self, instrument: instruments.InstrumentId, before_ms: int
    ) -> market.Candle | None:
        """Returns the newest stored 1s aggregate starting before a time.

        Args:
            instrument: Instrument to read.
            before_ms: Exclusive bound, epoch milliseconds.

        Returns:
            The aggregate, or ``None`` when none is stored before it.
        """
        ...

    def coverage(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[Interval]:
        """Returns coverage intervals overlapping ``[start, end)``.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Disjoint ascending intervals (not clipped to the range).
        """
        ...

    def minute_flows(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[recorded_trades.MinuteFlow]:
        """Returns per-minute roll-ups of minutes starting in the range.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending minute totals.
        """
        ...

    def covered_until(self, instrument: instruments.InstrumentId) -> int | None:
        """Returns the end of the newest coverage interval.

        Args:
            instrument: Instrument to read.

        Returns:
            Exclusive end, epoch milliseconds, or ``None`` without coverage.
        """
        ...

    def instruments(self) -> list[instruments.InstrumentId]:
        """Lists instruments with recorded coverage.

        Returns:
            Instruments.
        """
        ...

    def prune(self, before_ms: int) -> int:
        """Deletes aggregates and coverage before a retention cut-off.

        Args:
            before_ms: Cut-off, epoch milliseconds: older aggregates are
                deleted and coverage is cut at it.

        Returns:
            Number of 1s aggregates deleted.
        """
        ...


class _Series:
    """Recorded data of one instrument (in memory).

    Attributes:
        starts: Ascending starts of the stored 1s aggregates.
        bars: Stored 1s aggregates by start.
        coverage: Disjoint ascending coverage intervals.
    """

    def __init__(self) -> None:
        """Initializes an empty series."""
        self.starts: list[int] = []
        self.bars: dict[int, market.Candle] = {}
        self.coverage: list[Interval] = []


class MemoryTradeArchive:
    """Thread-safe in-memory ``TradeArchive`` (lost on restart)."""

    def __init__(self) -> None:
        """Initializes an empty archive."""
        self._series: dict[instruments.InstrumentId, _Series] = {}
        self._lock = threading.Lock()

    def put(
        self,
        instrument: instruments.InstrumentId,
        seconds: Sequence[market.Candle],
        covered: Sequence[Interval],
    ) -> None:
        """Stores complete 1s aggregates and merges their coverage.

        Args:
            instrument: Instrument the trades belong to.
            seconds: Final 1s aggregates (upserted by start).
            covered: Intervals in which every trade is recorded.
        """
        with self._lock:
            series = self._series.setdefault(instrument, _Series())
            for bar in seconds:
                if bar.start_ms not in series.bars:
                    bisect.insort(series.starts, bar.start_ms)
                series.bars[bar.start_ms] = bar
            series.coverage = recorded_trades.merge(
                [*series.coverage, *covered]
            )

    def _range(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[market.Candle]:
        """Returns stored aggregates in a range (lock held by caller).

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending candles.
        """
        series = self._series.get(instrument)
        if series is None:
            return []
        lo = bisect.bisect_left(series.starts, start_ms)
        hi = bisect.bisect_left(series.starts, end_ms)
        return [series.bars[s] for s in series.starts[lo:hi]]

    def seconds(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[market.Candle]:
        """Returns stored 1s aggregates starting in ``[start, end)``.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending 1s candles.
        """
        with self._lock:
            return self._range(instrument, start_ms, end_ms)

    def previous(
        self, instrument: instruments.InstrumentId, before_ms: int
    ) -> market.Candle | None:
        """Returns the newest stored 1s aggregate starting before a time.

        Args:
            instrument: Instrument to read.
            before_ms: Exclusive bound, epoch milliseconds.

        Returns:
            The aggregate, or ``None`` when none is stored before it.
        """
        with self._lock:
            series = self._series.get(instrument)
            if series is None:
                return None
            index = bisect.bisect_left(series.starts, before_ms)
            return series.bars[series.starts[index - 1]] if index else None

    def coverage(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[Interval]:
        """Returns coverage intervals overlapping ``[start, end)``.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Disjoint ascending intervals (not clipped to the range).
        """
        with self._lock:
            series = self._series.get(instrument)
            if series is None:
                return []
            return [
                (a, b)
                for a, b in series.coverage
                if b > start_ms and a < end_ms
            ]

    def minute_flows(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[recorded_trades.MinuteFlow]:
        """Returns per-minute roll-ups of minutes starting in the range.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending minute totals.
        """
        with self._lock:
            first = recorded_trades.floor_to(
                start_ms, recorded_trades.MINUTE_MS
            )
            bars = self._range(instrument, first, end_ms)
        return [
            flow
            for flow in recorded_trades.minute_flows(bars)
            if start_ms <= flow.start_ms < end_ms
        ]

    def covered_until(self, instrument: instruments.InstrumentId) -> int | None:
        """Returns the end of the newest coverage interval.

        Args:
            instrument: Instrument to read.

        Returns:
            Exclusive end, epoch milliseconds, or ``None`` without coverage.
        """
        with self._lock:
            series = self._series.get(instrument)
            if series is None or not series.coverage:
                return None
            return series.coverage[-1][1]

    def instruments(self) -> list[instruments.InstrumentId]:
        """Lists instruments with recorded coverage.

        Returns:
            Instruments in the order they were first recorded.
        """
        with self._lock:
            return [i for i, s in self._series.items() if s.coverage]

    def prune(self, before_ms: int) -> int:
        """Deletes aggregates and coverage before a retention cut-off.

        Args:
            before_ms: Cut-off, epoch milliseconds.

        Returns:
            Number of 1s aggregates deleted.
        """
        deleted = 0
        with self._lock:
            for series in self._series.values():
                cut = bisect.bisect_left(series.starts, before_ms)
                for start in series.starts[:cut]:
                    del series.bars[start]
                deleted += cut
                del series.starts[:cut]
                series.coverage = recorded_trades.clip(
                    series.coverage, before_ms
                )
        return deleted


class RecordedTrades(Protocol):
    """Read side of recorded trades consumed by ``MarketDataService``."""

    async def seconds_bars(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns archived seconds bars of a range (complete buckets).

        Args:
            instrument: Instrument to read.
            timeframe: Seconds timeframe.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending final candles.
        """
        ...

    def enrich(self, rows: Sequence[market.Candle]) -> list[market.Candle]:
        """Attaches recorded true delta to fully covered final bars.

        Args:
            rows: Candles of one series.

        Returns:
            Candles in the same order; final 1m-and-above bars of delta
            venues fully covered by recorded trades carry the true delta.
        """
        ...


class ArchiveReader:
    """Rebuilds seconds bars and true delta from a ``TradeArchive``.

    Attributes:
        archive: Underlying archive.
        delta_venues: Venues whose 1m-and-above bars get recorded delta.
    """

    def __init__(
        self,
        archive: TradeArchive,
        clock: clock_lib.Clock,
        *,
        delta_venues: Iterable[instruments.Venue] = (),
    ) -> None:
        """Initializes the reader.

        Args:
            archive: Archive to read.
            clock: Time source (decides which buckets have closed).
            delta_venues: Venues whose klines lack taker volume and whose
                trades are recorded.
        """
        self.archive = archive
        self.delta_venues = frozenset(delta_venues)
        self._clock = clock
        self._flows: collections.OrderedDict[
            tuple[instruments.InstrumentId, int, int],
            recorded_trades.RecordedFlow,
        ] = collections.OrderedDict()

    def seconds_bars_now(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Rebuilds archived seconds bars of a range.

        Args:
            instrument: Instrument to read.
            timeframe: Seconds timeframe.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending final candles of complete buckets.
        """
        coverage = self.archive.coverage(instrument, start_ms, end_ms)
        if not coverage:
            return []
        seconds = self.archive.seconds(instrument, start_ms, end_ms)
        return recorded_trades.rebuild_seconds(
            seconds,
            coverage,
            timeframe,
            start_ms,
            end_ms,
            previous=self.archive.previous(instrument, start_ms),
            now_ms=self._clock.now_ms(),
        )

    async def seconds_bars(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns archived seconds bars of a range (``seconds_bars_now``).

        Args:
            instrument: Instrument to read.
            timeframe: Seconds timeframe.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending final candles of complete buckets.
        """
        return self.seconds_bars_now(instrument, timeframe, start_ms, end_ms)

    def _cached(
        self, key: tuple[instruments.InstrumentId, int, int]
    ) -> recorded_trades.RecordedFlow | None:
        """Returns a cached flow and refreshes its recency.

        Args:
            key: ``(instrument, start_ms, end_ms)``.

        Returns:
            The cached flow, or ``None`` when it is not cached.
        """
        flow = self._flows.get(key)
        if flow is not None:
            self._flows.move_to_end(key)
        return flow

    def _remember(
        self,
        key: tuple[instruments.InstrumentId, int, int],
        flow: recorded_trades.RecordedFlow,
    ) -> None:
        """Caches a flow of a fully covered span (stable once covered).

        Args:
            key: ``(instrument, start_ms, end_ms)``.
            flow: Flow of the span.
        """
        self._flows[key] = flow
        while len(self._flows) > _FLOW_CACHE:
            self._flows.popitem(last=False)

    def flow(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> recorded_trades.RecordedFlow | None:
        """Returns the recorded flow of one span when coverage is complete.

        Args:
            instrument: Instrument to read.
            start_ms: Inclusive span start, epoch milliseconds.
            end_ms: Exclusive span end, epoch milliseconds.

        Returns:
            The flow, or ``None`` when the span is not wholly covered.
        """
        return self._flows_for(instrument, [(start_ms, end_ms)])[0]

    def _flows_for(
        self, instrument: instruments.InstrumentId, spans: Sequence[Interval]
    ) -> list[recorded_trades.RecordedFlow | None]:
        """Computes flows of several spans with two archive queries.

        Args:
            instrument: Instrument to read.
            spans: Half-open ``(start_ms, end_ms)`` spans.

        Returns:
            Flow per span, in order; ``None`` where coverage is incomplete.
        """
        results: list[recorded_trades.RecordedFlow | None] = [
            self._cached((instrument, a, b)) for a, b in spans
        ]
        missing = [i for i, flow in enumerate(results) if flow is None]
        if not missing:
            return results
        low = min(spans[i][0] for i in missing)
        high = max(spans[i][1] for i in missing)
        coverage = self.archive.coverage(instrument, low, high)
        covered = [
            i
            for i in missing
            if recorded_trades.covering(coverage, spans[i][0], spans[i][1])
        ]
        if not covered:
            return results
        low = min(spans[i][0] for i in covered)
        high = max(spans[i][1] for i in covered)
        flows = self.archive.minute_flows(instrument, low, high)
        for i in covered:
            start, end = spans[i]
            flow = recorded_trades.span_flow(flows, coverage, start, end)
            if flow is not None:
                self._remember((instrument, start, end), flow)
            results[i] = flow
        return results

    def enrich(self, rows: Sequence[market.Candle]) -> list[market.Candle]:
        """Attaches recorded true delta to fully covered final bars.

        Args:
            rows: Candles of one or more series.

        Returns:
            Candles in the same order; final 1m-and-above bars of
            ``delta_venues`` without taker volume carry the true delta when
            recorded trades cover their whole span and the recorded volume
            agrees with the bar's.
        """
        out = list(rows)
        wanted = [
            i
            for i, c in enumerate(out)
            if c.is_final
            and not c.timeframe.is_seconds
            and c.taker_buy_base_volume is None
            and c.instrument_id.venue in self.delta_venues
        ]
        by_instrument: dict[instruments.InstrumentId, list[int]] = {}
        for i in wanted:
            by_instrument.setdefault(out[i].instrument_id, []).append(i)
        for instrument, indexes in by_instrument.items():
            flows = self._flows_for(
                instrument, [(out[i].start_ms, out[i].end_ms) for i in indexes]
            )
            for i, flow in zip(indexes, flows, strict=True):
                out[i] = recorded_trades.with_true_delta(out[i], flow)
        return out
