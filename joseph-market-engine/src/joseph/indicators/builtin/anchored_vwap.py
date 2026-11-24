# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Anchored VWAP with standard-deviation bands (TradingView VWAP).

Over the bars of the current anchor period the study accumulates
``sum(src * v)``, ``sum(v)`` and ``sum(src**2 * v)`` in doubles, in bar
order; then ``vwap = sum(src * v) / sum(v)``, ``stdev = sqrt(max(0,
sum(src**2 * v) / sum(v) - vwap**2))`` and band ``k`` is ``vwap +/- m_k *
stdev``. This is the plain one-pass formula of TradingView's built-in VWAP,
not a two-pass variance. A bar whose volume is not positive adds nothing
(it still counts in ``bars``); while ``sum(v)`` is 0 the VWAP, stdev and
bands are null, and a band whose multiplier is 0 is always null.

Anchors:

* ``session`` / ``week`` / ``month``: the sums restart on the first bar of
  each period. On a 24x7 market the periods are the UTC day, the ISO week
  from Monday 00:00 UTC and the UTC calendar month, keyed by the bar's open
  time so a gap that skips a whole period still restarts them. On an
  exchange they are the calendar's ``1d``, ``1w`` and ``1M`` bars (one
  session, the sessions of an ISO week or of a calendar month in exchange
  time).
* ``time``: the sums start once, at the chart bar that contains
  ``anchor_ms`` (the anchored-VWAP drawing tool); every value is null before
  it. If the first computed bar is already past that bar, the anchor is
  outside the window: every value is null and ``anchor_in_window`` is
  False, so a too-distant anchor is never drawn from the wrong bar.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import math
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import errors
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.builtin import _series
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib

ANCHOR_TIME = "time"
"""Anchor mode: once, at the chart bar containing ``anchor_ms``."""

BAND_COUNT = 3
"""Number of standard-deviation bands (``upper_1`` ... ``lower_3``)."""

DAY_MS = 86_400_000
"""Milliseconds in a UTC day."""

_EPOCH_ORDINAL = datetime.date(1970, 1, 1).toordinal()
"""Proleptic Gregorian ordinal of the Unix epoch (a Thursday)."""

_CALENDAR_ANCHORS: Mapping[str, tfs.Timeframe] = {
    "session": tfs.Timeframe.D1,
    "week": tfs.Timeframe.W1,
    "month": tfs.Timeframe.MN1,
}
"""Exchange anchor periods: the calendar's daily, weekly and monthly bars."""

_BANDS: tuple[tuple[str, str], ...] = (
    ("upper_1", "Upper 1"),
    ("lower_1", "Lower 1"),
    ("upper_2", "Upper 2"),
    ("lower_2", "Lower 2"),
    ("upper_3", "Upper 3"),
    ("lower_3", "Lower 3"),
)
"""Band value keys and labels, in publication order."""

_PRICE_KEYS = ("vwap", "stdev", *(key for key, _ in _BANDS))
"""Published price values, in publication order."""


def utc_period_start(time_ms: int, anchor: str) -> int:
    """Returns the start of the UTC anchor period containing a time.

    Args:
        time_ms: UTC epoch milliseconds (a bar open time).
        anchor: ``"session"``, ``"week"`` or ``"month"``.

    Returns:
        UTC epoch milliseconds of the period's first instant: 00:00 of the
        day, of the week's Monday, or of the month's first day.

    Raises:
        ValueError: If ``anchor`` is not a calendar anchor.
    """
    day = time_ms // DAY_MS
    if anchor == "session":
        return day * DAY_MS
    if anchor == "week":
        # Day 0 (1970-01-01) is a Thursday, three days after a Monday.
        return (day - (day + 3) % 7) * DAY_MS
    if anchor == "month":
        date = datetime.date.fromordinal(_EPOCH_ORDINAL + day)
        first = date.replace(day=1).toordinal() - _EPOCH_ORDINAL
        return first * DAY_MS
    raise ValueError(f"not a calendar anchor: {anchor!r}")


