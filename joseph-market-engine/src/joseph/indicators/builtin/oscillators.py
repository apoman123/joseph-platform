# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Relative strength index and average true range (``ta.rsi``/``ta.atr``).

Both smooth a per-bar series with TradingView's ``ta.rma`` (Wilder's moving
average): ``alpha = 1 / length``, seeded with the simple average
(``math.fsum``) of the first ``length`` values, then
``alpha * x + (1 - alpha) * rma[1]``.

* RSI: ``up = max(src - src[1], 0)`` and ``down = max(src[1] - src, 0)``
  from the second bar on; ``rsi = 100`` when ``rma(down) == 0``, else 0
  when ``rma(up) == 0``, else ``100 - 100 / (1 + rma(up) / rma(down))``.
  The first value appears after ``length + 1`` bars.
* ATR: ``rma`` of the true range, ``high - low`` on the first bar and
  ``max(high - low, |high - close[1]|, |low - close[1]|)`` afterwards. The
  first value appears after ``length`` bars.
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

_RSI_PLACES = 4
"""Decimal places of the published RSI."""


@dataclasses.dataclass(frozen=True, slots=True)
class Rma:
    """Wilder moving average of a series, seeded with a simple average.

    Attributes:
        seed: Values collected until ``length`` exist (then empty).
        value: The average, ``None`` until the seed is complete.
    """

    seed: tuple[float, ...] = ()
    value: float | None = None

    def add(self, x: float, length: int) -> Rma:
        """Folds one value into the average.

        Args:
            x: New value.
            length: Smoothing length.

        Returns:
            The updated average.
        """
        if self.value is not None:
            alpha = 1 / length
            return Rma(value=alpha * x + (1 - alpha) * self.value)
        seed = (*self.seed, x)
        if len(seed) < length:
            return Rma(seed=seed)
        return Rma(value=math.fsum(seed) / length)


@dataclasses.dataclass(frozen=True, slots=True)
class RsiState:
    """State of the relative strength index.

    Attributes:
        header: Series bookkeeping.
        previous: Source price of the previous bar (``None`` before it).
        up: Average gain.
        down: Average loss.
    """

    header: protocol.SeriesHeader
    previous: float | None
    up: Rma
    down: Rma


@dataclasses.dataclass(frozen=True, slots=True)
class AtrState:
    """State of the average true range.

    Attributes:
        header: Series bookkeeping.
        tick: Series tick, fixed at the first bar (``None`` before it).
        previous_close: Close of the previous bar (``None`` before it).
        true_range: Average true range.
    """

    header: protocol.SeriesHeader
    tick: decimal.Decimal | None
    previous_close: float | None
    true_range: Rma


def rsi_value(up: float, down: float) -> float:
    """Computes the RSI from the average gain and loss.

    Args:
        up: Average gain.
        down: Average loss.

    Returns:
        100 without losses, 0 without gains, else
        ``100 - 100 / (1 + up / down)``.
    """
    if down == 0:
        return 100.0
    if up == 0:
        return 0.0
    return 100 - 100 / (1 + up / down)


class RsiPlugin(base.BasePlugin[RsiState]):
    """Relative strength index of a price source.

    Attributes:
        PLUGIN_ID: ``"rsi"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: ``rsi``.
        DISPLAY: A hidden ``rsi`` pane with levels 70, 50 and 30.
        SETTINGS: ``length`` and ``source``.
        STATE_TYPES: The state dataclasses.
    """

    PLUGIN_ID = "rsi"
    DISPLAY_NAME = "RSI"
    DESCRIPTION = (
        "Relative strength index from Wilder moving averages of gains and "
        "losses, seeded with their simple average; 100 when there are no "
        "losses, 0 when there are no gains."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "rsi",
            metadata.ValueType.DECIMAL,
            "Relative strength index, 0 to 100.",
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=False,
        z_index=50,
        plots=(metadata.PlotStyle("rsi", "RSI", pane="rsi"),),
        panes=(
            metadata.PaneStyle(
                "rsi",
                "RSI",
                levels=(
                    metadata.PaneLevel(70, "70"),
                    metadata.PaneLevel(50, "50"),
                    metadata.PaneLevel(30, "30"),
                ),
            ),
        ),
    )
    SETTINGS = (_series.length_spec(14, 500), _series.source_spec())
    STATE_TYPES = (RsiState, Rma)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the first value.

        Args:
            settings: Resolved settings.

        Returns:
            ``length + 1`` (the first bar has no change).
        """
        return settings_lib.get_int(settings, "length") + 1

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> RsiState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return RsiState(header=header, previous=None, up=Rma(), down=Rma())

    def _step(
        self,
        state: RsiState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[RsiState]:
        """Folds the bar's change into the average gain and loss.

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
        up, down = state.up, state.down
        if state.previous is not None:
            up = up.add(max(price - state.previous, 0.0), length)
            down = down.add(max(state.previous - price, 0.0), length)
        return base.StepResult(
            state=dataclasses.replace(state, previous=price, up=up, down=down)
        )

    def _publish(
        self, state: RsiState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the RSI.

        Args:
            state: Current state.

        Returns:
            No AOIs; ``rsi``, null until ``length + 1`` bars exist.
        """
        up, down = state.up.value, state.down.value
        if up is None or down is None:
            return (), {"rsi": None}
        return (), {
            "rsi": _series.publish_ratio(rsi_value(up, down), _RSI_PLACES)
        }


class AtrPlugin(base.BasePlugin[AtrState]):
    """Average true range.

    Attributes:
        PLUGIN_ID: ``"atr"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: ``atr``.
        DISPLAY: A hidden ``atr`` pane.
        SETTINGS: ``length`` and ``tick_size``.
        STATE_TYPES: The state dataclasses.
    """

    PLUGIN_ID = "atr"
    DISPLAY_NAME = "ATR"
    DESCRIPTION = (
        "Average true range: Wilder moving average of the true range, "
        "seeded with its simple average; the first true range is high "
        "minus low."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "atr",
            metadata.ValueType.DECIMAL,
            "Average true range.",
            metadata.Unit.PRICE,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=False,
        z_index=50,
        plots=(metadata.PlotStyle("atr", "ATR", pane="atr"),),
        panes=(metadata.PaneStyle("atr", "ATR", precision=4),),
    )
    SETTINGS = (_series.length_spec(14, 500), _series.tick_spec())
    STATE_TYPES = (AtrState, Rma)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the first value.

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
    ) -> AtrState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return AtrState(
            header=header, tick=None, previous_close=None, true_range=Rma()
        )

    def _step(
        self,
        state: AtrState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[AtrState]:
        """Folds the bar's true range into the average.

        Args:
            state: Previous state.
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state.
        """
        settings = header.settings_map()
        length = settings_lib.get_int(settings, "length")
        high = float(bar.candle.high)
        low = float(bar.candle.low)
        true_range = high - low
        if state.previous_close is not None:
            true_range = max(
                true_range,
                abs(high - state.previous_close),
                abs(low - state.previous_close),
            )
        tick = state.tick or _series.series_tick(settings, bar.candle)
        return base.StepResult(
            state=dataclasses.replace(
                state,
                tick=tick,
                previous_close=float(bar.candle.close),
                true_range=state.true_range.add(true_range, length),
            )
        )

    def _publish(
        self, state: AtrState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the average true range.

        Args:
            state: Current state.

        Returns:
            No AOIs; ``atr``, null until ``length`` bars exist.
        """
        value = None
        if state.tick is not None:
            value = _series.publish_price(state.true_range.value, state.tick)
        return (), {"atr": value}
