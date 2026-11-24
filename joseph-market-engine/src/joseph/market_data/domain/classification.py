# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Trade-side classification for markets without an aggressor flag (pure).

Stock exchanges publish trades and quotes but not which side initiated a
trade, so buy volume is *classified* (``BuyVolumeMethod.CLASSIFIED``):

* **Quote rule:** a trade priced above the prevailing quote midpoint is a
  buy, below it a sell; at the midpoint, or without a valid two-sided quote
  (a side missing, locked or crossed), it is unclassified.
* **Tick test:** a trade priced above the most recent *different* earlier
  trade price (an uptick or zero-uptick) is a buy, below it (a downtick or
  zero-downtick) a sell; without an earlier different price it is
  unclassified.
* **Lee-Ready** (Lee and Ready, 1991): the quote rule against the
  prevailing quote, falling back to the tick test at the midpoint or
  without a valid quote.

"Prevailing" means the last quote in force *before* the trade; callers
choose it (the adapters use the newest quote stamped strictly earlier than
the trade). Classification is an estimate: it is never reported as a
venue-published aggressor side.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph.domain import market

Side = market.AggressorSide


def valid_quote(
    bid: decimal.Decimal | None, ask: decimal.Decimal | None
) -> bool:
    """Checks that a quote has both sides and is neither locked nor crossed.

    Args:
        bid: Best bid (``None`` or ``<= 0`` when absent).
        ask: Best ask (``None`` or ``<= 0`` when absent).

    Returns:
        True when ``0 < bid < ask``.
    """
    return bid is not None and ask is not None and 0 < bid < ask


def quote_rule(
    price: decimal.Decimal,
    bid: decimal.Decimal | None,
    ask: decimal.Decimal | None,
) -> Side:
    """Classifies a trade against the quote midpoint.

    Args:
        price: Trade price.
        bid: Prevailing best bid.
        ask: Prevailing best ask.

    Returns:
        ``BUY`` above the midpoint, ``SELL`` below it, ``UNKNOWN`` at the
        midpoint or without a valid quote.
    """
    if bid is None or ask is None or not valid_quote(bid, ask):
        return Side.UNKNOWN
    # Twice the price against bid + ask: the midpoint test without a division.
    doubled = price * 2
    if doubled > bid + ask:
        return Side.BUY
    if doubled < bid + ask:
        return Side.SELL
    return Side.UNKNOWN


def tick_test(
    price: decimal.Decimal, reference: decimal.Decimal | None
) -> Side:
    """Classifies a trade against the last different earlier price.

    Args:
        price: Trade price.
        reference: Most recent earlier trade price that differs from
            ``price`` (``None`` when there is none).

    Returns:
        ``BUY`` on an uptick or zero-uptick, ``SELL`` on a downtick or
        zero-downtick, ``UNKNOWN`` without a reference.
    """
    if reference is None or reference == price:
        return Side.UNKNOWN
    return Side.BUY if price > reference else Side.SELL


def lee_ready(
    price: decimal.Decimal,
    bid: decimal.Decimal | None,
    ask: decimal.Decimal | None,
    reference: decimal.Decimal | None,
) -> Side:
    """Classifies a trade with the Lee-Ready algorithm.

    Args:
        price: Trade price.
        bid: Prevailing best bid.
        ask: Prevailing best ask.
        reference: Last different earlier trade price for the tick test.

    Returns:
        The quote-rule side, else the tick-test side, else ``UNKNOWN``.
    """
    side = quote_rule(price, bid, ask)
    if side is not Side.UNKNOWN:
        return side
    return tick_test(price, reference)


@dataclasses.dataclass(slots=True)
class TickTracker:
    """Remembers trade prices for the tick test.

    Attributes:
        last: Most recent trade price.
        previous_distinct: Most recent price that differs from ``last``.
    """

    last: decimal.Decimal | None = None
    previous_distinct: decimal.Decimal | None = None

    def reference(self, price: decimal.Decimal) -> decimal.Decimal | None:
        """Returns the last earlier price different from ``price``.

        Args:
            price: Price of the trade being classified.

        Returns:
            Reference price, or ``None`` when every earlier trade printed
            at ``price`` (or there was none).
        """
        if self.last is None:
            return None
        return self.last if self.last != price else self.previous_distinct

    def observe(self, price: decimal.Decimal) -> None:
        """Records a trade price.

        Args:
            price: Trade price.
        """
        if self.last is not None and price != self.last:
            self.previous_distinct = self.last
        self.last = price


@dataclasses.dataclass(slots=True)
class LeeReadyClassifier:
    """Stateful Lee-Ready classifier for one instrument's trade sequence.

    Attributes:
        ticks: Tick-test memory.
        by_quote: Trades classified by the quote rule.
        by_tick: Trades classified by the tick test.
        unclassified: Trades neither rule could classify.
    """

    ticks: TickTracker = dataclasses.field(default_factory=TickTracker)
    by_quote: int = 0
    by_tick: int = 0
    unclassified: int = 0

    def classify(
        self,
        price: decimal.Decimal,
        bid: decimal.Decimal | None,
        ask: decimal.Decimal | None,
    ) -> Side:
        """Classifies the next trade of the sequence.

        Args:
            price: Trade price.
            bid: Best bid prevailing before the trade.
            ask: Best ask prevailing before the trade.

        Returns:
            Classified side.
        """
        side = quote_rule(price, bid, ask)
        if side is not Side.UNKNOWN:
            self.by_quote += 1
        else:
            side = tick_test(price, self.ticks.reference(price))
            if side is Side.UNKNOWN:
                self.unclassified += 1
            else:
                self.by_tick += 1
        self.ticks.observe(price)
        return side

    def reset(self) -> None:
        """Forgets the price history (e.g. at a new session or a gap)."""
        self.ticks = TickTracker()
