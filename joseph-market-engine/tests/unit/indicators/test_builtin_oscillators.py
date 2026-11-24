# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the RSI and ATR built-ins.

Each study is compared bar by bar with a slow recomputation in
``decimal.Decimal`` (Wilder's moving average seeded with a simple average)
from the candles alone.
"""

from __future__ import annotations

import decimal
import itertools
import json
import pathlib
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import market
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.builtin import _series
from joseph.indicators.builtin import oscillators
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

_RSI_TOLERANCE = D("0.0001")
"""Largest accepted gap of the RSI, published with four places."""

_CONTRACT_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[4]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
    / "indicator-plugin.fixture.json"
)


def _rma(values: Sequence[D | None], length: int) -> Reference:
    """Computes Wilder's moving average exactly.

    Args:
        values: Series, ``None`` where it is undefined (leading bars only).
        length: Smoothing length.

    Returns:
        The average per bar: the simple average of the first ``length``
        defined values, then ``x / length + (1 - 1 / length) * rma[1]``.
    """
    alpha = D(1) / length
    seed: list[D] = []
    average: D | None = None
    result: Reference = []
    for value in values:
        if value is not None:
            if average is not None:
                average = alpha * value + (1 - alpha) * average
            else:
                seed.append(value)
                if len(seed) == length:
                    average = sum(seed, D(0)) / length
        result.append(average)
    return result


def _rsi_reference(sources: Sequence[D], length: int) -> Reference:
    """Computes the RSI exactly.

    Args:
        sources: Source price per bar.
        length: Smoothing length.

    Returns:
        The RSI per bar, ``None`` before ``length + 1`` bars.
    """
    ups: list[D | None] = [None]
    downs: list[D | None] = [None]
    for previous, price in itertools.pairwise(sources):
        ups.append(max(price - previous, D(0)))
        downs.append(max(previous - price, D(0)))
    result: Reference = []
    for up, down in zip(_rma(ups, length), _rma(downs, length), strict=True):
        if up is None or down is None:
            result.append(None)
        elif down == 0:
            result.append(D(100))
        elif up == 0:
            result.append(D(0))
        else:
            result.append(100 - 100 / (1 + up / down))
    return result


def _true_ranges(candles: Sequence[market.Candle]) -> list[D | None]:
    """Computes the true range of every bar exactly.

    Args:
        candles: Bars.

    Returns:
        ``high - low`` for the first bar, then the largest of ``high -
        low`` and the distances of high and low from the previous close.
    """
    ranges: list[D | None] = []
    previous: D | None = None
    for candle in candles:
        spread = candle.high - candle.low
        if previous is not None:
            spread = max(
                spread, abs(candle.high - previous), abs(candle.low - previous)
            )
        ranges.append(spread)
        previous = candle.close
    return ranges


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


def _source(candle: market.Candle, name: str) -> D:
    """Computes a price source exactly.

    Args:
        candle: Bar.
        name: ``close`` or ``hl2``.

    Returns:
        The source price as a decimal.
    """
    if name == "hl2":
        return (candle.high + candle.low) / 2
    return candle.close


def _final_rsi(closes: Iterable[int]) -> object:
    """Runs a 3-bar RSI over flat bars at the given closes.

    Args:
        closes: Close of each bar.

    Returns:
        The RSI published after the last bar.
    """
    candles = testing.candles_from_rows([(c, c, c, c) for c in closes])
    results = testing.run_bars(oscillators.RsiPlugin(), candles, {"length": 3})
    return results[-1][0].values["rsi"]


@pytest.mark.parametrize(
    ("length", "source"), [(1, "close"), (2, "hl2"), (14, "close")]
)
def test_rsi_matches_the_exact_recursion(length: int, source: str) -> None:
    """The RSI equals its decimal recomputation within the last place."""
    candles = testing.random_walk(160, seed=length + 3, timeframe=M5)
    sources = [_source(candle, source) for candle in candles]
    published = [
        snapshot.values["rsi"]
        for snapshot, _ in testing.run_bars(
            oscillators.RsiPlugin(),
            candles,
            {"length": length, "source": source},
        )
    ]
    _assert_close(published, _rsi_reference(sources, length), _RSI_TOLERANCE)


@pytest.mark.parametrize("length", [1, 5, 14])
def test_atr_matches_the_exact_recursion(length: int) -> None:
    """The ATR equals its decimal recomputation within the price quantum."""
    candles = testing.random_walk(160, seed=length + 9, timeframe=M5)
    published = [
        snapshot.values["atr"]
        for snapshot, _ in testing.run_bars(
            oscillators.AtrPlugin(),
            candles,
            {"length": length, "tick_size": "0.01"},
        )
    ]
    _assert_close(
        published, _rma(_true_ranges(candles), length), _PRICE_TOLERANCE
    )


def test_rsi_first_value_needs_length_plus_one_bars() -> None:
    """The first bar has no change, so the RSI waits one extra bar."""
    candles = testing.random_walk(6, seed=2, timeframe=M5)
    results = testing.run_bars(oscillators.RsiPlugin(), candles, {"length": 4})
    assert [s.values["rsi"] is None for s, _ in results] == [
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


def test_rsi_is_100_without_losses_and_0_without_gains() -> None:
    """One-way series pin the RSI to its bounds; no change at all is 100."""
    prices = range(10, 15)
    assert _final_rsi(prices) == D(100)
    assert _final_rsi(reversed(prices)) == D(0)
    assert _final_rsi([5] * 5) == D(100)


def test_atr_first_bar_is_its_range_and_waits_for_length() -> None:
    """The first true range is high minus low; the ATR needs ``length`` bars."""
    candles = testing.candles_from_rows(
        [("10", "13", "9", "11"), ("11", "12", "10", "11")]
    )
    plugin = oscillators.AtrPlugin()
    one = testing.run_bars(plugin, candles, {"length": 1})
    assert [s.values["atr"] for s, _ in one] == [D(4), D(2)]
    two = testing.run_bars(plugin, candles, {"length": 2})
    assert two[0][0].values["atr"] is None
    assert not two[0][0].warmup_complete
    assert two[1][0].values["atr"] == D(3)
    assert two[1][0].warmup_complete


_PLUGINS = (oscillators.RsiPlugin(), oscillators.AtrPlugin())


@pytest.mark.parametrize("plugin", _PLUGINS, ids=lambda p: p.plugin_id)
def test_info_conforms_to_the_contract(
    plugin: base.AnyPlugin, contract_validate: Validate
) -> None:
    """Descriptors validate against the schema and the metadata rules."""
    assert metadata.validate(plugin) == []
    contract_validate(
        "indicators.schema.json", "IndicatorPluginInfo", plugin.info()
    )


def test_rsi_info_matches_the_shared_contract_example() -> None:
    """The RSI descriptor equals the contract package's RSI example."""
    examples = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    rsi = next(info for info in examples["valid"] if info["plugin_id"] == "rsi")
    assert oscillators.RsiPlugin().info() == rsi


@pytest.mark.parametrize("plugin", _PLUGINS, ids=lambda p: p.plugin_id)
def test_checkpoint_restores_an_equal_state(plugin: base.AnyPlugin) -> None:
    """A JSON checkpoint restores to an equal state."""
    candles = testing.random_walk(30, seed=13, timeframe=M5)
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, {"length": 5})
    for candle in candles:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    blob = plugin.dump_state(state)
    restored = plugin.restore_state(json.loads(json.dumps(blob)))
    assert restored == state
    assert plugin.dump_state(restored) == blob
