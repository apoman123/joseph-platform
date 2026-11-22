"""Read-only market views consumed by risk and the OMS.

These are snapshots fetched from the market engine (or a static DEMO
gateway). The execution engine never computes indicators or signals.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import decimals
from joseph_execution.domain import enums


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentMeta:
    """Precision and status of one venue instrument.

    Attributes:
        instrument_id: Canonical instrument id.
        venue: Venue code.
        tick_size: Price increment.
        lot_size: Quantity increment (base units).
        min_qty: Minimum quantity.
        status: Trading status.
        contract_multiplier: Base units per contract.
        min_notional: Minimum order notional, if any.
        max_qty: Maximum order quantity, if any.
        max_leverage: Venue leverage cap, if known.
        quote: Quote currency.
    """

    instrument_id: str
    venue: str
    tick_size: decimal.Decimal
    lot_size: decimal.Decimal
    min_qty: decimal.Decimal
    status: enums.InstrumentStatus
    contract_multiplier: decimal.Decimal = decimals.ONE
    min_notional: decimal.Decimal | None = None
    max_qty: decimal.Decimal | None = None
    max_leverage: decimal.Decimal | None = None
    quote: str = "USD"


@dataclasses.dataclass(frozen=True, slots=True)
class MarketQuote:
    """Latest prices with provenance.

    Attributes:
        instrument_id: Canonical instrument id.
        feed_state: Provenance of the snapshot.
        as_of_ms: Observation time.
        mark: Mark price.
        last: Last trade price.
        best_bid: Best bid.
        best_ask: Best ask.
    """

    instrument_id: str
    feed_state: enums.FeedState
    as_of_ms: int
    mark: decimal.Decimal | None = None
    last: decimal.Decimal | None = None
    best_bid: decimal.Decimal | None = None
    best_ask: decimal.Decimal | None = None

    def age_ms(self, now_ms: int) -> int:
        """Returns the snapshot age.

        Args:
            now_ms: Current time.

        Returns:
            Non-negative age in milliseconds.
        """
        return max(0, now_ms - self.as_of_ms)

    @property
    def reference_price(self) -> decimal.Decimal | None:
        """Returns mark, else last, else mid; ``None`` if all missing."""
        if self.mark is not None:
            return self.mark
        if self.last is not None:
            return self.last
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread_pct(self) -> decimal.Decimal | None:
        """Returns the bid/ask spread in percent of mid, if available."""
        if self.best_bid is None or self.best_ask is None:
            return None
        mid = (self.best_bid + self.best_ask) / 2
        if mid <= decimals.ZERO:
            return None
        return (self.best_ask - self.best_bid) / mid * decimals.HUNDRED


@dataclasses.dataclass(frozen=True, slots=True)
class BookView:
    """Order book levels as ``(price, quantity)``; best first.

    Attributes:
        instrument_id: Canonical instrument id.
        bids: Bid levels, descending.
        asks: Ask levels, ascending.
        as_of_ms: Snapshot time.
        stale: True when the book failed validation or aged out.
    """

    instrument_id: str
    bids: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    asks: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    as_of_ms: int
    stale: bool = False

    def walk(
        self, side: enums.OrderSide, quantity: decimal.Decimal
    ) -> decimal.Decimal | None:
        """Computes the average fill price of a market order.

        Args:
            side: Taker side (buy consumes asks).
            quantity: Base quantity.

        Returns:
            Volume-weighted price, or ``None`` if depth is insufficient.
        """
        levels = self.asks if side is enums.OrderSide.BUY else self.bids
        remaining = quantity
        cost = decimals.ZERO
        for price, size in levels:
            take = min(remaining, size)
            cost += take * price
            remaining -= take
            if remaining <= decimals.ZERO:
                return cost / quantity
        return None

    def slippage_pct(
        self, side: enums.OrderSide, quantity: decimal.Decimal
    ) -> decimal.Decimal | None:
        """Estimates market-order slippage versus the best price.

        Args:
            side: Taker side.
            quantity: Base quantity.

        Returns:
            Slippage in percent, or ``None`` when depth is insufficient.
        """
        levels = self.asks if side is enums.OrderSide.BUY else self.bids
        if not levels or quantity <= decimals.ZERO:
            return None
        average = self.walk(side, quantity)
        if average is None:
            return None
        best = levels[0][0]
        return abs(average - best) / best * decimals.HUNDRED
