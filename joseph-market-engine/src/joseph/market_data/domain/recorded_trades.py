"""Recorded public trades: coverage, archived seconds and true delta (pure).

The engine records the venue's public trades as 1s aggregates (the
``1s`` candles folded from trades, see ``trade_bars``) together with explicit
*coverage*: venue-time intervals in which every trade was recorded. Only
complete seconds are archived, so an aggregate never double counts or
misses a trade, and coverage intervals are whole seconds.

From that archive:

* seconds bars of any seconds timeframe are rebuilt for buckets that lie
  wholly inside one coverage interval (same rules as the live builder:
  empty buckets after a recorded trade are flat at its close; nothing is
  filled before the first recorded trade of an interval);
* a 1m-and-above bar gets a true delta (``taker_buy_base_volume`` with
  ``taker_buy_method = taker``) only when its whole span lies inside one
  coverage interval, every recorded trade of the span has a published
  aggressor side, and the recorded volume equals the bar's volume (within
  ``VOLUME_TOLERANCE``, which absorbs venues' float rounding). Any other
  bar keeps no taker volume, so indicators use their labelled estimate.
"""

from __future__ import annotations

import bisect
import dataclasses
import decimal
from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import aggregation
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

SECOND_MS = 1_000
MINUTE_MS = 60_000
VOLUME_TOLERANCE = decimal.Decimal("0.000001")
"""Relative difference tolerated between recorded and kline volume."""

Interval = tuple[int, int]


