"""Performance smoke test: 5k 5m bars through all seven plugins."""

from __future__ import annotations

import time

import pytest

from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib

TF = timeframes.Timeframe


@pytest.mark.slow
def test_five_thousand_bars_all_plugins_within_budget() -> None:
    """5,000 5m frames (25,000 1m intrabars, 1h aux) finish under 30 s."""
    m1 = testing.random_walk(25_000, seed=99)
    data = {
        TF.M1: m1,
        TF.M5: testing.aggregate(m1, TF.M5),
        TF.H1: testing.aggregate(m1, TF.H1),
    }
    registry = catalog.build_default_registry()
    plugins = {pid: engine_lib.PluginSpec((TF.M5,)) for pid in registry.ids()}
    plugins["daily_bias"] = engine_lib.PluginSpec((TF.M5, TF.H1))
    engine = engine_lib.IndicatorEngine(
        registry,
        engine_lib.EngineConfig(TF.M5, plugins),
        testing.DEFAULT_INSTRUMENT,
    )
    started = time.perf_counter()
    frames = engine.run(data)
    elapsed = time.perf_counter() - started
    per_frame = elapsed / len(frames) * 1000
    message = f"[perf] {len(frames)} frames, 8 series, {elapsed:.2f}s"
    print(message, f"({per_frame:.2f} ms/frame)")  # noqa: T201
    assert len(frames) == 5_000
    assert elapsed < 30
