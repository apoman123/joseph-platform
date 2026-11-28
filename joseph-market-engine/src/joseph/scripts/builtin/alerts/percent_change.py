# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Percent change alert: the close moves by a percentage over N bars.

The change is ``(close - close N bars ago) / close N bars ago * 100``; it
fires on every closed bar whose change reaches the threshold.
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


DIRECTIONS = ("up", "down", "either")
"""Rises only, falls only, or both."""


class PercentChange(joseph_script.Alert):
    """Alerts when the close moves by a percentage over a number of bars.

    Attributes:
        closes: The last ``bars + 1`` closes.
    """

    ID = "percent-change"
    TITLE = "Percent change"
    SHORT_TITLE = "% change"
    DESCRIPTION = (
        "The close rises or falls by at least a percentage compared with "
        "the close a number of bars earlier."
    )
    CATEGORY = "Price"
    INPUTS = {
        "bars": joseph_script.Input(
            1, title="Over bars", minimum=1, maximum=1000, step=1
        ),
        "threshold_pct": joseph_script.Input(
            5.0, title="Change at least (%)", minimum=0.0001, maximum=100000
        ),
        "direction": joseph_script.Input(
            "either", title="Direction", options=DIRECTIONS
        ),
    }

    def __init__(self) -> None:
        """Starts without closes."""
        self.closes: collections.deque[float] = collections.deque()

    def on_bar(self, ctx: worker.Context) -> None:
        """Compares the close with the one ``bars`` bars earlier.

        Args:
            ctx: Alert context.
        """
        bars = int(ctx.input("bars"))
        threshold = float(ctx.input("threshold_pct"))
        direction = str(ctx.input("direction"))
        self.closes.append(float(ctx.bar["close"]))
        while len(self.closes) > bars + 1:
            self.closes.popleft()
        if len(self.closes) <= bars or self.closes[0] <= 0:
            return
        change = (self.closes[-1] - self.closes[0]) / self.closes[0] * 100
        ctx.alertcondition(
            direction != "down" and change >= threshold,
            "move-up",
            f"{ctx.instrument_id} rose {change:.2f}% over {bars} bars",
        )
        ctx.alertcondition(
            direction != "up" and change <= -threshold,
            "move-down",
            f"{ctx.instrument_id} fell {-change:.2f}% over {bars} bars",
        )
