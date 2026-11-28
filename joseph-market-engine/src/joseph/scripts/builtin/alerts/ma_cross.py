# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Moving-average cross alert: a fast average crosses a slow one.

Both averages use the closes; the exponential average is seeded with the
first close (``alpha = 2 / (length + 1)``) and the simple one waits for
``length`` bars. A cross up is commonly called a golden cross.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker


def show(value: float) -> str:
    """Formats a price or level for alert messages.

    Args:
        value: Number.

    Returns:
        Up to ten significant digits.
    """
    return f"{value:.10g}"


CONDITIONS = ("crossing", "crossing_up", "crossing_down")
"""Either direction, upward only or downward only."""


def crossed(
    previous: tuple[float, float], current: tuple[float, float]
) -> str | None:
    """Detects a cross of a series over or under another.

    A cross up means the series is above the other now and was at or below
    it on the previous bar; a cross down mirrors it.

    Args:
        previous: Series and other value on the previous bar.
        current: Series and other value on this bar.

    Returns:
        "up", "down" or None.
    """
    if current[0] > current[1] and previous[0] <= previous[1]:
        return "up"
    if current[0] < current[1] and previous[0] >= previous[1]:
        return "down"
    return None


class MovingAverageCross(joseph_script.Alert):
    """Alerts when a fast moving average crosses a slow one.

    Attributes:
        closes: Last closes, as many as the longer simple average needs.
        ema: Exponential averages keyed by length.
        previous: Fast and slow averages of the previous bar, if both exist.
    """

    ID = "ma-cross"
    TITLE = "Moving average cross"
    SHORT_TITLE = "MA cross"
    DESCRIPTION = (
        "A fast simple or exponential moving average of the close crosses "
        "a slow one, up (golden cross) or down (death cross)."
    )
    CATEGORY = "Moving averages"
    INPUTS = {
        "fast_length": joseph_script.Input(
            9, title="Fast length", minimum=1, maximum=1000, step=1
        ),
        "slow_length": joseph_script.Input(
            21, title="Slow length", minimum=2, maximum=1000, step=1
        ),
        "ma_type": joseph_script.Input(
            "EMA", title="Average", options=("EMA", "SMA")
        ),
        "condition": joseph_script.Input(
            "crossing", title="Condition", options=CONDITIONS
        ),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.closes: collections.deque[float] = collections.deque()
        self.ema: dict[int, float] = {}
        self.previous: tuple[float, float] | None = None

    def average(self, length: int, kind: str) -> float | None:
        """Reads one average after the current close was added.

        Args:
            length: Average length.
            kind: "EMA" or "SMA".

        Returns:
            The average, or None while a simple average lacks bars.
        """
        if kind == "SMA":
            if len(self.closes) < length:
                return None
            window = list(self.closes)[-length:]
            return sum(window) / length
        return self.ema[length]

    def on_bar(self, ctx: worker.Context) -> None:
        """Updates both averages and publishes crosses.

        Args:
            ctx: Alert context.
        """
        fast_length = int(ctx.input("fast_length"))
        slow_length = int(ctx.input("slow_length"))
        kind = str(ctx.input("ma_type"))
        close = float(ctx.bar["close"])
        self.closes.append(close)
        while len(self.closes) > max(fast_length, slow_length):
            self.closes.popleft()
        for length in {fast_length, slow_length}:
            alpha = 2 / (length + 1)
            last = self.ema.get(length)
            self.ema[length] = (
                close if last is None else alpha * close + (1 - alpha) * last
            )
        fast = self.average(fast_length, kind)
        slow = self.average(slow_length, kind)
        if fast is None or slow is None:
            return
        condition = str(ctx.input("condition"))
        if self.previous is not None:
            direction = crossed(self.previous, (fast, slow))
            label = f"{kind} {fast_length}/{slow_length}"
            ctx.alertcondition(
                direction == "up" and condition != "crossing_down",
                "ma-cross-up",
                f"{ctx.instrument_id} {label} crossed up at close "
                f"{show(close)}",
            )
            ctx.alertcondition(
                direction == "down" and condition != "crossing_up",
                "ma-cross-down",
                f"{ctx.instrument_id} {label} crossed down at close "
                f"{show(close)}",
            )
        self.previous = (fast, slow)
