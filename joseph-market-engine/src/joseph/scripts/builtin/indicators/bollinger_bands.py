# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bollinger Bands (TradingView ``ta.bb``).

Over the last ``length`` source prices ``basis`` is their mean, ``dev`` is
``mult`` times their population standard deviation (two passes),
``upper = basis + dev`` and ``lower = basis - dev``. ``bandwidth`` is
``(upper - lower) / basis * 100`` and ``%B`` is
``(source - lower) / (upper - lower)``; both are listed in the data window.
Every value is empty until ``length`` bars exist.
"""

from __future__ import annotations

import collections
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")
"""Selectable price sources."""


def source_value(bar: Mapping[str, Any], source: str) -> float:
    """Reads one price source from a bar.

    Args:
        bar: Closed bar with open/high/low/close fields.
        source: One of SOURCES.

    Returns:
        The source price.
    """
    if source == "hl2":
        return (float(bar["high"]) + float(bar["low"])) / 2
    if source == "hlc3":
        return (
            float(bar["high"]) + float(bar["low"]) + float(bar["close"])
        ) / 3
    if source == "ohlc4":
        total = sum(float(bar[key]) for key in ("open", "high", "low", "close"))
        return total / 4
    return float(bar[source])


class BollingerBands(joseph_script.Indicator):
    """Plots a moving average with bands at a multiple of its deviation.

    Attributes:
        window: Source prices of the last ``length`` bars, oldest first.
    """

    ID = "bollinger_bands"
    TITLE = "Bollinger Bands"
    SHORT_TITLE = "BB"
    DESCRIPTION = (
        "Simple moving average of the source price with bands mult "
        "population standard deviations above and below it."
    )
    CATEGORY = "Volatility"
    OVERLAY = True
    INPUTS = {
        "length": joseph_script.Input(
            20, title="Length", minimum=1, maximum=5000, step=1
        ),
        "source": joseph_script.Input("close", title="Source", options=SOURCES),
        "mult": joseph_script.Input(
            2.0, title="StdDev", minimum=0.001, maximum=50, step=0.1
        ),
        "offset": joseph_script.Input(
            0, title="Offset", minimum=-500, maximum=500
        ),
        "basis_color": joseph_script.Input(
            "#ff6d00", title="Basis", group="Style"
        ),
        "upper_color": joseph_script.Input(
            "#2962ff", title="Upper", group="Style"
        ),
        "lower_color": joseph_script.Input(
            "#089981", title="Lower", group="Style"
        ),
        "fill_color": joseph_script.Input(
            "#2196f3", title="Background", group="Style"
        ),
    }

    def __init__(self) -> None:
        """Starts with an empty window."""
        self.window: collections.deque[float] = collections.deque()

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes the bands of the last ``length`` source prices.

        Args:
            ctx: Script context.
        """
        length = int(ctx.input("length"))
        price = source_value(ctx.bar, str(ctx.input("source")))
        self.window.append(price)
        while len(self.window) > length:
            self.window.popleft()
        values: dict[str, float | None] = dict.fromkeys(
            ("basis", "upper", "lower", "bandwidth", "percent_b")
        )
        if len(self.window) == length:
            basis = math.fsum(self.window) / length
            variance = math.fsum((x - basis) ** 2 for x in self.window) / length
            dev = float(ctx.input("mult")) * math.sqrt(variance)
            upper, lower = basis + dev, basis - dev
            values.update(basis=basis, upper=upper, lower=lower)
            if basis != 0:
                values["bandwidth"] = (upper - lower) / basis * 100
            if upper != lower:
                values["percent_b"] = (price - lower) / (upper - lower)
        offset = int(ctx.input("offset"))
        for name, key in (("Basis", "basis"), ("Upper", "upper")):
            ctx.plot(
                name,
                values[key],
                color=str(ctx.input(f"{key}_color")),
                width=1,
                offset=offset,
            )
        ctx.plot(
            "Lower",
            values["lower"],
            color=str(ctx.input("lower_color")),
            width=1,
            offset=offset,
        )
        ctx.plot("Bandwidth", values["bandwidth"], display="data_window")
        ctx.plot("%B", values["percent_b"], display="data_window")
        if ctx.is_first:
            ctx.fill(
                "Upper",
                "Lower",
                color=str(ctx.input("fill_color")),
                opacity=0.08,
            )
