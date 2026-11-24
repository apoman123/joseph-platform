# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Simple and exponential moving averages (TradingView ``ta.sma``/``ta.ema``).

* SMA: the mean of the last ``length`` source prices (``math.fsum``), null
  until ``length`` bars exist.
* EMA: ``alpha = 2 / (length + 1)``, seeded with the first source price
  (``na(ema[1]) ? src : alpha * src + (1 - alpha) * ema[1]``, TradingView's
  published reference), so it publishes from the first bar; it counts as
  warm once ``length`` bars exist.
"""

from __future__ import annotations

import dataclasses
import decimal
import math
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import structures
from joseph.indicators.builtin import _series
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib


@dataclasses.dataclass(frozen=True, slots=True)
class WindowState:
    """State of a study over the last ``length`` source prices.

    Attributes:
        header: Series bookkeeping.
        tick: Series tick, fixed at the first bar (``None`` before it).
        window: Source prices of the last ``length`` bars, oldest first.
    """

    header: protocol.SeriesHeader
    tick: decimal.Decimal | None
    window: tuple[float, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class EmaState:
    """State of an exponential moving average.

    Attributes:
        header: Series bookkeeping.
        tick: Series tick, fixed at the first bar (``None`` before it).
        ema: Average after the last bar (``None`` before the first).
    """

    header: protocol.SeriesHeader
    tick: decimal.Decimal | None
    ema: float | None


class SmaPlugin(base.BasePlugin[WindowState]):
    """Simple moving average of a price source.

    Attributes:
        PLUGIN_ID: ``"sma"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: ``sma``.
        DISPLAY: A hidden price-pane line.
        SETTINGS: ``length``, ``source`` and ``tick_size``.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "sma"
    DISPLAY_NAME = "SMA"
    DESCRIPTION = (
        "Simple moving average: the mean of the source price over the last "
        "length bars."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "sma",
            metadata.ValueType.DECIMAL,
            "Simple moving average.",
            metadata.Unit.PRICE,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=False,
        z_index=40,
        plots=(metadata.PlotStyle("sma", "SMA"),),
    )
    SETTINGS = (
        _series.length_spec(20, 5000),
        _series.source_spec(),
        _series.tick_spec(),
    )
    STATE_TYPES = (WindowState,)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the first average.

        Args:
            settings: Resolved settings.

        Returns:
            ``length``.
        """
        return settings_lib.get_int(settings, "length")

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> WindowState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return WindowState(header=header, tick=None, window=())

    def _step(
        self,
        state: WindowState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[WindowState]:
        """Adds the bar's source price to the window.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state.
        """
        settings = header.settings_map()
        length = settings_lib.get_int(settings, "length")
        price = _series.price_source(
            bar.candle, settings_lib.get_str(settings, "source")
        )
        tick = state.tick or _series.series_tick(settings, bar.candle)
        return base.StepResult(
            state=dataclasses.replace(
                state,
                tick=tick,
                window=_series.push_window(state.window, price, length),
            )
        )

    def _publish(
        self, state: WindowState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the average.

        Args:
            state: Current state.

        Returns:
            No AOIs; ``sma``, null until ``length`` bars exist.
        """
        length = settings_lib.get_int(state.header.settings_map(), "length")
        value = None
        if state.tick is not None and len(state.window) == length:
            value = _series.publish_price(
                math.fsum(state.window) / length, state.tick
            )
        return (), {"sma": value}


class EmaPlugin(base.BasePlugin[EmaState]):
    """Exponential moving average of a price source.

    Attributes:
        PLUGIN_ID: ``"ema"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: ``ema``.
        DISPLAY: A price-pane line, visible by default.
        SETTINGS: ``length``, ``source`` and ``tick_size``.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "ema"
    DISPLAY_NAME = "EMA"
    DESCRIPTION = (
        "Exponential moving average of the source price, alpha = 2 / "
        "(length + 1), seeded with the first source value."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "ema",
            metadata.ValueType.DECIMAL,
            "Exponential moving average.",
            metadata.Unit.PRICE,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=True,
        z_index=45,
        plots=(metadata.PlotStyle("ema", "EMA"),),
    )
    SETTINGS = (
        _series.length_spec(20, 5000),
        _series.source_spec(),
        _series.tick_spec(),
    )
    STATE_TYPES = (EmaState,)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the seed has decayed enough to count as warm.

        Args:
            settings: Resolved settings.

        Returns:
            ``length``.
        """
        return settings_lib.get_int(settings, "length")

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> EmaState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return EmaState(header=header, tick=None, ema=None)

    def _step(
        self,
        state: EmaState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[EmaState]:
        """Folds the bar's source price into the average.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state.
        """
        settings = header.settings_map()
        alpha = 2 / (settings_lib.get_int(settings, "length") + 1)
        price = _series.price_source(
            bar.candle, settings_lib.get_str(settings, "source")
        )
        ema = (
            price
            if state.ema is None
            else alpha * price + (1 - alpha) * state.ema
        )
        tick = state.tick or _series.series_tick(settings, bar.candle)
        return base.StepResult(
            state=dataclasses.replace(state, tick=tick, ema=ema)
        )

    def _publish(
        self, state: EmaState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the average.

        Args:
            state: Current state.

        Returns:
            No AOIs; ``ema`` from the first bar.
        """
        value = None
        if state.tick is not None:
            value = _series.publish_price(state.ema, state.tick)
        return (), {"ema": value}
