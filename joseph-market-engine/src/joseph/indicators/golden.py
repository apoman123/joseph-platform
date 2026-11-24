# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Golden fixtures: hand-built scenarios and future Pine parity exports.

Two fixture families live under ``fixtures/indicators/``:

``<plugin_id>/<scenario>.json`` (hand-constructed, checked in now)::

    {"plugin_id": "...", "description": "...", "timeframe": "5m",
     "start_ms": 1794787200000, "settings": {...},
     "bars": [[open, high, low, close, volume], ...],      # strings
     "intrabars": {"<bar index>": {"1m": [[o, h, l, c, v], ...]}},
     "aux": {"<bar index>": {"1h": [[start_ms, o, h, l, c, v], ...]}},
     "expect": {
       "events": [{"bar": 17, "name": "FVG_FORMED", ...subset...}],
       "no_events": ["IFVG_FORMED"],              # never emitted
       "final_values": {"active_count": 1},
       "final_aois": [{"kind": "FVG", "state": "active", ...subset...}]}}

``pine_exports/<venue>/<symbol>/<tf>/<range>.jsonl`` (to be produced from
the pinned private Pine repository once access is granted). Line 1 is a
header ``{"format": "joseph.pine_export.v1", "plugin_id", "pine_commit",
"settings"}``; every following line is one closed bar ``{"bar": [start_ms,
o, h, l, c, v], "events": [names...], "values": {...}}``. The parity
harness compares bar by bar and reports the first divergence.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import cast

from joseph.domain import decimals
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib

PINE_EXPORT_FORMAT = "joseph.pine_export.v1"


@dataclasses.dataclass(frozen=True, slots=True)
class Scenario:
    """A hand-constructed fixture.

    Attributes:
        name: File stem.
        plugin_id: Plugin under test.
        description: What the scenario demonstrates.
        timeframe: Series timeframe.
        settings: Setting overrides.
        candles: Series bars.
        intrabars: Intrabars per bar index.
        aux: Aux bars per bar index.
        expect: Parsed expectations.
    """

    name: str
    plugin_id: str
    description: str
    timeframe: timeframes.Timeframe
    settings: Mapping[str, object]
    candles: tuple[market.Candle, ...]
    intrabars: Mapping[
        int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]
    ]
    aux: Mapping[int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]]
    expect: Expectations


@dataclasses.dataclass(frozen=True, slots=True)
class Expectations:
    """Parsed ``expect`` block of a scenario.

    Attributes:
        events: Expected events (``bar`` index plus contract subset).
        no_events: Event names that must never be emitted.
        final_values: Subset of the final snapshot values.
        final_aois: AOI contract subsets that must exist at the end.
    """

    events: tuple[Mapping[str, Any], ...] = ()
    no_events: frozenset[str] = frozenset()
    final_values: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    final_aois: tuple[Mapping[str, Any], ...] = ()


def load_scenario(path: pathlib.Path) -> Scenario:
    """Parses a scenario fixture file.

    Args:
        path: JSON file.

    Returns:
        The scenario.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    tf = timeframes.parse(body["timeframe"])
    start = int(body.get("start_ms", testing.DEFAULT_START_MS))
    candles = tuple(testing.candles_from_rows(body["bars"], tf, start))
    intrabars: dict[
        int, dict[timeframes.Timeframe, tuple[market.Candle, ...]]
    ] = {}
    for index_text, by_tf in body.get("intrabars", {}).items():
        index = int(index_text)
        parent = candles[index]
        intrabars[index] = {}
        for tf_text, rows in by_tf.items():
            sub_tf = timeframes.parse(tf_text)
            intrabars[index][sub_tf] = tuple(
                testing.candles_from_rows(rows, sub_tf, parent.start_ms)
            )
    aux: dict[int, dict[timeframes.Timeframe, tuple[market.Candle, ...]]] = {}
    for index_text, by_tf in body.get("aux", {}).items():
        index = int(index_text)
        aux[index] = {}
        for tf_text, rows in by_tf.items():
            aux_tf = timeframes.parse(tf_text)
            aux[index][aux_tf] = tuple(
                testing.make_candle(aux_tf, int(row[0]), *row[1:6])
                for row in rows
            )
    return Scenario(
        name=path.stem,
        plugin_id=body["plugin_id"],
        description=body.get("description", ""),
        timeframe=tf,
        settings=body.get("settings", {}),
        candles=candles,
        intrabars=intrabars,
        aux=aux,
        expect=Expectations(
            events=tuple(body["expect"].get("events", [])),
            no_events=frozenset(body["expect"].get("no_events", [])),
            final_values=body["expect"].get("final_values", {}),
            final_aois=tuple(body["expect"].get("final_aois", [])),
        ),
    )


def _matches(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Checks that every expected key equals the actual contract value.

    Decimal strings compare numerically.

    Args:
        actual: Contract dict.
        expected: Subset of expected fields.

    Returns:
        True when all expected fields match.
    """
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, str) and isinstance(got, str):
            try:
                if decimals.to_decimal(want) == decimals.to_decimal(got):
                    continue
            except ValueError:
                pass
        if got != want:
            return False
    return True


