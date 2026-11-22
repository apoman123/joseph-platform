"""Immutable technical-analysis building blocks with Pine semantics.

* ``Atr`` mirrors ``ta.atr(length)``: Wilder RMA of true range, seeded with
  the simple average of the first ``length`` true ranges; ``None`` (Pine
  ``na``) until seeded. The first bar's true range is ``high - low``.
* ``PivotTracker`` mirrors ``ta.pivothigh/pivotlow(left, right)``: a pivot
  at bar ``i - right`` is confirmed at the close of bar ``i`` and is known
  only from that moment (no look-ahead). Equal highs/lows inside the window
  do not form a pivot (strict comparison on both sides).

Every update returns a new object; inputs are never mutated.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph.domain import decimals
from joseph.domain import market

_SCORE_MAX = 100


@dataclasses.dataclass(frozen=True, slots=True)
class Atr:
    """Average true range state (Wilder RMA).

    Attributes:
        length: Smoothing length.
        seed_sum: Sum of true ranges while seeding.
        count: Number of true ranges observed.
        value: Current ATR, ``None`` until ``count >= length``.
        prev_close: Previous bar close for true range.
    """

    length: int = 14
    seed_sum: decimal.Decimal = decimals.ZERO
    count: int = 0
    value: decimal.Decimal | None = None
    prev_close: decimal.Decimal | None = None

    def update(self, candle: market.Candle) -> Atr:
        """Applies one closed bar.

        Args:
            candle: The bar.

        Returns:
            Updated ATR state.
        """
        if self.prev_close is None:
            true_range = candle.high - candle.low
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - self.prev_close),
                abs(candle.low - self.prev_close),
            )
        count = self.count + 1
        length = decimal.Decimal(self.length)
        if self.value is None:
            seed_sum = self.seed_sum + true_range
            value = seed_sum / length if count >= self.length else None
            return dataclasses.replace(
                self,
                seed_sum=seed_sum,
                count=count,
                value=value,
                prev_close=candle.close,
            )
        value = (self.value * (length - 1) + true_range) / length
        return dataclasses.replace(
            self, count=count, value=value, prev_close=candle.close
        )


@dataclasses.dataclass(frozen=True, slots=True)
class BarPoint:
    """Compact bar record kept in rolling windows.

    Attributes:
        start_ms: Bar start.
        end_ms: Bar end.
        open: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Base volume.
    """

    start_ms: int
    end_ms: int
    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    volume: decimal.Decimal

    @classmethod
    def of(cls, candle: market.Candle) -> BarPoint:
        """Builds a bar point from a candle.

        Args:
            candle: Source candle.

        Returns:
            The bar point.
        """
        return cls(
            start_ms=candle.start_ms,
            end_ms=candle.end_ms,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.base_volume,
        )

    @property
    def body(self) -> decimal.Decimal:
        """Returns the absolute body size ``|close - open|``."""
        return abs(self.close - self.open)

    @property
    def range(self) -> decimal.Decimal:
        """Returns ``high - low``."""
        return self.high - self.low


@dataclasses.dataclass(frozen=True, slots=True)
class Pivot:
    """A confirmed swing point.

    Attributes:
        price: Pivot high or low price.
        start_ms: Start of the pivot bar.
        confirmed_at_ms: Close of the bar that confirmed it.
    """

    price: decimal.Decimal
    start_ms: int
    confirmed_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class PivotTracker:
    """Rolling window that confirms pivot highs and lows.

    Attributes:
        left: Bars required on the left of the pivot.
        right: Bars required on the right (confirmation delay).
        window: Most recent ``left + right + 1`` bars.
    """

    left: int = 3
    right: int = 3
    window: tuple[BarPoint, ...] = ()

    def update(
        self, bar: BarPoint
    ) -> tuple[PivotTracker, Pivot | None, Pivot | None]:
        """Adds a closed bar and reports pivots confirmed at its close.

        Args:
            bar: The newly closed bar.

        Returns:
            ``(tracker, pivot_high, pivot_low)``; pivots are ``None`` when
            the bar ``right`` positions back is not a pivot.
        """
        size = self.left + self.right + 1
        window = (*self.window, bar)[-size:]
        tracker = dataclasses.replace(self, window=window)
        if len(window) < size:
            return tracker, None, None
        center = window[self.left]
        others = window[: self.left] + window[self.left + 1 :]
        high = None
        low = None
        if all(center.high > other.high for other in others):
            high = Pivot(center.high, center.start_ms, bar.end_ms)
        if all(center.low < other.low for other in others):
            low = Pivot(center.low, center.start_ms, bar.end_ms)
        return tracker, high, low


def saturate(value: decimal.Decimal, cap: decimal.Decimal) -> decimal.Decimal:
    """Scales ``value / cap`` into ``[0, 1]``.

    Args:
        value: Non-negative measurement.
        cap: Value at which the score saturates.

    Returns:
        Fraction in ``[0, 1]``.
    """
    if cap <= decimals.ZERO or value <= decimals.ZERO:
        return decimals.ZERO
    return min(value / cap, decimals.ONE)


def score(*weighted: tuple[decimal.Decimal, decimal.Decimal]) -> int:
    """Combines weighted fractions into a 0-100 integer score.

    Args:
        *weighted: ``(weight_points, fraction)`` pairs.

    Returns:
        Rounded score clamped to ``0..100``.
    """
    total = sum(
        (points * fraction for points, fraction in weighted), decimals.ZERO
    )
    rounded = int(total.to_integral_value(rounding=decimal.ROUND_HALF_UP))
    return max(0, min(_SCORE_MAX, rounded))


def decay(value: int, factor: decimal.Decimal) -> int:
    """Applies a multiplicative decay to an integer score.

    Args:
        value: Score in ``0..100``.
        factor: Multiplier such as ``0.70``.

    Returns:
        Rounded, clamped score.
    """
    return score((decimal.Decimal(value), factor))


def quantize_price(value: decimal.Decimal) -> decimal.Decimal:
    """Rounds a derived price to 8 decimal places for publication.

    Args:
        value: Derived price (e.g. a midpoint or retracement level).

    Returns:
        The rounded value with trailing zeros removed.
    """
    return value.quantize(
        decimal.Decimal("0.00000001"), rounding=decimal.ROUND_HALF_EVEN
    ).normalize()
