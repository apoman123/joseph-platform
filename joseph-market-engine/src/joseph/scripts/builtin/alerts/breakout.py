# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""New high/low alert: a closed bar breaks the prior N-bar range.

A new high is a high above every high of the ``length`` bars before it; a
new low is a low below every low of those bars.
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


EXTREMES = ("high", "low", "either")
"""Which breaks publish an event."""


class NewHighLow(joseph_script.Alert):
    """Alerts when a bar makes a new high or low of the preceding bars.

    Attributes:
        highs: Highs of the preceding bars, at most ``length``.
        lows: Lows of the preceding bars, at most ``length``.
    """

    ID = "breakout"
    TITLE = "New high or low breakout"
    SHORT_TITLE = "Breakout"
    DESCRIPTION = (
        "A closed bar's high is above, or its low below, every bar of the "
        "preceding length bars."
    )
    CATEGORY = "Price"
    INPUTS = {
        "length": joseph_script.Input(
            20, title="Bars", minimum=1, maximum=1000, step=1
        ),
        "extreme": joseph_script.Input(
            "either", title="Extreme", options=EXTREMES
        ),
    }

    def __init__(self) -> None:
        """Starts without a range."""
        self.highs: collections.deque[float] = collections.deque()
        self.lows: collections.deque[float] = collections.deque()

    def on_bar(self, ctx: worker.Context) -> None:
        """Compares the bar with the preceding range.

        Args:
            ctx: Alert context.
        """
        length = int(ctx.input("length"))
        extreme = str(ctx.input("extreme"))
        high = float(ctx.bar["high"])
        low = float(ctx.bar["low"])
        if len(self.highs) == length:
            ctx.alertcondition(
                extreme != "low" and high > max(self.highs),
                "new-high",
                f"{ctx.instrument_id} made a {length}-bar high at {show(high)}",
            )
            ctx.alertcondition(
                extreme != "high" and low < min(self.lows),
                "new-low",
                f"{ctx.instrument_id} made a {length}-bar low at {show(low)}",
            )
        self.highs.append(high)
        self.lows.append(low)
        while len(self.highs) > length:
            self.highs.popleft()
            self.lows.popleft()
