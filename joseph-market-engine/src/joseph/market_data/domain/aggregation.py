# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bars aggregated from a shorter native timeframe that tiles them (pure).

Some venues lack a timeframe natively although a shorter native timeframe
tiles it exactly on the venue's session calendar: Hyperliquid's ``1M`` is
30-day epoch buckets and Lighter has no ``1w`` or ``1M``, yet ``1d`` bars
tile calendar weeks and months of a 24x7 market. Such bars are folded from
the child bars with the rules venues use for their own klines, so they equal
native bars:

* open of the first child, close of the last, the extreme high and low;
* base volume is a sum; quote turnover, trade count and taker buy volume
  are sums too, each ``None`` unless every child carries it (taker volume
  also needs one buy-volume method across the children);
* completeness: a parent is built only when every child slot of its span
  up to the current one holds a child bar. A missing child (an outage, a
  listing part-way through the period, or a venue that omits empty bars)
  leaves the parent as a gap instead of a guess. Only the in-progress child
  slot may be empty (no trade yet), so the live parent is the closed
  children plus the current child's updates;
* finality: a parent is final once its span has ended and every child of
  it is final.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Iterable
from collections.abc import Sequence
from typing import TypeAlias

from joseph.domain import decimals
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules

# Fields of a parent bar whose change is worth re-emitting; ``is_final`` last.
_EmitKey: TypeAlias = tuple[object, ...]


def source_timeframe(
    calendar: sessions.BarCalendar,
    target: timeframes.Timeframe,
    native: Iterable[timeframes.Timeframe],
) -> timeframes.Timeframe | None:
    """Returns the longest native timeframe that tiles ``target`` exactly.

    Args:
        calendar: Session calendar of the venue.
        target: Timeframe the venue lacks.
        native: Timeframes the venue publishes as klines.

    Returns:
        The child timeframe, or ``None`` when none tiles ``target``.
    """
    candidates = [
        tf
        for tf in native
        if not tf.is_seconds
        and tf is not target
        and calendar.contains(target, tf)
    ]
    return max(candidates, key=lambda tf: tf.duration_ms, default=None)


def derived_timeframes(
    calendar: sessions.BarCalendar, native: Iterable[timeframes.Timeframe]
) -> dict[timeframes.Timeframe, timeframes.Timeframe]:
    """Maps every minute-and-above timeframe a venue lacks to its source.

    Args:
        calendar: Session calendar of the venue.
        native: Timeframes the venue publishes as klines.

    Returns:
        ``{missing timeframe: native child timeframe}`` in timeframe order.
    """
    have = frozenset(native)
    derived: dict[timeframes.Timeframe, timeframes.Timeframe] = {}
    for target in timeframes.MINUTE_AND_ABOVE:
        if target in have:
            continue
        child = source_timeframe(calendar, target, have)
        if child is not None:
            derived[target] = child
    return derived


def aggregated_source(source: str, child: timeframes.Timeframe) -> str:
    """Returns the provenance label of a bar aggregated from ``child`` bars.

    Args:
        source: Source of the newest child (e.g. ``hyperliquid.rest``).
        child: Child timeframe.

    Returns:
        Label such as ``hyperliquid.rest.agg1d``.
    """
    return f"{source}.agg{child.value}"


def _total(values: Sequence[decimal.Decimal | None]) -> decimal.Decimal | None:
    """Sums values that must all be present.

    Args:
        values: Optional values.

    Returns:
        The sum, or ``None`` if any value is missing.
    """
    if any(value is None for value in values):
        return None
    return sum((v for v in values if v is not None), decimals.ZERO)


def _buy_volume(
    children: Sequence[market.Candle],
) -> tuple[decimal.Decimal | None, market.BuyVolumeMethod | None]:
    """Sums the children's buy volume when they share one method.

    Args:
        children: Child bars.

    Returns:
        ``(taker_buy_base_volume, taker_buy_method)``; both ``None`` when a
        child lacks buy volume or the methods differ.
    """
    taker = _total([c.taker_buy_base_volume for c in children])
    if taker is None:
        return None, None
    methods = {c.buy_volume_method for c in children}
    if len(methods) != 1:
        return None, None
    # Keep the children's own label when they agree (``None`` reads as taker),
    # so folding unlabelled bars yields an unlabelled parent like a native one.
    labels = {c.taker_buy_method for c in children}
    return taker, labels.pop() if len(labels) == 1 else methods.pop()


