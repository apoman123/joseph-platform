# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Volume filter: rolling 24-hour (or latest session) volume in base units."""

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


class VolumeRange(joseph_script.Filter):
    """Compares the rolling 24h base volume with one or two values."""

    ID = "volume"
    TITLE = "Volume"
    SHORT_TITLE = "Vol"
    DESCRIPTION = (
        "Rolling 24-hour or latest-session volume in base units (shares, "
        "coins or contracts) above, below or between values."
    )
    CATEGORY = "Volume"
    INPUTS = operator_inputs(1000000.0, 1000000000.0, minimum=0)

    def matches(self, ctx: worker.Context) -> bool:
        """Compares the 24h volume.

        Args:
            ctx: Filter context with the instrument's snapshot.

        Returns:
            Whether the 24h volume satisfies the condition.

        Raises:
            joseph_script.Unavailable: When the snapshot has no 24h volume.
        """
        measured = number(ctx.snapshot.get("volume_24h_base"))
        if measured is None:
            raise joseph_script.Unavailable("24h volume is unavailable")
        return compare(
            measured,
            str(ctx.input("operator")),
            float(ctx.input("value")),
            float(ctx.input("value2")),
        )
