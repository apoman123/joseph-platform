# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the built-in pivot points study."""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib
import zoneinfo
from collections.abc import Callable
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.builtin import pivot_points
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib

TF = timeframes.Timeframe
D = decimal.Decimal
ACME = testing.STOCK_INSTRUMENT
NY = zoneinfo.ZoneInfo("America/New_York")
TICK = D("0.01")
LEVELS = ("p", "r1", "r2", "r3", "s1", "s2", "s3")
Validate: TypeAlias = Callable[[str, str, object], None]
_PLUGIN_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[4]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
    / "indicator-plugin.fixture.json"
)


def _ny(day: int, hour: int, minute: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time in November 2026.

    Args:
        day: Day of the month.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(2026, 11, day, hour, minute, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _reference(candle: market.Candle) -> dict[str, decimal.Decimal]:
    """Recomputes the floor levels of a period bar in exact decimals.

    Args:
        candle: Completed period bar.

    Returns:
        Level by value key.
    """
    high, low, close = candle.high, candle.low, candle.close
    pivot = (high + low + close) / 3
    return {
        "p": pivot,
        "r1": 2 * pivot - low,
        "r2": pivot + (high - low),
        "r3": high + 2 * (pivot - low),
        "s1": 2 * pivot - high,
        "s2": pivot - (high - low),
        "s3": low - 2 * (high - pivot),
    }


def _engine(
    instrument: instruments.InstrumentId,
    chart: timeframes.Timeframe,
    settings: dict[str, object] | None = None,
) -> engine_lib.IndicatorEngine:
    """Builds an engine running only the pivot points study.

    Args:
        instrument: Instrument.
        chart: Chart timeframe.
        settings: Setting overrides.

    Returns:
        The engine.
    """
    registry = registry_lib.PluginRegistry()
    registry.register(pivot_points.PivotPointsPlugin())
    return engine_lib.IndicatorEngine(
        registry,
        engine_lib.EngineConfig(
            chart,
            {
                "pivot_points": engine_lib.PluginSpec(
                    (chart,), {"tick_size": "0.01", **(settings or {})}
                )
            },
        ),
        instrument,
    )


def _check_frames(
    frames: Sequence[structures.BarFrame],
    periods: Sequence[market.Candle],
    calendar: sessions.BarCalendar,
) -> int:
    """Checks every frame's levels against the visible period bars.

    The expected levels come from the latest period bar that closed at or
    before the frame and whose next period started by the frame's close.

    Args:
        frames: Engine frames, oldest first.
        periods: Completed period bars, oldest first.
        calendar: Instrument calendar.

    Returns:
        Number of frames that published levels.
    """
    published = 0
    for frame in frames:
        snapshot = frame.snapshot("pivot_points", frame.timeframe)
        assert snapshot is not None
        due = [
            period
            for period in periods
            if period.end_ms <= frame.close_ms
            and calendar.shift(period.timeframe, period.start_ms, 1)
            <= frame.close_ms
        ]
        values = snapshot.values
        if not due:
            assert all(values[key] is None for key in LEVELS)
            assert not snapshot.aois
            continue
        published += 1
        source = due[-1]
        applies = calendar.shift(source.timeframe, source.start_ms, 1)
        assert values["period_start_ms"] == applies
        expected = _reference(source)
        for key in LEVELS:
            got = values[key]
            assert isinstance(got, D)
            assert abs(got - expected[key]) <= TICK, (key, frame.close_ms)
        live = [aoi for aoi in snapshot.aois if aoi.state.is_live]
        assert [aoi.kind for aoi in live] == list(
            pivot_points.PivotPointsPlugin.OUTPUT_KINDS
        )
        for aoi in live:
            assert aoi.created_at_ms == applies
            assert applies <= aoi.confirmed_at_ms <= frame.close_ms
            assert aoi.lower == aoi.upper
    return published


def test_info_matches_the_contract_fixture(contract_validate: Validate) -> None:
    """The descriptor validates and equals the shared contract example."""
    plugin = pivot_points.PivotPointsPlugin()
    assert metadata.validate(plugin) == []
    info = plugin.info()
    contract_validate("indicators.schema.json", "IndicatorPluginInfo", info)
    fixture = json.loads(_PLUGIN_FIXTURES.read_text(encoding="utf-8"))
    assert info == fixture["valid"][3]


def test_streams_follow_the_period() -> None:
    """The plugin reads the latest completed bar of its period."""
    plugin = pivot_points.PivotPointsPlugin()
    assert plugin.required_streams(None) == frozenset(
        {protocol.CHART_STREAM, "aux:1d"}
    )
    weekly = {"period": "1w"}
    assert plugin.required_streams(weekly) == frozenset(
        {protocol.CHART_STREAM, "aux:1w"}
    )
    assert plugin.lookback(weekly, "aux:1w") == 1
    assert plugin.lookback(weekly, "aux:1d") == 0


@pytest.mark.parametrize(
    ("chart", "period"),
    [(TF.D1, "1d"), (TF.W1, "1d"), (TF.W1, "1w")],
)
def test_chart_at_or_above_the_period_is_refused(
    chart: timeframes.Timeframe, period: str
) -> None:
    """Levels of a period are meaningless on a chart that long."""
    plugin = pivot_points.PivotPointsPlugin()
    with pytest.raises(errors.ValidationFailedError, match="shorter"):
        plugin.initialize(testing.DEFAULT_INSTRUMENT, chart, {"period": period})
    with pytest.raises(errors.ValidationFailedError):
        _engine(testing.DEFAULT_INSTRUMENT, chart, {"period": period})


def test_seconds_and_monthly_charts_are_unsupported() -> None:
    """Only minute to weekly charts are offered."""
    timeframes_ = pivot_points.PivotPointsPlugin().timeframes
    assert timeframes_[0] is TF.M1
    assert timeframes_[-1] is TF.W1
    assert TF.MN1 not in timeframes_


def test_levels_match_a_decimal_reference_on_a_24x7_market(
    contract_validate: Validate,
) -> None:
    """Hourly frames carry the previous UTC day's levels from its close."""
    hours = testing.random_walk(24 * 6, seed=4, timeframe=TF.H1)
    days = testing.aggregate(hours, TF.D1)
    frames = _engine(testing.DEFAULT_INSTRUMENT, TF.H1).run(
        {TF.H1: hours, TF.D1: days}
    )
    assert len(frames) == len(hours)
    published = _check_frames(frames, days, sessions.CONTINUOUS)
    assert published == len(hours) - 23
    # The bar closing with the first day publishes the next day's levels.
    first = frames[23].snapshot("pivot_points", TF.H1)
    assert first is not None
    assert first.values["period_start_ms"] == frames[23].close_ms
    assert all(a.confirmed_at_ms == frames[23].close_ms for a in first.aois)
    for frame in frames[::11]:
        snapshot = frame.snapshot("pivot_points", TF.H1)
        assert snapshot is not None
        contract = snapshot.to_contract()
        contract_validate(
            "indicators.schema.json", "IndicatorSnapshot", contract
        )
        for aoi in snapshot.aois:
            contract_validate(
                "indicators.schema.json", "AoiRecord", aoi.to_contract()
            )


def test_session_levels_appear_at_the_next_open_on_xnys() -> None:
    """Exchange levels wait for the session they apply to."""
    hours = testing.random_walk(
        7 * 5,
        seed=6,
        timeframe=TF.H1,
        instrument_id=ACME,
        start_ms=testing.STOCK_START_MS,
    )
    days = testing.aggregate(hours, TF.D1)
    assert len(days) == 5
    frames = _engine(ACME, TF.H1).run({TF.H1: hours, TF.D1: days})
    published = _check_frames(frames, days, sessions.XNYS)
    assert published == 7 * 4
    closing = frames[6].snapshot("pivot_points", TF.H1)
    assert closing is not None
    assert frames[6].close_ms == _ny(16, 16)
    assert closing.values["p"] is None
    opening = frames[7].snapshot("pivot_points", TF.H1)
    assert opening is not None
    assert opening.values["period_start_ms"] == _ny(17, 9, 30)
    assert {aoi.confirmed_at_ms for aoi in opening.aois} == {_ny(17, 10, 30)}


def test_weekly_and_monthly_periods() -> None:
    """Longer periods use the calendar's week and month bars."""
    days = testing.random_walk(
        70, seed=2, timeframe=TF.D1, start_ms=1_793_491_200_000
    )
    for period, tf in (("1w", TF.W1), ("1M", TF.MN1)):
        bars = testing.aggregate(days, tf)
        frames = _engine(testing.DEFAULT_INSTRUMENT, TF.D1, {"period": period})
        published = _check_frames(
            frames.run({TF.D1: days, tf: bars}), bars, sessions.CONTINUOUS
        )
        assert published > 0


def _level_run(
    rows: Sequence[tuple[int, int, int, int]],
    periods: dict[int, market.Candle],
) -> list[tuple[structures.IndicatorSnapshot, protocol.PluginOutput]]:
    """Runs 6h bars with the given completed day bars.

    Args:
        rows: ``(open, high, low, close)`` of each 6h bar.
        periods: Latest completed day bar visible at each bar index.

    Returns:
        ``(snapshot, output)`` after each bar.
    """
    candles = testing.candles_from_rows(rows, TF.H6)
    return testing.run_bars(
        pivot_points.PivotPointsPlugin(),
        candles,
        {"tick_size": "0.01"},
        aux={index: {TF.D1: (day,)} for index, day in periods.items()},
    )


def test_levels_are_tested_once_then_expire_with_the_next_period() -> None:
    """A touch marks a level tested; the next levels expire all of them."""
    start = testing.DEFAULT_START_MS
    day = 86_400_000
    first = testing.make_candle(TF.D1, start, 100, 112, 88, 103)
    second = testing.make_candle(TF.D1, start + day, 103, 120, 90, 111)
    rows = [(100, 112, 98, 108)] * 4 + [
        (103, 105, 102, 104),
        (104, 105, 90, 96),
        (96, 101, 95, 100),
        (100, 104, 99, 103),
    ]
    results = _level_run(
        rows, {3: first, 4: first, 5: first, 6: first, 7: second}
    )
    published, output = results[3]
    assert {aoi.state for aoi in published.aois} == {structures.AoiState.ACTIVE}
    assert len(output.updated_aois) == 7
    untouched, output = results[4]
    assert output.updated_aois == ()
    assert untouched.aois == published.aois
    touched, output = results[5]
    by_kind = {aoi.kind: aoi for aoi in touched.aois}
    assert by_kind["PIVOT_P"].lower == D(101)
    assert by_kind["PIVOT_P"].state is structures.AoiState.TESTED
    assert by_kind["PIVOT_S1"].state is structures.AoiState.TESTED
    assert by_kind["PIVOT_S2"].state is structures.AoiState.ACTIVE
    assert {aoi.kind for aoi in output.updated_aois} == {"PIVOT_P", "PIVOT_S1"}
    again, output = results[6]
    assert all(aoi.kind != "PIVOT_P" for aoi in output.updated_aois)
    assert {a.kind: a.state for a in again.aois}["PIVOT_P"] is (
        structures.AoiState.TESTED
    )
    rolled, output = results[7]
    ended = [aoi for aoi in rolled.aois if not aoi.state.is_live]
    assert len(ended) == 7
    assert all(aoi.state is structures.AoiState.EXPIRED for aoi in ended)
    assert all(aoi.invalidated_at_ms == rolled.as_of_ms for aoi in ended)
    assert {a.kind: a.lower for a in rolled.aois if a.state.is_live}[
        "PIVOT_P"
    ] == D(107)
    assert len(output.updated_aois) == 14
    final, _ = testing.run_bars(
        pivot_points.PivotPointsPlugin(),
        testing.candles_from_rows([*rows, (103, 104, 102, 103)], TF.H6),
        {"tick_size": "0.01"},
        aux={7: {TF.D1: (second,)}, 8: {TF.D1: (second,)}},
    )[-1]
    assert all(aoi.state.is_live for aoi in final.aois)


def test_aoi_ids_are_stable_and_distinct_per_period() -> None:
    """The same period and kind always yield the same AOI id."""
    start = testing.DEFAULT_START_MS
    day = testing.make_candle(TF.D1, start, 100, 112, 88, 103)
    rows = [(100, 112, 98, 108)] * 4
    one = _level_run(rows, {3: day})[-1][0]
    two = _level_run(rows, {3: day})[-1][0]
    assert [a.aoi_id for a in one.aois] == [a.aoi_id for a in two.aois]
    assert len({a.aoi_id for a in one.aois}) == 7
