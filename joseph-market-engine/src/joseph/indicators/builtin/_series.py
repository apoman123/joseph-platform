# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Helpers shared by the built-in indicators.

Built-ins compute with IEEE-754 doubles in bar order, as TradingView does,
and turn results into decimals only when they publish them:

* prices are rounded half-up to four decimal places below the instrument
  tick (``publish_price``), so float noise such as ``0.30000000000000004``
  never reaches a contract while sub-tick levels keep their value;
* dimensionless values (percentages, ratios) to a fixed number of places
  (``publish_ratio``);
* volumes in base units to eight places (``publish_quantity``).

The tick comes from the ``tick_size`` setting, which the engines fill from
the instrument (TradingView ``syminfo.mintick``), or else from the price
precision of the series' first bar (``infer_tick``).
"""

from __future__ import annotations

import decimal
import functools
import math
from collections.abc import Mapping

from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.indicators import ticks
from joseph.indicators.sdk import settings as settings_lib

SOURCES: tuple[str, ...] = (
    "close",
    "open",
    "high",
    "low",
    "hl2",
    "hlc3",
    "ohlc4",
)
"""Price sources of the moving averages and oscillators."""

_PLAIN_SOURCES = frozenset({"open", "high", "low", "close"})
"""Sources that are one candle field."""

AVERAGE_SOURCES: tuple[str, ...] = ("close", "hl2", "hlc3", "ohlc4")
"""Price sources of the anchored VWAP and the regression channel."""

TICK_SETTING = ticks.TICK_SETTING
"""Setting of every built-in that publishes prices."""

EXTRA_PLACES = 4
"""Decimal places kept beyond the tick's own precision."""

QUANTITY_PLACES = 8
"""Decimal places of published volumes (base units)."""

MAX_INFERRED_PLACES = 12
"""Most decimal places ``infer_tick`` derives from a price."""

ANCHOR_SETTING = "anchor_ms"
"""Setting holding the anchor time of an anchored study (0: none)."""

MAX_ANCHOR_MS = 4_102_444_800_000
"""Latest accepted anchor time (2100-01-01 UTC)."""


def price_source(candle: market.Candle, name: str) -> float:
    """Evaluates a TradingView built-in price source on a bar.

    The operations follow TradingView's definitions in order, e.g. ``hlc3``
    is ``(high + low + close) / 3`` in doubles.

    Args:
        candle: Closed bar.
        name: One of ``SOURCES``.

    Returns:
        The source price.

    Raises:
        ValueError: If ``name`` is not a known source.
    """
    if name in _PLAIN_SOURCES:
        return float(getattr(candle, name))
    if name == "hl2":
        return (float(candle.high) + float(candle.low)) / 2
    if name == "hlc3":
        return (
            float(candle.high) + float(candle.low) + float(candle.close)
        ) / 3
    if name == "ohlc4":
        return (
            float(candle.open)
            + float(candle.high)
            + float(candle.low)
            + float(candle.close)
        ) / 4
    raise ValueError(f"source must be one of {', '.join(SOURCES)}: {name!r}")


def source_spec(
    default: str = "close", choices: tuple[str, ...] = SOURCES
) -> settings_lib.SettingSpec:
    """Builds the ``source`` setting.

    Args:
        default: Default source.
        choices: Allowed sources.

    Returns:
        Enum setting.
    """
    return settings_lib.SettingSpec(
        "source",
        settings_lib.SettingKind.ENUM,
        default,
        "Price the study is computed from.",
        choices=choices,
    )


def length_spec(default: int, maximum: int) -> settings_lib.SettingSpec:
    """Builds the ``length`` setting.

    Args:
        default: Default bar count.
        maximum: Largest accepted bar count.

    Returns:
        Integer setting with a minimum of 1.
    """
    return settings_lib.SettingSpec(
        "length",
        settings_lib.SettingKind.INT,
        default,
        "Number of bars.",
        minimum=1,
        maximum=maximum,
    )


def anchor_spec(description: str) -> settings_lib.SettingSpec:
    """Builds the ``anchor_ms`` setting of an anchored study.

    Args:
        description: Meaning of the anchor for the study.

    Returns:
        Integer setting, 0 to ``MAX_ANCHOR_MS``.
    """
    return settings_lib.SettingSpec(
        ANCHOR_SETTING,
        settings_lib.SettingKind.INT,
        0,
        description,
        minimum=0,
        maximum=MAX_ANCHOR_MS,
    )


