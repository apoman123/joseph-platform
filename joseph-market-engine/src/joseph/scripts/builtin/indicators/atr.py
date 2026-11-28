# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Average true range (TradingView ``ta.atr``).

Wilder's moving average (``alpha = 1 / length``, seeded with the simple
average of the first ``length`` values) of the true range: ``high - low``
on the first bar, then ``max(high - low, |high - close[1]|,
|low - close[1]|)``. The first value appears after ``length`` bars.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker


class AverageTrueRange(joseph_script.Indicator):
    """Plots the average true range in its own pane.

    Attributes:
        previous_close: Close of the previous bar.
        seed: True ranges collected until ``length`` exist.
        value: The average, None until the seed is complete.
    """

    ID = "atr"
    TITLE = "Average True Range"
    SHORT_TITLE = "ATR"
    DESCRIPTION = (
        "Average true range: Wilder moving average of the true range, "
        "seeded with its simple average; the first true range is high "
        "minus low."
    )
    CATEGORY = "Volatility"
    OVERLAY = False
    INPUTS = {
        "length": joseph_script.Input(
            14, title="Length", minimum=1, maximum=500, step=1
        ),
        "color": joseph_script.Input("#b71c1c", title="ATR", group="Style"),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.previous_close: float | None = None
        self.seed: list[float] = []
        self.value: float | None = None

    def on_bar(self, ctx: worker.Context) -> None:
        """Folds the bar's true range and publishes the average.

        Args:
            ctx: Script context.
        """
        length = int(ctx.input("length"))
        high, low = float(ctx.bar["high"]), float(ctx.bar["low"])
        true_range = high - low
        if self.previous_close is not None:
            true_range = max(
                true_range,
                abs(high - self.previous_close),
                abs(low - self.previous_close),
            )
        self.previous_close = float(ctx.bar["close"])
        if self.value is not None:
            alpha = 1 / length
            self.value = alpha * true_range + (1 - alpha) * self.value
        else:
            self.seed.append(true_range)
            if len(self.seed) == length:
                self.value = math.fsum(self.seed) / length
        ctx.plot("ATR", self.value, color=str(ctx.input("color")), width=1)
