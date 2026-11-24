# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in indicators, available on every installation.

Generic studies with TradingView built-in semantics, implemented from their
published definitions: SMA, EMA, anchored VWAP, regression channel,
Bollinger Bands, RSI, ATR, volume delta and floor pivot points (the only
built-in that publishes AOIs). Each is a ``BasePlugin`` over a frozen state
of tuples and doubles; values become decimals only when published
(``_series``). Built-ins depend on the plugin SDK and the domain only, and
never on each other.
"""

from __future__ import annotations

from joseph.indicators.builtin import anchored_vwap
from joseph.indicators.builtin import bollinger
from joseph.indicators.builtin import moving_average
from joseph.indicators.builtin import oscillators
from joseph.indicators.builtin import pivot_points
from joseph.indicators.builtin import regression_channel
from joseph.indicators.builtin import volume_delta
from joseph.indicators.sdk import protocol

BUILTIN_IDS: tuple[str, ...] = (
    "sma",
    "ema",
    "anchored_vwap",
    "regression_channel",
    "bollinger_bands",
    "rsi",
    "atr",
    "volume_delta",
    "pivot_points",
)
"""Built-in plugin ids in registration (display) order."""


def builtin_plugins() -> tuple[protocol.IndicatorPlugin, ...]:
    """Instantiates every built-in plugin.

    Returns:
        Plugins in ``BUILTIN_IDS`` order.
    """
    return (
        moving_average.SmaPlugin(),
        moving_average.EmaPlugin(),
        anchored_vwap.AnchoredVwapPlugin(),
        regression_channel.RegressionChannelPlugin(),
        bollinger.BollingerBandsPlugin(),
        oscillators.RsiPlugin(),
        oscillators.AtrPlugin(),
        volume_delta.VolumeDeltaPlugin(),
        pivot_points.PivotPointsPlugin(),
    )
