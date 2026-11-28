# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""RSI filter: Wilder's relative strength index of the closes.

The averages of gains and losses are seeded with their simple mean over
the first ``length`` changes, then smoothed with ``(previous * (length -
1) + change) / length``; RSI is 100 without losses and 0 without gains.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

OPERATORS = ("above", "below", "between")
"""Comparisons: strictly above or below ``value``, or inclusively between
``value`` and ``value2``."""


def compare(value: float, operator: str, first: float, second: float) -> bool:
    """Applies a screener comparison.

    Args:
        value: Measured value.
        operator: One of OPERATORS.
        first: ``value`` input.
        second: ``value2`` input, used by "between".

    Returns:
        Whether the value satisfies the comparison.
    """
    if operator == "above":
        return value > first
    if operator == "below":
        return value < first
    low, high = sorted((first, second))
    return low <= value <= high


def operator_inputs(
    value: float,
    value2: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, joseph_script.Input]:
    """Declares the operator, value and value2 inputs.

    Args:
        value: Default of the first value.
        value2: Default of the second value.
        minimum: Optional lower bound of both values.
        maximum: Optional upper bound of both values.

    Returns:
        Input declarations in display order.
    """
    return {
        "operator": joseph_script.Input(
            "above", title="Condition", options=OPERATORS
        ),
        "value": joseph_script.Input(
            value, title="Value", minimum=minimum, maximum=maximum
        ),
        "value2": joseph_script.Input(
            value2,
            title="Second value",
            minimum=minimum,
            maximum=maximum,
            tooltip="Upper or lower bound for between.",
        ),
    }


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


def rsi(closes: list[float], length: int) -> float:
    """Computes Wilder's RSI at the last close.

    Args:
        closes: Closing prices, oldest first; at least ``length + 1``.
        length: Smoothing length.

    Returns:
        RSI from 0 through 100 (50 for a flat series).
    """
    changes = [b - a for a, b in itertools.pairwise(closes)]
    gain = sum(max(c, 0.0) for c in changes[:length]) / length
    loss = sum(max(-c, 0.0) for c in changes[:length]) / length
    for change in changes[length:]:
        gain = (gain * (length - 1) + max(change, 0.0)) / length
        loss = (loss * (length - 1) + max(-change, 0.0)) / length
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


class RelativeStrengthRange(joseph_script.Filter):
    """Compares the RSI of the closes with one or two values."""

    ID = "rsi"
    TITLE = "Relative Strength Index"
    SHORT_TITLE = "RSI"
    DESCRIPTION = (
        "Wilder's RSI of the closed bars above, below or between values, "
        "such as below 30 for oversold markets."
    )
    CATEGORY = "Oscillators"
    INPUTS = {
        "length": joseph_script.Input(
            14, title="Length", minimum=2, maximum=499, step=1
        ),
        **operator_inputs(70.0, 100.0, minimum=0, maximum=100),
    }

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the RSI.

        Args:
            ctx: Filter context with closed bars.

        Returns:
            Whether the RSI satisfies the condition.
        """
        length = int(ctx.input("length"))
        bars = closed_bars(ctx, length + 1)
        return compare(
            rsi([float(bar["close"]) for bar in bars], length),
            str(ctx.input("operator")),
            float(ctx.input("value")),
            float(ctx.input("value2")),
        )
