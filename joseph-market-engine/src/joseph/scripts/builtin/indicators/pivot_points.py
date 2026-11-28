# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Classic floor pivot points from the previous period's range.

From the high ``H``, low ``L`` and close ``C`` of the last completed period
bar (``1d``, ``1w`` or ``1M``; on exchanges the calendar's session, week and
month bars)::

    P = (H + L + C) / 3
    R1 = 2P - L          S1 = 2P - H
    R2 = P + (H - L)     S2 = P - (H - L)
    R3 = H + 2(P - L)    S3 = L - 2(H - P)

The levels apply to the period after the completed bar, from its start
(``applies``). They are published on the first chart bar that sees the
completed bar and closes at or after ``applies``: on a 24x7 market the bar
closing with the period, on an exchange the first bar of the next session.
Each period's levels are drawn as labelled lines from ``applies`` to the
next period; the first confirmed bar inside the period whose range
includes a level raises a touch alert for it. The ``auto`` period is the
day on intraday charts, the week on daily and the month on weekly charts.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

LEVELS = ("p", "r1", "r2", "r3", "s1", "s2", "s3")
"""Level keys in publication order."""

UNIT_MS = {
    "s": 1000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
    "M": 2_592_000_000,
}
"""Approximate length of one timeframe unit, for ordering resolutions."""


def duration_ms(timeframe: str) -> int:
    """Approximates the length of a resolution such as "5m" or "1M".

    Args:
        timeframe: Count followed by a unit letter.

    Returns:
        Milliseconds (a month counts thirty days).
    """
    return int(timeframe[:-1] or 1) * UNIT_MS[timeframe[-1]]


def auto_period(timeframe: str) -> str | None:
    """Picks the period of the ``auto`` setting for a chart resolution.

    Args:
        timeframe: Chart resolution.

    Returns:
        "1d" below daily charts, "1w" below weekly, "1M" below monthly,
        otherwise None.
    """
    for period in ("1d", "1w", "1M"):
        if duration_ms(timeframe) < duration_ms(period):
            return period
    return None


def floor_levels(high: float, low: float, close: float) -> dict[str, float]:
    """Computes the classic floor pivot levels.

    Args:
        high: High of the completed period.
        low: Its low.
        close: Its close.

    Returns:
        Level by key (``p``, ``r1`` ... ``s3``).
    """
    pivot = (high + low + close) / 3
    return {
        "p": pivot,
        "r1": 2 * pivot - low,
        "r2": pivot + (high - low),
        "r3": high + 2 * (pivot - low),
        "s1": 2 * pivot - high,
        "s2": pivot - (high - low),
        "s3": low - 2 * (high - pivot),
    }


