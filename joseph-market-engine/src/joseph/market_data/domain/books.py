"""Order-book reconciliation and book-walk slippage (pure).

``OrderBookBuilder`` maintains a book from a REST snapshot plus validated
increments. Any sequence gap marks the book as requiring a resync; a book
that needs resync or exceeds its age budget is ``stale`` and must never be
used to price real slippage.
"""

from __future__ import annotations

import decimal
import enum
from collections.abc import Iterable

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market

DEFAULT_SLIPPAGE_NOTIONAL_USD = decimal.Decimal(50_000)

LevelInput = tuple[decimal.Decimal, decimal.Decimal]


class ApplyResult(enum.StrEnum):
    """Outcome of applying a book increment."""

    APPLIED = "APPLIED"
    IGNORED_OLD = "IGNORED_OLD"
    RESYNC_REQUIRED = "RESYNC_REQUIRED"


class OrderBookBuilder:
    """Snapshot + delta order book with sequence validation."""

    def __init__(
        self,
        instrument_id: instruments.InstrumentId,
        source: str,
        max_age_ms: int = 5_000,
    ) -> None:
        """Initializes an empty (resync-required) builder.

        Args:
            instrument_id: Exact venue instrument.
            source: Provenance label.
            max_age_ms: Age after which the book is stale.
        """
        self._instrument_id = instrument_id
        self._source = source
        self._max_age_ms = max_age_ms
        self._bids: dict[decimal.Decimal, decimal.Decimal] = {}
        self._asks: dict[decimal.Decimal, decimal.Decimal] = {}
        self._sequence: int | None = None
        self._ts_ms = 0
        self._received_ms = 0
        self._needs_resync = True
        self._awaiting_first_diff = False
        self.resyncs = 0

    @property
    def needs_resync(self) -> bool:
        """Whether a fresh snapshot is required."""
        return self._needs_resync

    @property
    def sequence(self) -> int | None:
        """Last applied venue sequence/update id."""
        return self._sequence

    def apply_snapshot(
        self,
        bids: Iterable[LevelInput],
        asks: Iterable[LevelInput],
        sequence: int | None,
        ts_ms: int,
        received_ms: int,
    ) -> None:
        """Replaces the book with a snapshot.

        Args:
            bids: Bid levels.
            asks: Ask levels.
            sequence: Snapshot sequence (``lastUpdateId`` on Binance).
            ts_ms: Venue time.
            received_ms: Receipt time.
        """
        self._bids = {p: q for p, q in bids if q > decimals.ZERO}
        self._asks = {p: q for p, q in asks if q > decimals.ZERO}
        self._sequence = sequence
        self._ts_ms = ts_ms
        self._received_ms = received_ms
        self._needs_resync = False
        self._awaiting_first_diff = True

    def _apply_levels(
        self,
        side: dict[decimal.Decimal, decimal.Decimal],
        levels: Iterable[LevelInput],
    ) -> None:
        """Applies absolute level quantities (0 removes the level).

        Args:
            side: Side mapping to mutate.
            levels: Price/quantity pairs.
        """
        for price, quantity in levels:
            if quantity <= decimals.ZERO:
                side.pop(price, None)
            else:
                side[price] = quantity

    def _resync(self) -> ApplyResult:
        """Marks the book as requiring a new snapshot.

        Returns:
            ``RESYNC_REQUIRED``.
        """
        self._needs_resync = True
        self.resyncs += 1
        return ApplyResult.RESYNC_REQUIRED

    def apply_binance_diff(
        self,
        *,
        first_update_id: int,
        final_update_id: int,
        previous_final_update_id: int,
        bids: Iterable[LevelInput],
        asks: Iterable[LevelInput],
        ts_ms: int,
        received_ms: int,
    ) -> ApplyResult:
        """Applies a Binance USD-M ``depthUpdate`` (U/u/pu rules).

        Events older than the snapshot are dropped. The first applied event
        must straddle the snapshot id; later events must chain ``pu`` to the
        previous ``u``.

        Args:
            first_update_id: ``U``.
            final_update_id: ``u``.
            previous_final_update_id: ``pu``.
            bids: Bid level updates.
            asks: Ask level updates.
            ts_ms: Venue event time.
            received_ms: Receipt time.

        Returns:
            The apply outcome.
        """
        if self._needs_resync or self._sequence is None:
            return ApplyResult.RESYNC_REQUIRED
        if final_update_id < self._sequence:
            return ApplyResult.IGNORED_OLD
        if self._awaiting_first_diff:
            if not first_update_id <= self._sequence <= final_update_id:
                return self._resync()
            self._awaiting_first_diff = False
        elif previous_final_update_id != self._sequence:
            return self._resync()
        self._apply_levels(self._bids, bids)
        self._apply_levels(self._asks, asks)
        self._sequence = final_update_id
        self._ts_ms = ts_ms
        self._received_ms = received_ms
        return ApplyResult.APPLIED

    def apply_delta(
        self,
        sequence: int,
        bids: Iterable[LevelInput],
        asks: Iterable[LevelInput],
        ts_ms: int,
        received_ms: int,
    ) -> ApplyResult:
        """Applies a generic contiguous-sequence delta.

        Args:
            sequence: Delta sequence; must equal previous + 1.
            bids: Bid level updates.
            asks: Ask level updates.
            ts_ms: Venue event time.
            received_ms: Receipt time.

        Returns:
            The apply outcome.
        """
        if self._needs_resync or self._sequence is None:
            return ApplyResult.RESYNC_REQUIRED
        if sequence <= self._sequence:
            return ApplyResult.IGNORED_OLD
        if sequence != self._sequence + 1:
            return self._resync()
        self._awaiting_first_diff = False
        self._apply_levels(self._bids, bids)
        self._apply_levels(self._asks, asks)
        self._sequence = sequence
        self._ts_ms = ts_ms
        self._received_ms = received_ms
        return ApplyResult.APPLIED

    def book(self, now_ms: int, depth: int = 50) -> market.OrderBook:
        """Materializes the current book.

        Args:
            now_ms: Current time for the staleness check.
            depth: Levels per side.

        Returns:
            Order book; ``stale`` when resync is needed, sides are crossed or
            the data is older than ``max_age_ms``.
        """
        bids = sorted(self._bids.items(), key=lambda kv: kv[0], reverse=True)
        asks = sorted(self._asks.items(), key=lambda kv: kv[0])
        crossed = bool(bids and asks and bids[0][0] >= asks[0][0])
        stale = (
            self._needs_resync
            or crossed
            or now_ms - self._received_ms > self._max_age_ms
        )
        return market.OrderBook(
            instrument_id=self._instrument_id,
            bids=tuple(market.BookLevel(p, q) for p, q in bids[:depth]),
            asks=tuple(market.BookLevel(p, q) for p, q in asks[:depth]),
            snapshot_ts_ms=self._ts_ms,
            received_at_ms=self._received_ms,
            source=self._source,
            stale=stale,
            venue_sequence=self._sequence,
        )


