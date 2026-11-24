# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bollinger Bands (TradingView ``ta.bb``).

Over the last ``length`` source prices: ``basis`` is their mean
(``math.fsum``), ``dev`` is ``mult`` times their population standard
deviation (TradingView ``ta.stdev`` with its default biased estimate,
computed in two passes), ``upper = basis + dev`` and
``lower = basis - dev``. ``bandwidth`` is ``(upper - lower) / basis * 100``
and ``percent_b`` is ``(source - lower) / (upper - lower)``. Every value is
null until ``length`` bars exist.
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

_RATIO_PLACES = 4
"""Decimal places of ``bandwidth`` and ``percent_b``."""

_VALUE_KEYS = ("basis", "upper", "lower", "bandwidth", "percent_b")
"""Published value keys."""


@dataclasses.dataclass(frozen=True, slots=True)
class BandsState:
    """State of the bands over the last ``length`` source prices.

    Attributes:
        header: Series bookkeeping.
        tick: Series tick, fixed at the first bar (``None`` before it).
        window: Source prices of the last ``length`` bars, oldest first.
    """

    header: protocol.SeriesHeader
    tick: decimal.Decimal | None
    window: tuple[float, ...]


class BollingerBandsPlugin(base.BasePlugin[BandsState]):
    """Moving average with bands at a multiple of the standard deviation.

    Attributes:
        PLUGIN_ID: ``"bollinger_bands"``.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        OUTPUT_VALUES: ``basis``, ``upper``, ``lower``, ``bandwidth`` and
            ``percent_b``.
        DISPLAY: Three hidden price-pane lines.
        SETTINGS: ``length``, ``source``, ``mult`` and ``tick_size``.
        STATE_TYPES: The state dataclass.
    """

    PLUGIN_ID = "bollinger_bands"
    DISPLAY_NAME = "Bollinger Bands"
    DESCRIPTION = (
        "Simple moving average of the source price with bands mult "
        "population standard deviations above and below it."
    )
    OUTPUT_VALUES = (
        metadata.ValueSpec(
            "basis",
            metadata.ValueType.DECIMAL,
            "Simple moving average of the source price.",
            metadata.Unit.PRICE,
        ),
        metadata.ValueSpec(
            "upper",
            metadata.ValueType.DECIMAL,
            "Basis plus mult standard deviations.",
            metadata.Unit.PRICE,
        ),
        metadata.ValueSpec(
            "lower",
            metadata.ValueType.DECIMAL,
            "Basis minus mult standard deviations.",
            metadata.Unit.PRICE,
        ),
        metadata.ValueSpec(
            "bandwidth",
            metadata.ValueType.DECIMAL,
            "Band width as a percentage of the basis; null when the basis "
            "is 0.",
            metadata.Unit.PERCENT,
        ),
        metadata.ValueSpec(
            "percent_b",
            metadata.ValueType.DECIMAL,
            "Position of the source price between the bands (0 at the lower "
            "band, 1 at the upper band); null when the bands coincide.",
            metadata.Unit.RATIO,
        ),
    )
    DISPLAY = metadata.Display(
        default_visible=False,
        z_index=30,
        plots=(
            metadata.PlotStyle("basis", "Basis"),
            metadata.PlotStyle("upper", "Upper", tone=metadata.Tone.NEUTRAL),
            metadata.PlotStyle("lower", "Lower", tone=metadata.Tone.NEUTRAL),
        ),
    )
    SETTINGS = (
        _series.length_spec(20, 5000),
        _series.source_spec(),
        settings_lib.SettingSpec(
            "mult",
            settings_lib.SettingKind.DECIMAL,
            decimal.Decimal(2),
            "Band distance from the basis in standard deviations.",
            minimum=decimal.Decimal("0.001"),
            maximum=decimal.Decimal(50),
        ),
        _series.tick_spec(),
    )
    STATE_TYPES = (BandsState,)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns bars until the first bands.

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
    ) -> BandsState:
        """Builds the empty state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            State before any bar.
        """
        del settings  # Unused.
        return BandsState(header=header, tick=None, window=())

    def _step(
        self,
        state: BandsState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[BandsState]:
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
        self, state: BandsState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Publishes the bands.

        Args:
            state: Current state.

        Returns:
            No AOIs; every value null until ``length`` bars exist.
        """
        settings = state.header.settings_map()
        length = settings_lib.get_int(settings, "length")
        values: dict[str, structures.ValueType] = dict.fromkeys(_VALUE_KEYS)
        if state.tick is None or len(state.window) < length:
            return (), values
        mult = float(settings_lib.get_decimal(settings, "mult"))
        basis = math.fsum(state.window) / length
        deviations = [x - basis for x in state.window]
        variance = math.fsum(d * d for d in deviations) / length
        dev = mult * math.sqrt(variance)
        upper = basis + dev
        lower = basis - dev
        values["basis"] = _series.publish_price(basis, state.tick)
        values["upper"] = _series.publish_price(upper, state.tick)
        values["lower"] = _series.publish_price(lower, state.tick)
        if basis != 0:
            values["bandwidth"] = _series.publish_ratio(
                (upper - lower) / basis * 100, _RATIO_PLACES
            )
        if upper != lower:
            values["percent_b"] = _series.publish_ratio(
                (state.window[-1] - lower) / (upper - lower), _RATIO_PLACES
            )
        return (), values
