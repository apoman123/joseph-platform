# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Simple moving average: the mean of the last ``length`` source values."""

from __future__ import annotations

import collections
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


class SimpleMovingAverage(joseph_script.Indicator):
    """Plots the mean of the source over the last ``length`` bars.

    Attributes:
        window: Source values of the last ``length`` bars, oldest first.
    """

    ID = "sma"
    TITLE = "Moving Average Simple"
    SHORT_TITLE = "SMA"
    DESCRIPTION = (
        "Simple moving average: the mean of the source price over the last "
        "length bars; empty until length bars exist."
    )
    CATEGORY = "Moving averages"
    OVERLAY = True
    INPUTS = {
        "length": joseph_script.Input(
            9, title="Length", minimum=1, maximum=5000, step=1
        ),
        "source": joseph_script.Input("close", title="Source", options=SOURCES),
        "offset": joseph_script.Input(
            0, title="Offset", minimum=-500, maximum=500
        ),
        "color": joseph_script.Input("#2962ff", title="Color", group="Style"),
    }

    def __init__(self) -> None:
        """Starts with an empty window."""
        self.window: collections.deque[float] = collections.deque()

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes the average of the last ``length`` source values.

        Args:
            ctx: Script context.
        """
        length = int(ctx.input("length"))
        self.window.append(source_value(ctx.bar, str(ctx.input("source"))))
        while len(self.window) > length:
            self.window.popleft()
        average = (
            sum(self.window) / length if len(self.window) == length else None
        )
        ctx.plot(
            "SMA",
            average,
            color=str(ctx.input("color")),
            offset=int(ctx.input("offset")),
        )
