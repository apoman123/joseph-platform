# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public-trade helpers (pure): contract encoding, de-duplication, coverage.

A ``TradeCoverage`` states which venue-time interval a set of trades
describes *completely*. Bars may only be built (and empty seconds filled)
inside coverage, so a disconnect or a page cap never turns into fabricated
quiet periods.
"""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import market


def to_contract(trade: market.Trade) -> dict[str, object]:
    """Serializes a trade to the contract ``Trade`` object.

    Args:
        trade: Trade.

    Returns:
        JSON-compatible dict.
    """
    return {
        "instrument_id": str(trade.instrument_id),
        "trade_id": trade.trade_id,
        "venue_event_ts_ms": trade.venue_event_ts_ms,
        "received_at_ms": trade.received_at_ms,
        "price": decimals.format_decimal(trade.price),
        "base_quantity": decimals.format_decimal(trade.base_quantity),
        "quote_quantity": decimals.format_optional(trade.quote_quantity),
        "aggressor_side": trade.aggressor_side.value,
        "is_aggregated": trade.is_aggregated,
    }


class RecentIds:
    """Bounded set of recently seen trade ids (oldest forgotten first)."""

    def __init__(self, capacity: int = 50_000) -> None:
        """Initializes an empty set.

        Args:
            capacity: Maximum remembered ids.

        Raises:
            ValueError: If ``capacity`` is not positive.
        """
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._order: collections.deque[str] = collections.deque()
        self._ids: set[str] = set()

    def __len__(self) -> int:
        """Returns the number of remembered ids."""
        return len(self._ids)

    def __contains__(self, trade_id: object) -> bool:
        """Checks whether an id was seen recently.

        Args:
            trade_id: Venue trade id.

        Returns:
            True when remembered.
        """
        return trade_id in self._ids

    def add(self, trade_id: str) -> bool:
        """Remembers an id.

        Args:
            trade_id: Venue trade id.

        Returns:
            True when the id is new, False for a duplicate.
        """
        if trade_id in self._ids:
            return False
        self._ids.add(trade_id)
        self._order.append(trade_id)
        while len(self._order) > self._capacity:
            self._ids.discard(self._order.popleft())
        return True

    def fresh(self, trades: Iterable[market.Trade]) -> list[market.Trade]:
        """Filters out trades whose id was already seen, remembering the rest.

        Args:
            trades: Candidate trades.

        Returns:
            Trades seen for the first time, in input order.
        """
        return [t for t in trades if self.add(t.trade_id)]


def sort_trades(trades: Iterable[market.Trade]) -> list[market.Trade]:
    """Sorts trades by venue time, keeping venue order within a millisecond.

    Args:
        trades: Trades in venue order (possibly several pages).

    Returns:
        Trades ascending by ``venue_event_ts_ms`` (stable).
    """
    return sorted(trades, key=lambda t: t.venue_event_ts_ms)


def keep_latest(
    trades: Sequence[market.Trade], limit: int | None
) -> list[market.Trade]:
    """Keeps the most recent ``limit`` trades, cutting at a whole millisecond.

    Dropping part of a millisecond would leave an incomplete instant at the
    start, so trades sharing the cut millisecond are dropped together
    (unless that would drop everything).

    Args:
        trades: Ascending trades.
        limit: Maximum trades, or ``None`` for all.

    Returns:
        Ascending suffix of ``trades``.
    """
    if limit is None or len(trades) <= limit:
        return list(trades)
    if limit <= 0:
        return []
    kept = list(trades[-limit:])
    cut_ms = kept[0].venue_event_ts_ms
    if trades[-limit - 1].venue_event_ts_ms == cut_ms:
        whole = [t for t in kept if t.venue_event_ts_ms > cut_ms]
        if whole:
            return whole
    return kept


@dataclasses.dataclass(frozen=True, slots=True)
class TradeCoverage:
    """Trades known to be complete over ``[from_ms, to_ms)`` (venue time).

    Attributes:
        trades: Every public trade of the interval, ascending.
        from_ms: Inclusive start of the complete interval.
        to_ms: Exclusive end of the complete interval.
    """

    trades: tuple[market.Trade, ...]
    from_ms: int
    to_ms: int

    def __post_init__(self) -> None:
        """Validates the interval and that trades lie inside it.

        Raises:
            ValueError: If the interval is inverted or a trade lies outside.
        """
        if self.to_ms < self.from_ms:
            raise ValueError("coverage ends before it starts")
        for trade in self.trades:
            if not self.from_ms <= trade.venue_event_ts_ms < self.to_ms:
                raise ValueError("trade outside its coverage interval")

    @property
    def is_empty(self) -> bool:
        """Whether the covered interval has zero length."""
        return self.to_ms <= self.from_ms


def coverage_from_suffix(
    trades: Sequence[market.Trade], start_ms: int, end_ms: int, complete: bool
) -> TradeCoverage:
    """Builds the coverage of a newest-first paging result.

    Args:
        trades: Ascending trades collected backwards from ``end_ms``.
        start_ms: Requested inclusive start.
        end_ms: Exclusive end the paging started from.
        complete: True when paging reached ``start_ms``.

    Returns:
        Coverage ``[start_ms, end_ms)`` when complete, otherwise from the
        first whole millisecond of the collected trades.
    """
    inside = [t for t in trades if start_ms <= t.venue_event_ts_ms < end_ms]
    if complete:
        return TradeCoverage(tuple(inside), start_ms, end_ms)
    if not inside:
        return TradeCoverage((), end_ms, end_ms)
    first_ms = inside[0].venue_event_ts_ms
    whole = tuple(t for t in inside if t.venue_event_ts_ms > first_ms)
    return TradeCoverage(whole, first_ms + 1, end_ms)


def coverage_from_prefix(
    trades: Sequence[market.Trade], start_ms: int, end_ms: int, complete: bool
) -> TradeCoverage:
    """Builds the coverage of an oldest-first paging result.

    Args:
        trades: Ascending trades collected forwards from ``start_ms``.
        start_ms: Inclusive start the paging started from.
        end_ms: Requested exclusive end.
        complete: True when paging reached ``end_ms``.

    Returns:
        Coverage ``[start_ms, end_ms)`` when complete, otherwise up to the
        last whole millisecond of the collected trades.
    """
    inside = [t for t in trades if start_ms <= t.venue_event_ts_ms < end_ms]
    if complete:
        return TradeCoverage(tuple(inside), start_ms, end_ms)
    if not inside:
        return TradeCoverage((), start_ms, start_ms)
    last_ms = inside[-1].venue_event_ts_ms
    whole = tuple(t for t in inside if t.venue_event_ts_ms < last_ms)
    return TradeCoverage(whole, start_ms, last_ms)