def fold(
    children: Sequence[market.Candle],
    *,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    end_ms: int,
    is_final: bool,
    source: str,
) -> market.Candle:
    """Folds ascending child bars into one bar of ``timeframe``.

    Args:
        children: Non-empty ascending child bars inside the span.
        timeframe: Parent timeframe.
        start_ms: Parent start, epoch milliseconds.
        end_ms: Parent exclusive end, epoch milliseconds.
        is_final: Parent finality.
        source: Provenance label.

    Returns:
        Parent candle.
    """
    first, last = children[0], children[-1]
    counts = [c.trade_count for c in children]
    events = [c.event_ts_ms for c in children if c.event_ts_ms is not None]
    taker, method = _buy_volume(children)
    return market.Candle(
        instrument_id=first.instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=first.open,
        high=max(c.high for c in children),
        low=min(c.low for c in children),
        close=last.close,
        base_volume=sum((c.base_volume for c in children), decimals.ZERO),
        is_final=is_final,
        source=source,
        feed_state=last.feed_state,
        received_at_ms=max(c.received_at_ms for c in children),
        revision=max(c.revision for c in children),
        quote_turnover=_total([c.quote_turnover for c in children]),
        trade_count=(
            None
            if any(n is None for n in counts)
            else sum(n for n in counts if n is not None)
        ),
        event_ts_ms=max(events) if events else None,
        taker_buy_base_volume=taker,
        taker_buy_method=method,
    )


def child_slots(
    calendar: sessions.BarCalendar,
    target: timeframes.Timeframe,
    child: timeframes.Timeframe,
    parent_start: int,
) -> list[int]:
    """Lists the child bar starts that tile one parent bar.

    Args:
        calendar: Session calendar.
        target: Parent timeframe.
        child: Child timeframe.
        parent_start: Aligned parent start, epoch milliseconds.

    Returns:
        Ascending child starts, epoch milliseconds.
    """
    parent_end = calendar.end_of(target, parent_start)
    slots: list[int] = []
    cursor = parent_start
    while cursor < parent_end:
        slots.append(cursor)
        cursor = calendar.shift(child, cursor, 1)
    return slots


def aggregate_one(
    children: Sequence[market.Candle],
    *,
    calendar: sessions.BarCalendar,
    target: timeframes.Timeframe,
    child: timeframes.Timeframe,
    parent_start: int,
    now_ms: int,
) -> market.Candle | None:
    """Builds one parent bar when its children are complete.

    Args:
        children: Child bars of the parent (any order, one per start).
        calendar: Session calendar.
        target: Parent timeframe.
        child: Child timeframe.
        parent_start: Aligned parent start, epoch milliseconds.
        now_ms: Current time, epoch milliseconds (decides which child slots
            have closed).

    Returns:
        Parent candle, or ``None`` when a closed child slot is missing.
    """
    parent_end = calendar.end_of(target, parent_start)
    by_start = {c.start_ms: c for c in children}
    current = calendar.floor(child, now_ms)
    ordered: list[market.Candle] = []
    complete = True
    for slot in child_slots(calendar, target, child, parent_start):
        bar = by_start.get(slot)
        if bar is not None:
            ordered.append(bar)
        elif slot < current or calendar.end_of(child, slot) <= now_ms:
            return None
        else:
            complete = False
    if not ordered:
        return None
    final = (
        complete
        and parent_end <= now_ms
        and all(bar.is_final for bar in ordered)
    )
    return fold(
        ordered,
        timeframe=target,
        start_ms=parent_start,
        end_ms=parent_end,
        is_final=final,
        source=aggregated_source(ordered[-1].source, child),
    )


def aggregate(
    children: Iterable[market.Candle],
    *,
    calendar: sessions.BarCalendar,
    target: timeframes.Timeframe,
    child: timeframes.Timeframe,
    now_ms: int,
) -> list[market.Candle]:
    """Aggregates child bars into every complete parent bar.

    Args:
        children: Child bars of one series (duplicates are merged).
        calendar: Session calendar.
        target: Parent timeframe.
        child: Child timeframe tiling ``target``.
        now_ms: Current time, epoch milliseconds.

    Returns:
        Ascending parent bars (incomplete parents are omitted).

    Raises:
        ValueError: If ``child`` does not tile ``target`` on the calendar, or a
            bar in ``children`` is not of the ``child`` timeframe.
    """
    if not calendar.contains(target, child):
        raise ValueError(f"{child.value} does not tile {target.value}")
    groups: dict[int, list[market.Candle]] = {}
    for bar in candle_rules.sort_unique(children):
        if bar.timeframe is not child:
            raise ValueError("child bar of another timeframe")
        groups.setdefault(calendar.floor(target, bar.start_ms), []).append(bar)
    out: list[market.Candle] = []
    for parent_start in sorted(groups):
        parent = aggregate_one(
            groups[parent_start],
            calendar=calendar,
            target=target,
            child=child,
            parent_start=parent_start,
            now_ms=now_ms,
        )
        if parent is not None:
            out.append(parent)
    return out


