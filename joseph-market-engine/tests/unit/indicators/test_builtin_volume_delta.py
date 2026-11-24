# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the built-in volume delta study."""

from __future__ import annotations

import dataclasses
import decimal
import json
import pathlib
import random
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.builtin import volume_delta
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib

TF = timeframes.Timeframe
D = decimal.Decimal
ACME = testing.STOCK_INSTRUMENT
Validate: TypeAlias = Callable[[str, str, object], None]
Intrabars: TypeAlias = dict[int, dict[TF, tuple[market.Candle, ...]]]
_PLUGIN_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[4]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
    / "indicator-plugin.fixture.json"
)


def _with_buy_volume(
    candles: Sequence[market.Candle],
    share: str,
    method: market.BuyVolumeMethod | None = None,
) -> list[market.Candle]:
    """Adds a buy volume of ``share`` x volume to candles.

    Args:
        candles: Source candles.
        share: Fraction of the volume bought, a decimal string.
        method: How the buy volume was obtained (taker when None).

    Returns:
        Copies of the candles carrying ``taker_buy_base_volume``.
    """
    return [
        dataclasses.replace(
            candle,
            taker_buy_base_volume=candle.base_volume * D(share),
            taker_buy_method=method,
        )
        for candle in candles
    ]


def _registry() -> registry_lib.PluginRegistry:
    """Builds a registry holding only the volume delta study.

    Returns:
        The registry.
    """
    registry = registry_lib.PluginRegistry()
    registry.register(volume_delta.VolumeDeltaPlugin())
    return registry


def test_info_matches_the_contract_fixture(contract_validate: Validate) -> None:
    """The descriptor validates and equals the shared contract example."""
    plugin = volume_delta.VolumeDeltaPlugin()
    assert metadata.validate(plugin) == []
    info = plugin.info()
    contract_validate("indicators.schema.json", "IndicatorPluginInfo", info)
    fixture = json.loads(_PLUGIN_FIXTURES.read_text(encoding="utf-8"))
    assert info == fixture["valid"][2]


def test_true_delta_uses_taker_volume_and_is_labelled_trade() -> None:
    """Bars with taker volume publish true delta, never as parity output."""
    candles = _with_buy_volume(
        testing.random_walk(30, timeframe=TF.M5, seed=3), "0.75"
    )
    snapshot, _ = testing.run_bars(volume_delta.VolumeDeltaPlugin(), candles)[
        -1
    ]
    volume = candles[-1].base_volume
    assert snapshot.values["method"] == "taker_volume"
    assert snapshot.method is structures.IndicatorMethod.TRADE
    assert snapshot.values["delta_pct"] == D(50)
    assert snapshot.values["buy_volume"] == volume * D("0.75")
    assert snapshot.values["sell_volume"] == volume * D("0.25")
    assert snapshot.values["delta"] == volume * D("0.5")
    assert snapshot.values["coverage_pct"] == D(100)
    assert snapshot.values["intrabar_count"] == 0
    assert snapshot.data_quality.coverage_pct == "100"
    assert snapshot.to_contract()["method"] == "trade"


def test_estimate_setting_ignores_taker_volume() -> None:
    """``delta_source = estimate`` never reads the bar's buy volume."""
    candles = _with_buy_volume(
        testing.random_walk(30, timeframe=TF.M5, seed=3), "0.75"
    )
    snapshot, _ = testing.run_bars(
        volume_delta.VolumeDeltaPlugin(),
        candles,
        {"delta_source": "estimate"},
    )[-1]
    assert snapshot.values["method"] == "candle_direction"
    assert snapshot.method is structures.IndicatorMethod.PINE_PARITY
    assert snapshot.values["delta_pct"] is None


