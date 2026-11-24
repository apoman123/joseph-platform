# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Contract tests: indicator outputs conform to indicators.schema.json."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import clock
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import service
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import registry as registry_lib

TF = timeframes.Timeframe
Validate: TypeAlias = Callable[[str, str, object], None]
SCHEMA = "indicators.schema.json"


class _Source:
    """In-memory candle source."""

    def __init__(self, data: dict[TF, list[market.Candle]]) -> None:
        """Stores candles.

        Args:
            data: Candles per timeframe.
        """
        self._data = data

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: TF,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns stored candles in the window.

        Args:
            instrument_id: Ignored (single instrument).
            timeframe: Timeframe to serve.
            start_ms: Window start, inclusive, epoch ms.
            end_ms: Window end, exclusive, epoch ms.

        Returns:
            The stored candles that start inside the window.
        """
        del instrument_id
        return [
            c
            for c in self._data.get(timeframe, [])
            if start_ms <= c.start_ms < end_ms
        ]


def _registry() -> registry_lib.PluginRegistry:
    """Builds the built-ins plus the test plugins.

    Returns:
        The registry.
    """
    registry = catalog.build_default_registry()
    registry.register(testing.EchoPlugin())
    registry.register(testing.ZoneEchoPlugin())
    return registry


@pytest.fixture(scope="module")
def frames() -> list[structures.BarFrame]:
    """Runs every plugin on 5m, and the echo plugin also on 1h.

    Returns:
        The 5m frames of 2,400 synthetic minutes (a complete day, so the
        pivot points publish).
    """
    m1 = testing.random_walk(2400, seed=17)
    data = {
        tf: testing.aggregate(m1, tf) if tf is not TF.M1 else m1
        for tf in (TF.M1, TF.M5, TF.H1, TF.H4, TF.D1)
    }
    registry = _registry()
    plugins = {pid: engine_lib.PluginSpec((TF.M5,)) for pid in registry.ids()}
    plugins["echo"] = engine_lib.PluginSpec((TF.M5, TF.H1))
    engine = engine_lib.IndicatorEngine(
        registry,
        engine_lib.EngineConfig(TF.M5, plugins),
        testing.DEFAULT_INSTRUMENT,
    )
    return engine.run(data)


def test_snapshots_conform(
    contract_validate: Validate, frames: list[structures.BarFrame]
) -> None:
    """Snapshots of every plugin, with nested AOIs and events, validate."""
    seen = set()
    for frame in frames[::7] + frames[-1:]:
        for key, snapshot in frame.snapshots.items():
            contract_validate(
                SCHEMA, "IndicatorSnapshot", snapshot.to_contract()
            )
            seen.add(key[0])
    assert len(seen) == 11


def test_every_emitted_aoi_and_event_conforms(
    contract_validate: Validate, frames: list[structures.BarFrame]
) -> None:
    """Events and last-frame AOIs validate; every declared kind appears."""
    kinds = set()
    names = set()
    for frame in frames:
        for event in frame.events:
            contract_validate(SCHEMA, "StructureEvent", event.to_contract())
            names.add(event.name)
        for snapshot in frame.snapshots.values():
            for aoi in snapshot.aois:
                kinds.add(aoi.kind)
                contract_validate(SCHEMA, "AoiRecord", aoi.to_contract())
    for snapshot in frames[-1].snapshots.values():
        for aoi in snapshot.aois:
            contract_validate(SCHEMA, "AoiRecord", aoi.to_contract())
    assert kinds == {
        "PIVOT_P",
        "PIVOT_R1",
        "PIVOT_R2",
        "PIVOT_R3",
        "PIVOT_S1",
        "PIVOT_S2",
        "PIVOT_S3",
        "ECHO_ZONE",
    }
    assert names == {"ECHO_UP", "ECHO_DOWN", "AOI_EXPIRED"}


def test_plugin_infos_conform(contract_validate: Validate) -> None:
    """Plugin descriptors and the list envelope validate."""
    registry = _registry()
    listing = registry.list_infos()
    contract_validate(SCHEMA, "IndicatorPluginList", listing)
    for info in registry.infos(include_unavailable=True):
        contract_validate(SCHEMA, "IndicatorPluginInfo", info)
        assert info["licensing_status"] == "CLEAN_ROOM_FROM_SPEC"
        assert info["parity_status"] == "SPEC_ONLY"


def test_query_service_outputs_conform(contract_validate: Validate) -> None:
    """IndicatorSnapshotList, AoiList and IndicatorSeries validate."""
    m1 = testing.random_walk(1800, seed=23)
    data = {
        tf: testing.aggregate(m1, tf) if tf is not TF.M1 else m1
        for tf in (TF.M1, TF.M5, TF.H1, TF.H4, TF.D1)
    }
    query = service.IndicatorQueryService(
        _Source(data),
        catalog.build_default_registry(),
        clock.ManualClock(m1[-1].end_ms),
    )
    snapshots = asyncio.run(query.snapshots(testing.DEFAULT_INSTRUMENT, TF.M5))
    contract_validate(SCHEMA, "IndicatorSnapshotList", snapshots)
    aois = asyncio.run(query.aois(testing.DEFAULT_INSTRUMENT, TF.M5))
    contract_validate(SCHEMA, "AoiList", aois)
    series = asyncio.run(
        query.series(
            testing.DEFAULT_INSTRUMENT,
            TF.M5,
            "volume_delta",
            settings={"min_intrabars": 3},
        )
    )
    contract_validate(SCHEMA, "IndicatorSeries", series)
    assert series["points"], "warm series must publish points"
    tuned = asyncio.run(
        query.aois(
            testing.DEFAULT_INSTRUMENT,
            TF.M5,
            ["pivot_points"],
            settings={"pivot_points": {"tick_size": "0.5"}},
        )
    )
    contract_validate(SCHEMA, "AoiList", tuned)


def test_series_points_have_intrabar_data_across_the_window() -> None:
    """Series fetch intrabars for every bar, not only the recent ones."""
    m1 = testing.random_walk(1800, seed=29)
    data = {
        tf: testing.aggregate(m1, tf) if tf is not TF.M1 else m1
        for tf in (TF.M1, TF.M5)
    }
    query = service.IndicatorQueryService(
        _Source(data),
        catalog.build_default_registry(),
        clock.ManualClock(m1[-1].end_ms),
        service.QueryConfig(intrabar_lookback_bars=10),
    )
    series = asyncio.run(
        query.series(testing.DEFAULT_INSTRUMENT, TF.M5, "volume_delta")
    )
    points = series["points"]
    assert isinstance(points, list) and len(points) > 100
    counts = [point["values"]["intrabar_count"] for point in points]
    assert all(count == 5 for count in counts)
