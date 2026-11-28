# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Anchored VWAP with standard-deviation bands (TradingView VWAP).

Over the bars of the current anchor period the study accumulates
``sum(src * v)``, ``sum(v)`` and ``sum(src**2 * v)``; then
``vwap = sum(src * v) / sum(v)``, ``stdev = sqrt(max(0, sum(src**2 * v) /
sum(v) - vwap**2))`` and band ``k`` is ``vwap +/- m_k * stdev``. A bar
without positive volume adds nothing (it still counts in ``Bars``); while
``sum(v)`` is 0 every value is empty, and a band whose multiplier is 0 is
not drawn.

Anchors:

* ``session`` / ``week`` / ``month`` restart the sums on the first bar of
  each calendar period: the UTC day, ISO week and month on 24x7 markets,
  the exchange session, its week and month elsewhere.
* ``time`` starts once, at the chart bar containing ``anchor_ms`` (or the
  first bar after it when it falls between sessions); the anchored-VWAP
  drawing tool uses it. When the first loaded bar already starts after
  the anchor, the anchor is outside the loaded bars and nothing is drawn.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

SOURCES = ("hlc3", "close", "hl2", "ohlc4")
"""Selectable price sources."""

PERIODS = {"session": "1d", "week": "1w", "month": "1M"}
"""Calendar period of each periodic anchor."""


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


class AnchoredVwap(joseph_script.Indicator):
    """Plots the VWAP since the anchor and up to three bands.

    Attributes:
        period_start: Start of the current anchor period.
        started: Whether the sums have started.
        outside: Whether a time anchor precedes the loaded bars.
        bars: Bars since the anchor bar, counting the current one.
        sums: ``sum(src * v)``, ``sum(v)`` and ``sum(src**2 * v)``.
    """

    ID = "anchored_vwap"
    TITLE = "Volume Weighted Average Price"
    SHORT_TITLE = "VWAP"
    DESCRIPTION = (
        "Volume-weighted average price from a session, week, month or a "
        "chosen bar, with up to three standard-deviation bands."
    )
    CATEGORY = "Volume"
    OVERLAY = True
    PERIODS = ("1d", "1w", "1M")
    INPUTS = {
        "anchor": joseph_script.Input(
            "session",
            title="Anchor period",
            options=("session", "week", "month", "time"),
        ),
        "anchor_ms": joseph_script.Input(
            0,
            title="Anchor time",
            minimum=0,
            maximum=4_102_444_800_000,
            tooltip="UTC milliseconds of the anchor bar when anchor is time.",
        ),
        "source": joseph_script.Input("hlc3", title="Source", options=SOURCES),
        "band_1": joseph_script.Input(
            1.0, title="Band 1 multiplier", minimum=0, maximum=10, step=0.5
        ),
        "band_2": joseph_script.Input(
            0.0, title="Band 2 multiplier", minimum=0, maximum=10, step=0.5
        ),
        "band_3": joseph_script.Input(
            0.0, title="Band 3 multiplier", minimum=0, maximum=10, step=0.5
        ),
        "color": joseph_script.Input("#2962ff", title="VWAP", group="Style"),
        "band_color": joseph_script.Input(
            "#4caf50", title="Bands", group="Style"
        ),
    }

    def __init__(self) -> None:
        """Starts before the anchor."""
        self.period_start: int | None = None
        self.started = False
        self.outside = False
        self.bars = 0
        self.sums = [0.0, 0.0, 0.0]

    def reset(self) -> None:
        """Restarts the sums at the current bar."""
        self.started = True
        self.bars = 0
        self.sums = [0.0, 0.0, 0.0]

    def follow_anchor(self, ctx: worker.Context) -> None:
        """Restarts the sums when the current bar starts an anchor period.

        Args:
            ctx: Script context.

        Raises:
            ValueError: If the time anchor has no anchor time.
        """
        anchor = str(ctx.input("anchor"))
        if anchor != "time":
            start, _ = ctx.period(PERIODS[anchor])
            if start != self.period_start:
                self.period_start = start
                self.reset()
            return
        anchor_ms = int(ctx.input("anchor_ms"))
        if anchor_ms <= 0:
            raise ValueError("anchor_ms is required for the time anchor")
        if ctx.is_first and int(ctx.bar["time_ms"]) > anchor_ms:
            self.outside = True
        if (
            not self.started
            and not self.outside
            and int(ctx.bar["end_ms"]) > anchor_ms
        ):
            self.reset()

    def on_bar(self, ctx: worker.Context) -> None:
        """Adds the bar to the sums and publishes the VWAP and bands.

        Args:
            ctx: Script context.
        """
        self.follow_anchor(ctx)
        values: dict[str, float | None] = {"VWAP": None, "Stdev": None}
        multipliers = [float(ctx.input(f"band_{k}")) for k in (1, 2, 3)]
        if self.started:
            self.bars += 1
            volume = float(ctx.bar["volume"])
            if volume > 0:
                price = source_value(ctx.bar, str(ctx.input("source")))
                self.sums[0] += price * volume
                self.sums[1] += volume
                self.sums[2] += price * price * volume
            if self.sums[1] > 0:
                vwap = self.sums[0] / self.sums[1]
                variance = self.sums[2] / self.sums[1] - vwap * vwap
                values["VWAP"] = vwap
                values["Stdev"] = math.sqrt(max(0.0, variance))
        ctx.plot("VWAP", values["VWAP"], color=str(ctx.input("color")))
        band_color = str(ctx.input("band_color"))
        for index, multiplier in enumerate(multipliers, start=1):
            if multiplier <= 0:
                continue
            for name, sign in (("Upper", 1), ("Lower", -1)):
                centre, spread = values["VWAP"], values["Stdev"]
                ctx.plot(
                    f"{name} {index}",
                    None
                    if centre is None or spread is None
                    else centre + sign * multiplier * spread,
                    color=band_color,
                    width=1,
                )
        ctx.plot("Stdev", values["Stdev"], display="data_window")
        ctx.plot(
            "Bars", self.bars if self.started else None, display="data_window"
        )
        if ctx.is_first and multipliers[0] > 0:
            ctx.fill("Upper 1", "Lower 1", color=band_color, opacity=0.08)
        if ctx.is_last and self.outside:
            ctx.table(
                "VWAP",
                [["Anchor is outside the loaded bars"]],
                position="top_left",
            )
