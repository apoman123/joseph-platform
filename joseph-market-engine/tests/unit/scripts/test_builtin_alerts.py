# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in Alert scripts on deterministic closed bars."""

from __future__ import annotations

from typing import Any

import pytest

import joseph_script
from joseph.scripts import builtin
from joseph.scripts import worker

_HOUR = 3_600_000


def _bars(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    final: bool = True,
) -> list[dict[str, Any]]:
    """Builds closed hourly bars from closes.

    Args:
        closes: Closing prices, oldest first.
        volumes: Optional volumes, 100 each by default.
        final: Whether every bar is closed.

    Returns:
        Bar records; highs and lows equal the close.
    """
    return [
        {
            "time_ms": index * _HOUR,
            "end_ms": (index + 1) * _HOUR,
            "is_final": final,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": (volumes or [100.0] * len(closes))[index],
        }
        for index, close in enumerate(closes)
    ]


def _events(
    module: str, rows: list[dict[str, Any]], /, **inputs: Any
) -> list[tuple[int, str]]:
    """Runs one built-in alert and lists its events.

    Args:
        module: Module name below ``builtin.alerts``.
        rows: Closed bars.
        **inputs: Input overrides.

    Returns:
        ``(bar index, event name)`` in order.
    """
    cls = builtin.script_class(f"alerts.{module}")
    assert issubclass(cls, joseph_script.Alert)
    defaults = {name: spec.default for name, spec in cls.INPUTS.items()}
    result = worker.run_class(
        cls,
        {
            "kind": "alert",
            "bars": rows,
            "instrument_id": "mock:perp:BTCUSD",
            "inputs": {**defaults, **inputs},
        },
    )
    for event in result["alerts"]:
        assert "mock:perp:BTCUSD" in event["message"]
    return [
        (event["time_ms"] // _HOUR, event["name"]) for event in result["alerts"]
    ]


def test_price_cross_fires_on_the_crossing_bar_only() -> None:
    """Touching the level is not a cross; closing through it is."""
    bars = _bars([99, 100, 101, 102, 99, 98])
    assert _events("price_cross", bars, level=100) == [
        (2, "crossing-up"),
        (4, "crossing-down"),
    ]
    assert _events("price_cross", bars, level=100, condition="crossing_up") == [
        (2, "crossing-up")
    ]
    assert _events(
        "price_cross", bars, level=100, condition="crossing_down"
    ) == [(4, "crossing-down")]


def test_alerts_ignore_unconfirmed_bars() -> None:
    """Provisional bars never publish events."""
    assert (
        _events("price_cross", _bars([99, 101], final=False), level=100) == []
    )


def test_channel_entering_exiting_inside_outside() -> None:
    """Entering and exiting fire on the change; inside/outside every bar."""
    bars = _bars([80, 95, 100, 120, 125])
    assert _events("channel", bars, lower=90, upper=110) == [
        (1, "entering-channel")
    ]
    assert _events(
        "channel", bars, lower=90, upper=110, condition="exiting"
    ) == [(3, "exiting-channel")]
    assert [
        index
        for index, _ in _events(
            "channel", bars, lower=90, upper=110, condition="inside"
        )
    ] == [1, 2]
    assert [
        index
        for index, _ in _events(
            "channel", bars, lower=90, upper=110, condition="outside"
        )
    ] == [0, 3, 4]
    with pytest.raises(ValueError, match="lower level"):
        _events("channel", bars, lower=110, upper=90)


@pytest.mark.parametrize("ma_type", ["EMA", "SMA"])
def test_ma_cross_up_and_down(ma_type: str) -> None:
    """A rally lifts the fast average over the slow one, a drop reverses.

    Args:
        ma_type: Average kind.
    """
    closes = [10.0] * 6 + [20.0] * 6 + [5.0] * 8
    events = _events(
        "ma_cross", _bars(closes), fast_length=2, slow_length=4, ma_type=ma_type
    )
    names = [name for _, name in events]
    assert names == ["ma-cross-up", "ma-cross-down"]
    up, down = (index for index, _ in events)
    assert 6 <= up < 12 <= down


def test_rsi_level_crossings() -> None:
    """A rally crosses overbought; the following slide crosses oversold."""
    closes = [100.0 + (index % 2) for index in range(20)]
    closes += [101.0 + 2 * index for index in range(1, 11)]
    closes += [closes[-1] - 3 * index for index in range(1, 21)]
    events = _events("rsi_level", _bars(closes), length=14)
    names = [name for _, name in events]
    assert names == ["rsi-overbought", "rsi-oversold"]
    assert _events("rsi_level", _bars(closes), condition="oversold") == [
        events[1]
    ]


def test_volume_spike_against_the_preceding_average() -> None:
    """The spike bar does not raise its own baseline."""
    volumes = [100.0] * 5 + [250.0, 150.0]
    events = _events(
        "volume_spike", _bars([1.0] * 7, volumes=volumes), length=5
    )
    assert events == [(5, "volume-spike")]


def test_percent_change_over_bars() -> None:
    """Rises and falls of at least the threshold over N bars."""
    bars = _bars([100, 103, 106, 100, 95])
    assert _events("percent_change", bars, bars=2, threshold_pct=5) == [
        (2, "move-up"),
        (4, "move-down"),
    ]
    assert _events(
        "percent_change", bars, bars=2, threshold_pct=5, direction="down"
    ) == [(4, "move-down")]


def test_breakout_needs_a_full_preceding_range() -> None:
    """A high above every preceding high is a new high."""
    bars = _bars([5, 4, 3, 6, 2, 1])
    assert _events("breakout", bars, length=3) == [
        (3, "new-high"),
        (4, "new-low"),
        (5, "new-low"),
    ]
    assert _events("breakout", bars, length=3, extreme="high") == [
        (3, "new-high")
    ]
