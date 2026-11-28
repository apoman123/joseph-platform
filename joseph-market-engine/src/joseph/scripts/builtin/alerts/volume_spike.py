# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Volume spike alert: a bar's volume exceeds a multiple of its average.

The average is the mean volume of the ``length`` closed bars before the
evaluated bar, so the spike itself never raises its own baseline.
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


class VolumeSpike(joseph_script.Alert):
    """Alerts when a bar's volume exceeds a multiple of the recent average.

    Attributes:
        volumes: Volumes of the preceding bars, at most ``length``.
    """

    ID = "volume-spike"
    TITLE = "Volume spike"
    SHORT_TITLE = "Vol spike"
    DESCRIPTION = (
        "A closed bar's volume is above a multiple of the mean volume of "
        "the bars before it."
    )
    CATEGORY = "Volume"
    INPUTS = {
        "length": joseph_script.Input(
            20, title="Average length", minimum=1, maximum=1000, step=1
        ),
        "multiplier": joseph_script.Input(
            2.0, title="Multiple of average", minimum=1, maximum=1000
        ),
    }

    def __init__(self) -> None:
        """Starts without volume history."""
        self.volumes: collections.deque[float] = collections.deque()

    def on_bar(self, ctx: worker.Context) -> None:
        """Compares this bar's volume with the preceding average.

        Args:
            ctx: Alert context.
        """
        length = int(ctx.input("length"))
        multiplier = float(ctx.input("multiplier"))
        volume = float(ctx.bar["volume"])
        if len(self.volumes) == length:
            average = sum(self.volumes) / length
            ctx.alertcondition(
                average > 0 and volume > multiplier * average,
                "volume-spike",
                f"{ctx.instrument_id} volume {show(volume)} is "
                f"{volume / average if average else 0:.1f}x the "
                f"{length}-bar average",
            )
        self.volumes.append(volume)
        while len(self.volumes) > length:
            self.volumes.popleft()
