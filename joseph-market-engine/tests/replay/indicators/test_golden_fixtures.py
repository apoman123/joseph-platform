# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Runs the hand-constructed golden scenarios and the parity harness."""

from __future__ import annotations

import json
import pathlib

import pytest

from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import golden
from joseph.indicators import testing

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "indicators"
)
SCENARIOS = golden.scenario_paths(FIXTURES)
EXPORTS = golden.pine_export_paths(FIXTURES)


def test_every_plugin_has_a_scenario() -> None:
    """Each built-in ships golden scenarios, and no other plugin does."""
    counts: dict[str, int] = {}
    for path in SCENARIOS:
        plugin_id = golden.load_scenario(path).plugin_id
        counts[plugin_id] = counts.get(plugin_id, 0) + 1
    assert set(counts) == set(catalog.build_default_registry().ids())
    assert all(count >= 2 for count in counts.values()), counts


@pytest.mark.parametrize(
    "path", SCENARIOS, ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_scenario(path: pathlib.Path) -> None:
    """The scenario's expectations hold bar by bar."""
    scenario = golden.load_scenario(path)
    assert (
        golden.check_scenario(scenario, catalog.build_default_registry()) == []
    )


@pytest.mark.skipif(not EXPORTS, reason="no TradingView exports available yet")
@pytest.mark.parametrize("path", EXPORTS or [None])
def test_pine_parity(path: pathlib.Path) -> None:
    """Plugin output matches the TradingView reference bar for bar."""
    assert (
        golden.compare_pine_export(path, catalog.build_default_registry()) == []
    )


def test_parity_harness_detects_divergence(tmp_path: pathlib.Path) -> None:
    """A self-generated export passes; a tampered one is reported."""
    candles = testing.random_walk(60, seed=2, timeframe=timeframes.Timeframe.M5)
    registry = catalog.build_default_registry()
    results = testing.run_bars(registry.get("ema"), candles)
    lines = [
        json.dumps(
            {
                "format": golden.PINE_EXPORT_FORMAT,
                "plugin_id": "ema",
                "pine_commit": "test",
                "settings": {},
            }
        )
    ]
    for candle, (snapshot, output) in zip(candles, results, strict=True):
        lines.append(
            json.dumps(
                {
                    "bar": [
                        candle.start_ms,
                        str(candle.open),
                        str(candle.high),
                        str(candle.low),
                        str(candle.close),
                        str(candle.base_volume),
                    ],
                    "events": [e.name for e in output.events],
                    "values": {"ema": str(snapshot.values["ema"])},
                }
            )
        )
    path = tmp_path / "binance" / "BTCUSDT" / "5m" / "sample.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines))
    assert golden.compare_pine_export(path, registry) == []
    tampered = json.loads(lines[-1])
    tampered["values"]["ema"] = "999"
    path.write_text("\n".join([*lines[:-1], json.dumps(tampered)]))
    assert golden.compare_pine_export(path, registry)
