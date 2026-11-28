# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Linear regression channel (ordinary least squares).

Over a window of ``n`` bars, with ``x`` the bar's position (0 for the
oldest) and ``y`` its source price, the study fits ``y = a + b * x``:

* ``b = Sxy / Sxx`` through ``(mean_x, mean_y)``, from centred two-pass
  sums with prices offset by the window's first price;
* the channel is the line ``+/- deviations * stdev``, ``stdev`` being the
  sample standard deviation of the residuals,
  ``sqrt(max(0, Syy - b * Sxy) / (n - 1))``;
* ``Pearson's R = Sxy / sqrt(Sxx * Syy)``, 0 for constant prices;
* ``Max up = max(high - line)`` and ``Max down = max(line - low)``.

The window is the last ``length`` bars (rolling) or, with ``anchor_ms``,
every bar from the chart bar containing it; it ends at the bar containing
``end_ms`` (the regression-channel drawing tool's second anchor) or at the
last bar. Only that final window is fitted and drawn; its statistics are
listed in the data window on the end bar. An anchor before the loaded bars
draws nothing and says so.
"""

from __future__ import annotations

import collections
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

SOURCES = ("close", "hl2", "hlc3", "ohlc4")
"""Selectable price sources."""


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


def fit(rows: list[tuple[int, float, float, float]]) -> dict[str, float]:
    """Fits the least-squares line of a window.

    Args:
        rows: ``(start_ms, price, high, low)`` per bar, oldest first; at
            least two.

    Returns:
        Line start/end, slope, residual stdev, Pearson's R and the largest
        excursions above and below the line.
    """
    count = len(rows)
    origin = rows[0][1]
    ys = [row[1] - origin for row in rows]
    mean_y = math.fsum(ys) / count
    centred = [y - mean_y for y in ys]
    mean_x = (count - 1) / 2
    sxx = count * (count * count - 1) / 12
    sxy = math.fsum(x * c for x, c in enumerate(centred)) - mean_x * math.fsum(
        centred
    )
    syy = math.fsum(c * c for c in centred)
    slope = sxy / sxx
    centre = origin + mean_y
    intercept = centre - slope * mean_x
    pearson = 0.0
    if syy > 0:
        pearson = min(1.0, max(-1.0, sxy / math.sqrt(sxx * syy)))
    return {
        "start": centre - slope * mean_x,
        "end": centre + slope * mean_x,
        "slope": slope,
        "stdev": math.sqrt(max(0.0, syy - slope * sxy) / (count - 1)),
        "pearson_r": pearson,
        "max_up": max(row[2] - slope * x for x, row in enumerate(rows))
        - intercept,
        "max_down": intercept
        - min(row[3] - slope * x for x, row in enumerate(rows)),
    }


class RegressionChannel(joseph_script.Indicator):
    """Draws a least-squares trend channel over a rolling or anchored window.

    Attributes:
        rows: Window bars as ``(start_ms, price, high, low)``.
        started: Whether an anchored window has started.
        outside: Whether the anchor precedes the loaded bars.
        done: Whether the end bar has been drawn.
    """

    ID = "regression_channel"
    TITLE = "Linear Regression Channel"
    SHORT_TITLE = "LinReg"
    DESCRIPTION = (
        "Least-squares trend line with standard-deviation bands and "
        "Pearson's R, over the last bars or from an anchor bar."
    )
    CATEGORY = "Trend"
    OVERLAY = True
    INPUTS = {
        "length": joseph_script.Input(
            100, title="Length", minimum=2, maximum=5000, step=1
        ),
        "anchor_ms": joseph_script.Input(
            0,
            title="Anchor time",
            minimum=0,
            maximum=4_102_444_800_000,
            tooltip="UTC milliseconds of the first bar; 0 uses length.",
        ),
        "end_ms": joseph_script.Input(
            0,
            title="End time",
            minimum=0,
            maximum=4_102_444_800_000,
            tooltip="UTC milliseconds of the last bar; 0 is the latest bar.",
        ),
        "source": joseph_script.Input("close", title="Source", options=SOURCES),
        "deviations": joseph_script.Input(
            2.0, title="Deviations", minimum=0, maximum=10, step=0.5
        ),
        "show_pearson": joseph_script.Input(True, title="Show Pearson's R"),
        "color": joseph_script.Input("#2962ff", title="Line", group="Style"),
        "band_color": joseph_script.Input(
            "#ef5350", title="Bands", group="Style"
        ),
    }

    def __init__(self) -> None:
        """Starts with an empty window."""
        self.rows: collections.deque[tuple[int, float, float, float]] = (
            collections.deque()
        )
        self.started = False
        self.outside = False
        self.done = False

    def on_bar(self, ctx: worker.Context) -> None:
        """Adds the bar to the window and draws the channel at its end.

        Args:
            ctx: Script context.
        """
        if self.done:
            return
        anchor_ms = int(ctx.input("anchor_ms"))
        end_ms = int(ctx.input("end_ms"))
        bar = ctx.bar
        if anchor_ms > 0:
            if ctx.is_first and int(bar["time_ms"]) > anchor_ms:
                self.outside = True
            if not self.outside and int(bar["end_ms"]) > anchor_ms:
                self.started = True
        else:
            self.started = True
        if self.started:
            self.rows.append(
                (
                    int(bar["time_ms"]),
                    source_value(bar, str(ctx.input("source"))),
                    float(bar["high"]),
                    float(bar["low"]),
                )
            )
            if anchor_ms <= 0:
                while len(self.rows) > int(ctx.input("length")):
                    self.rows.popleft()
        at_end = ctx.is_last or (end_ms > 0 and int(bar["end_ms"]) > end_ms)
        if not at_end:
            return
        self.done = True
        if self.outside:
            ctx.table(
                "Regression",
                [["Anchor is outside the loaded bars"]],
                position="top_left",
            )
            return
        if len(self.rows) < 2 or (
            anchor_ms <= 0 and len(self.rows) < int(ctx.input("length"))
        ):
            return
        self.draw(ctx, list(self.rows))

    def draw(
        self, ctx: worker.Context, rows: list[tuple[int, float, float, float]]
    ) -> None:
        """Draws the fitted channel and lists its statistics.

        Args:
            ctx: Script context.
            rows: Window bars, oldest first.
        """
        values = fit(rows)
        width = float(ctx.input("deviations")) * values["stdev"]
        first, last = rows[0][0], rows[-1][0]
        color, band = str(ctx.input("color")), str(ctx.input("band_color"))
        for name, shift, stroke in (
            ("centre", 0.0, color),
            ("upper", width, band),
            ("lower", -width, band),
        ):
            ctx.line(
                first,
                values["start"] + shift,
                last,
                values["end"] + shift,
                id=name,
                color=stroke,
                width=2 if name == "centre" else 1,
            )
        ctx.linefill("upper", "lower", color=band, opacity=0.06)
        if ctx.input("show_pearson"):
            ctx.label(
                f"{values['pearson_r']:.4f}",
                time_ms=first,
                price=values["start"] - width,
                id="pearson",
                color=band,
                position="below",
            )
        for name, key in (
            ("Pearson's R", "pearson_r"),
            ("Slope", "slope"),
            ("Stdev", "stdev"),
            ("Max up", "max_up"),
            ("Max down", "max_down"),
        ):
            ctx.plot(name, values[key], display="data_window")
        ctx.plot("Bars", len(rows), display="data_window")
