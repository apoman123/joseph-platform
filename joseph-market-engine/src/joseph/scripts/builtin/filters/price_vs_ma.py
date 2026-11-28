# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Moving-average filter: the latest price above or below an average.

The average (simple, or exponential seeded with the first close) uses the
closed bars of the scan's timeframe; the price is the snapshot's latest
trade, else its mark.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker


def number(value: Any) -> float | None:
    """Parses a contract decimal string or number.

    Args:
        value: Decimal string, number or None.

    Returns:
        The finite float, or None when absent or malformed.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def closed_bars(ctx: worker.Context, needed: int) -> list[dict[str, Any]]:
    """Reads the closed history, requiring enough bars.

    Args:
        ctx: Filter context.
        needed: Minimum number of closed bars.

    Returns:
        Closed bars, oldest first.

    Raises:
        joseph_script.Unavailable: With fewer than ``needed`` bars.
    """
    bars = [bar for bar in ctx.history if bar.get("is_final", True)]
    if len(bars) < needed:
        raise joseph_script.Unavailable(
            f"needs {needed} closed bars, got {len(bars)}; scan with "
            f"history_bars of at least {needed}"
        )
    return bars


def average(closes: list[float], length: int, kind: str) -> float:
    """Computes a simple or exponential moving average at the last close.

    Args:
        closes: Closing prices, oldest first; at least ``length``.
        length: Average length.
        kind: "SMA" or "EMA".

    Returns:
        The average.
    """
    if kind == "SMA":
        return sum(closes[-length:]) / length
    alpha = 2 / (length + 1)
    value = closes[0]
    for close in closes[1:]:
        value = alpha * close + (1 - alpha) * value
    return value


class PriceVersusAverage(joseph_script.Filter):
    """Compares the latest price with a moving average of the closes."""

    ID = "price-vs-ma"
    TITLE = "Price vs moving average"
    SHORT_TITLE = "Price/MA"
    DESCRIPTION = (
        "Latest price above or below the simple or exponential moving "
        "average of the closed bars."
    )
    CATEGORY = "Moving averages"
    INPUTS = {
        "ma_type": joseph_script.Input(
            "SMA", title="Average", options=("SMA", "EMA")
        ),
        "length": joseph_script.Input(
            50, title="Length", minimum=1, maximum=500, step=1
        ),
        "position": joseph_script.Input(
            "above", title="Price is", options=("above", "below")
        ),
    }

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the price with the average.

        Args:
            ctx: Filter context with the snapshot and closed bars.

        Returns:
            Whether the price is strictly on the selected side.

        Raises:
            joseph_script.Unavailable: Without a price.
        """
        length = int(ctx.input("length"))
        bars = closed_bars(ctx, length)
        price = number(ctx.snapshot.get("last"))
        if price is None:
            price = number(ctx.snapshot.get("mark"))
        if price is None:
            raise joseph_script.Unavailable("latest price is unavailable")
        level = average(
            [float(bar["close"]) for bar in bars],
            length,
            str(ctx.input("ma_type")),
        )
        if ctx.input("position") == "below":
            return price < level
        return price > level
