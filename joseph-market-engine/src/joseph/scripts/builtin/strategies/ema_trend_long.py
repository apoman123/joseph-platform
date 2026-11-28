# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""EMA trend long: a close crossing above its EMA, confirmed by momentum.

Entry on a closed bar, while flat and outside the cooldown, when all hold:

* the close crosses above the EMA (previous close at or below the previous
  EMA, current close above the current EMA);
* the RSI of closed one-hour bars is above the threshold;
* the bar's volume delta, ``(2 * taker buy volume - volume) / volume``, is
  above the minimum when the venue publishes taker buy volume (bars without
  it never qualify while the flow filter is on);
* the net reward/risk ``(reward - cost) / (risk + cost)``, with
  ``cost = close * fee_bps * 2 / 10000``, reaches the minimum.

The stop sits ``stop_atr`` ATRs below the close and the target ``target_r``
times the risk above it. The simulated position closes at the next open
after a bar trades through either level; live entries carry both levels.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker


class WilderAverage:
    """Wilder's running average, seeded with the mean of the first values.

    Attributes:
        length: Smoothing length.
        value: Current average; None until ``length`` values were added.
        seed: Values collected before the first average.
    """

    def __init__(self, length: int) -> None:
        """Starts without values.

        Args:
            length: Smoothing length, at least one.
        """
        self.length = length
        self.value: float | None = None
        self.seed: list[float] = []

    def add(self, value: float) -> float | None:
        """Adds one value.

        Args:
            value: Next value.

        Returns:
            The updated average, or None while seeding.
        """
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.length:
                self.value = math.fsum(self.seed) / self.length
                self.seed = []
            return self.value
        self.value += (value - self.value) / self.length
        return self.value


class RelativeStrength:
    """Wilder's relative strength index of a price series.

    Attributes:
        gains: Average gain.
        losses: Average loss.
        previous: Previous price, None before the first.
        value: Current RSI, None until ``length + 1`` prices were added.
    """

    def __init__(self, length: int) -> None:
        """Starts without prices.

        Args:
            length: RSI length.
        """
        self.gains = WilderAverage(length)
        self.losses = WilderAverage(length)
        self.previous: float | None = None
        self.value: float | None = None

    def add(self, price: float) -> float | None:
        """Adds one price.

        Args:
            price: Next closing price.

        Returns:
            The updated RSI, or None while seeding.
        """
        if self.previous is not None:
            change = price - self.previous
            up = self.gains.add(max(change, 0.0))
            down = self.losses.add(max(-change, 0.0))
            if up is not None and down is not None:
                self.value = 100.0 if down == 0 else 100 - 100 / (1 + up / down)
        self.previous = price
        return self.value


class AverageTrueRange:
    """Wilder's average true range.

    Attributes:
        average: Average of the true ranges.
        previous_close: Previous close, None before the first bar.
    """

    def __init__(self, length: int) -> None:
        """Starts without bars.

        Args:
            length: ATR length.
        """
        self.average = WilderAverage(length)
        self.previous_close: float | None = None

    def add(self, high: float, low: float, close: float) -> float | None:
        """Adds one bar.

        Args:
            high: Bar high.
            low: Bar low.
            close: Bar close.

        Returns:
            The updated ATR, or None while seeding.
        """
        true_range = high - low
        if self.previous_close is not None:
            true_range = max(
                true_range,
                abs(high - self.previous_close),
                abs(low - self.previous_close),
            )
        self.previous_close = close
        return self.average.add(true_range)


