# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Price crossing alert: the close crosses a fixed level.

Fires once per closed bar on which the close moves from at or below the
level to above it (crossing up) or from at or above to below (crossing
down).
"""

from __future__ import annotations

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


class PriceCrossing(joseph_script.Alert):
    """Alerts when the close crosses a price level.

    Attributes:
        previous: Close of the previous bar, None before the first bar.
    """

    ID = "price-cross"
    TITLE = "Price crossing"
    SHORT_TITLE = "Cross"
    DESCRIPTION = (
        "The closed price crosses a level: crossing in either direction, "
        "crossing up or crossing down."
    )
    CATEGORY = "Price"
    INPUTS = {
        "level": joseph_script.Input(100.0, title="Level", minimum=0),
        "condition": joseph_script.Input(
            "crossing", title="Condition", options=CONDITIONS
        ),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.previous: float | None = None

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes crossing-up and crossing-down events.

        Args:
            ctx: Alert context.
        """
        level = float(ctx.input("level"))
        close = float(ctx.bar["close"])
        condition = str(ctx.input("condition"))
        if self.previous is not None:
            direction = crossed((self.previous, level), (close, level))
            ctx.alertcondition(
                direction == "up" and condition != "crossing_down",
                "crossing-up",
                f"{ctx.instrument_id} crossed up {show(level)}: close "
                f"{show(close)}",
            )
            ctx.alertcondition(
                direction == "down" and condition != "crossing_up",
                "crossing-down",
                f"{ctx.instrument_id} crossed down {show(level)}: close "
                f"{show(close)}",
            )
        self.previous = close
