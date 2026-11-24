# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Linear regression channel (ordinary least squares).

Over a window of ``n`` bars, with ``x`` the bar's position in the window (0
for the oldest) and ``y`` its source price, the study fits the line
``y = a + b * x`` that minimises the squared residuals:

* ``b = Sxy / Sxx`` and the line passes through ``(mean_x, mean_y)``, where
  ``Sxx``, ``Sxy`` and ``Syy`` are the centred sums of squares and products;
* ``stdev`` is the sample standard deviation of the residuals,
  ``sqrt(sum(r**2) / (n - 1))``, and the channel is the line ``+/-
  deviations * stdev``. The residual sum of squares is evaluated as
  ``Syy - b * Sxy`` (clamped at 0), which equals ``sum(r**2)`` in real
  arithmetic;
* ``pearson_r = Sxy / sqrt(Sxx * Syy)``, 0 when the prices are constant;
* ``max_up = max(high - line)`` and ``max_down = max(line - low)`` over the
  window, the largest excursions beyond the fitted line.

Windows:

* **Rolling** (``anchor_ms = 0``): the last ``length`` bars, refitted every
  bar from centred two-pass sums (``math.fsum`` / ``math.sumprod``). Every
  value is null until ``length`` bars exist.
* **Anchored**: every bar from the chart bar containing ``anchor_ms``
  through the current one (``length`` is ignored; the regression-channel
  drawing tool reads the point at its second anchor). The fit is O(1) per
  bar: ``x`` counts bars from the anchor bar, prices are offset by the
  anchor bar's source price, and the means and centred co-moments are
  updated Welford-style, so thousands of bars at prices near 60000 do not
  lose precision to cancellation. ``max_up`` / ``max_down`` come from
  incrementally maintained upper convex hulls of the highs and the negated
  lows. Every value is null until two bars exist, and an anchor bar outside
  the computed window yields null values and ``anchor_in_window`` False.