class PivotPoints(joseph_script.Indicator):
    """Draws the previous period's floor pivot levels.

    Attributes:
        source_start: Start of the completed period behind the current
            levels.
        applies: Start of the period the current levels apply to.
        levels: Current level prices by key.
        tested: Keys of current levels already touched.
        shown: Applies times of drawn periods, oldest first.
    """

    ID = "pivot_points"
    TITLE = "Pivot Points Standard"
    SHORT_TITLE = "Pivots"
    DESCRIPTION = (
        "Classic floor pivot levels P, R1 to R3 and S1 to S3 from the "
        "previous period's high, low and close."
    )
    CATEGORY = "Support and resistance"
    OVERLAY = True
    PERIODS = ("1d", "1w", "1M")
    INPUTS = {
        "period": joseph_script.Input(
            "auto",
            title="Pivots timeframe",
            options=("auto", "1d", "1w", "1M"),
        ),
        "pivots_back": joseph_script.Input(
            15, title="Number of pivots back", minimum=1, maximum=60, step=1
        ),
        "show_labels": joseph_script.Input(True, title="Show labels"),
        "pivot_color": joseph_script.Input("#f7a600", title="P", group="Style"),
        "resistance_color": joseph_script.Input(
            "#ef5350", title="R levels", group="Style"
        ),
        "support_color": joseph_script.Input(
            "#26a69a", title="S levels", group="Style"
        ),
    }
    REQUESTS = {
        "period": joseph_script.Request("1d", tf_input="period", limit=100)
    }

    def __init__(self) -> None:
        """Starts before the first publication."""
        self.source_start: int | None = None
        self.applies: int | None = None
        self.levels: dict[str, float] = {}
        self.tested: set[str] = set()
        self.shown: collections.deque[int] = collections.deque()

    def period(self, ctx: worker.Context) -> str | None:
        """Resolves the period setting for the chart.

        Args:
            ctx: Script context.

        Returns:
            The period, or None when ``auto`` has none for this chart.

        Raises:
            ValueError: If an explicit period is not longer than the chart.
        """
        chosen = str(ctx.input("period"))
        if chosen == "auto":
            return auto_period(ctx.timeframe)
        if duration_ms(ctx.timeframe) >= duration_ms(chosen):
            raise ValueError(
                f"the {ctx.timeframe} chart must be shorter than the "
                f"{chosen} period"
            )
        return chosen

    def color(self, ctx: worker.Context, key: str) -> str:
        """Returns the style color of a level.

        Args:
            ctx: Script context.
            key: Level key.

        Returns:
            Hex color.
        """
        group = {"p": "pivot", "r": "resistance", "s": "support"}[key[0]]
        return str(ctx.input(f"{group}_color"))

    def draw(self, ctx: worker.Context, until: int) -> None:
        """Draws or extends the current period's labelled lines.

        Args:
            ctx: Script context.
            until: Right end of the lines in UTC milliseconds.
        """
        if self.applies is None:
            return
        for key, price in self.levels.items():
            name = f"pivot:{self.applies}:{key}"
            color = self.color(ctx, key)
            ctx.line(
                self.applies,
                price,
                until,
                price,
                id=name,
                color=color,
                style="dashed",
            )
            if ctx.input("show_labels"):
                ctx.label(
                    f"{key.upper()} ({price:.6g})",
                    time_ms=self.applies,
                    price=price,
                    id=f"{name}:label",
                    color=color,
                    position="above",
                )

    def on_bar(self, ctx: worker.Context) -> None:
        """Publishes new levels when due and raises touch alerts.

        Args:
            ctx: Script context.
        """
        period = self.period(ctx)
        if period is None:
            if ctx.is_last:
                ctx.table("Pivots", [["Pivots need a chart below 1M"]])
            return
        start, following = ctx.period(period)
        completed = ctx.closed_bar("period")
        if completed is not None and (
            self.source_start is None
            or int(completed["time_ms"]) > self.source_start
        ):
            source = int(completed["time_ms"])
            applies = following if start <= source else start
            if int(ctx.bar["end_ms"]) >= applies:
                self.draw(ctx, applies)
                self.source_start = source
                self.applies = applies
                self.levels = floor_levels(
                    float(completed["high"]),
                    float(completed["low"]),
                    float(completed["close"]),
                )
                self.tested = set()
                self.shown.append(applies)
                while len(self.shown) > int(ctx.input("pivots_back")):
                    old = self.shown.popleft()
                    for key in LEVELS:
                        ctx.delete(f"pivot:{old}:{key}")
                        ctx.delete(f"pivot:{old}:{key}:label")
        if self.applies is not None and ctx.is_confirmed:
            low, high = float(ctx.bar["low"]), float(ctx.bar["high"])
            for key, price in self.levels.items():
                if (
                    key not in self.tested
                    and int(ctx.bar["time_ms"]) >= self.applies
                    and low <= price <= high
                ):
                    self.tested.add(key)
                    ctx.alert(
                        f"Price touched pivot {key.upper()} at {price:.6g}",
                        name=f"pivot_{key}",
                    )
        if ctx.is_last and self.applies is not None:
            until = following if start >= self.applies else 0
            if not until:
                until = self.applies + (self.applies - start)
            self.draw(ctx, until)