def _emit_key(candle: market.Candle) -> _EmitKey:
    """Returns the values whose change makes a parent worth re-emitting.

    Args:
        candle: Parent candle.

    Returns:
        Comparable tuple.
    """
    return (
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.base_volume,
        candle.quote_turnover,
        candle.trade_count,
        candle.taker_buy_base_volume,
        candle.is_final,
    )


@dataclasses.dataclass(slots=True)
class LiveAggregator:
    """Maintains live parent bars from a stream of child bar updates.

    Seed it with the children of the current parent (closed children over
    REST), then feed every child update; it returns the parents whose
    values or finality changed.

    Attributes:
        calendar: Session calendar.
        target: Parent timeframe.
        child: Child timeframe.
        keep_parents: Number of newest parents kept for late child updates;
            updates for older parents are dropped.
    """

    calendar: sessions.BarCalendar
    target: timeframes.Timeframe
    child: timeframes.Timeframe
    keep_parents: int = 2
    _children: dict[int, dict[int, market.Candle]] = dataclasses.field(
        default_factory=dict
    )
    _emitted: dict[int, _EmitKey] = dataclasses.field(default_factory=dict)

    def parent_start(self, child_start: int) -> int:
        """Returns the parent start of a child bar.

        Args:
            child_start: Child bar start, epoch milliseconds.

        Returns:
            Parent start, epoch milliseconds.
        """
        return self.calendar.floor(self.target, child_start)

    def missing_before(self, child_start: int) -> list[int]:
        """Lists the earlier child slots of the same parent not yet held.

        Args:
            child_start: Child bar start, epoch milliseconds.

        Returns:
            Starts of earlier child slots of that parent not yet seen.
        """
        parent = self.parent_start(child_start)
        held = self._children.get(parent, {})
        return [
            slot
            for slot in child_slots(
                self.calendar, self.target, self.child, parent
            )
            if slot < child_start and slot not in held
        ]

    def update(
        self, children: Iterable[market.Candle], now_ms: int
    ) -> list[market.Candle]:
        """Merges child updates and rebuilds the affected parents.

        Args:
            children: Child bar updates (any order); bars of another timeframe
                are ignored.
            now_ms: Current time, epoch milliseconds.

        Returns:
            Changed parents, ascending (incomplete parents are omitted).
        """
        touched: set[int] = set()
        for bar in children:
            if bar.timeframe is not self.child:
                continue
            parent = self.parent_start(bar.start_ms)
            # A parent older than every held one was already evicted.
            if self._children and parent < min(self._children):
                continue
            held = self._children.setdefault(parent, {})
            result = candle_rules.merge_candle(held.get(bar.start_ms), bar)
            if result.changed:
                held[bar.start_ms] = result.candle
                touched.add(parent)
        # Finality depends on time, so parents never emitted and parents whose
        # span has ended since their last non-final emission are rebuilt too.
        touched.update(p for p in self._children if p not in self._emitted)
        touched.update(
            p
            for p in self._children
            if self.calendar.end_of(self.target, p) <= now_ms
            and not self._emitted.get(p, (False,))[-1]
        )
        out: list[market.Candle] = []
        for parent in sorted(touched):
            candle = aggregate_one(
                list(self._children[parent].values()),
                calendar=self.calendar,
                target=self.target,
                child=self.child,
                parent_start=parent,
                now_ms=now_ms,
            )
            if candle is None:
                continue
            key = _emit_key(candle)
            if self._emitted.get(parent) == key:
                continue
            self._emitted[parent] = key
            out.append(candle)
        while len(self._children) > self.keep_parents:
            oldest = min(self._children)
            del self._children[oldest]
            self._emitted.pop(oldest, None)
        return out
