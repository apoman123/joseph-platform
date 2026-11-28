# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Gap filter: the latest closed bar's open against the previous close.

Gap % is ``(open - previous close) / previous close * 100`` of the latest
closed bar on the scan's timeframe (daily bars give the session gap).
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


class GapPercent(joseph_script.Filter):
    """Compares the latest bar's opening gap with one or two values."""

    ID = "gap"
    TITLE = "Gap %"
    SHORT_TITLE = "Gap%"
    DESCRIPTION = (
        "Opening gap of the latest closed bar in percent of the previous "
        "close, above, below or between values."
    )
    CATEGORY = "Price"
    INPUTS = operator_inputs(2.0, 10.0, minimum=-100, maximum=100000)

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the gap.

        Args:
            ctx: Filter context with closed bars.

        Returns:
            Whether the gap satisfies the condition.

        Raises:
            joseph_script.Unavailable: Without two bars or a positive
                previous close.
        """
        bars = closed_bars(ctx, 2)
        previous = float(bars[-2]["close"])
        if previous <= 0:
            raise joseph_script.Unavailable("previous close is not positive")
        return compare(
            (float(bars[-1]["open"]) - previous) / previous * 100,
            str(ctx.input("operator")),
            float(ctx.input("value")),
            float(ctx.input("value2")),
        )