Both windows offset prices by the window's first source price, so a window
of constant prices has ``Syy == 0`` exactly and ``pearson_r`` is 0. All
arithmetic is in doubles, in bar order.
"""

from __future__ import annotations

import dataclasses
import decimal
import math
import operator
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import errors
from joseph.domain import sessions
from joseph.domain import structures
from joseph.indicators.builtin import _series
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

_PRICE_KEYS = (
    "start",
    "end",
    "upper_start",
    "upper_end",
    "lower_start",
    "lower_end",
    "slope",
    "stdev",
    "max_up",
    "max_down",
)
"""Published price values, in publication order."""


def sxx(count: int) -> float:
    """Returns the centred sum of squares of ``x = 0 .. count - 1``.

    Args:
        count: Number of bars.

    Returns:
        ``count * (count**2 - 1) / 12``, exact for any realistic count.
    """
    return count * (count * count - 1) / 12


@dataclasses.dataclass(frozen=True, slots=True)
class Fit:
    """A least-squares fit over a window of ``count`` bars.

    ``x`` runs from 0 (oldest bar) to ``count - 1`` (current bar).

    Attributes:
        count: Bars in the window (at least 2).
        start_time_ms: Open time of the oldest bar.
        centre: Line value at ``mean_x = (count - 1) / 2`` (the mean price).
        slope: Least-squares slope, price per bar.
        sxy: Centred sum of ``x * y`` products.
        syy: Centred sum of squared prices.
        max_up: Largest ``high - line``.
        max_down: Largest ``line - low``.
    """

    count: int
    start_time_ms: int
    centre: float
    slope: float
    sxy: float
    syy: float
    max_up: float
    max_down: float


@dataclasses.dataclass(frozen=True, slots=True)
class UpperHull:
    """Upper convex hull of points added with strictly increasing ``x``.

    Answers ``max(y - slope * x)`` over every point added so far: the
    maximum is attained on the hull, where it is unimodal along ``x``.

    Attributes:
        xs: Abscissas of the hull vertices, ascending.
        ys: Ordinates of the hull vertices.
    """

    xs: tuple[float, ...] = ()
    ys: tuple[float, ...] = ()

    def add(self, x: float, y: float) -> UpperHull:
        """Adds a point to the right of every earlier point.

        Args:
            x: Abscissa, greater than every earlier one.
            y: Ordinate.

        Returns:
            The hull including the point.
        """
        xs, ys = self.xs, self.ys
        kept = len(xs)
        while kept >= 2:
            cross = (xs[kept - 1] - xs[kept - 2]) * (y - ys[kept - 2]) - (
                ys[kept - 1] - ys[kept - 2]
            ) * (x - xs[kept - 2])
            if cross < 0:
                break
            kept -= 1
        return UpperHull(xs=(*xs[:kept], x), ys=(*ys[:kept], y))

    def max_offset(self, slope: float) -> float:
        """Returns ``max(y - slope * x)`` over the points added.

        Args:
            slope: Slope of the line subtracted from each point.

        Returns:
            The maximum; the hull must not be empty.
        """
        xs, ys = self.xs, self.ys
        low, high = 0, len(xs) - 1
        while low < high:
            middle = (low + high) // 2
            if ys[middle + 1] - ys[middle] > slope * (
                xs[middle + 1] - xs[middle]
            ):
                low = middle + 1
            else:
                high = middle
        return ys[low] - slope * xs[low]


@dataclasses.dataclass(frozen=True, slots=True)
class RegressionState:
    """State of one regression-channel series.

    Attributes:
        header: Series bookkeeping.
        anchor_start_ms: Start of the chart bar containing ``anchor_ms``,
            0 for the rolling window.
        tick: Series tick, fixed at the first bar (``None`` before it).
        times: Rolling window: bar open times, oldest first.
        prices: Rolling window: source prices.
        highs: Rolling window: highs.
        lows: Rolling window: lows.
        anchor_bar_ms: Anchored window: open time of its first bar.
        count: Anchored window: bars fitted.
        origin: Anchored window: source price of the first bar.
        mean_y: Anchored window: mean offset price.
        sxy: Anchored window: centred co-moment of ``x`` and ``y``.
        syy: Anchored window: centred sum of squared offset prices.
        hull_highs: Anchored window: hull of ``(x, high - origin)``.
        hull_lows: Anchored window: hull of ``(x, origin - low)``.
        fit: Fit after the last bar, ``None`` while undefined.
    """

    header: protocol.SeriesHeader
    anchor_start_ms: int
    tick: decimal.Decimal | None = None
    times: tuple[int, ...] = ()
    prices: tuple[float, ...] = ()
    highs: tuple[float, ...] = ()
    lows: tuple[float, ...] = ()
    anchor_bar_ms: int | None = None
    count: int = 0
    origin: float = 0.0
    mean_y: float = 0.0
    sxy: float = 0.0
    syy: float = 0.0
    hull_highs: UpperHull = UpperHull()
    hull_lows: UpperHull = UpperHull()
    fit: Fit | None = None


def rolling_fit(state: RegressionState, length: int) -> Fit | None:
    """Refits the full rolling window from scratch.

    Args:
        state: State whose rolling window includes the current bar.
        length: Window size.

    Returns:
        The fit, or ``None`` until ``length`` bars exist.
    """
    prices = state.prices
    count = len(prices)
    if count < length:
        return None
    origin = prices[0]
    ys = [price - origin for price in prices]
    mean_y = math.fsum(ys) / count
    centred = [y - mean_y for y in ys]
    xs = range(count)
    mean_x = (count - 1) / 2
    # sum(x * c) - mean_x * sum(c) is sum((x - mean_x) * c) exactly.
    sxy = math.sumprod(xs, centred) - mean_x * math.fsum(centred)
    slope = sxy / sxx(count)
    intercept = origin + mean_y - slope * mean_x
    steps = [slope * x for x in xs]
    return Fit(
        count=count,
        start_time_ms=state.times[0],
        centre=origin + mean_y,
        slope=slope,
        sxy=sxy,
        syy=math.sumprod(centred, centred),
        max_up=max(map(operator.sub, state.highs, steps)) - intercept,
        max_down=intercept - min(map(operator.sub, state.lows, steps)),
    )


def anchored_step(
    state: RegressionState, start_ms: int, price: float, high: float, low: float
) -> RegressionState:
    """Adds a bar to the anchored window and refits it.

    Args:
        state: Previous state.
        start_ms: Open time of the bar (at or after the anchor bar).
        price: Its source price.
        high: Its high.
        low: Its low.

    Returns:
        The state with the bar added and ``fit`` updated.
    """
    anchor_bar = state.anchor_bar_ms
    origin = state.origin
    if anchor_bar is None:
        anchor_bar = start_ms
        origin = price
    x = float(state.count)
    y = price - origin
    count = state.count + 1
    # Welford co-moment update; mean_x = (count - 1) / 2 exactly.
    dx = x - (count - 2) / 2 if count > 1 else 0.0
    dy = y - state.mean_y
    mean_y = state.mean_y + dy / count
    sxy = state.sxy + dx * (y - mean_y)
    syy = state.syy + dy * (y - mean_y)
    hull_highs = state.hull_highs.add(x, high - origin)
    hull_lows = state.hull_lows.add(x, origin - low)
    fit = None
    if count >= 2:
        slope = sxy / sxx(count)
        # Line value at x = 0, still relative to the anchor price.
        intercept = mean_y - slope * (count - 1) / 2
        fit = Fit(
            count=count,
            start_time_ms=anchor_bar,
            centre=origin + mean_y,
            slope=slope,
            sxy=sxy,
            syy=syy,
            max_up=hull_highs.max_offset(slope) - intercept,
            max_down=intercept + hull_lows.max_offset(-slope),
        )
    return dataclasses.replace(
        state,
        anchor_bar_ms=anchor_bar,
        count=count,
        origin=origin,
        mean_y=mean_y,
        sxy=sxy,
        syy=syy,
        hull_highs=hull_highs,
        hull_lows=hull_lows,
        fit=fit,
    )


def fit_values(fit: Fit, deviations: float) -> dict[str, float]:
    """Computes the channel of a fit.

    Args:
        fit: Current fit.
        deviations: Channel half-width in residual stdevs.

    Returns:
        Line, band, slope, stdev, Pearson's R and excursion values.
    """
    half_span = (fit.count - 1) / 2
    start = fit.centre - fit.slope * half_span
    end = fit.centre + fit.slope * half_span
    sse = max(0.0, fit.syy - fit.slope * fit.sxy)
    stdev = math.sqrt(sse / (fit.count - 1))
    width = deviations * stdev
    pearson_r = 0.0
    if fit.syy > 0:
        pearson_r = fit.sxy / math.sqrt(sxx(fit.count) * fit.syy)
        pearson_r = min(1.0, max(-1.0, pearson_r))
    return {
        "start": start,
        "end": end,
        "upper_start": start + width,
        "upper_end": end + width,
        "lower_start": start - width,
        "lower_end": end - width,
        "slope": fit.slope,
        "stdev": stdev,
        "pearson_r": pearson_r,
        "max_up": fit.max_up,
        "max_down": fit.max_down,
    }


def _price_value(
    key: str, description: str, unit: metadata.Unit = metadata.Unit.PRICE
) -> metadata.ValueSpec:
    """Declares one decimal value.

    Args:
        key: Value key.
        description: Meaning.
        unit: Unit.

    Returns:
        The value spec.
    """
    return metadata.ValueSpec(
        key, metadata.ValueType.DECIMAL, description, unit
    )


class RegressionChannelPlugin(base.BasePlugin[RegressionState]):
    """Least-squares regression channel, rolling or from an anchor bar.

    Attributes:
        PLUGIN_ID: ``"regression_channel"``.
        VERSION: Algorithm version.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: Line, bands, fit statistics and window bookkeeping.
        DISPLAY: Not a chart layer: the regression-channel drawing tool
            reads the values.
        SETTINGS: Rolling ``length`` or ``anchor_ms``, the price source,
            band width in standard deviations and ``tick_size``.
        STATE_TYPES: The state dataclasses.
    """

    PLUGIN_ID = "regression_channel"
    VERSION = "2.0.0"
    DISPLAY_NAME = "Regression channel"
    DESCRIPTION = (
        "Least-squares trend line with standard-deviation bands and "
        "Pearson's R, rolling or from an anchor bar."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "start_ms",
            metadata.ValueType.TIMESTAMP_MS,
            "Open time of the oldest bar of the fit.",
        ),
        _price_value("start", "Line value at the oldest bar."),
        _price_value("end", "Line value at the current bar."),
        _price_value("upper_start", "Upper band at the oldest bar."),
        _price_value("upper_end", "Upper band at the current bar."),
        _price_value("lower_start", "Lower band at the oldest bar."),
        _price_value("lower_end", "Lower band at the current bar."),
        _price_value("slope", "Slope of the line, price per bar."),
        _price_value("stdev", "Sample standard deviation of the residuals."),
        _price_value(
            "pearson_r",
            "Pearson's correlation of price and bar index.",
            metadata.Unit.RATIO,
        ),
        _price_value("max_up", "Largest high above the line."),
        _price_value("max_down", "Largest low below the line."),
        metadata.ValueSpec(
            "bars",
            metadata.ValueType.INTEGER,
            "Bars in the fit.",
            metadata.Unit.BARS,
        ),
        metadata.ValueSpec(
            "anchor_in_window",
            metadata.ValueType.BOOLEAN,
            "False when the anchor bar was not computed (every value is "
            "then null).",
        ),
    )
    DISPLAY = metadata.Display(layer=False, default_visible=False)
    SETTINGS = (
        settings_lib.SettingSpec(
            "length",
            settings_lib.SettingKind.INT,
            100,
            "Bars in the rolling window (ignored with an anchor).",
            minimum=2,
            maximum=5000,
        ),
        _series.anchor_spec("Anchor time (UTC ms); 0 uses the rolling window."),
        _series.source_spec("close", _series.AVERAGE_SOURCES),
        settings_lib.SettingSpec(
            "deviations",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(2),
            "Band width in standard deviations.",
            minimum=decimal.Decimal(0),
            maximum=decimal.Decimal(10),
        ),
        _series.tick_spec(),
    )
    STATE_TYPES = (RegressionState, Fit, UpperHull)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the first fit.

        Args:
            settings: Resolved settings.

        Returns:
            ``length`` for the rolling window, 2 with an anchor.
        """
        if settings_lib.get_int(settings, _series.ANCHOR_SETTING) > 0:
            return 2
        return settings_lib.get_int(settings, "length")

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> RegressionState:
        """Builds the empty state with the anchor bar on the calendar.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.

        Raises:
            ValidationFailedError: If the anchor precedes the calendar.
        """
        try:
            anchor_start = _series.anchor_bar_start(
                settings,
                header.timeframe,
                sessions.for_instrument(header.instrument_id),
            )
        except ValueError as err:
            raise errors.ValidationFailedError(
                f"{self.plugin_id}: {err}"
            ) from err
        return RegressionState(header=header, anchor_start_ms=anchor_start)

    def _step(
        self,
        state: RegressionState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[RegressionState]:
        """Adds one bar to the window and refits it.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state.
        """
        settings = header.settings_map()
        candle = bar.candle
        if state.tick is None:
            state = dataclasses.replace(
                state, tick=_series.series_tick(settings, candle)
            )
        price = _series.price_source(
            candle, settings_lib.get_str(settings, "source")
        )
        high, low = float(candle.high), float(candle.low)
        if state.anchor_start_ms == 0:
            length = settings_lib.get_int(settings, "length")
            state = dataclasses.replace(
                state,
                times=_series.push_window(state.times, candle.start_ms, length),
                prices=_series.push_window(state.prices, price, length),
                highs=_series.push_window(state.highs, high, length),
                lows=_series.push_window(state.lows, low, length),
            )
            state = dataclasses.replace(state, fit=rolling_fit(state, length))
        elif candle.start_ms >= state.anchor_start_ms:
            state = anchored_step(state, candle.start_ms, price, high, low)
        return base.StepResult(state=state)

    def _publish(
        self, state: RegressionState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the channel.

        Args:
            state: Current state.

        Returns:
            No AOIs; the values (see the module docstring).
        """
        fit = state.fit
        started = fit.start_time_ms if fit is not None else None
        in_window = state.anchor_start_ms == 0 or started in (
            None,
            state.anchor_start_ms,
        )
        raw: dict[str, float] = {}
        if fit is not None and in_window and state.tick is not None:
            deviations = float(
                settings_lib.get_decimal(
                    state.header.settings_map(), "deviations"
                )
            )
            raw = fit_values(fit, deviations)
        values: dict[str, structures.ValueType] = {
            key: (
                _series.publish_price(raw[key], state.tick)
                if key in raw and state.tick is not None
                else None
            )
            for key in _PRICE_KEYS
        }
        values["start_ms"] = started if in_window else None
        values["pearson_r"] = (
            _series.publish_ratio(raw["pearson_r"], 4) if raw else None
        )
        values["bars"] = fit.count if fit is not None and in_window else 0
        values["anchor_in_window"] = in_window
        return (), values