def check_scenario(
    scenario: Scenario, registry: registry_lib.PluginRegistry
) -> list[str]:
    """Runs a scenario and lists every unmet expectation.

    Args:
        scenario: Fixture.
        registry: Registry providing the plugin.

    Returns:
        Human-readable failures (empty when the scenario passes).
    """
    plugin = registry.get(scenario.plugin_id)
    results = testing.run_bars(
        plugin,
        scenario.candles,
        scenario.settings,
        intrabars=scenario.intrabars,
        aux=scenario.aux,
    )
    failures: list[str] = []
    expect = scenario.expect
    for want in expect.events:
        index = int(want["bar"])
        fields = {k: v for k, v in want.items() if k != "bar"}
        emitted = [e.to_contract() for e in results[index][1].events]
        if not any(_matches(event, fields) for event in emitted):
            failures.append(
                f"bar {index}: expected event {fields}, got "
                f"{[e['name'] for e in emitted]}"
            )
    for index, (_, output) in enumerate(results):
        for event in output.events:
            if event.name.value in expect.no_events:
                failures.append(f"bar {index}: unexpected {event.name.value}")
    final = results[-1][0].to_contract()
    values = cast(dict[str, object], final["values"])
    for key, want in expect.final_values.items():
        got = values.get(key)
        if not _matches({key: got}, {key: want}):
            failures.append(f"final value {key}: expected {want}, got {got}")
    aois = cast(list[dict[str, object]], final["aois"])
    for want_aoi in expect.final_aois:
        if not any(_matches(aoi, want_aoi) for aoi in aois):
            failures.append(f"final AOI {dict(want_aoi)} not found")
    return failures


def scenario_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Lists scenario fixtures (excluding Pine exports).

    Args:
        root: ``fixtures/indicators`` directory.

    Returns:
        Sorted paths.
    """
    return sorted(
        path
        for path in root.glob("*/*.json")
        if path.parent.name != "pine_exports"
    )


def pine_export_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Lists Pine parity exports.

    Args:
        root: ``fixtures/indicators`` directory.

    Returns:
        Sorted ``.jsonl`` paths (empty until exports exist).
    """
    return sorted((root / "pine_exports").glob("*/*/*/*.jsonl"))


def compare_pine_export(
    path: pathlib.Path, registry: registry_lib.PluginRegistry
) -> list[str]:
    """Compares a Pine export bar by bar with the Python plugin.

    Args:
        path: Export file.
        registry: Registry providing the plugin.

    Returns:
        Divergences (first 20).

    Raises:
        ValueError: If the header format is unknown.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    if header.get("format") != PINE_EXPORT_FORMAT:
        raise ValueError(f"{path}: unknown export format")
    plugin = registry.get(header["plugin_id"])
    tf = timeframes.parse(path.parent.name)
    rows = [json.loads(line) for line in lines[1:] if line.strip()]
    candles = [
        testing.make_candle(tf, int(row["bar"][0]), *row["bar"][1:6])
        for row in rows
    ]
    results = testing.run_bars(plugin, candles, header.get("settings"))
    divergences: list[str] = []
    for index, (row, (snapshot, output)) in enumerate(
        zip(rows, results, strict=True)
    ):
        names = sorted(e.name.value for e in output.events)
        if names != sorted(row.get("events", [])):
            divergences.append(
                f"bar {index}: events {names} != "
                f"{sorted(row.get('events', []))}"
            )
        values = cast(dict[str, object], snapshot.to_contract()["values"])
        for key, want in row.get("values", {}).items():
            got = values.get(key)
            if not _matches({key: got}, {key: want}):
                divergences.append(f"bar {index}: {key} {got} != {want}")
        if len(divergences) >= 20:
            break
    return divergences


def run_snapshots(
    plugin: protocol.IndicatorPlugin,
    candles: Sequence[market.Candle],
) -> list[structures.IndicatorSnapshot]:
    """Convenience wrapper returning only snapshots.

    Args:
        plugin: Plugin.
        candles: Bars.

    Returns:
        Snapshot after each bar.
    """
    return [snapshot for snapshot, _ in testing.run_bars(plugin, candles)]