def test_bars_without_taker_volume_fall_back_to_the_estimate() -> None:
    """Missing taker volume never fabricates a true delta."""
    candles = testing.random_walk(30, timeframe=TF.M5, seed=3)
    snapshot, _ = testing.run_bars(volume_delta.VolumeDeltaPlugin(), candles)[
        -1
    ]
    assert snapshot.values["method"] == "candle_direction"
    assert snapshot.values["intrabar_count"] == 0
    assert snapshot.values["buy_volume"] is None
    assert snapshot.values["delta_pct"] is None
    assert snapshot.values["coverage_pct"] == D(0)


def test_classified_delta_is_labelled_trade_classification(
    contract_validate: Validate,
) -> None:
    """Classified buy volume is never presented as true taker delta."""
    candles = _with_buy_volume(
        testing.random_walk(
            30,
            timeframe=TF.M5,
            seed=3,
            instrument_id=ACME,
            start_ms=testing.STOCK_START_MS,
        ),
        "0.75",
        market.BuyVolumeMethod.CLASSIFIED,
    )
    snapshot, _ = testing.run_bars(volume_delta.VolumeDeltaPlugin(), candles)[
        -1
    ]
    assert snapshot.values["method"] == "trade_classification"
    assert snapshot.method is structures.IndicatorMethod.TRADE_CLASSIFICATION
    assert snapshot.values["delta_pct"] == D(50)
    contract = snapshot.to_contract()
    assert contract["method"] == "trade_classification"
    contract_validate("indicators.schema.json", "IndicatorSnapshot", contract)


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
    plugin = volume_delta.VolumeDeltaPlugin()
    streams = plugin.required_streams(None, timeframe=chart)
    expected = {protocol.CHART_STREAM}
    if intrabar is not None:
        expected.add(protocol.intrabar_stream(intrabar))
    assert streams == frozenset(expected)


def test_descriptor_and_explicit_intrabars() -> None:
    """The descriptor names 1m intrabars; an explicit choice wins."""
    plugin = volume_delta.VolumeDeltaPlugin()
    assert plugin.required_streams(None) == frozenset(
        {protocol.CHART_STREAM, "intrabars:1m"}
    )
    assert plugin.required_streams(
        {"intrabar_tf": "15s"}, timeframe=TF.M1
    ) == frozenset({protocol.CHART_STREAM, "intrabars:15s"})


@pytest.mark.parametrize(
    "instrument", [testing.DEFAULT_INSTRUMENT, ACME], ids=["24x7", "XNYS"]
)
def test_engine_accepts_every_chart_timeframe(
    instrument: instruments.InstrumentId,
) -> None:
    """Auto intrabars always nest, so no chart timeframe is refused."""
    registry = _registry()
    for chart in TF:
        engine_lib.IndicatorEngine(
            registry,
            engine_lib.EngineConfig(
                chart, {"volume_delta": engine_lib.PluginSpec((chart,))}
            ),
            instrument,
        )


def _intrabar_walk(seed: int, minutes: int) -> list[market.Candle]:
    """Builds 1m bars with many flat candles and flat closes.

    Args:
        seed: Random seed.
        minutes: Number of bars.

    Returns:
        1m candles whose moves are often zero, so the direction carries.
    """
    rng = random.Random(seed)
    rows = []
    price = 100
    for _ in range(minutes):
        open_ = price if rng.random() < 0.7 else price + rng.choice((-1, 1))
        close = open_ + rng.choice((-2, -1, 0, 0, 0, 1, 2))
        rows.append(
            (
                open_,
                max(open_, close) + 1,
                min(open_, close) - 1,
                close,
                rng.randint(1, 40),
            )
        )
        price = close
    return testing.candles_from_rows(rows, TF.M1)


def _sign(value: decimal.Decimal) -> int:
    """Returns the sign of a decimal.

    Args:
        value: Number.

    Returns:
        1, -1 or 0.
    """
    return (value > 0) - (value < 0)


