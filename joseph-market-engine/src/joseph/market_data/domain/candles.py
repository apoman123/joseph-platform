"""Candle merge semantics and gap detection (pure).

Merge rules for two observations of the same bar:

* A final bar is never replaced by a non-final one (late or out-of-order
  live updates are ignored).
* A final observation replaces a non-final one.
* Live (non-final) updates must not go backwards in receipt time or volume.
* A final bar whose values change is never mutated silently: the result is
  ``REVISED`` with ``revision`` incremented so callers can emit an audit
  record. Fired signals and orders are never rewritten.

Gaps and coverage: on venues with *sparse bars* (stock exchanges: an
interval without trades has no bar, the Alpaca minute-bar convention) a
missing bar is ambiguous. A fetch or a live recording that delivered every
bar of a span proves that span's missing bars had no trades; such spans are
*coverage*. ``find_gaps`` reports a missing bar only when its slot is not
wholly inside coverage, so quiet intervals are not gaps while outages (no
fetch, no recording) still are.
"""

from __future__ import annotations

import bisect
import dataclasses
import enum
from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes

Span = tuple[int, int]
"""Half-open ``[from_ms, to_ms)`` interval."""


class MergeAction(enum.StrEnum):
    """Outcome of merging an incoming candle."""

    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    FINALIZED = "FINALIZED"
    REVISED = "REVISED"
    DUPLICATE = "DUPLICATE"
    IGNORED_STALE = "IGNORED_STALE"


@dataclasses.dataclass(frozen=True, slots=True)
class MergeResult:
    """Result of ``merge_candle``.

    Attributes:
        action: What happened.
        candle: The candle now stored (unchanged on ignore).
    """

    action: MergeAction
    candle: market.Candle

    @property
    def changed(self) -> bool:
        """Whether the stored candle changed."""
        return self.action in (
            MergeAction.INSERTED,
            MergeAction.UPDATED,
            MergeAction.FINALIZED,
            MergeAction.REVISED,
        )


def _same_values(a: market.Candle, b: market.Candle) -> bool:
    """Compares OHLCV values of two observations.

    Args:
        a: First candle.
        b: Second candle.

    Returns:
        True when prices and volume match exactly.
    """
    return (a.open, a.high, a.low, a.close, a.base_volume) == (
        b.open,
        b.high,
        b.low,
        b.close,
        b.base_volume,
    )


def merge_candle(  # noqa: PLR0911 - one return per merge rule.
    existing: market.Candle | None, incoming: market.Candle
) -> MergeResult:
    """Merges an incoming observation into the stored bar.

    Args:
        existing: Currently stored observation of the same bar, if any.
        incoming: New observation.

    Returns:
        The merge outcome.

    Raises:
        ValueError: If the candles describe different bars.
    """
    if existing is None:
        return MergeResult(MergeAction.INSERTED, incoming)
    if (
        existing.instrument_id != incoming.instrument_id
        or existing.timeframe != incoming.timeframe
        or existing.start_ms != incoming.start_ms
    ):
        raise ValueError("merge_candle requires the same bar")
    if existing.is_final:
        if not incoming.is_final:
            return MergeResult(MergeAction.IGNORED_STALE, existing)
        if _same_values(existing, incoming):
            return MergeResult(MergeAction.DUPLICATE, existing)
        revision = max(incoming.revision, existing.revision + 1)
        return MergeResult(
            MergeAction.REVISED,
            dataclasses.replace(incoming, revision=revision),
        )
    if incoming.is_final:
        return MergeResult(
            MergeAction.FINALIZED,
            dataclasses.replace(
                incoming, revision=max(incoming.revision, existing.revision)
            ),
        )
    if (
        incoming.received_at_ms < existing.received_at_ms
        or incoming.base_volume < existing.base_volume
    ):
        return MergeResult(MergeAction.IGNORED_STALE, existing)
    if _same_values(existing, incoming):
        return MergeResult(MergeAction.DUPLICATE, existing)
    return MergeResult(MergeAction.UPDATED, incoming)


@dataclasses.dataclass(frozen=True, slots=True)
class CandleCoverage:
    """Candles of one fetch or stream event and the spans it proved.

    Attributes:
        candles: Ascending candles.
        covered: Spans in which every bar of the series was delivered, so
            a slot there without a bar had no trades (sparse-bar venues
            only; empty when nothing is proven).
        complete: True when no page or size cap truncated the fetch, so
            repeating it now cannot return more bars.
    """

    candles: tuple[market.Candle, ...]
    covered: tuple[Span, ...] = ()
    complete: bool = False


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    """Unites spans that overlap or touch.

    Args:
        spans: Half-open spans in any order (empty ones ignored).

    Returns:
        Disjoint ascending spans.
    """
    out: list[Span] = []
    for low, high in sorted(span for span in spans if span[1] > span[0]):
        if out and low <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], high))
        else:
            out.append((low, high))
    return out


def clip_spans(spans: Iterable[Span], from_ms: int, to_ms: int) -> list[Span]:
    """Restricts spans to ``[from_ms, to_ms)``.

    Args:
        spans: Disjoint ascending spans.
        from_ms: Inclusive start.
        to_ms: Exclusive end.

    Returns:
        Non-empty clipped spans, ascending.
    """
    return [
        (max(low, from_ms), min(high, to_ms))
        for low, high in spans
        if min(high, to_ms) > max(low, from_ms)
    ]


def subtract_spans(spans: Iterable[Span], holes: Iterable[Span]) -> list[Span]:
    """Removes holes from spans.

    Args:
        spans: Spans in any order.
        holes: Spans to cut out, in any order.

    Returns:
        Disjoint ascending remainder.
    """
    remainder = merge_spans(spans)
    for hole_low, hole_high in merge_spans(holes):
        cut: list[Span] = []
        for low, high in remainder:
            if high <= hole_low or low >= hole_high:
                cut.append((low, high))
                continue
            if low < hole_low:
                cut.append((low, hole_low))
            if high > hole_high:
                cut.append((hole_high, high))
        remainder = cut
    return remainder


