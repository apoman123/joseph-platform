"""Exact decimal helpers for money, prices and quantities."""

from __future__ import annotations

import decimal

ZERO = decimal.Decimal(0)
ONE = decimal.Decimal(1)
HUNDRED = decimal.Decimal(100)
BPS = decimal.Decimal(10_000)

_CONTEXT = decimal.Context(prec=38, rounding=decimal.ROUND_HALF_EVEN)


def to_decimal(value: object) -> decimal.Decimal:
    """Parses a value into ``Decimal`` without binary-float rounding.

    Args:
        value: ``str``, ``int``, ``float`` or ``Decimal``.

    Returns:
        The exact decimal.

    Raises:
        ValueError: If the value is not a finite number.
    """
    if isinstance(value, bool):
        raise ValueError("booleans are not numeric values")
    if isinstance(value, decimal.Decimal):
        result = value
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


def to_optional(value: object) -> decimal.Decimal | None:
    """Parses an optional decimal, preserving ``None`` and empty strings.

    Args:
        value: Value or ``None``.

    Returns:
        Decimal or ``None``.
    """
    if value is None or value == "":
        return None
    return to_decimal(value)


def fmt(value: decimal.Decimal) -> str:
    """Formats a decimal as a contract ``DecimalString``.

    Args:
        value: Finite decimal.

    Returns:
        Plain decimal text without exponent or trailing zeros.
    """
    if value.is_zero():
        return "0"
    text = format(value.normalize(_CONTEXT), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def fmt_optional(value: decimal.Decimal | None) -> str | None:
    """Formats an optional decimal.

    Args:
        value: Decimal or ``None``.

    Returns:
        Text or ``None``.
    """
    return None if value is None else fmt(value)


def floor_to_step(
    value: decimal.Decimal, step: decimal.Decimal
) -> decimal.Decimal:
    """Rounds down to a multiple of ``step`` (never increases exposure).

    Args:
        value: Value to round.
        step: Positive increment.

    Returns:
        Largest multiple of ``step`` not above ``value``.

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
    """Rounds to the nearest multiple of ``step`` (half-even).

    Args:
        value: Value to round.
        step: Positive increment.

    Returns:
        Nearest multiple of ``step``.

    Raises:
        ValueError: If ``step`` is not positive.
    """
    if step <= ZERO:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
    return units * step


def is_multiple(value: decimal.Decimal, step: decimal.Decimal) -> bool:
    """Checks whether ``value`` is an exact multiple of ``step``.

    Args:
        value: Value.
        step: Positive increment.

    Returns:
        True when aligned.
    """
    if step <= ZERO:
        return False
    ratio = value / step
    return ratio == ratio.to_integral_value()
