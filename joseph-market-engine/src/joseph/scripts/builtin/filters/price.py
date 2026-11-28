# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Price filter: the latest traded price (else the mark) against a value.

Prices are in the instrument's quote currency.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

OPERATORS = ("above", "below", "between")
"""Comparisons: strictly above or below ``value``, or inclusively between
``value`` and ``value2``."""


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


class PriceRange(joseph_script.Filter):
    """Compares the latest price with one or two values."""

    ID = "price"
    TITLE = "Price"
    SHORT_TITLE = "Price"
    DESCRIPTION = (
        "Latest traded price, or the mark price when the venue publishes "
        "no trade, above, below or between values."
    )
    CATEGORY = "Price"
    INPUTS = operator_inputs(10.0, 1000.0, minimum=0)

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the latest price.

        Args:
            ctx: Filter context with the instrument's snapshot.

        Returns:
            Whether the latest price satisfies the condition.

        Raises:
            joseph_script.Unavailable: When the snapshot has no latest price.
        """
        measured = number(ctx.snapshot.get("last"))
        if measured is None:
            measured = number(ctx.snapshot.get("mark"))
        if measured is None:
            raise joseph_script.Unavailable("latest price is unavailable")
        return compare(
            measured,
            str(ctx.input("operator")),
            float(ctx.input("value")),
            float(ctx.input("value2")),
        )