def _span_end(span: Span) -> int:
    """Returns a span's end (bisect key).

    Args:
        span: Span.

    Returns:
        Exclusive end.
    """
    return span[1]


def _uncovered_bars(
    gap: Span,
    spans: Sequence[Span],
    timeframe: timeframes.Timeframe,
    calendar: sessions.BarCalendar,
) -> list[Span]:
    """Keeps the bars of a gap whose slots are not wholly covered.

    Args:
        gap: ``(first missing bar start, next present bar start or end)``.
        spans: Disjoint ascending coverage.
        timeframe: Bar resolution.
        calendar: Session calendar.

    Returns:
        Sub-gaps, each starting at a bar start and ending at the next bar
        start outside it (or the gap's end).
    """
    low, high = gap
    holes: list[Span] = []
    cursor = low
    for span_low, span_high in spans[
        bisect.bisect_right(spans, low, key=_span_end) :
    ]:
        if span_low >= high:
            break
        if span_low > cursor:
            holes.append((cursor, span_low))
        cursor = max(cursor, span_high)
        if cursor >= high:
            break
    if cursor < high:
        holes.append((cursor, high))
    pieces: list[Span] = []
    for hole_low, hole_high in holes:
        start = calendar.floor(timeframe, hole_low)
        if calendar.end_of(timeframe, start) <= hole_low:
            start = calendar.ceil(timeframe, hole_low)
        if start >= hole_high:
            continue  # No bar overlaps the hole (time between sessions).
        end = (
            high
            if hole_high >= high
            else min(high, calendar.ceil(timeframe, hole_high))
        )
        if pieces and start <= pieces[-1][1]:
            pieces[-1] = (pieces[-1][0], max(pieces[-1][1], end))
        else:
            pieces.append((start, end))
    return pieces


def find_gaps(
    starts: Iterable[int],
    timeframe: timeframes.Timeframe,
    from_ms: int,
    to_ms: int,
    *,
    calendar: sessions.BarCalendar = sessions.CONTINUOUS,
    covered: Iterable[Span] = (),
) -> list[Span]:
    """Finds missing bars in ``[from_ms, to_ms)``.

    Runs in time proportional to the bars present (and the coverage spans),
    not to the slots in the range, so wide ranges of seconds bars stay
    cheap. Bars follow the instrument's session calendar: the time between
    two sessions (nights, weekends, holidays) belongs to no bar and is
    never a gap.

    Args:
        starts: Bar start times present (any order, duplicates allowed).
        timeframe: Bar resolution.
        from_ms: Inclusive range start (rounded up to a bar boundary).
        to_ms: Exclusive range end.
        calendar: Session calendar of the series (``sessions.for_instrument``;
            UTC-aligned bars by default).
        covered: Coverage of a sparse-bar series: a missing bar whose slot
            lies wholly inside one span had no trades and is not a gap.

    Returns:
        Missing ranges as ``(from_ms, to_ms)`` half-open pairs; each starts
        at the first missing bar start and ends at the next bar start that
        is present or covered (or at ``to_ms``).
    """
    first = calendar.ceil(timeframe, from_ms)
    present = sorted(
        start
        for start in set(starts)
        if first <= start < to_ms and calendar.is_aligned(timeframe, start)
    )
    gaps: list[Span] = []
    cursor = first
    for start in present:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = calendar.shift(timeframe, start, 1)
    if cursor < to_ms:
        gaps.append((cursor, to_ms))
    spans = merge_spans(covered)
    if not spans:
        return gaps
    return [
        piece
        for gap in gaps
        for piece in _uncovered_bars(gap, spans, timeframe, calendar)
    ]


class StreamCoverage:
    """Coverage proven by consecutive messages of one live connection.

    A connection that delivers every bar of a series in time order proves,
    between two bars it delivered, that the slots in between had no trades.
    ``restart`` forgets the chain (a reconnect may have lost bars).
    """

    def __init__(self) -> None:
        """Initializes an empty chain."""
        self._end: int | None = None

    def restart(self) -> None:
        """Forgets the chain (after a reconnect)."""
        self._end = None

    def observe(self, start_ms: int, end_ms: int) -> Span | None:
        """Records a bar delivered by the connection.

        Args:
            start_ms: Bar start (any bar of the stream, also outside
                sessions).
            end_ms: Bar end.

        Returns:
            The span newly proven, or ``None`` (first bar of the chain, or
            a repeat of an older bar).
        """
        if self._end is None:
            self._end = end_ms
            return None
        if start_ms < self._end:
            return None
        span = (self._end, end_ms)
        self._end = end_ms
        return span


def sort_unique(candles: Iterable[market.Candle]) -> list[market.Candle]:
    """Sorts by start time, merging duplicate observations of each bar.

    Args:
        candles: Candles of one series in any order.

    Returns:
        Ascending candles with one entry per bar.
    """
    by_start: dict[int, market.Candle] = {}
    for candle in sorted(candles, key=lambda c: (c.start_ms, c.received_at_ms)):
        by_start[candle.start_ms] = merge_candle(
            by_start.get(candle.start_ms), candle
        ).candle
    return [by_start[start] for start in sorted(by_start)]


def finals_only(candles: Sequence[market.Candle]) -> list[market.Candle]:
    """Filters to closed bars.

    Args:
        candles: Candles.

    Returns:
        Only ``is_final`` candles, preserving order.
    """
    return [candle for candle in candles if candle.is_final]
