# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the SMA, EMA and Bollinger Bands built-ins.

Each study is compared bar by bar with a slow recomputation in
``decimal.Decimal`` from the candles alone; published prices may differ
from the exact value only by their rounding quantum (four places below the
tick), well within one tick.
"""

from __future__ import annotations

import decimal
import json
import statistics
from collections.abc import Callable
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import market
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.builtin import _series
from joseph.indicators.builtin import bollinger
from joseph.indicators.builtin import moving_average
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol

D = decimal.Decimal
M5 = timeframes.Timeframe.M5
TICK = D("0.01")
Validate: TypeAlias = Callable[[str, str, object], None]
Reference: TypeAlias = list[D | None]

_PRICE_TOLERANCE = _series.price_quantum(TICK)
"""Largest accepted gap between a published price and the exact value."""

_RATIO_TOLERANCE = D("0.0001")
"""Largest accepted gap for values published with four places."""


def _source(candle: market.Candle, name: str) -> D:
    """Computes a price source exactly.

    Args:
        candle: Bar.
        name: Source name.

    Returns:
        The source price as a decimal.
    """
    high, low, close = candle.high, candle.low, candle.close
    exact = {
        "open": candle.open,
        "high": high,
        "low": low,
        "close": close,
        "hl2": (high + low) / 2,
        "hlc3": (high + low + close) / 3,
        "ohlc4": (candle.open + high + low + close) / 4,
    }
    return exact[name]


def _published(
    plugin: base.AnyPlugin,
    candles: Sequence[market.Candle],
    settings: dict[str, object],
    key: str,
) -> list[object]:
    """Runs a plugin and collects one published value per bar.

    Args:
        plugin: Plugin under test.
        candles: Bars.
        settings: Setting overrides.
        key: Value key.

    Returns:
        The value after each bar.
    """
    return [
        snapshot.values[key]
        for snapshot, _ in testing.run_bars(plugin, candles, settings)
    ]


def _assert_close(
    published: Sequence[object], expected: Reference, tolerance: D
) -> None:
    """Checks published values against exact ones bar by bar.

    Args:
        published: Values the plugin published.
        expected: Exact values (``None`` where nothing is published).
        tolerance: Largest accepted absolute difference.
    """
    assert len(published) == len(expected)
    for index, (got, want) in enumerate(zip(published, expected, strict=True)):
        if want is None:
            assert got is None, f"bar {index}: {got} published early"
            continue
        assert isinstance(got, D), f"bar {index}: {got!r}"
        assert abs(got - want) <= tolerance, f"bar {index}: {got} != {want}"


def _sma_reference(sources: Sequence[D], length: int) -> Reference:
    """Computes simple moving averages exactly.

    Args:
        sources: Source price per bar.
        length: Window size.

    Returns:
        The average per bar, ``None`` before ``length`` bars.
    """
    return [
        sum(sources[index + 1 - length : index + 1], D(0)) / length
        if index + 1 >= length
        else None
        for index in range(len(sources))
    ]


def _ema_reference(sources: Sequence[D], length: int) -> Reference:
    """Computes exponential moving averages exactly.

    Args:
        sources: Source price per bar.
        length: Smoothing length.

    Returns:
        The average per bar, seeded with the first source price.
    """
    alpha = D(2) / (length + 1)
    values: Reference = []
    ema: D | None = None
    for price in sources:
        ema = price if ema is None else alpha * price + (1 - alpha) * ema
        values.append(ema)
    return values


@pytest.mark.parametrize(
    ("length", "source"), [(1, "close"), (5, "hl2"), (20, "ohlc4")]
)
def test_sma_matches_the_exact_average(length: int, source: str) -> None:
    """The SMA equals the decimal mean of the last ``length`` sources."""
    candles = testing.random_walk(120, seed=length, timeframe=M5)
    sources = [_source(candle, source) for candle in candles]
    published = _published(
        moving_average.SmaPlugin(),
        candles,
        {"length": length, "source": source, "tick_size": "0.01"},
        "sma",
    )
    _assert_close(published, _sma_reference(sources, length), _PRICE_TOLERANCE)


@pytest.mark.parametrize(
    ("length", "source"), [(1, "close"), (9, "hlc3"), (50, "low")]
)
def test_ema_matches_the_exact_average(length: int, source: str) -> None:
    """The EMA equals the decimal recursion seeded with the first source."""
    candles = testing.random_walk(150, seed=length + 1, timeframe=M5)
    sources = [_source(candle, source) for candle in candles]
    published = _published(
        moving_average.EmaPlugin(),
        candles,
        {"length": length, "source": source, "tick_size": "0.01"},
        "ema",
    )
    _assert_close(published, _ema_reference(sources, length), _PRICE_TOLERANCE)


@pytest.mark.parametrize(
    ("length", "mult", "source"),
    [(2, "1", "close"), (20, "2", "close"), (14, "2.5", "hl2")],
)
def test_bollinger_matches_the_exact_bands(
    length: int, mult: str, source: str
) -> None:
    """Every band value equals its decimal recomputation."""
    candles = testing.random_walk(120, seed=length + 7, timeframe=M5)
    sources = [_source(candle, source) for candle in candles]
    results = testing.run_bars(
        bollinger.BollingerBandsPlugin(),
        candles,
        {"length": length, "mult": mult, "source": source, "tick_size": "0.01"},
    )
    expected: dict[str, Reference] = {
        key: [] for key in ("basis", "upper", "lower", "bandwidth", "percent_b")
    }
    for index in range(len(sources)):
        if index + 1 < length:
            for values in expected.values():
                values.append(None)
            continue
        window = sources[index + 1 - length : index + 1]
        basis = sum(window, D(0)) / length
        dev = D(mult) * statistics.pstdev(window)
        upper, lower = basis + dev, basis - dev
        expected["basis"].append(basis)
        expected["upper"].append(upper)
        expected["lower"].append(lower)
        expected["bandwidth"].append((upper - lower) / basis * 100)
        expected["percent_b"].append(
            (window[-1] - lower) / (upper - lower) if upper != lower else None
        )
    for key, want in expected.items():
        tolerance = (
            _PRICE_TOLERANCE
            if key in ("basis", "upper", "lower")
            else _RATIO_TOLERANCE
        )
        got = [snapshot.values[key] for snapshot, _ in results]
        _assert_close(got, want, tolerance)


def test_sma_is_null_and_cold_before_length_bars() -> None:
    """No average and no warm-up before ``length`` bars."""
    candles = testing.random_walk(6, seed=3, timeframe=M5)
    results = testing.run_bars(
        moving_average.SmaPlugin(), candles, {"length": 5}
    )
    assert [s.values["sma"] is None for s, _ in results] == [
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    assert [s.warmup_complete for s, _ in results] == [
        False,
        False,
        False,
        False,
        True,
        True,
    ]


def test_ema_publishes_from_the_first_bar_and_warms_after_length() -> None:
    """The EMA starts at the first close and is warm after ``length`` bars."""
    candles = testing.random_walk(4, seed=5, timeframe=M5)
    results = testing.run_bars(
        moving_average.EmaPlugin(), candles, {"length": 3}
    )
    assert results[0][0].values["ema"] == candles[0].close
    assert [s.warmup_complete for s, _ in results] == [
        False,
        False,
        True,
        True,
    ]


def test_bollinger_is_null_before_length_and_flat_has_no_percent_b() -> None:
    """Bands wait for ``length`` bars; equal bands give a null percent_b."""
    candles = testing.candles_from_rows([("7", "7", "7", "7")] * 4)
    results = testing.run_bars(
        bollinger.BollingerBandsPlugin(), candles, {"length": 3}
    )
    assert all(value is None for value in results[1][0].values.values())
    final = results[-1][0].values
    assert final["basis"] == final["upper"] == final["lower"] == D(7)
    assert final["bandwidth"] == D(0)
    assert final["percent_b"] is None


def test_bollinger_bandwidth_is_null_for_a_zero_basis() -> None:
    """A zero basis has no bandwidth (no division by zero)."""
    candles = testing.candles_from_rows(
        [("1", "1", "-1", "-1"), ("-1", "1", "-1", "1")]
    )
    final = testing.run_bars(
        bollinger.BollingerBandsPlugin(), candles, {"length": 2}
    )[-1][0].values
    assert final["basis"] == D(0)
    assert final["bandwidth"] is None
    assert final["percent_b"] == D("0.75")


_PLUGINS = (
    moving_average.SmaPlugin(),
    moving_average.EmaPlugin(),
    bollinger.BollingerBandsPlugin(),
)


@pytest.mark.parametrize("plugin", _PLUGINS, ids=lambda p: p.plugin_id)
def test_info_conforms_to_the_contract(
    plugin: base.AnyPlugin, contract_validate: Validate
) -> None:
    """Descriptors validate against the schema and the metadata rules."""
    assert metadata.validate(plugin) == []
    contract_validate(
        "indicators.schema.json", "IndicatorPluginInfo", plugin.info()
    )


@pytest.mark.parametrize("plugin", _PLUGINS, ids=lambda p: p.plugin_id)
def test_checkpoint_restores_an_equal_state(plugin: base.AnyPlugin) -> None:
    """A JSON checkpoint restores to an equal state."""
    candles = testing.random_walk(30, seed=11, timeframe=M5)
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, {"length": 5})
    for candle in candles:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    blob = plugin.dump_state(state)
    restored = plugin.restore_state(json.loads(json.dumps(blob)))
    assert restored == state
    assert plugin.dump_state(restored) == blob