class EmaTrendLong(joseph_script.Strategy):
    """Buys a close crossing above the EMA in a higher-timeframe uptrend.

    Attributes:
        ema: Current EMA, None before the first bar.
        previous_close: Previous bar close.
        atr: Average true range of the chart bars.
        rsi: RSI of closed one-hour bars.
        cursor: Number of one-hour bars already added to the RSI.
        bars_since_entry: Bars since the last entry order, None before one.
    """

    ID = "ema-trend-long"
    TITLE = "EMA Trend Long"
    SHORT_TITLE = "EMA Trend"
    DESCRIPTION = (
        "Long when the close crosses above the EMA while the one-hour RSI is "
        "above 50 and volume delta is positive; ATR stop, fixed 3R target."
    )
    CATEGORY = "Trend"
    OVERLAY = True
    INPUTS = {
        "ema_length": joseph_script.Input(
            20, title="EMA length", minimum=1, maximum=500, step=1
        ),
        "rsi_length": joseph_script.Input(
            14, title="1h RSI length", minimum=2, maximum=100, step=1
        ),
        "rsi_threshold": joseph_script.Input(
            50, title="1h RSI above", minimum=0, maximum=100
        ),
        "require_flow": joseph_script.Input(
            True,
            title="Require positive volume delta",
            tooltip="Bars without venue taker buy volume never qualify.",
        ),
        "min_delta_pct": joseph_script.Input(
            0, title="Volume delta above (%)", minimum=-100, maximum=100
        ),
        "atr_length": joseph_script.Input(
            14, title="ATR length", minimum=1, maximum=100, step=1, group="Risk"
        ),
        "stop_atr": joseph_script.Input(
            1.5,
            title="Stop distance (ATR)",
            minimum=0.1,
            maximum=20,
            step=0.1,
            group="Risk",
        ),
        "target_r": joseph_script.Input(
            3.0,
            title="Target (R multiple)",
            minimum=0.5,
            maximum=20,
            step=0.5,
            group="Risk",
        ),
        "min_rr": joseph_script.Input(
            2.0,
            title="Minimum net reward/risk",
            minimum=0,
            maximum=20,
            step=0.1,
            group="Risk",
        ),
        "fee_bps": joseph_script.Input(
            5, title="Fee estimate (bps)", minimum=0, maximum=100, group="Risk"
        ),
        "cooldown_bars": joseph_script.Input(
            12, title="Cooldown (bars)", minimum=0, maximum=500, step=1
        ),
        "position_pct": joseph_script.Input(
            90,
            title="Simulated position (% of equity)",
            minimum=1,
            maximum=100,
            group="Simulation",
        ),
        "color": joseph_script.Input(
            "#2962ff", title="EMA color", group="Style"
        ),
    }
    REQUESTS = {"htf": joseph_script.Request("1h", limit=500)}

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.ema: float | None = None
        self.previous_close: float | None = None
        self.atr: AverageTrueRange | None = None
        self.rsi: RelativeStrength | None = None
        self.cursor = 0
        self.bars_since_entry: int | None = None

    def higher_rsi(self, ctx: worker.Context) -> float | None:
        """Adds the one-hour bars closed by the current bar's close.

        Args:
            ctx: Script context.

        Returns:
            RSI of the closed one-hour bars, None while seeding.
        """
        if self.rsi is None:
            self.rsi = RelativeStrength(int(ctx.input("rsi_length")))
        series = ctx.extra_series.get("htf", [])
        end = ctx.bar.get("end_ms", ctx.bar["time_ms"])
        while (
            self.cursor < len(series) and series[self.cursor]["end_ms"] <= end
        ):
            self.rsi.add(float(series[self.cursor]["close"]))
            self.cursor += 1
        return self.rsi.value

    def flow_ok(self, ctx: worker.Context) -> bool:
        """Checks the volume delta of the current bar.

        Args:
            ctx: Script context.

        Returns:
            True when the filter is off or the published delta is above the
            minimum.
        """
        if not ctx.input("require_flow"):
            return True
        taker = ctx.bar.get("taker_buy_volume")
        volume = float(ctx.bar["volume"])
        if taker is None or volume <= 0:
            return False
        delta_pct = (2 * float(taker) - volume) / volume * 100
        return delta_pct > float(ctx.input("min_delta_pct"))

    def on_bar(self, ctx: worker.Context) -> None:
        """Manages the open position and looks for a new entry.

        Args:
            ctx: Script context.
        """
        if self.atr is None:
            self.atr = AverageTrueRange(int(ctx.input("atr_length")))
        high = float(ctx.bar["high"])
        low = float(ctx.bar["low"])
        close = float(ctx.bar["close"])
        previous_close, previous_ema = self.previous_close, self.ema
        alpha = 2 / (int(ctx.input("ema_length")) + 1)
        ema = (
            close
            if self.ema is None
            else alpha * close + (1 - alpha) * self.ema
        )
        self.ema = ema
        self.previous_close = close
        atr = self.atr.add(high, low, close)
        rsi = self.higher_rsi(ctx)
        if self.bars_since_entry is not None:
            self.bars_since_entry += 1
        ctx.plot("EMA", ema, color=str(ctx.input("color")))
        ctx.plot("Stop", ctx.position_stop, color="#ef5350")
        ctx.plot("Target", ctx.position_target, color="#26a69a")
        if ctx.position_size > 0:
            stop, target = ctx.position_stop, ctx.position_target
            if stop is not None and low <= stop:
                ctx.close("stop reached")
            elif target is not None and high >= target:
                ctx.close("target reached")
            return
        crossed = (
            previous_close is not None
            and previous_ema is not None
            and previous_close <= previous_ema
            and close > ema
        )
        cooled = self.bars_since_entry is None or self.bars_since_entry >= int(
            ctx.input("cooldown_bars")
        )
        if not (crossed and cooled and atr is not None and rsi is not None):
            return
        if rsi <= float(ctx.input("rsi_threshold")) or not self.flow_ok(ctx):
            return
        stop = close - float(ctx.input("stop_atr")) * atr
        risk = close - stop
        if stop <= 0 or risk <= 0:
            return
        target = close + float(ctx.input("target_r")) * risk
        cost = close * float(ctx.input("fee_bps")) * 2 / 10000
        rr = (target - close - cost) / (risk + cost)
        if rr < float(ctx.input("min_rr")):
            return
        quantity = ctx.equity * float(ctx.input("position_pct")) / 100 / close
        ctx.entry(
            "long",
            quantity,
            stop=stop,
            target=target,
            comment=f"close crossed above EMA; 1h RSI {rsi:.1f}; R:R {rr:.2f}",
        )
        self.bars_since_entry = 0
