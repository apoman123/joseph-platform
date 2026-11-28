# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Exponential moving average (``alpha = 2 / (length + 1)``).

Seeded with the first source value (``na(ema[1]) ? src : alpha * src +
(1 - alpha) * ema[1]``), so it publishes from the first bar.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

SOURCES = ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4")
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


class ExponentialMovingAverage(joseph_script.Indicator):
    """Plots an exponential moving average of the source.

    Attributes:
        value: Average after the last bar, None before the first.
    """

    ID = "ema"
    TITLE = "Moving Average Exponential"
    SHORT_TITLE = "EMA"
    DESCRIPTION = (
        "Exponential moving average with alpha 2 / (length + 1), seeded with "
        "the first source value."
    )
    CATEGORY = "Moving averages"
    OVERLAY = True
    INPUTS = {
        "length": joseph_script.Input(
            20, title="Length", minimum=1, maximum=5000, step=1
        ),
        "source": joseph_script.Input("close", title="Source", options=SOURCES),
        "offset": joseph_script.Input(
            0, title="Offset", minimum=-500, maximum=500
        ),
        "color": joseph_script.Input("#2962ff", title="Color", group="Style"),
    }

    def __init__(self) -> None:
        """Starts before the first value."""
        self.value: float | None = None

    def on_bar(self, ctx: worker.Context) -> None:
        """Updates and publishes the average.

        Args:
            ctx: Script context.
        """
        alpha = 2 / (int(ctx.input("length")) + 1)
        price = source_value(ctx.bar, str(ctx.input("source")))
        self.value = (
            price
            if self.value is None
            else alpha * price + (1 - alpha) * self.value
        )
        ctx.plot(
            "EMA",
            self.value,
            color=str(ctx.input("color")),
            offset=int(ctx.input("offset")),
        )