def anchor_bar_start(
    settings: Mapping[str, settings_lib.SettingValue],
    timeframe: timeframes.Timeframe,
    calendar: sessions.BarCalendar,
) -> int:
    """Returns the start of the chart bar containing ``anchor_ms``.

    Args:
        settings: Resolved settings holding ``anchor_ms``.
        timeframe: Chart timeframe.
        calendar: Session calendar of the instrument (between sessions the
            anchor bar is the last bar before the anchor time).

    Returns:
        The aligned anchor bar start, or 0 when no anchor is set.

    Raises:
        ValueError: If the anchor precedes the calendar's first session.
    """
    anchor = settings_lib.get_int(settings, ANCHOR_SETTING)
    return calendar.floor(timeframe, anchor) if anchor > 0 else 0


def tick_spec() -> settings_lib.SettingSpec:
    """Builds the ``tick_size`` setting.

    Returns:
        Decimal setting; 0 means "filled from the instrument, else
        inferred from the first bar".
    """
    return settings_lib.SettingSpec(
        TICK_SETTING,
        settings_lib.SettingKind.DECIMAL,
        decimal.Decimal(0),
        "Symbol tick size. 0 = filled from the instrument, else inferred "
        "from the first bar's price precision.",
        minimum=decimal.Decimal(0),
    )


def infer_tick(candle: market.Candle) -> decimal.Decimal:
    """Infers a tick size from the decimals of one candle's prices.

    Args:
        candle: First candle of a series.

    Returns:
        ``10 ** -places`` for the most precise OHLC price, with at most
        ``MAX_INFERRED_PLACES`` places.
    """
    places = 0
    for value in (candle.open, candle.high, candle.low, candle.close):
        exponent = value.normalize().as_tuple().exponent
        if isinstance(exponent, int):
            places = max(places, -exponent)
    return decimal.Decimal(1).scaleb(-min(places, MAX_INFERRED_PLACES))


def series_tick(
    settings: Mapping[str, settings_lib.SettingValue],
    candle: market.Candle,
) -> decimal.Decimal:
    """Resolves the tick of a series at its first bar.

    Args:
        settings: Resolved settings holding ``tick_size``.
        candle: First bar of the series.

    Returns:
        The configured tick when positive, else ``infer_tick(candle)``.
    """
    tick = settings_lib.get_decimal(settings, TICK_SETTING)
    return tick if tick > 0 else infer_tick(candle)


def push_window[ValueT](
    window: tuple[ValueT, ...], value: ValueT, length: int
) -> tuple[ValueT, ...]:
    """Appends a value to a window that keeps the last ``length`` values.

    Args:
        window: Values, oldest first.
        value: New value.
        length: Window size, at least 1.

    Returns:
        The new window, oldest first.
    """
    if length <= 1:
        return (value,)
    return (*window[-(length - 1) :], value)


@functools.lru_cache(maxsize=256)
def price_quantum(tick: decimal.Decimal) -> decimal.Decimal:
    """Returns the rounding quantum of published prices for a tick.

    Args:
        tick: Positive tick size.

    Returns:
        ``10 ** -(tick places + EXTRA_PLACES)``.
    """
    exponent = tick.normalize().as_tuple().exponent
    places = max(-int(exponent), 0) if isinstance(exponent, int) else 0
    return decimal.Decimal(1).scaleb(-(places + EXTRA_PLACES))


def _rounded(
    value: float | None, quantum: decimal.Decimal
) -> decimal.Decimal | None:
    """Rounds a double half-up to a quantum.

    Args:
        value: Double or ``None``.
        quantum: Rounding step, a power of ten.

    Returns:
        The exact double rounded half-up and normalized (``0`` without a
        sign or exponent), or ``None`` for ``None``, NaN and infinities.
    """
    if value is None or not math.isfinite(value):
        return None
    rounded = decimal.Decimal(value).quantize(
        quantum, rounding=decimal.ROUND_HALF_UP
    )
    return rounded.normalize() if rounded != 0 else decimal.Decimal(0)


def publish_price(
    value: float | None, tick: decimal.Decimal
) -> decimal.Decimal | None:
    """Converts a computed price to its published decimal.

    Args:
        value: Double price or ``None``.
        tick: Series tick.

    Returns:
        The price rounded half-up to four places below the tick, or
        ``None`` for ``None``, NaN and infinities.
    """
    return _rounded(value, price_quantum(tick))


def publish_ratio(value: float | None, places: int) -> decimal.Decimal | None:
    """Converts a dimensionless double (percent, share) to a decimal.

    Args:
        value: Double or ``None``.
        places: Decimal places kept.

    Returns:
        The value rounded half-up to ``places``, or ``None`` for ``None``,
        NaN and infinities.
    """
    return _rounded(value, decimal.Decimal(1).scaleb(-places))


def publish_quantity(value: float | None) -> decimal.Decimal | None:
    """Converts a volume in base units to a decimal.

    Args:
        value: Double volume or ``None``.

    Returns:
        The volume rounded half-up to ``QUANTITY_PLACES`` places, or
        ``None`` for ``None``, NaN and infinities.
    """
    return publish_ratio(value, QUANTITY_PLACES)
