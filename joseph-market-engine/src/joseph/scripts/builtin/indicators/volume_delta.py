# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Volume delta: buy minus sell volume of each bar, as a percentage.

* True delta (``use_buy_volume`` and the bar carries a buy volume): ``buy``
  is the venue's taker buy volume (or trades classified against the quote
  where the market publishes no aggressor side), ``sell = volume - buy``
  and the coverage is 100 %.
* Estimate otherwise: each lower-timeframe bar is up or down by
  ``sign(close - open)``; a flat one takes ``sign(close - previous close)``
  and, when that is flat too, keeps the previous direction. The previous
  close and direction carry across chart bars. The estimate is published
  only with at least ``min_intrabars`` intrabars covering at least
  ``min_coverage`` of a positive bar volume; otherwise the delta is empty
  while the coverage and intrabar count say why.

The host fetches intrabars only for the newest chart bars without a buy
volume (from the displayed window when the chart names it), so a venue
that publishes taker volume costs no extra request. ``Delta %`` is
``(buy - sell) / covered * 100``; estimated bars are drawn translucent and
the last bar's method is shown in a table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

TAKER_VOLUME = "taker_volume"
"""Method of true delta from the venue's aggressor flag."""

TRADE_CLASSIFICATION = "trade_classification"
"""Method of buy volume classified from trades against the quote."""

CANDLE_DIRECTION = "candle_direction"
"""Method of the intrabar candle-direction estimate."""

METHOD_LABELS = {
    TAKER_VOLUME: "Taker buy volume",
    TRADE_CLASSIFICATION: "Classified trades",
    CANDLE_DIRECTION: "Intrabar estimate",
}
"""Table label of each method."""


def sign(value: float) -> int:
    """Returns the sign of a number.

    Args:
        value: Number.

    Returns:
        1, -1 or 0.
    """
    return (value > 0) - (value < 0)


class VolumeDelta(joseph_script.Indicator):
    """Plots each bar's volume delta as a histogram in its own pane.

    Attributes:
        last_close: Close of the last intrabar seen.
        last_direction: Direction of the last intrabar: 1 up, -1 down, 0
            unknown.
    """

    ID = "volume_delta"
    TITLE = "Volume Delta"
    SHORT_TITLE = "Delta %"
    DESCRIPTION = (
        "Buy minus sell volume of each bar as a percentage of its volume: "
        "venue taker volume or classified trades when available, otherwise "
        "an estimate from intrabar candle direction."
    )
    CATEGORY = "Volume"
    OVERLAY = False
    INPUTS = {
        "use_buy_volume": joseph_script.Input(
            True,
            title="Use venue buy volume",
            tooltip=(
                "True delta from the bar's taker buy volume when the source "
                "publishes it; off always estimates from intrabars."
            ),
        ),
        "intrabar_tf": joseph_script.Input(
            "auto",
            title="Intrabar timeframe",
            options=("auto", "1s", "5s", "15s", "1m", "3m", "5m", "15m"),
        ),
        "min_intrabars": joseph_script.Input(
            5, title="Minimum intrabars", minimum=1, maximum=100, step=1
        ),
        "min_coverage": joseph_script.Input(
            0.8,
            title="Minimum coverage",
            minimum=0.5,
            maximum=1,
            step=0.05,
            tooltip="Share of the bar volume the intrabars must cover.",
        ),
        "up_color": joseph_script.Input("#26a69a", title="Up", group="Style"),
        "down_color": joseph_script.Input(
            "#ef5350", title="Down", group="Style"
        ),
    }
    REQUESTS = {
        "intrabars": joseph_script.Request(
            "auto",
            intrabars=True,
            tf_input="intrabar_tf",
            unless="taker_buy_volume",
            unless_input="use_buy_volume",
            limit=3000,
        )
    }

    def __init__(self) -> None:
        """Starts before the first intrabar."""
        self.last_close: float | None = None
        self.last_direction = 0

    def split(self, intrabars: Sequence[dict[str, Any]]) -> list[float]:
        """Splits intrabar volume into buys and sells by candle direction.

        Args:
            intrabars: Intrabars of the chart bar, oldest first.

        Returns:
            Buy, sell and covered volume.
        """
        buy = sell = covered = 0.0
        for intrabar in intrabars:
            close = float(intrabar["close"])
            volume = float(intrabar["volume"])
            current = sign(close - float(intrabar["open"]))
            if current == 0 and self.last_close is not None:
                current = sign(close - self.last_close)
            if current != 0:
                self.last_direction = current
            if self.last_direction > 0:
                buy += volume
            elif self.last_direction < 0:
                sell += volume
            covered += volume
            self.last_close = close
        return [buy, sell, covered]

    def on_bar(self, ctx: worker.Context) -> None:
        """Computes and publishes the bar's delta.

        Args:
            ctx: Script context.
        """
        intrabars = ctx.intrabars("intrabars")
        buy, sell, covered = self.split(intrabars)
        volume = float(ctx.bar["volume"])
        raw = ctx.bar.get("taker_buy_volume")
        taker = None if raw is None else float(raw)
        estimated = not (ctx.input("use_buy_volume") and taker is not None)
        count = 0 if not estimated else len(intrabars)
        if taker is not None and not estimated:
            buy, sell, covered = taker, volume - taker, volume
            coverage: float | None = 100.0
            method = (
                TRADE_CLASSIFICATION
                if ctx.bar.get("taker_buy_method") == "classified"
                else TAKER_VOLUME
            )
            sufficient = True
        else:
            coverage = covered / volume * 100 if volume > 0 else None
            method = CANDLE_DIRECTION
            sufficient = (
                coverage is not None
                and count >= int(ctx.input("min_intrabars"))
                and coverage >= float(ctx.input("min_coverage")) * 100
            )
        delta_pct = None
        if sufficient and covered > 0:
            delta_pct = (buy - sell) / covered * 100
        color = str(
            ctx.input("up_color" if (delta_pct or 0) >= 0 else "down_color")
        )
        ctx.plot(
            "Delta %",
            delta_pct,
            color=color + "99" if estimated and len(color) == 7 else color,
            style="histogram",
            width=1,
        )
        shown = sufficient and covered > 0
        ctx.plot("Delta", buy - sell if shown else None, display="data_window")
        ctx.plot("Buy volume", buy if shown else None, display="data_window")
        ctx.plot("Sell volume", sell if shown else None, display="data_window")
        ctx.plot("Coverage %", coverage, display="data_window")
        ctx.plot("Intrabars", count, display="data_window")
        if ctx.is_first:
            ctx.hline("Zero", 0.0, style="solid")
        if ctx.is_last:
            detail = METHOD_LABELS[method]
            if estimated and not sufficient:
                detail = "Insufficient intrabar coverage"
            ctx.table(
                "Volume delta",
                [
                    ["Method", detail],
                    [
                        "Coverage",
                        "n/a" if coverage is None else f"{coverage:.0f}%",
                    ],
                ],
                position="top_left",
            )