def _reference(
    chart: Sequence[market.Candle],
    intrabars: Mapping[int, Sequence[market.Candle]],
) -> list[dict[str, decimal.Decimal | int | None]]:
    """Recomputes the default estimate bar by bar in exact decimals.

    Args:
        chart: Chart bars.
        intrabars: 1m intrabars per chart bar index.

    Returns:
        Expected ``delta_pct``, ``buy_volume``, ``sell_volume``,
        ``coverage_pct`` and ``intrabar_count`` per bar.
    """
    last_close: decimal.Decimal | None = None
    direction = 0
    expected = []
    for index, candle in enumerate(chart):
        buy = sell = covered = D(0)
        subs = intrabars.get(index, ())
        for sub in subs:
            current = _sign(sub.close - sub.open)
            if current == 0 and last_close is not None:
                current = _sign(sub.close - last_close)
            direction = current or direction
            if direction > 0:
                buy += sub.base_volume
            elif direction < 0:
                sell += sub.base_volume
            covered += sub.base_volume
            last_close = sub.close
        volume = candle.base_volume
        coverage = covered / volume if volume > 0 else None
        sufficient = (
            coverage is not None and len(subs) >= 5 and coverage >= D("0.8")
        )
        expected.append(
            {
                "delta_pct": (buy - sell) / covered * 100
                if sufficient
                else None,
                "buy_volume": buy if sufficient else None,
                "sell_volume": sell if sufficient else None,
                "coverage_pct": None if coverage is None else coverage * 100,
                "intrabar_count": len(subs),
            }
        )
    return expected


def _close(
    got: object, want: decimal.Decimal | int | None, tolerance: str
) -> bool:
    """Compares a published value with a reference value.

    Args:
        got: Published value.
        want: Exact reference.
        tolerance: Largest allowed difference, a decimal string.

    Returns:
        True when both are None or they differ by at most ``tolerance``.
    """
    if want is None or got is None:
        return got is None and want is None
    assert isinstance(got, (decimal.Decimal, int))
    return abs(D(got) - D(want)) <= D(tolerance)


def test_estimate_matches_a_decimal_reference_bar_by_bar() -> None:
    """Every bar's estimate equals an exact recompute within 0.01."""
    minutes = _intrabar_walk(seed=5, minutes=1_500)
    chart = testing.aggregate(minutes, TF.M5)
    rng = random.Random(9)
    intrabars: Intrabars = {}
    by_index: dict[int, list[market.Candle]] = {}
    for index, candle in enumerate(chart):
        subs = [
            m for m in minutes if candle.start_ms <= m.start_ms < candle.end_ms
        ]
        if rng.random() < 0.15:
            del subs[rng.randrange(len(subs))]
        by_index[index] = subs
        intrabars[index] = {TF.M1: tuple(subs)}
    results = testing.run_bars(
        volume_delta.VolumeDeltaPlugin(), chart, intrabars=intrabars
    )
    expected = _reference(chart, by_index)
    nulls = 0
    for index, ((snapshot, _), want) in enumerate(
        zip(results, expected, strict=True)
    ):
        values = snapshot.values
        assert values["method"] == "candle_direction"
        assert values["intrabar_count"] == want["intrabar_count"], index
        assert _close(values["delta_pct"], want["delta_pct"], "0.01"), index
        assert _close(values["coverage_pct"], want["coverage_pct"], "0.01")
        assert _close(values["buy_volume"], want["buy_volume"], "1e-8")
        assert _close(values["sell_volume"], want["sell_volume"], "1e-8")
        nulls += values["delta_pct"] is None
    assert 0 < nulls < len(chart)


def _bar(
    start_ms: int,
    intrabar_rows: Sequence[tuple[int, int, int, int, int]],
    buy: str | None = None,
) -> tuple[market.Candle, tuple[market.Candle, ...]]:
    """Builds a 5m bar from its 1m intrabars.

    Args:
        start_ms: Bar start.
        intrabar_rows: ``(open, high, low, close, volume)`` of each 1m bar.
        buy: Optional taker buy volume of the 5m bar.

    Returns:
        The 5m bar (its volume is the intrabars' total) and its intrabars.
    """
    subs = tuple(testing.candles_from_rows(intrabar_rows, TF.M1, start_ms))
    candle = testing.make_candle(
        TF.M5,
        start_ms,
        subs[0].open,
        max(sub.high for sub in subs),
        min(sub.low for sub in subs),
        subs[-1].close,
        sum((sub.base_volume for sub in subs), D(0)),
    )
    if buy is not None:
        candle = dataclasses.replace(candle, taker_buy_base_volume=D(buy))
    return candle, subs


