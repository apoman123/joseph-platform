# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Channel alert: the close enters, exits or stays inside or outside a band.

The channel is the inclusive range from ``lower`` to ``upper``. Entering
and exiting fire on the bar that changes side; inside and outside fire on
every closed bar in that state.
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


CONDITIONS = ("entering", "exiting", "inside", "outside")
"""Channel conditions in TradingView order."""


class Channel(joseph_script.Alert):
    """Alerts when the close enters or exits a price channel.

    Attributes:
        was_inside: Whether the previous close was inside, None at first.
    """

    ID = "channel"
    TITLE = "Price channel"
    SHORT_TITLE = "Channel"
    DESCRIPTION = (
        "The closed price enters or exits a channel between two levels, or "
        "closes inside or outside it."
    )
    CATEGORY = "Price"
    INPUTS = {
        "upper": joseph_script.Input(110.0, title="Upper level", minimum=0),
        "lower": joseph_script.Input(90.0, title="Lower level", minimum=0),
        "condition": joseph_script.Input(
            "entering", title="Condition", options=CONDITIONS
        ),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.was_inside: bool | None = None

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes the selected channel event.

        Args:
            ctx: Alert context.

        Raises:
            ValueError: If the lower level is not below the upper level.
        """
        upper = float(ctx.input("upper"))
        lower = float(ctx.input("lower"))
        if lower >= upper:
            raise ValueError("the lower level must be below the upper level")
        close = float(ctx.bar["close"])
        inside = lower <= close <= upper
        condition = str(ctx.input("condition"))
        band = f"{show(lower)}-{show(upper)}"
        changed = self.was_inside is not None and self.was_inside != inside
        events = {
            "entering": (changed and inside, "entered"),
            "exiting": (changed and not inside, "exited"),
            "inside": (inside, "closed inside"),
            "outside": (not inside, "closed outside"),
        }
        matched, verb = events[condition]
        ctx.alertcondition(
            matched,
            f"{condition}-channel",
            f"{ctx.instrument_id} {verb} the channel {band}: close "
            f"{show(close)}",
        )
        self.was_inside = inside
