# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Exact decimal helpers for prices, quantities and money.

Contracts transport decimals as strings. These helpers parse venue values
without passing through binary floating point and format results without
scientific notation so they validate against ``DecimalString``.
"""

from __future__ import annotations

import decimal

ZERO = decimal.Decimal(0)
ONE = decimal.Decimal(1)
HUNDRED = decimal.Decimal(100)

_CONTEXT = decimal.Context(prec=38, rounding=decimal.ROUND_HALF_EVEN)


def to_decimal(value: object) -> decimal.Decimal:
    """Converts a venue value to ``Decimal`` without float rounding.

    Floats are converted through ``repr`` so ``0.1`` becomes ``Decimal("0.1")``
    rather than its binary expansion.

    Args:
        value: A ``str``, ``int``, ``float`` or ``Decimal``.

    Returns:
        The exact decimal value.

    Raises:
        ValueError: If the value is not a finite number.
    """
    if isinstance(value, decimal.Decimal):
        result = value
    elif isinstance(value, bool):
        raise ValueError("booleans are not numeric values")
    elif isinstance(value, int):
        result = decimal.Decimal(value)
    elif isinstance(value, float):
        result = decimal.Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = decimal.Decimal(value.strip())
        except decimal.InvalidOperation as err:
            raise ValueError(f"not a decimal: {value!r}") from err
    else:
        raise ValueError(f"unsupported numeric type: {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return result


def to_optional_decimal(value: object) -> decimal.Decimal | None:
    """Converts a value to ``Decimal`` while preserving ``None``/empty.

    Args:
        value: Value accepted by ``to_decimal``, ``None`` or ``""``.

    Returns:
        The decimal value, or ``None`` when the input is missing.
    """
    if value is None or value == "":
        return None
    return to_decimal(value)


def format_decimal(value: decimal.Decimal) -> str:
    """Formats a decimal as a plain contract ``DecimalString``.

    Trailing zeros are removed and exponent notation is never produced.

    Args:
        value: Finite decimal to format.

    Returns:
        A string such as ``"94700.5"`` or ``"0"``.
    """
    if value.is_zero():
        return "0"
    normalized = value.normalize(_CONTEXT)
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_optional(value: decimal.Decimal | None) -> str | None:
    """Formats an optional decimal, preserving ``None``.

    Args:
        value: Decimal or ``None``.

    Returns:
        The formatted string or ``None``.
    """
    return None if value is None else format_decimal(value)


def floor_to_step(
    value: decimal.Decimal, step: decimal.Decimal
) -> decimal.Decimal:
    """Rounds ``value`` down to an integer multiple of ``step``.

    Used for lot sizes, where rounding up could exceed the requested risk.

    Args:
        value: Value to round.
        step: Positive increment such as a lot or tick size.

    Returns:
        The largest multiple of ``step`` that is ``<= value``.

    Raises:
        ValueError: If ``step`` is not positive.
    """
    if step <= ZERO:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=decimal.ROUND_FLOOR)
    return units * step


def round_to_step(
    value: decimal.Decimal, step: decimal.Decimal
) -> decimal.Decimal:
    """Rounds ``value`` to the nearest multiple of ``step`` (half-even).

    Args:
        value: Value to round, typically a price.
        step: Positive tick size.

    Returns:
        The nearest multiple of ``step``.

    Raises:
        ValueError: If ``step`` is not positive.
    """
    if step <= ZERO:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
    return units * step


def is_multiple_of(value: decimal.Decimal, step: decimal.Decimal) -> bool:
    """Checks whether ``value`` is an exact multiple of ``step``.

    Args:
        value: Value to check.
        step: Positive increment.

    Returns:
        True when ``value / step`` is integral.
    """
    if step <= ZERO:
        return False
    return (value / step) == (value / step).to_integral_value()
