"""Candle merge semantics and gap detection (pure).

Merge rules for two observations of the same bar:

* A final bar is never replaced by a non-final one (late or out-of-order
  live updates are ignored).
* A final observation replaces a non-final one.
* Live (non-final) updates must not go backwards in receipt time or volume.
* A final bar whose values change is never mutated silently: the result is
  ``REVISED`` with ``revision`` incremented so callers can emit an audit
  record. Fired signals and orders are never rewritten.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import market
from joseph.domain import timeframes


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


def find_gaps(
    starts: Iterable[int],
    timeframe: timeframes.Timeframe,
    from_ms: int,
    to_ms: int,
) -> list[tuple[int, int]]:
    """Finds missing bars in ``[from_ms, to_ms)``.

    Runs in time proportional to the bars present, not to the slots in the
    range, so wide ranges of seconds bars stay cheap.

    Args:
        starts: Bar start times present (any order, duplicates allowed).
        timeframe: Bar resolution.
        from_ms: Inclusive range start (rounded up to a bar boundary).
        to_ms: Exclusive range end.

    Returns:
        Missing ranges as ``(from_ms, to_ms)`` half-open pairs.
    """
    first = timeframe.floor(from_ms)
    if first < from_ms:
        first = timeframe.end_of(first)
    present = sorted(
        start
        for start in set(starts)
        if first <= start < to_ms and timeframe.is_aligned(start)
    )
    gaps: list[tuple[int, int]] = []
    cursor = first
    for start in present:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = timeframe.end_of(start)
    if cursor < to_ms:
        gaps.append((cursor, to_ms))
    return gaps


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
