# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""New high/low filter: the latest closed bar sets an N-bar extreme.

A new high is a latest high at or above every high of the last ``length``
bars (itself included); a new low mirrors it with lows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker


def closed_bars(ctx: worker.Context, needed: int) -> list[dict[str, Any]]:
    """Reads the closed history, requiring enough bars.

    Args:
        ctx: Filter context.
        needed: Minimum number of closed bars.

    Returns:
        Closed bars, oldest first.

    Raises:
        joseph_script.Unavailable: With fewer than ``needed`` bars.
    """
    bars = [bar for bar in ctx.history if bar.get("is_final", True)]
    if len(bars) < needed:
        raise joseph_script.Unavailable(
            f"needs {needed} closed bars, got {len(bars)}; scan with "
            f"history_bars of at least {needed}"
        )
    return bars


class NewHighLow(joseph_script.Filter):
    """Matches instruments whose latest bar sets a ``length``-bar extreme."""

    ID = "new-high-low"
    TITLE = "New high or low"
    SHORT_TITLE = "New H/L"
    DESCRIPTION = (
        "The latest closed bar makes the highest high or lowest low of the "
        "last length bars, such as a 52-week high on weekly bars."
    )
    CATEGORY = "Price"
    INPUTS = {
        "length": joseph_script.Input(
            52, title="Bars", minimum=2, maximum=500, step=1
        ),
        "extreme": joseph_script.Input(
            "high", title="Extreme", options=("high", "low")
        ),
    }

    def matches(self, ctx: worker.Context) -> bool:
        """Tests the latest bar against the window.

        Args:
            ctx: Filter context with closed bars.

        Returns:
            Whether the latest bar sets the selected extreme.
        """
        length = int(ctx.input("length"))
        window = closed_bars(ctx, length)[-length:]
        if ctx.input("extreme") == "low":
            return float(window[-1]["low"]) <= min(
                float(bar["low"]) for bar in window
            )
        return float(window[-1]["high"]) >= max(
            float(bar["high"]) for bar in window
        )
