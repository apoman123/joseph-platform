# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in Strategy scripts on deterministic bars: orders, fills, exits."""

from __future__ import annotations

from typing import Any

import pytest

import joseph_script
from joseph.scripts import worker
from joseph.scripts.builtin.strategies import ema_trend_long
from joseph.scripts.builtin.strategies import pivot_bounce_long
from joseph.scripts.builtin.strategies import rsi_reversion_short

MINUTE = 60_000
"""One minute in milliseconds."""

START = 1_793_000_000_000
"""Arbitrary aligned UTC start of the chart bars."""


def _bar(
    time_ms: int,
    span_ms: int,
    ohlc: tuple[float, float, float, float],
    *,
    taker: float | None = 8.0,
) -> dict[str, Any]:
    """Builds one closed bar of volume 10.

    Args:
        time_ms: Bar start.
        span_ms: Bar length.
        ohlc: Open, high, low and close.
        taker: Taker buy volume, None when unpublished.

    Returns:
        Worker bar dictionary.
    """
    open_, high, low, close = ohlc
    return {
        "time_ms": time_ms,
        "end_ms": time_ms + span_ms,
        "is_final": True,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10.0,
        "taker_buy_volume": taker,
    }


def _run(
    cls: type[joseph_script.Strategy],
    bars: list[dict[str, Any]],
    inputs: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Runs a built-in strategy class with its declared input defaults.

    Args:
        cls: Strategy class.
        bars: Chart bars.
        inputs: Input overrides.
        **extra: Extra bundle fields such as extra_series.

    Returns:
        Worker result.
    """
    defaults = {name: spec.default for name, spec in cls.INPUTS.items()}
    return worker.run_class(
        cls,
        {
            "kind": "strategy",
            "bars": bars,
            "initial_equity": 10000,
            "inputs": defaults | (inputs or {}),
            **extra,
        },
    )


def _trend_bars(taker: float | None = 8.0) -> list[dict[str, Any]]:
    """Flat 15m bars, an upward EMA cross, then a bar through the target.

    Args:
        taker: Taker buy volume of every bar.

    Returns:
        Thirty-five bars: cross on index 30, target hit on index 32.
    """
    span = 15 * MINUTE
    bars = [
        _bar(START + i * span, span, (100, 100.5, 99.5, 100), taker=taker)
        for i in range(30)
    ]
    bars.append(
        _bar(START + 30 * span, span, (100, 101.2, 99.9, 101), taker=taker)
    )
    bars.append(
        _bar(START + 31 * span, span, (101.1, 101.5, 100.8, 101.2), taker=taker)
    )
    bars.append(
        _bar(START + 32 * span, span, (101.2, 110, 101, 109), taker=taker)
    )
    bars.extend(
        _bar(START + i * span, span, (108, 109, 107, 108), taker=taker)
        for i in range(33, 35)
    )
    return bars


def _rising_hours() -> list[dict[str, Any]]:
    """Twenty closed one-hour bars with rising closes before the chart.

    Returns:
        Hourly bars whose RSI is 100.
    """
    hour = 60 * MINUTE
    first = START - 20 * hour
    return [
        _bar(first + i * hour, hour, (90 + i, 91 + i, 89 + i, 90.5 + i))
        for i in range(20)
    ]


def test_ema_trend_long_enters_on_cross_and_exits_at_target() -> None:
    """The cross queues a long with ATR stop and 3R target; target exits."""
    bars = _trend_bars()
    result = _run(
        ema_trend_long.EmaTrendLong,
        bars,
        extra_series={"htf": _rising_hours()},
    )
    orders = result["orders"]
    assert [order["action"] for order in orders] == ["long", "close"]
    entry, exit_ = orders
    assert entry["time_ms"] == bars[30]["time_ms"]
    assert entry["status"] == "filled"
    assert entry["fill_time_ms"] == bars[31]["time_ms"]
    assert entry["reference"] == 101
    risk = 101 - entry["stop"]
    assert risk == pytest.approx(1.5 * _atr(bars[:31], 14))
    assert entry["target"] == pytest.approx(101 + 3 * risk)
    assert exit_["time_ms"] == bars[32]["time_ms"]
    assert exit_["comment"] == "target reached"
    assert exit_["fill_time_ms"] == bars[33]["time_ms"]
    assert [fill["effect"] for fill in result["fills"]] == ["entry", "exit"]
    assert result["fills"][0]["price"] == bars[31]["open"]
    assert result["position_size"] == 0
    assert {plot["name"] for plot in result["plots"]} >= {"EMA", "Stop"}


def test_ema_trend_long_requires_published_flow_and_higher_rsi() -> None:
    """Without taker volume, or with a falling hourly RSI, nothing enters."""
    no_flow = _run(
        ema_trend_long.EmaTrendLong,
        _trend_bars(taker=None),
        extra_series={"htf": _rising_hours()},
    )
    assert no_flow["orders"] == []
    falling = [{**bar, "close": 200 - bar["close"]} for bar in _rising_hours()]
    weak = _run(
        ema_trend_long.EmaTrendLong,
        _trend_bars(),
        extra_series={"htf": falling},
    )
    assert weak["orders"] == []
    allowed = _run(
        ema_trend_long.EmaTrendLong,
        _trend_bars(taker=None),
        extra_series={"htf": _rising_hours()},
        inputs={"require_flow": False},
    )
    assert allowed["orders"][0]["action"] == "long"


def _atr(bars: list[dict[str, Any]], length: int) -> float:
    """Computes Wilder's ATR independently of the strategy module.

    Args:
        bars: Bars, oldest first.
        length: ATR length.

    Returns:
        ATR after the last bar.
    """
    ranges = []
    previous = None
    for bar in bars:
        high, low = bar["high"], bar["low"]
        value = high - low
        if previous is not None:
            value = max(value, abs(high - previous), abs(low - previous))
        ranges.append(value)
        previous = bar["close"]
    atr = sum(ranges[:length]) / length
    for value in ranges[length:]:
        atr += (value - atr) / length
    return float(atr)


def _reversion_bars() -> list[dict[str, Any]]:
    """Choppy 5m bars, a three-bar rally above the band, then a drop.

    Returns:
        Bars: rally on indices 30 to 32, RSI cross below 70 on index 33.
    """
    span = 5 * MINUTE
    bars = []
    for i in range(30):
        close = 100 if i % 2 == 0 else 100.2
        bars.append(
            _bar(
                START + i * span, span, (close, close + 0.3, close - 0.3, close)
            )
        )
    for i, close in enumerate((102.2, 104.2, 106.2), start=30):
        bars.append(
            _bar(
                START + i * span,
                span,
                (close - 2, close + 0.3, close - 2, close),
            )
        )
    bars.append(_bar(START + 33 * span, span, (106, 106.1, 103, 103.2)))
    bars.append(_bar(START + 34 * span, span, (103, 103.5, 102.5, 103)))
    return bars


def test_rsi_reversion_short_enters_after_band_close() -> None:
    """A cross below 70 within three bars of a band close sells short."""
    bars = _reversion_bars()
    result = _run(rsi_reversion_short.RsiReversionShort, bars)
    orders = result["orders"]
    assert len(orders) == 1
    order = orders[0]
    assert order["action"] == "short"
    assert order["time_ms"] == bars[33]["time_ms"]
    highest = max(bar["high"] for bar in bars[24:34])
    assert order["stop"] == pytest.approx(highest + 0.5 * _atr(bars[:34], 14))
    risk = order["stop"] - 103.2
    assert order["target"] == pytest.approx(103.2 - 2 * risk)
    assert result["fills"][0]["side"] == "sell"
    assert result["position_size"] < 0


def test_rsi_reversion_short_needs_a_recent_band_close() -> None:
    """With a one-bar window the earlier band close no longer qualifies."""
    bars = _reversion_bars()
    result = _run(
        rsi_reversion_short.RsiReversionShort,
        bars,
        inputs={"within_bars": 1},
    )
    assert result["orders"] == []


NOV_2_2026_0920_NY = 1_793_629_200_000
"""Monday 2026-11-02 09:20 in New York (14:20 UTC, standard time)."""


def _pivot_bars(touch_index: int) -> list[dict[str, Any]]:
    """5m bars around 95 with one wick through S1 (90).

    Args:
        touch_index: Index of the touching bar; index 20 starts at 09:30.

    Returns:
        Thirty bars starting 07:50 New York time.
    """
    span = 5 * MINUTE
    first = NOV_2_2026_0920_NY - 18 * span
    bars = []
    for i in range(30):
        ohlc = (95.0, 95.5, 94.5, 95.0)
        if i == touch_index:
            ohlc = (92.0, 92.5, 89.5, 91.0)
        bars.append(_bar(first + i * span, span, ohlc))
    return bars


def _daily() -> list[dict[str, Any]]:
    """One closed daily bar with H 110, L 90 and C 100.

    Returns:
        Pivot P 100, R1 110, S1 90, S2 80.
    """
    day = 24 * 60 * MINUTE
    end = NOV_2_2026_0920_NY - 3 * 60 * MINUTE
    return [_bar(end - day, day, (95, 110, 90, 100))]


def test_pivot_bounce_long_buys_s1_touch_in_the_morning_window() -> None:
    """A 09:30 wick through S1 buys with a stop below it and P as target."""
    bars = _pivot_bars(touch_index=20)
    result = _run(
        pivot_bounce_long.PivotBounceLong,
        bars,
        extra_series={"daily": _daily()},
    )
    order = result["orders"][0]
    assert order["action"] == "long"
    assert order["time_ms"] == bars[20]["time_ms"]
    assert order["target"] == pytest.approx(100)
    assert order["stop"] == pytest.approx(90 - 0.2 * _atr(bars[:21], 14))
    assert "S1" in order["comment"]


def test_pivot_bounce_long_ignores_touches_outside_the_window() -> None:
    """The same touch at 09:25 New York time does not trade."""
    bars = _pivot_bars(touch_index=19)
    result = _run(
        pivot_bounce_long.PivotBounceLong,
        bars,
        extra_series={"daily": _daily()},
        inputs={"touch_bars": 1},
    )
    assert result["orders"] == []


def test_new_york_time_follows_daylight_saving() -> None:
    """Summer is UTC-4, winter UTC-5, switching on the US Sundays."""
    assert pivot_bounce_long.new_york_time(NOV_2_2026_0920_NY) == (0, 560)
    july = 1_783_085_400_000  # Friday 2026-07-03 13:30 UTC.
    assert pivot_bounce_long.new_york_time(july) == (4, 570)


def test_entry_rejects_levels_on_the_wrong_side() -> None:
    """A long stop above the close is a script error."""
    context = worker.Context({"kind": "strategy", "bars": []})
    context.advance(_bar(START, MINUTE, (100, 101, 99, 100)))
    with pytest.raises(ValueError, match="wrong side"):
        context.entry("long", 1, stop=101)
    with pytest.raises(ValueError, match="wrong side"):
        context.entry("short", 1, target=101)
    context.entry("short", 1, stop=101, target=99)
    assert context.orders[-1]["status"] == "pending"