def _walk(
    levels: Iterable[market.BookLevel], notional: decimal.Decimal
) -> decimal.Decimal | None:
    """Walks levels until ``notional`` quote currency is filled.

    Args:
        levels: Levels best first.
        notional: Quote notional to fill.

    Returns:
        Volume-weighted average fill price, or ``None`` if depth runs out.
    """
    remaining = notional
    filled_base = decimals.ZERO
    spent = decimals.ZERO
    for level in levels:
        if level.price <= decimals.ZERO or level.quantity <= decimals.ZERO:
            continue
        level_notional = level.price * level.quantity
        take = min(level_notional, remaining)
        filled_base += take / level.price
        spent += take
        remaining -= take
        if remaining <= decimals.ZERO:
            return spent / filled_base
    return None


def worst_side_slippage_pct(
    book: market.OrderBook,
    notional_usd: decimal.Decimal = DEFAULT_SLIPPAGE_NOTIONAL_USD,
) -> decimal.Decimal | None:
    """Computes worst-side book-walk slippage for a USD notional.

    Slippage is measured from the mid price (so it includes half the spread)
    for both a buy walking the asks and a sell walking the bids; the larger
    of the two is returned. Quote currency is treated as USD (USDT/USDC
    settled linear contracts).

    Args:
        book: Order book.
        notional_usd: Order notional in USD.

    Returns:
        Slippage in percent, or ``None`` if the book is stale, one-sided or
        too shallow to fill the notional.
    """
    if book.stale or not book.bids or not book.asks:
        return None
    best_bid, best_ask = book.bids[0].price, book.asks[0].price
    if best_bid >= best_ask:
        return None
    mid = (best_bid + best_ask) / 2
    buy_avg = _walk(book.asks, notional_usd)
    sell_avg = _walk(book.bids, notional_usd)
    if buy_avg is None or sell_avg is None:
        return None
    buy = (buy_avg - mid) / mid * decimals.HUNDRED
    sell = (mid - sell_avg) / mid * decimals.HUNDRED
    return max(buy, sell)


def snapshot_book(
    instrument_id: instruments.InstrumentId,
    bids: Iterable[LevelInput],
    asks: Iterable[LevelInput],
    *,
    ts_ms: int,
    received_ms: int,
    source: str,
    sequence: int | None = None,
) -> market.OrderBook:
    """Builds a sorted one-shot book from a REST snapshot.

    Args:
        instrument_id: Exact venue instrument.
        bids: Bid levels (any order).
        asks: Ask levels (any order).
        ts_ms: Venue time.
        received_ms: Receipt time.
        source: Provenance label.
        sequence: Optional venue sequence.

    Returns:
        Order book (not stale at creation).
    """
    builder = OrderBookBuilder(instrument_id, source)
    builder.apply_snapshot(bids, asks, sequence, ts_ms, received_ms)
    return builder.book(received_ms, depth=10_000)