def period_start(
    anchor: str, start_ms: int, calendar: sessions.BarCalendar
) -> int:
    """Returns the start of the anchor period containing a bar.

    Args:
        anchor: ``"session"``, ``"week"`` or ``"month"``.
        start_ms: Bar open time.
        calendar: Instrument calendar.

    Returns:
        The UTC period start on a 24x7 market, else the start of the
        calendar's ``1d``/``1w``/``1M`` bar containing the bar.
    """
    if calendar.continuous:
        return utc_period_start(start_ms, anchor)
    return calendar.floor(_CALENDAR_ANCHORS[anchor], start_ms)


@dataclasses.dataclass(frozen=True, slots=True)
class VwapState:
    """State of one anchored VWAP series.

    Attributes:
        header: Series bookkeeping.
        anchor_start_ms: Start of the chart bar containing ``anchor_ms``
            (0 without an anchor time).
        tick: Series tick, fixed at the first bar (``None`` before it).
        period_ms: Start of the current anchor period (calendar anchors).
        anchor_bar_ms: Open time of the bar the sums started at (``None``
            before it).
        bars: Bars since that bar, counting the current one.
        sum_source_volume: ``sum(src * v)``.
        sum_volume: ``sum(v)``.
        sum_source_squared_volume: ``sum(src * src * v)``.
    """

    header: protocol.SeriesHeader
    anchor_start_ms: int
    tick: decimal.Decimal | None = None
    period_ms: int | None = None
    anchor_bar_ms: int | None = None
    bars: int = 0
    sum_source_volume: float = 0.0
    sum_volume: float = 0.0
    sum_source_squared_volume: float = 0.0


