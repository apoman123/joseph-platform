# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Read-only market views consumed by risk and the OMS.

These are snapshots fetched from the market engine (or a static DEMO
gateway). The execution engine never computes indicators or signals.
Quantities are in base units, prices in the quote currency, and times are
UTC epoch milliseconds.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import decimals
from joseph_execution.domain import enums

CONTINUOUS_CALENDAR = "24x7"
"""Calendar id of markets that never close (contract ``CalendarId``)."""


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class TickStep:
    """One rung of a price-dependent tick ladder (contract ``TickStep``).

    Attributes:
        min_price: Prices at or above this move in ``tick_size`` steps.
        tick_size: Price increment.
    """

    min_price: decimal.Decimal
    tick_size: decimal.Decimal


@dataclasses.dataclass(frozen=True, slots=True)
class TradingSession:
    """One exchange session, open over ``[open_ms, close_ms)``.

    Contract ``TradingSession``.

    Attributes:
        day: Exchange-local trading date (ISO ``YYYY-MM-DD``).
        open_ms: Session open.
        close_ms: Session close (exclusive).
    """

    day: str
    open_ms: int
    close_ms: int

    def contains(self, ts_ms: int) -> bool:
        """Checks whether a time falls inside the session.

        Args:
            ts_ms: Time.

        Returns:
            True when ``open_ms <= ts_ms < close_ms``.
        """
        return self.open_ms <= ts_ms < self.close_ms


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentMeta:
    """Precision and status of one venue instrument.

    Attributes:
        instrument_id: Canonical instrument id.
        venue: Venue code.
        tick_size: Price increment.
        lot_size: Quantity increment.
        min_qty: Minimum order quantity.
        status: Trading status.
        contract_multiplier: Base units per contract.
        min_notional: Minimum order notional in the quote currency, if any.
        max_qty: Maximum order quantity, if any.
        max_leverage: Venue leverage cap, if known.
        quote: Quote currency.
        calendar: Session calendar id (``24x7`` for crypto).
        listing_exchange: Listing exchange of a security, e.g. ``NYSE
            Arca``; ``None`` for crypto.
        tick_ladder: Price-dependent ticks (TWSE, TPEx); empty when
            ``tick_size`` applies at every price.
        price_limit_pct: Daily limit from the reference price in percent
            (TWSE and TPEx 10); ``None`` when unlimited.
        board_lot: Shares per board lot (TWSE/TPEx 1000); ``None`` when
            orders are in single units.
        asset_class: Asset class (``BOND`` for TWSE/TPEx bond ETFs, which
            selects their transaction tax).
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
    calendar: str = CONTINUOUS_CALENDAR
    listing_exchange: str | None = None
    tick_ladder: tuple[TickStep, ...] = ()
    price_limit_pct: decimal.Decimal | None = None
    board_lot: decimal.Decimal | None = None
    asset_class: enums.AssetClass = enums.AssetClass.UNKNOWN

    @property
    def market_type(self) -> str:
        """The middle part of the ``venue:market_type:symbol`` id, or ``""``."""
        parts = self.instrument_id.split(":", 2)
        return parts[1] if len(parts) == 3 else ""

    @property
    def continuous(self) -> bool:
        """Whether the market never closes (``24x7`` calendar)."""
        return self.calendar == CONTINUOUS_CALENDAR

    def tick_for(self, price: decimal.Decimal) -> decimal.Decimal:
        """Returns the tick size that applies at a price.

        Args:
            price: Price.

        Returns:
            The tick of the highest ladder rung whose ``min_price`` is at or
            below ``price``; ``tick_size`` without a ladder or below its
            lowest rung.
        """
        tick = self.tick_size
        for step in sorted(self.tick_ladder):
            if price >= step.min_price:
                tick = step.tick_size
        return tick


@dataclasses.dataclass(frozen=True, slots=True)
class MarketQuote:
    """The latest prices of an instrument, with provenance.

    Attributes:
        instrument_id: Canonical instrument id.
        feed_state: Provenance of the snapshot.
        as_of_ms: Observation time.
        mark: Mark price, if known.
        last: Last trade price, if known.
        best_bid: Best bid, if known.
        best_ask: Best ask, if known.
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
            Age in milliseconds; 0 when ``as_of_ms`` is in the future.
        """
        return max(0, now_ms - self.as_of_ms)

    @property
    def reference_price(self) -> decimal.Decimal | None:
        """The mark, else the last trade, else the bid/ask mid; or ``None``."""
        if self.mark is not None:
            return self.mark
        if self.last is not None:
            return self.last
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread_pct(self) -> decimal.Decimal | None:
        """The bid/ask spread in percent of a positive mid, else ``None``."""
        if self.best_bid is None or self.best_ask is None:
            return None
        mid = (self.best_bid + self.best_ask) / 2
        if mid <= decimals.ZERO:
            return None
        return (self.best_ask - self.best_bid) / mid * decimals.HUNDRED


@dataclasses.dataclass(frozen=True, slots=True)
class BookView:
    """Order book levels as ``(price, quantity)`` pairs, best first.

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
            quantity: Base quantity; must be positive.

        Returns:
            Volume-weighted average price of the levels the order would
            consume, or ``None`` if the book is too thin to fill it.
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
            Distance of the average fill price from the best level, in
            percent; ``None`` for an empty side, a non-positive quantity or
            a book too thin to fill it.
        """
        levels = self.asks if side is enums.OrderSide.BUY else self.bids
        if not levels or quantity <= decimals.ZERO:
            return None
        average = self.walk(side, quantity)
        if average is None:
            return None
        best = levels[0][0]
        return abs(average - best) / best * decimals.HUNDRED
