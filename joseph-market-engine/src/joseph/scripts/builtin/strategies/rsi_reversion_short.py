# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""RSI reversion short after an overextension above the upper band.

Entry on a closed bar, while flat and outside the cooldown, when all hold:

* the RSI crosses below the level (previous RSI at or above it, current
  RSI below it);
* a close above the upper Bollinger Band (``basis + mult * stdev`` of the
  last ``bb_length`` closes, population deviation) happened on this bar or
  one of the previous ``within_bars - 1`` bars;
* the net reward/risk ``(reward - cost) / (risk + cost)``, with
  ``cost = close * fee_bps * 2 / 10000``, reaches the minimum.

The stop sits ``stop_atr`` ATRs above the highest high of the last
``swing_bars`` bars and the target ``target_r`` times the risk below the
close. The simulated position closes at the next open after a bar trades
through either level; live entries carry both levels. Scripts read
OHLCV bars only, so no open-interest condition is applied.
"""

from __future__ import annotations

import collections
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


class RsiReversionShort(joseph_script.Strategy):
    """Sells an RSI cross back below the level after a close above the band.

    Attributes:
        rsi: RSI of the closes.
        atr: Average true range.
        closes: Last ``bb_length`` closes.
        highs: Last ``swing_bars`` highs.
        bars_since_above: Bars since a close above the upper band, None
            before one.
        bars_since_entry: Bars since the last entry order, None before one.
    """

    ID = "rsi-reversion-short"
    TITLE = "RSI Reversion Short"
    SHORT_TITLE = "RSI Reversion"
    DESCRIPTION = (
        "Short when the RSI falls back below 70 within three bars of a close "
        "above the upper Bollinger Band; swing-high stop, fixed 2R target."
    )
    CATEGORY = "Mean reversion"
    OVERLAY = True
    INPUTS = {
        "rsi_length": joseph_script.Input(
            14, title="RSI length", minimum=2, maximum=100, step=1
        ),
        "rsi_level": joseph_script.Input(
            70, title="RSI crosses below", minimum=1, maximum=99
        ),
        "bb_length": joseph_script.Input(
            20, title="Bollinger length", minimum=2, maximum=500, step=1
        ),
        "bb_mult": joseph_script.Input(
            2.0, title="Bollinger multiplier", minimum=0.1, maximum=10, step=0.1
        ),
        "within_bars": joseph_script.Input(
            3, title="Band close within (bars)", minimum=1, maximum=50, step=1
        ),
        "swing_bars": joseph_script.Input(
            10,
            title="Swing lookback (bars)",
            minimum=1,
            maximum=200,
            group="Risk",
        ),
        "atr_length": joseph_script.Input(
            14, title="ATR length", minimum=1, maximum=100, step=1, group="Risk"
        ),
        "stop_atr": joseph_script.Input(
            0.5,
            title="Stop buffer (ATR)",
            minimum=0,
            maximum=20,
            step=0.1,
            group="Risk",
        ),
        "target_r": joseph_script.Input(
            2.0,
            title="Target (R multiple)",
            minimum=0.5,
            maximum=20,
            step=0.5,
            group="Risk",
        ),
        "min_rr": joseph_script.Input(
            1.5,
            title="Minimum net reward/risk",
            minimum=0,
            maximum=20,
            step=0.1,
            group="Risk",
        ),
        "fee_bps": joseph_script.Input(
            0, title="Fee estimate (bps)", minimum=0, maximum=100, group="Risk"
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
            "#ff9800", title="Band color", group="Style"
        ),
    }

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.rsi: RelativeStrength | None = None
        self.atr: AverageTrueRange | None = None
        self.closes: collections.deque[float] = collections.deque()
        self.highs: collections.deque[float] = collections.deque()
        self.bars_since_above: int | None = None
        self.bars_since_entry: int | None = None

    def upper_band(self, ctx: worker.Context, close: float) -> float | None:
        """Adds the close and computes the bands.

        Args:
            ctx: Script context.
            close: Current close.

        Returns:
            The upper band, None until ``bb_length`` closes exist.
        """
        length = int(ctx.input("bb_length"))
        self.closes.append(close)
        while len(self.closes) > length:
            self.closes.popleft()
        if len(self.closes) < length:
            return None
        basis = math.fsum(self.closes) / length
        variance = math.fsum((x - basis) ** 2 for x in self.closes) / length
        ctx.plot("Basis", basis, color=str(ctx.input("color")))
        upper = basis + float(ctx.input("bb_mult")) * math.sqrt(variance)
        ctx.plot("Upper band", upper, color=str(ctx.input("color")))
        return upper

    def on_bar(self, ctx: worker.Context) -> None:
        """Manages the open position and looks for a new entry.

        Args:
            ctx: Script context.
        """
        if self.rsi is None or self.atr is None:
            self.rsi = RelativeStrength(int(ctx.input("rsi_length")))
            self.atr = AverageTrueRange(int(ctx.input("atr_length")))
        high = float(ctx.bar["high"])
        low = float(ctx.bar["low"])
        close = float(ctx.bar["close"])
        previous_rsi = self.rsi.value
        rsi = self.rsi.add(close)
        atr = self.atr.add(high, low, close)
        self.highs.append(high)
        while len(self.highs) > int(ctx.input("swing_bars")):
            self.highs.popleft()
        upper = self.upper_band(ctx, close)
        if self.bars_since_above is not None:
            self.bars_since_above += 1
        if upper is not None and close > upper:
            self.bars_since_above = 0
        if self.bars_since_entry is not None:
            self.bars_since_entry += 1
        ctx.plot("Stop", ctx.position_stop, color="#ef5350")
        ctx.plot("Target", ctx.position_target, color="#26a69a")
        if ctx.position_size < 0:
            stop, target = ctx.position_stop, ctx.position_target
            if stop is not None and high >= stop:
                ctx.close("stop reached")
            elif target is not None and low <= target:
                ctx.close("target reached")
            return
        level = float(ctx.input("rsi_level"))
        crossed = (
            previous_rsi is not None
            and rsi is not None
            and previous_rsi >= level > rsi
        )
        extended = self.bars_since_above is not None and (
            self.bars_since_above < int(ctx.input("within_bars"))
        )
        cooled = self.bars_since_entry is None or self.bars_since_entry >= int(
            ctx.input("cooldown_bars")
        )
        if not (crossed and extended and cooled and atr is not None):
            return
        stop = max(self.highs) + float(ctx.input("stop_atr")) * atr
        risk = stop - close
        target = close - float(ctx.input("target_r")) * risk
        if risk <= 0 or target <= 0:
            return
        cost = close * float(ctx.input("fee_bps")) * 2 / 10000
        rr = (close - target - cost) / (risk + cost)
        if rr < float(ctx.input("min_rr")):
            return
        quantity = ctx.equity * float(ctx.input("position_pct")) / 100 / close
        ctx.entry(
            "short",
            quantity,
            stop=stop,
            target=target,
            comment=f"RSI crossed below {level:g} after a band close",
        )
        self.bars_since_entry = 0
