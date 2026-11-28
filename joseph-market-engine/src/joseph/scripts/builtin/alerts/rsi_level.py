# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""RSI alert: Wilder's RSI crosses into overbought or oversold territory.

The gain and loss averages are seeded with their simple mean over the
first ``length`` changes and then smoothed (``(previous * (length - 1) +
change) / length``). Overbought fires when RSI rises above its level from
at or below it; oversold when it falls below its level from at or above.
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


CONDITIONS = ("both", "overbought", "oversold")
"""Which crossings publish an event."""


class RsiLevel(joseph_script.Alert):
    """Alerts when RSI crosses its overbought or oversold level.

    Attributes:
        changes: Close-to-close changes collected before the seed.
        last_close: Previous close, None before the first bar.
        gain: Smoothed average gain once seeded.
        loss: Smoothed average loss once seeded.
        previous: RSI of the previous bar, None before the first value.
    """

    ID = "rsi-level"
    TITLE = "RSI overbought / oversold"
    SHORT_TITLE = "RSI alert"
    DESCRIPTION = (
        "Wilder's RSI of the close crosses above the overbought level or "
        "below the oversold level."
    )
    CATEGORY = "Oscillators"
    INPUTS = {
        "length": joseph_script.Input(
            14, title="Length", minimum=2, maximum=500, step=1
        ),
        "overbought": joseph_script.Input(
            70.0, title="Overbought", minimum=0, maximum=100
        ),
        "oversold": joseph_script.Input(
            30.0, title="Oversold", minimum=0, maximum=100
        ),
        "condition": joseph_script.Input(
            "both", title="Condition", options=CONDITIONS
        ),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.changes: list[float] = []
        self.last_close: float | None = None
        self.gain: float | None = None
        self.loss: float | None = None
        self.previous: float | None = None

    def update(self, close: float, length: int) -> float | None:
        """Adds one close to the smoothed averages.

        Args:
            close: Closing price.
            length: RSI length.

        Returns:
            RSI after this close, or None before ``length`` changes.
        """
        last, self.last_close = self.last_close, close
        if last is None:
            return None
        change = close - last
        if self.gain is None or self.loss is None:
            self.changes.append(change)
            if len(self.changes) < length:
                return None
            self.gain = sum(max(c, 0.0) for c in self.changes) / length
            self.loss = sum(max(-c, 0.0) for c in self.changes) / length
        else:
            self.gain = (self.gain * (length - 1) + max(change, 0.0)) / length
            self.loss = (self.loss * (length - 1) + max(-change, 0.0)) / length
        if self.loss == 0:
            return 100.0 if self.gain > 0 else 50.0
        return 100 - 100 / (1 + self.gain / self.loss)

    def on_bar(self, ctx: worker.Context) -> None:
        """Updates RSI and publishes level crossings.

        Args:
            ctx: Alert context.
        """
        value = self.update(float(ctx.bar["close"]), int(ctx.input("length")))
        if value is None:
            return
        high = float(ctx.input("overbought"))
        low = float(ctx.input("oversold"))
        condition = str(ctx.input("condition"))
        if self.previous is not None:
            ctx.alertcondition(
                condition != "oversold" and self.previous <= high < value,
                "rsi-overbought",
                f"{ctx.instrument_id} RSI rose above {show(high)}: {value:.2f}",
            )
            ctx.alertcondition(
                condition != "overbought" and self.previous >= low > value,
                "rsi-oversold",
                f"{ctx.instrument_id} RSI fell below {show(low)}: {value:.2f}",
            )
        self.previous = value
