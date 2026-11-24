# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Adapters added with library 1.1: true delta and the native studies."""

from __future__ import annotations

import dataclasses
import decimal

import pytest

from joseph.domain import errors
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.packaged import plugins as packaged
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib

TF = timeframes.Timeframe
_START = testing.DEFAULT_START_MS


def _with_taker(candle: market.Candle, share: str) -> market.Candle:
    """Returns ``candle`` with a taker buy volume of ``share`` x volume.

    Args:
        candle: Source candle.
        share: Fraction of the base volume bought by takers.

    Returns:
        The candle carrying ``taker_buy_base_volume``.
    """
    return dataclasses.replace(
        candle,
        taker_buy_base_volume=candle.base_volume * decimal.Decimal(share),
    )


def test_true_delta_uses_taker_volume_and_is_labelled_trade() -> None:
    """Bars with taker volume publish true delta, never as Pine parity."""
    plugin = packaged.OrderFlowPressurePlugin()
    candles = [
        _with_taker(c, "0.75")
        for c in testing.random_walk(30, timeframe=TF.M5, seed=3)
    ]
    snapshot, _ = testing.run_bars(plugin, candles)[-1]
    assert snapshot.values["method"] == "taker_volume"
    assert snapshot.method is structures.IndicatorMethod.TRADE
    assert snapshot.values["delta_pct"] == decimal.Decimal(50)
    volume = candles[-1].base_volume
    assert snapshot.values["buy_volume"] == volume * decimal.Decimal("0.75")
    assert snapshot.values["sell_volume"] == volume * decimal.Decimal("0.25")
    assert snapshot.to_contract()["method"] == "trade"


def test_estimate_setting_ignores_taker_volume() -> None:
    """``delta_source = estimate`` reproduces the Pine estimate."""
    plugin = packaged.OrderFlowPressurePlugin()
    candles = [
        _with_taker(c, "0.75")
        for c in testing.random_walk(30, timeframe=TF.M5, seed=3)
    ]
    snapshot, _ = testing.run_bars(
        plugin, candles, {"delta_source": "estimate"}
    )[-1]
    assert snapshot.values["method"] == "candle_direction"
    assert snapshot.method is structures.IndicatorMethod.PINE_PARITY
    assert snapshot.values["delta_pct"] is None


def test_bars_without_taker_volume_fall_back_to_the_estimate() -> None:
    """Missing taker volume never fabricates a true delta."""
    plugin = packaged.OrderFlowPressurePlugin()
    candles = testing.random_walk(30, timeframe=TF.M5, seed=3)
    snapshot, _ = testing.run_bars(plugin, candles)[-1]
    assert snapshot.values["method"] == "candle_direction"
    assert snapshot.values["buy_volume"] is not None or (
        snapshot.values["intrabar_count"] == 0
    )


@pytest.mark.parametrize(
    ("chart", "intrabar"),
    [
        (TF.S1, None),
        (TF.S15, TF.S1),
        (TF.M1, None),
        (TF.M5, TF.M1),
        (TF.H4, TF.M1),
        (TF.D1, TF.M5),
        (TF.W1, TF.H1),
        (TF.MN1, TF.H1),
    ],
)
def test_auto_intrabars_follow_the_chart_timeframe(
    chart: timeframes.Timeframe, intrabar: timeframes.Timeframe | None
) -> None:
    """``intrabar_tf = auto`` picks a nesting lower timeframe."""
    plugin = packaged.OrderFlowPressurePlugin()
    streams = plugin.required_streams(None, timeframe=chart)
    expected = {protocol.CHART_STREAM}
    if intrabar is not None:
        expected.add(protocol.intrabar_stream(intrabar))
    assert streams == frozenset(expected)


def test_engine_accepts_order_flow_on_every_timeframe() -> None:
    """Auto intrabars always nest, so no chart timeframe is refused."""
    plugins = registry_lib.PluginRegistry()
    plugins.register(packaged.OrderFlowPressurePlugin())
    for chart in TF:
        engine_lib.IndicatorEngine(
            plugins,
            engine_lib.EngineConfig(
                chart, {"order_flow_pressure": engine_lib.PluginSpec((chart,))}
            ),
            testing.DEFAULT_INSTRUMENT,
        )


def test_time_anchor_requires_anchor_ms() -> None:
    """An anchored VWAP without an anchor time is a validation error."""
    plugin = packaged.AnchoredVwapPlugin()
    with pytest.raises(errors.ValidationFailedError, match="anchor_ms"):
        plugin.initialize(testing.DEFAULT_INSTRUMENT, TF.M5, {"anchor": "time"})


def test_session_vwap_resets_each_utc_day() -> None:
    """The default session anchor restarts at 00:00 UTC."""
    plugin = packaged.AnchoredVwapPlugin()
    day = 86_400_000
    first = _START - _START % day
    candles = [
        testing.make_candle(TF.H12, first, "10", "10", "10", "10", "1"),
        testing.make_candle(TF.H12, first + day // 2, "20", "20", "20", "20"),
        testing.make_candle(TF.H12, first + day, "30", "30", "30", "30", "1"),
    ]
    results = testing.run_bars(plugin, candles, {"source": "close"})
    assert results[1][0].values["anchor_ms"] == first
    assert results[2][0].values["vwap"] == decimal.Decimal(30)
    assert results[2][0].values["anchor_ms"] == first + day
    assert results[2][0].values["bars"] == 1


def test_rolling_regression_warms_up_after_length_bars() -> None:
    """The rolling channel publishes once ``length`` bars exist."""
    plugin = packaged.RegressionChannelPlugin()
    candles = testing.random_walk(12, timeframe=TF.M5, seed=5)
    results = testing.run_bars(plugin, candles, {"length": 10})
    assert results[8][0].values["end"] is None
    assert not results[8][0].warmup_complete
    snapshot = results[-1][0]
    assert snapshot.warmup_complete
    assert snapshot.values["bars"] == 10
    assert snapshot.values["start_ms"] == candles[2].start_ms
    assert snapshot.values["anchor_in_window"] is True
