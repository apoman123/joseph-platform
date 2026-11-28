# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Relative volume filter: the last closed bar's volume over its average.

Relative volume is the volume of the latest closed bar divided by the
mean volume of the ``length`` bars before it, on the scan's timeframe.
"""

from __future__ import annotations

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


class RelativeVolume(joseph_script.Filter):
    """Compares the latest bar's volume with the preceding average."""

    ID = "relative-volume"
    TITLE = "Relative volume"
    SHORT_TITLE = "Rel Vol"
    DESCRIPTION = (
        "Volume of the latest closed bar divided by the mean volume of the "
        "length bars before it."
    )
    CATEGORY = "Volume"
    INPUTS = {
        "length": joseph_script.Input(
            10, title="Average length", minimum=1, maximum=499, step=1
        ),
        **operator_inputs(2.0, 5.0, minimum=0),
    }

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the relative volume.

        Args:
            ctx: Filter context with closed bars.

        Returns:
            Whether the relative volume satisfies the condition.

        Raises:
            joseph_script.Unavailable: Without enough bars or with zero
                average volume.
        """
        length = int(ctx.input("length"))
        bars = closed_bars(ctx, length + 1)
        previous = [float(bar["volume"]) for bar in bars[-length - 1 : -1]]
        average = sum(previous) / length
        if average <= 0:
            raise joseph_script.Unavailable("average volume is zero")
        return compare(
            float(bars[-1]["volume"]) / average,
            str(ctx.input("operator")),
            float(ctx.input("value")),
            float(ctx.input("value2")),
        )
