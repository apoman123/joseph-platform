"""Runs the hand-constructed golden scenarios and the Pine parity harness."""

from __future__ import annotations

import pathlib

import pytest

from joseph.indicators import catalog
from joseph.indicators import golden

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3] / "fixtures" / ("indicators")
)
SCENARIOS = golden.scenario_paths(FIXTURES)
EXPORTS = golden.pine_export_paths(FIXTURES)


def test_every_plugin_has_a_scenario() -> None:
    """Each first-party plugin ships at least one golden scenario."""
    covered = {golden.load_scenario(p).plugin_id for p in SCENARIOS}
    assert covered == set(catalog.build_default_registry().ids())


@pytest.mark.parametrize(
    "path", SCENARIOS, ids=lambda p: p.parent.name + "/" + p.stem
)
def test_scenario(path: pathlib.Path) -> None:
    """The scenario's expectations hold bar by bar."""
    scenario = golden.load_scenario(path)
    assert (
        golden.check_scenario(scenario, catalog.build_default_registry()) == []
    )


@pytest.mark.skipif(not EXPORTS, reason="no Pine exports available yet")
@pytest.mark.parametrize("path", EXPORTS or [None])
def test_pine_parity(path: pathlib.Path) -> None:
    """Python output matches the Pine reference bar for bar."""
    assert (
        golden.compare_pine_export(path, catalog.build_default_registry()) == []
    )


def test_parity_harness_detects_divergence(tmp_path: pathlib.Path) -> None:
    """A self-generated export passes; a tampered one is reported."""
    import json  # noqa: PLC0415 - test-local helper.

    from joseph.domain import timeframes  # noqa: PLC0415
    from joseph.indicators import testing  # noqa: PLC0415

    candles = testing.random_walk(60, seed=2, timeframe=timeframes.Timeframe.M5)
    registry = catalog.build_default_registry()
    results = testing.run_bars(registry.get("fvg_ifvg"), candles)
    lines = [
        json.dumps(
            {
                "format": golden.PINE_EXPORT_FORMAT,
                "plugin_id": "fvg_ifvg",
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
                    "events": [e.name.value for e in output.events],
                    "values": {"active_count": snapshot.values["active_count"]},
                }
            )
        )
    path = tmp_path / "binance" / "BTCUSDT" / "5m" / "sample.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines))
    assert golden.compare_pine_export(path, registry) == []
    tampered = json.loads(lines[-1])
    tampered["values"]["active_count"] = 999
    path.write_text("\n".join([*lines[:-1], json.dumps(tampered)]))
    assert golden.compare_pine_export(path, registry)
