# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Pivot bounce long early in the US cash session.

Classic floor pivots come from the high ``H``, low ``L`` and close ``C`` of
the last closed daily bar (on exchanges the session bar)::

    P = (H + L + C) / 3    R1 = 2P - L    S1 = 2P - H    S2 = P - (H - L)

A support level is touched when a bar's low reaches it and the bar closes
above it. Entry on a closed bar, while flat and outside the cooldown, when
all hold:

* a selected support (S1, S2 or both) was touched on this bar or one of the
  previous ``touch_bars - 1`` bars, and that level has not been traded yet
  today when ``once_per_level`` is on;
* the bar starts inside the New York time window (US daylight-saving rules
  since 2007), on a weekday when ``weekdays_only`` is on;
* the bar carries venue or trade-classified buy volume when
  ``require_trade_delta`` is on;
* the nearest selected target level (P, R1 or both) above the close exists
  and the net reward/risk ``(reward - cost) / (risk + cost)``, with
  ``cost = close * fee_bps * 2 / 10000``, reaches the minimum.

The stop sits ``stop_atr`` ATRs below the touched level. The simulated
position closes at the next open after a bar trades through the stop or the
target; live entries carry both levels.
"""

from __future__ import annotations

import datetime
import math
from typing import TYPE_CHECKING

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

SUPPORTS = {"S1": ("s1",), "S2": ("s2",), "S1 and S2": ("s1", "s2")}
"""Support level selections."""

TARGETS = {"P": ("p",), "R1": ("r1",), "P and R1": ("p", "r1")}
"""Target level selections."""


def sunday_switch_utc(year: int, month: int, nth: int, hour: int) -> int:
    """Finds the UTC instant of a US daylight-saving switch.

    Args:
        year: Calendar year.
        month: Month of the switch (3 or 11).
        nth: Which Sunday of the month (2 in March, 1 in November).
        hour: UTC hour of the switch on that Sunday.

    Returns:
        UTC epoch milliseconds.
    """
    first = datetime.date(year, month, 1)
    offset = (6 - first.weekday()) % 7
    day = first + datetime.timedelta(days=offset + 7 * (nth - 1))
    moment = datetime.datetime(
        day.year, day.month, day.day, hour, tzinfo=datetime.UTC
    )
    return int(moment.timestamp() * 1000)


def new_york_time(time_ms: int) -> tuple[int, int]:
    """Converts a UTC instant to New York local weekday and minute of day.

    Args:
        time_ms: UTC epoch milliseconds.

    Returns:
        Weekday (Monday is 0) and minutes after local midnight.
    """
    moment = datetime.datetime.fromtimestamp(time_ms / 1000, tz=datetime.UTC)
    start = sunday_switch_utc(moment.year, 3, 2, 7)
    end = sunday_switch_utc(moment.year, 11, 1, 6)
    hours = -4 if start <= time_ms < end else -5
    local = moment + datetime.timedelta(hours=hours)
    return local.weekday(), local.hour * 60 + local.minute


def clock_minutes(text: str) -> int:
    """Parses an ``HH:MM`` input.

    Args:
        text: Time of day such as "09:30".

    Returns:
        Minutes after midnight.

    Raises:
        ValueError: If the text is not a valid time of day.
    """
    hours, _, minutes = str(text).partition(":")
    value = int(hours) * 60 + int(minutes)
    if not 0 <= value <= 24 * 60:
        raise ValueError("times of day use HH:MM between 00:00 and 24:00")
    return value


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


class PivotBounceLong(joseph_script.Strategy):
    """Buys a wick rejection of a daily support pivot in the morning session.

    Attributes:
        average: Wilder average of true ranges (ATR).
        previous_close: Previous chart close.
        cursor: Number of daily bars already read.
        day_ms: Start of the daily bar the levels come from.
        levels: Current pivot levels by name.
        touches: Recent support touches as (bar index, level name).
        traded: Level names already traded for the current levels.
        bars_since_entry: Bars since the last entry order, None before one.
    """

    ID = "pivot-bounce-long"
    TITLE = "Pivot Bounce Long"
    SHORT_TITLE = "Pivot Bounce"
    DESCRIPTION = (
        "Long on a wick touch of the daily S1 or S2 pivot between 09:30 and "
        "11:30 New York time on weekdays; stop below the level, target the "
        "nearest of P and R1."
    )
    CATEGORY = "Levels"
    OVERLAY = True
    INPUTS = {
        "supports": joseph_script.Input(
            "S1 and S2", title="Support levels", options=tuple(SUPPORTS)
        ),
        "targets": joseph_script.Input(
            "P and R1", title="Target levels", options=tuple(TARGETS)
        ),
        "touch_bars": joseph_script.Input(
            3, title="Touch within (bars)", minimum=1, maximum=50, step=1
        ),
        "session_start": joseph_script.Input(
            "09:30", title="Window start (New York)"
        ),
        "session_end": joseph_script.Input(
            "11:30", title="Window end (New York)"
        ),
        "weekdays_only": joseph_script.Input(True, title="Weekdays only"),
        "require_trade_delta": joseph_script.Input(
            True,
            title="Require buy volume data",
            tooltip="Bars without venue or trade-classified buy volume "
            "never qualify.",
        ),
        "once_per_level": joseph_script.Input(
            True, title="Trade each level once per day"
        ),
        "atr_length": joseph_script.Input(
            14, title="ATR length", minimum=1, maximum=100, step=1, group="Risk"
        ),
        "stop_atr": joseph_script.Input(
            0.2,
            title="Stop buffer (ATR)",
            minimum=0,
            maximum=20,
            step=0.1,
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
            6, title="Cooldown (bars)", minimum=0, maximum=500, step=1
        ),
        "position_pct": joseph_script.Input(
            90,
            title="Simulated position (% of equity)",
            minimum=1,
            maximum=100,
            group="Simulation",
        ),
    }
    REQUESTS = {"daily": joseph_script.Request("1d", limit=60)}

    def __init__(self) -> None:
        """Starts before the first bar."""
        self.average: WilderAverage | None = None
        self.previous_close: float | None = None
        self.cursor = 0
        self.day_ms: int | None = None
        self.levels: dict[str, float] = {}
        self.touches: list[tuple[int, str]] = []
        self.traded: set[str] = set()
        self.bars_since_entry: int | None = None

    def update_levels(self, ctx: worker.Context) -> None:
        """Reads the daily bars closed by the current bar's close.

        Args:
            ctx: Script context.
        """
        series = ctx.extra_series.get("daily", [])
        end = ctx.bar.get("end_ms", ctx.bar["time_ms"])
        latest = None
        while (
            self.cursor < len(series) and series[self.cursor]["end_ms"] <= end
        ):
            latest = series[self.cursor]
            self.cursor += 1
        if latest is None:
            return
        high, low = float(latest["high"]), float(latest["low"])
        pivot = (high + low + float(latest["close"])) / 3
        self.levels = {
            "p": pivot,
            "r1": 2 * pivot - low,
            "s1": 2 * pivot - high,
            "s2": pivot - (high - low),
        }
        self.day_ms = int(latest["time_ms"])
        self.touches = []
        self.traded = set()

    def true_range(self, high: float, low: float, close: float) -> float:
        """Computes the current true range.

        Args:
            high: Bar high.
            low: Bar low.
            close: Bar close.

        Returns:
            The true range.
        """
        value = high - low
        if self.previous_close is not None:
            value = max(
                value,
                abs(high - self.previous_close),
                abs(low - self.previous_close),
            )
        self.previous_close = close
        return value

    def in_window(self, ctx: worker.Context) -> bool:
        """Checks the New York time window of the current bar.

        Args:
            ctx: Script context.

        Returns:
            True when the bar starts inside the window.
        """
        weekday, minute = new_york_time(int(ctx.bar["time_ms"]))
        if ctx.input("weekdays_only") and weekday >= 5:
            return False
        start = clock_minutes(str(ctx.input("session_start")))
        end = clock_minutes(str(ctx.input("session_end")))
        return start <= minute < end

    def on_bar(self, ctx: worker.Context) -> None:
        """Manages the open position and looks for a new entry.

        Args:
            ctx: Script context.
        """
        if self.average is None:
            self.average = WilderAverage(int(ctx.input("atr_length")))
        high = float(ctx.bar["high"])
        low = float(ctx.bar["low"])
        close = float(ctx.bar["close"])
        atr = self.average.add(self.true_range(high, low, close))
        self.update_levels(ctx)
        if self.bars_since_entry is not None:
            self.bars_since_entry += 1
        for name, color in (
            ("p", "#9e9e9e"),
            ("r1", "#ef5350"),
            ("s1", "#26a69a"),
            ("s2", "#00897b"),
        ):
            ctx.plot(name.upper(), self.levels.get(name), color=color)
        ctx.plot("Stop", ctx.position_stop, color="#ef5350")
        ctx.plot("Target", ctx.position_target, color="#26a69a")
        supports = SUPPORTS[str(ctx.input("supports"))]
        for name in supports:
            level = self.levels.get(name)
            if level is not None and low <= level < close:
                self.touches.append((ctx.bar_index, name))
        oldest = ctx.bar_index - int(ctx.input("touch_bars")) + 1
        self.touches = [touch for touch in self.touches if touch[0] >= oldest]
        if ctx.position_size > 0:
            stop, target = ctx.position_stop, ctx.position_target
            if stop is not None and low <= stop:
                ctx.close("stop reached")
            elif target is not None and high >= target:
                ctx.close("target reached")
            return
        self.consider_entry(ctx, close, atr)

    def consider_entry(
        self, ctx: worker.Context, close: float, atr: float | None
    ) -> None:
        """Enters long when every entry condition holds.

        Args:
            ctx: Script context.
            close: Current close.
            atr: Current ATR, None while seeding.
        """
        candidates = [
            name
            for _, name in reversed(self.touches)
            if not (ctx.input("once_per_level") and name in self.traded)
        ]
        cooled = self.bars_since_entry is None or self.bars_since_entry >= int(
            ctx.input("cooldown_bars")
        )
        if not candidates or not cooled or atr is None:
            return
        if not self.in_window(ctx):
            return
        if (
            ctx.input("require_trade_delta")
            and ctx.bar.get("taker_buy_volume") is None
        ):
            return
        anchor = candidates[0]
        stop = self.levels[anchor] - float(ctx.input("stop_atr")) * atr
        above = [
            self.levels[name]
            for name in TARGETS[str(ctx.input("targets"))]
            if self.levels[name] > close
        ]
        risk = close - stop
        if not above or stop <= 0 or risk <= 0:
            return
        target = min(above)
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
            comment=f"{anchor.upper()} pivot bounce; R:R {rr:.2f}",
        )
        self.traded.add(anchor)
        self.bars_since_entry = 0
