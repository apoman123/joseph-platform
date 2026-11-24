# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Value conversions between ``smc_indicators`` and the platform domain.

The library computes with IEEE-754 floats (as Pine does); the platform
publishes decimals. Prices are converted once, at this boundary, rounded to
four decimal places below the instrument tick so float noise such as
``0.30000000000000004`` never reaches a contract while sub-tick levels (an
equilibrium halfway between two ticks) keep their value.
"""

from __future__ import annotations

import decimal
import math

from smc_indicators import types as smc_types

from joseph.domain import market
from joseph.domain import structures

_SIDES: dict[int, structures.ZoneSide] = {
    1: structures.ZoneSide.BULLISH,
    -1: structures.ZoneSide.BEARISH,
    0: structures.ZoneSide.NEUTRAL,
}

_STATES: dict[str, structures.AoiState] = {
    "active": structures.AoiState.ACTIVE,
    "tested": structures.AoiState.TESTED,
    "inverted": structures.AoiState.INVERTED,
    "swept": structures.AoiState.SWEPT,
}

_EXTRA_PLACES = 4
"""Decimal places kept beyond the tick's own precision."""

_QUANTITY_PLACES = 8
"""Decimal places of published volumes (base units)."""


def to_bar(candle: market.Candle) -> smc_types.Bar:
    """Converts a closed platform candle to a library bar.

    Args:
        candle: Closed candle.

    Returns:
        Library bar keyed by open time, with base volume as Pine ``volume``.
    """
    return smc_types.Bar(
        time_ms=candle.start_ms,
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        volume=float(candle.base_volume),
        close_time_ms=candle.end_ms,
        buy_volume=(
            None
            if candle.taker_buy_base_volume is None
            else float(candle.taker_buy_base_volume)
        ),
    )


def quantum(tick: decimal.Decimal) -> decimal.Decimal:
    """Returns the rounding quantum used for a tick size.

    Args:
        tick: Positive tick size.

    Returns:
        ``10 ** -(tick places + 4)``.
    """
    exponent = tick.normalize().as_tuple().exponent
    places = max(-int(exponent), 0) if isinstance(exponent, int) else 0
    return decimal.Decimal(1).scaleb(-(places + _EXTRA_PLACES))


def price(value: float | None, step: decimal.Decimal) -> decimal.Decimal | None:
    """Converts a library price to a decimal.

    Args:
        value: Float price or ``None``.
        step: Quantum from ``quantum``.

    Returns:
        Normalized decimal, or ``None`` for ``None``/NaN/inf.
    """
    if value is None or not math.isfinite(value):
        return None
    rounded = decimal.Decimal(value).quantize(
        step, rounding=decimal.ROUND_HALF_UP
    )
    return rounded.normalize() if rounded != 0 else decimal.Decimal(0)


def ratio(value: float | None, places: int = 4) -> decimal.Decimal | None:
    """Converts a dimensionless float (percent, share) to a decimal.

    Args:
        value: Float or ``None``.
        places: Decimal places kept.

    Returns:
        Normalized decimal or ``None``.
    """
    if value is None or not math.isfinite(value):
        return None
    rounded = decimal.Decimal(value).quantize(
        decimal.Decimal(1).scaleb(-places), rounding=decimal.ROUND_HALF_UP
    )
    return rounded.normalize() if rounded != 0 else decimal.Decimal(0)


def quantity(value: float | None) -> decimal.Decimal | None:
    """Converts a library volume (base units) to a decimal.

    Args:
        value: Float volume or ``None``.

    Returns:
        Decimal rounded half-up to eight places, or ``None`` when missing
        or not finite.
    """
    return ratio(value, _QUANTITY_PLACES)


def side(value: int) -> structures.ZoneSide:
    """Maps a library side to a zone side.

    Args:
        value: ``1``, ``-1`` or ``0``.

    Returns:
        Zone side (neutral for unknown values).
    """
    return _SIDES.get(value, structures.ZoneSide.NEUTRAL)


def state(value: str) -> structures.AoiState:
    """Maps a library zone state to a live AOI state.

    Args:
        value: Library state text.

    Returns:
        AOI state; unknown states count as active.
    """
    return _STATES.get(value, structures.AoiState.ACTIVE)


def direction(value: object) -> str | None:
    """Maps a signed direction to ``BULLISH`` / ``BEARISH``.

    Args:
        value: Integer direction or ``None``.

    Returns:
        Direction text, or ``None`` when flat/unknown.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value == 0:
        return None
    return "BULLISH" if value > 0 else "BEARISH"


def trend(value: object) -> str:
    """Maps a signed direction to ``BULLISH`` / ``BEARISH`` / ``NEUTRAL``.

    Args:
        value: Integer direction or ``None``.

    Returns:
        Trend text.
    """
    return direction(value) or "NEUTRAL"


def scalar(
    value: smc_types.Value, step: decimal.Decimal
) -> structures.ValueType:
    """Converts a published library value generically.

    Floats become price decimals; other scalars pass through.

    Args:
        value: Library value.
        step: Price quantum.

    Returns:
        Platform value.
    """
    if isinstance(value, float):
        return price(value, step)
    return value