def ceil_to(ts_ms: int, unit_ms: int) -> int:
    """Rounds a time up to a multiple of ``unit_ms``.

    Args:
        ts_ms: Time.
        unit_ms: Unit length.

    Returns:
        Smallest multiple of ``unit_ms`` not below ``ts_ms``.
    """
    return -(-ts_ms // unit_ms) * unit_ms


def floor_to(ts_ms: int, unit_ms: int) -> int:
    """Rounds a time down to a multiple of ``unit_ms``.

    Args:
        ts_ms: Time.
        unit_ms: Unit length.

    Returns:
        Largest multiple of ``unit_ms`` not above ``ts_ms``.
    """
    return ts_ms - ts_ms % unit_ms


def whole_seconds(from_ms: int, to_ms: int) -> Interval | None:
    """Shrinks a complete interval to the whole seconds inside it.

    Args:
        from_ms: Inclusive start of a complete trade interval.
        to_ms: Exclusive end.

    Returns:
        ``(start, end)`` aligned to seconds, or ``None`` when no whole
        second lies inside.
    """
    start, end = ceil_to(from_ms, SECOND_MS), floor_to(to_ms, SECOND_MS)
    return (start, end) if end > start else None


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Unites intervals that overlap or touch.

    Args:
        intervals: Half-open intervals in any order (empty ones ignored).

    Returns:
        Disjoint ascending intervals.
    """
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def covering(intervals: Sequence[Interval], start: int, end: int) -> bool:
    """Checks whether one interval contains ``[start, end)``.

    Args:
        intervals: Disjoint ascending intervals.
        start: Inclusive start.
        end: Exclusive end.

    Returns:
        True when the whole span is covered.
    """
    index = bisect.bisect_right(intervals, (start, float("inf"))) - 1
    if index < 0:
        return False
    low, high = intervals[index]
    return low <= start and end <= high


def uncovered(
    intervals: Sequence[Interval], start: int, end: int
) -> list[Interval]:
    """Lists the parts of ``[start, end)`` outside the intervals.

    Args:
        intervals: Disjoint ascending intervals.
        start: Inclusive start.
        end: Exclusive end.

    Returns:
        Ascending holes.
    """
    holes: list[Interval] = []
    cursor = start
    for low, high in intervals:
        if high <= cursor:
            continue
        if low >= end:
            break
        if low > cursor:
            holes.append((cursor, min(low, end)))
        cursor = max(cursor, high)
        if cursor >= end:
            break
    if cursor < end:
        holes.append((cursor, end))
    return holes


def clip(intervals: Sequence[Interval], before_ms: int) -> list[Interval]:
    """Drops coverage before a retention cut-off.

    Args:
        intervals: Disjoint ascending intervals.
        before_ms: Cut-off.

    Returns:
        Intervals restricted to ``[before_ms, inf)``.
    """
    return [(max(a, before_ms), b) for a, b in intervals if b > before_ms]


def archivable(bar: market.Candle) -> bool:
    """Checks whether a built 1s bar is stored as a recorded aggregate.

    Flat (no-trade) seconds are not stored: coverage already proves them.

    Args:
        bar: 1s candle.

    Returns:
        True for a final 1s bar with traded volume.
    """
    return (
        bar.timeframe is timeframes.Timeframe.S1
        and bar.is_final
        and bar.base_volume > decimals.ZERO
    )


def archivable_seconds(
    coverage: trades_lib.TradeCoverage,
    instrument: instruments.InstrumentId,
    *,
    source: str,
    now_ms: int,
) -> tuple[list[market.Candle], Interval | None]:
    """Folds a complete trade interval into archivable 1s aggregates.

    The live seconds builder is used (with a short flat-run window, so a
    sparse interval of days stays cheap); flat seconds are implied by the
    returned coverage rather than stored.

    Args:
        coverage: Trades complete over an interval.
        instrument: Instrument.
        source: Provenance label of the aggregates.
        now_ms: Receipt time.

    Returns:
        ``(aggregates, whole-second coverage)``; the coverage is ``None``
        when no whole second lies inside the interval.
    """
    window = whole_seconds(coverage.from_ms, coverage.to_ms)
    if window is None:
        return [], None
    builder = trade_bars.TradeBarBuilder(
        instrument,
        timeframes.Timeframe.S1,
        source=source,
        retention_bars=600,
        dedupe_capacity=max(1, len(coverage.trades)),
    )
    builder.start_segment(coverage.from_ms)
    bars: dict[int, market.Candle] = {}
    ordered = trades_lib.sort_trades(coverage.trades)
    for bar in builder.add(ordered, now_ms):
        bars[bar.start_ms] = bar
    for bar in builder.finalize_through(coverage.to_ms, now_ms):
        bars[bar.start_ms] = bar
    kept = [
        bars[start]
        for start in sorted(bars)
        if archivable(bars[start])
        and window[0] <= start
        and bars[start].end_ms <= window[1]
    ]
    return kept, window


def _flat(
    previous: market.Candle,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    now_ms: int,
) -> market.Candle:
    """Builds a no-trade bar at the previous close.

    Args:
        previous: Last recorded 1s aggregate before the bar.
        timeframe: Seconds timeframe.
        start_ms: Bar start.
        now_ms: Receipt time.

    Returns:
        Flat final candle (the live builder's convention).
    """
    price = previous.close
    return market.Candle(
        instrument_id=previous.instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=timeframe.end_of(start_ms),
        open=price,
        high=price,
        low=price,
        close=price,
        base_volume=decimals.ZERO,
        is_final=True,
        source=previous.source,
        feed_state=previous.feed_state,
        received_at_ms=now_ms,
        quote_turnover=decimals.ZERO,
        trade_count=0,
        taker_buy_base_volume=decimals.ZERO,
    )


def rebuild_seconds(
    seconds: Sequence[market.Candle],
    coverage: Sequence[Interval],
    timeframe: timeframes.Timeframe,
    start_ms: int,
    end_ms: int,
    *,
    previous: market.Candle | None,
    now_ms: int,
) -> list[market.Candle]:
    """Rebuilds seconds bars from archived 1s aggregates.

    Args:
        seconds: Archived 1s aggregates in ``[start_ms, end_ms)``,
            ascending.
        coverage: Disjoint ascending coverage intervals.
        timeframe: Seconds timeframe to build.
        start_ms: Inclusive start.
        end_ms: Exclusive end.
        previous: Last archived aggregate before ``start_ms`` (for the
            close of leading empty buckets).
        now_ms: Receipt time stamped on flat bars.

    Returns:
        Final bars of every bucket wholly inside one coverage interval.

    Raises:
        ValueError: For a timeframe that is not seconds.
    """
    if not timeframe.is_seconds:
        raise ValueError("only seconds bars are rebuilt from 1s aggregates")
    starts = [bar.start_ms for bar in seconds]
    out: list[market.Candle] = []
    for low, high in coverage:
        begin = max(low, start_ms)
        cursor = timeframe.floor(begin)
        if cursor < begin:
            cursor = timeframe.end_of(cursor)
        stop = min(high, end_ms)
        while timeframe.end_of(cursor) <= stop:
            bucket_end = timeframe.end_of(cursor)
            lo = bisect.bisect_left(starts, cursor)
            hi = bisect.bisect_left(starts, bucket_end)
            inside = seconds[lo:hi]
            if inside:
                out.append(
                    aggregation.fold(
                        inside,
                        timeframe=timeframe,
                        start_ms=cursor,
                        end_ms=bucket_end,
                        is_final=True,
                        source=inside[-1].source,
                    )
                )
            else:
                prior = seconds[lo - 1] if lo > 0 else previous
                if prior is not None and prior.start_ms >= low:
                    out.append(_flat(prior, timeframe, cursor, now_ms))
            cursor = bucket_end
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class MinuteFlow:
    """Recorded trade totals of one minute (an archive roll-up).

    Attributes:
        start_ms: Minute start.
        base_volume: Recorded base volume.
        taker_buy_base_volume: Recorded buyer-aggressor volume, ``None``
            when a trade of the minute had no published side.
    """

    start_ms: int
    base_volume: decimal.Decimal
    taker_buy_base_volume: decimal.Decimal | None


def minute_flows(seconds: Iterable[market.Candle]) -> list[MinuteFlow]:
    """Rolls archived 1s aggregates up into per-minute totals.

    Args:
        seconds: 1s aggregates (any order).

    Returns:
        Ascending minute totals (minutes without trades are absent).
    """
    volumes: dict[int, decimal.Decimal] = {}
    takers: dict[int, decimal.Decimal | None] = {}
    for bar in seconds:
        minute = floor_to(bar.start_ms, MINUTE_MS)
        volumes[minute] = volumes.get(minute, decimals.ZERO) + bar.base_volume
        held = takers.get(minute, decimals.ZERO)
        taker = bar.taker_buy_base_volume
        takers[minute] = None if held is None or taker is None else held + taker
    return [
        MinuteFlow(minute, volumes[minute], takers[minute])
        for minute in sorted(volumes)
    ]


@dataclasses.dataclass(frozen=True, slots=True)
class RecordedFlow:
    """Recorded trade totals of a fully covered span.

    Attributes:
        base_volume: Recorded base volume.
        taker_buy_base_volume: Recorded buyer-aggressor volume.
    """

    base_volume: decimal.Decimal
    taker_buy_base_volume: decimal.Decimal


def span_flow(
    flows: Sequence[MinuteFlow],
    coverage: Sequence[Interval],
    start_ms: int,
    end_ms: int,
) -> RecordedFlow | None:
    """Sums the recorded trades of a span that coverage proves complete.

    Args:
        flows: Minute totals, ascending (may extend beyond the span).
        coverage: Disjoint ascending coverage intervals.
        start_ms: Span start (minute aligned).
        end_ms: Span end (minute aligned).

    Returns:
        Totals, or ``None`` when the span is not wholly covered, is not
        minute aligned, or a trade of it had no published side.
    """
    if start_ms % MINUTE_MS or end_ms % MINUTE_MS:
        return None
    if not covering(coverage, start_ms, end_ms):
        return None
    keys = [flow.start_ms for flow in flows]
    lo = bisect.bisect_left(keys, start_ms)
    hi = bisect.bisect_left(keys, end_ms)
    volume = decimals.ZERO
    taker = decimals.ZERO
    for flow in flows[lo:hi]:
        if flow.taker_buy_base_volume is None:
            return None
        volume += flow.base_volume
        taker += flow.taker_buy_base_volume
    return RecordedFlow(volume, taker)


def volumes_agree(recorded: decimal.Decimal, kline: decimal.Decimal) -> bool:
    """Checks that recorded trades account for the whole kline volume.

    Args:
        recorded: Sum of recorded trade quantities.
        kline: The venue bar's base volume.

    Returns:
        True when they agree within ``VOLUME_TOLERANCE`` (relative).
    """
    scale = max(abs(recorded), abs(kline))
    return abs(recorded - kline) <= scale * VOLUME_TOLERANCE


def with_true_delta(
    candle: market.Candle, flow: RecordedFlow | None
) -> market.Candle:
    """Attaches a recorded true delta to a fully covered final bar.

    Args:
        candle: Final 1m-and-above bar without taker volume.
        flow: Recorded totals of its span (``None`` when not covered).

    Returns:
        The bar with ``taker_buy_base_volume`` and ``taker = method`` when
        the recorded volume matches the bar's, otherwise ``candle``
        unchanged.
    """
    if (
        flow is None
        or not candle.is_final
        or candle.timeframe.is_seconds
        or candle.taker_buy_base_volume is not None
        or not volumes_agree(flow.base_volume, candle.base_volume)
    ):
        return candle
    return dataclasses.replace(
        candle,
        taker_buy_base_volume=min(
            flow.taker_buy_base_volume, candle.base_volume
        ),
        taker_buy_method=market.BuyVolumeMethod.TAKER,
    )