class AnchoredVwapPlugin(base.BasePlugin[VwapState]):
    """Anchored VWAP with up to three standard-deviation bands.

    Attributes:
        PLUGIN_ID: ``"anchored_vwap"``.
        VERSION: Algorithm version.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: VWAP, stdev, bands and the anchor bookkeeping.
        DISPLAY: Hidden price-pane lines for the VWAP and its bands.
        SETTINGS: Anchor period or ``time`` with ``anchor_ms``, the price
            source, three band multipliers and ``tick_size``.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "anchored_vwap"
    VERSION = "2.0.0"
    DISPLAY_NAME = "VWAP"
    DESCRIPTION = (
        "Volume-weighted average price from a session, week, month or a "
        "chosen bar, with up to three standard-deviation bands."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "vwap",
            metadata.ValueType.DECIMAL,
            "Volume-weighted average price since the anchor.",
            metadata.Unit.PRICE,
        ),
        metadata.ValueSpec(
            "stdev",
            metadata.ValueType.DECIMAL,
            "Volume-weighted standard deviation of the source.",
            metadata.Unit.PRICE,
        ),
        *(
            metadata.ValueSpec(
                key,
                metadata.ValueType.DECIMAL,
                f"{label} band (null when its multiplier is 0).",
                metadata.Unit.PRICE,
            )
            for key, label in _BANDS
        ),
        metadata.ValueSpec(
            "anchor_ms",
            metadata.ValueType.TIMESTAMP_MS,
            "Open time of the bar the average started at.",
        ),
        metadata.ValueSpec(
            "bars",
            metadata.ValueType.INTEGER,
            "Bars since the anchor bar, counting the current one.",
            metadata.Unit.BARS,
        ),
        metadata.ValueSpec(
            "anchor_in_window",
            metadata.ValueType.BOOLEAN,
            "False when the anchor bar was not computed (every value is "
            "then null).",
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=False,
        z_index=50,
        plots=(
            metadata.PlotStyle("vwap", "VWAP"),
            *(
                metadata.PlotStyle(
                    key,
                    label,
                    tone=metadata.Tone.NEUTRAL,
                    line_style=metadata.LineStyle.DASHED,
                )
                for key, label in _BANDS
            ),
        ),
    )
    SETTINGS = (
        settings_lib.SettingSpec(
            "anchor",
            settings_lib.SettingKind.ENUM,
            "session",
            "Reset period (session or UTC day, week, month) or a fixed "
            "anchor bar (time).",
            choices=("session", "week", "month", ANCHOR_TIME),
        ),
        _series.anchor_spec("Anchor time (UTC ms) when anchor is time."),
        _series.source_spec("hlc3", _series.AVERAGE_SOURCES),
        *(
            settings_lib.SettingSpec(
                f"band_{index}",
                settings_lib.SettingKind.DECIMAL,
                decimal.Decimal(default),
                f"Band {index} width in standard deviations (0 hides it).",
                minimum=decimal.Decimal(0),
                maximum=decimal.Decimal(10),
            )
            for index, default in ((1, "1"), (2, "0"), (3, "0"))
        ),
        _series.tick_spec(),
    )
    STATE_TYPES = (VwapState,)

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> VwapState:
        """Builds the empty state with the anchor bar on the calendar.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.

        Raises:
            ValidationFailedError: If ``anchor`` is ``time`` without
                ``anchor_ms``, or the anchor precedes the calendar.
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
        if settings_lib.get_str(settings, "anchor") == ANCHOR_TIME and (
            anchor_start == 0
        ):
            raise errors.ValidationFailedError(
                f"{self.plugin_id}: anchor_ms is required for time"
            )
        return VwapState(header=header, anchor_start_ms=anchor_start)

    def _step(
        self,
        state: VwapState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[VwapState]:
        """Adds one bar to the sums, restarting them at a new anchor.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state.
        """
        settings = header.settings_map()
        candle = bar.candle
        anchor = settings_lib.get_str(settings, "anchor")
        tick = state.tick
        if tick is None:
            tick = _series.series_tick(settings, candle)
        period = state.period_ms
        if anchor == ANCHOR_TIME:
            reset = (
                state.anchor_bar_ms is None
                and candle.start_ms >= state.anchor_start_ms
            )
        else:
            period = period_start(
                anchor,
                candle.start_ms,
                sessions.for_instrument(header.instrument_id),
            )
            reset = period != state.period_ms
        if reset:
            state = dataclasses.replace(
                state,
                anchor_bar_ms=candle.start_ms,
                bars=0,
                sum_source_volume=0.0,
                sum_volume=0.0,
                sum_source_squared_volume=0.0,
            )
        elif state.anchor_bar_ms is None:
            return base.StepResult(
                state=dataclasses.replace(state, tick=tick, period_ms=period)
            )
        sums = (
            state.sum_source_volume,
            state.sum_volume,
            state.sum_source_squared_volume,
        )
        volume = float(candle.base_volume)
        if volume > 0:
            price = _series.price_source(
                candle, settings_lib.get_str(settings, "source")
            )
            sums = (
                sums[0] + price * volume,
                sums[1] + volume,
                sums[2] + price * price * volume,
            )
        return base.StepResult(
            state=dataclasses.replace(
                state,
                tick=tick,
                period_ms=period,
                bars=state.bars + 1,
                sum_source_volume=sums[0],
                sum_volume=sums[1],
                sum_source_squared_volume=sums[2],
            )
        )

    def _publish(
        self, state: VwapState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the VWAP, its bands and the anchor bookkeeping.

        Args:
            state: Current state.

        Returns:
            No AOIs; the values (see the module docstring).
        """
        settings = state.header.settings_map()
        in_window = True
        if settings_lib.get_str(settings, "anchor") == ANCHOR_TIME:
            in_window = state.anchor_bar_ms in (None, state.anchor_start_ms)
        raw: dict[str, float | None] = dict.fromkeys(_PRICE_KEYS)
        if state.anchor_bar_ms is not None and state.sum_volume > 0:
            vwap = state.sum_source_volume / state.sum_volume
            variance = (
                state.sum_source_squared_volume / state.sum_volume - vwap * vwap
            )
            stdev = math.sqrt(max(0.0, variance))
            raw["vwap"] = vwap
            raw["stdev"] = stdev
            for index in range(1, BAND_COUNT + 1):
                multiplier = float(
                    settings_lib.get_decimal(settings, f"band_{index}")
                )
                if multiplier > 0:
                    raw[f"upper_{index}"] = vwap + multiplier * stdev
                    raw[f"lower_{index}"] = vwap - multiplier * stdev
        values: dict[str, structures.ValueType] = {
            key: (
                _series.publish_price(value, state.tick)
                if in_window and state.tick is not None
                else None
            )
            for key, value in raw.items()
        }
        values["anchor_ms"] = state.anchor_bar_ms if in_window else None
        values["bars"] = state.bars if in_window else 0
        values["anchor_in_window"] = in_window
        return (), values