def test_direction_carries_across_chart_bars() -> None:
    """A flat first intrabar takes the last direction of the bar before.

    Bar 0 uses true delta (it carries a buy volume) yet its intrabars still
    set the carried direction: its last intrabar falls, so bar 1's flat
    first intrabar at the same close counts as selling.
    """
    start = testing.DEFAULT_START_MS
    first = _bar(
        start,
        [(10, 11, 9, 11, 5)] * 4 + [(11, 11, 9, 10, 5)],
        buy="20",
    )
    second = _bar(
        start + 300_000,
        [(10, 10, 10, 10, 30)] + [(10, 11, 9, 11, 10)] * 4,
    )
    plugin = volume_delta.VolumeDeltaPlugin()
    results = testing.run_bars(
        plugin,
        [first[0], second[0]],
        intrabars={0: {TF.M1: first[1]}, 1: {TF.M1: second[1]}},
    )
    assert results[0][0].values["method"] == "taker_volume"
    values = results[1][0].values
    assert values["method"] == "candle_direction"
    assert values["sell_volume"] == D(30)
    assert values["buy_volume"] == D(40)
    assert values["delta_pct"] == D("14.29")
    alone = testing.run_bars(
        plugin, [second[0]], intrabars={0: {TF.M1: second[1]}}
    )
    assert alone[0][0].values["sell_volume"] == D(0)


def test_too_few_intrabars_or_no_volume_publish_no_delta() -> None:
    """Insufficient intrabars null the delta values but explain why."""
    plugin = volume_delta.VolumeDeltaPlugin()
    start = testing.DEFAULT_START_MS
    few, few_subs = _bar(start, [(10, 11, 9, 11, 5)] * 4)
    values = testing.run_bars(plugin, [few], intrabars={0: {TF.M1: few_subs}})[
        0
    ][0].values
    assert values["intrabar_count"] == 4
    assert values["coverage_pct"] == D(100)
    assert values["delta_pct"] is None
    assert values["delta"] is None
    assert values["buy_volume"] is None
    assert values["sell_volume"] is None
    lowered = testing.run_bars(
        plugin, [few], {"min_intrabars": 4}, intrabars={0: {TF.M1: few_subs}}
    )[0][0].values
    assert lowered["delta_pct"] == D(100)
    empty, empty_subs = _bar(start, [(10, 10, 10, 10, 0)] * 5)
    values = testing.run_bars(
        plugin, [empty], intrabars={0: {TF.M1: empty_subs}}
    )[0][0].values
    assert values["coverage_pct"] is None
    assert values["delta_pct"] is None


def test_snapshots_conform_to_the_contract(contract_validate: Validate) -> None:
    """Engine snapshots of the estimate validate against the schema."""
    minutes = testing.random_walk(300, seed=8)
    data = {TF.M1: minutes, TF.M5: testing.aggregate(minutes, TF.M5)}
    engine = engine_lib.IndicatorEngine(
        _registry(),
        engine_lib.EngineConfig(
            TF.M5, {"volume_delta": engine_lib.PluginSpec((TF.M5,))}
        ),
        testing.DEFAULT_INSTRUMENT,
    )
    frames = engine.run(data)
    assert len(frames) == len(data[TF.M5])
    for frame in frames[::5]:
        snapshot = frame.snapshot("volume_delta", TF.M5)
        assert snapshot is not None
        assert snapshot.values["intrabar_count"] == 5
        assert snapshot.values["delta_pct"] is not None
        contract_validate(
            "indicators.schema.json",
            "IndicatorSnapshot",
            snapshot.to_contract(),
        )
