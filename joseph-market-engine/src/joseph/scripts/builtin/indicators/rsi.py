# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Relative strength index (TradingView ``ta.rsi``).

Gains ``max(src - src[1], 0)`` and losses ``max(src[1] - src, 0)`` from the
second bar on are smoothed with Wilder's moving average (``ta.rma``:
``alpha = 1 / length``, seeded with the simple average of the first
``length`` values). ``rsi = 100`` without losses, ``0`` without gains, else
``100 - 100 / (1 + up / down)``; the first value appears after
``length + 1`` bars.
"""

from __future__ import annotations

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


class Rma:
    """Wilder moving average seeded with the simple average.

    Attributes:
        seed: Values collected until ``length`` exist.
        value: The average, None until the seed is complete.
    """

    def __init__(self) -> None:
        """Starts before the first value."""
        self.seed: list[float] = []
        self.value: float | None = None

    def add(self, x: float, length: int) -> float | None:
        """Folds one value into the average.

        Args:
            x: New value.
            length: Smoothing length.

        Returns:
            The updated average, None while seeding.
        """
        if self.value is not None:
            alpha = 1 / length
            self.value = alpha * x + (1 - alpha) * self.value
        else:
            self.seed.append(x)
            if len(self.seed) == length:
                self.value = math.fsum(self.seed) / length
                self.seed = []
        return self.value


class RelativeStrengthIndex(joseph_script.Indicator):
    """Plots the RSI in its own pane with overbought and oversold levels.

    Attributes:
        previous: Source price of the previous bar.
        up: Average gain.
        down: Average loss.
    """

    ID = "rsi"
    TITLE = "Relative Strength Index"
    SHORT_TITLE = "RSI"
    DESCRIPTION = (
        "Relative strength index from Wilder moving averages of gains and "
        "losses; 100 without losses, 0 without gains."
    )
    CATEGORY = "Momentum"
    OVERLAY = False
    INPUTS = {
        "length": joseph_script.Input(
            14, title="Length", minimum=1, maximum=500, step=1
        ),
        "source": joseph_script.Input("close", title="Source", options=SOURCES),
        "upper": joseph_script.Input(
            70, title="Upper band", minimum=0, maximum=100, group="Levels"
        ),
        "middle": joseph_script.Input(
            50, title="Middle band", minimum=0, maximum=100, group="Levels"
        ),
        "lower": joseph_script.Input(
            30, title="Lower band", minimum=0, maximum=100, group="Levels"
        ),
        "color": joseph_script.Input("#7e57c2", title="RSI", group="Style"),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.previous: float | None = None
        self.up = Rma()
        self.down = Rma()

    def on_bar(self, ctx: worker.Context) -> None:
        """Folds the bar's change and publishes the RSI.

        Args:
            ctx: Script context.
        """
        length = int(ctx.input("length"))
        price = source_value(ctx.bar, str(ctx.input("source")))
        value = None
        if self.previous is not None:
            up = self.up.add(max(price - self.previous, 0.0), length)
            down = self.down.add(max(self.previous - price, 0.0), length)
            if up is not None and down is not None:
                if down == 0:
                    value = 100.0
                elif up == 0:
                    value = 0.0
                else:
                    value = 100 - 100 / (1 + up / down)
        self.previous = price
        ctx.plot("RSI", value, color=str(ctx.input("color")), width=1)
        if ctx.is_first:
            ctx.hline("Upper band", float(ctx.input("upper")))
            ctx.hline("Middle band", float(ctx.input("middle")), style="dotted")
            ctx.hline("Lower band", float(ctx.input("lower")))
            ctx.fill(
                "Upper band",
                "Lower band",
                color=str(ctx.input("color")),
                opacity=0.08,
            )
