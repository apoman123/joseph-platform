"""Performance smoke test: 5k 5m bars through every indicator plugin."""

from __future__ import annotations

import sys
import time

import pytest

from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib

TF = timeframes.Timeframe

_BUDGET_S = 30.0
"""Wall-clock budget without instrumentation."""

_TRACED_FACTOR = 3.0
"""Coverage tracing slows pure-Python code roughly 2-3x."""


def _budget() -> float:
    """Returns the time budget, scaled when coverage tracing is active.

    Returns:
        Seconds allowed for the run.
    """
    coverage = sys.modules.get("coverage")
    traced = coverage is not None and coverage.Coverage.current() is not None
    return _BUDGET_S * (_TRACED_FACTOR if traced else 1.0)


@pytest.mark.slow
def test_five_thousand_bars_all_plugins_within_budget() -> None:
    """5,000 5m frames (1m intrabars, 1h/4h/1d aux) finish within budget."""
    m1 = testing.random_walk(25_000, seed=99)
    data = {
        tf: testing.aggregate(m1, tf) if tf is not TF.M1 else m1
        for tf in (TF.M1, TF.M5, TF.H1, TF.H4, TF.D1)
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
    series = sum(len(spec.timeframes) for spec in plugins.values())
    message = f"[perf] {len(frames)} frames, {series} series, {elapsed:.2f}s"
    print(message, f"({per_frame:.2f} ms/frame)")  # noqa: T201
    assert len(frames) == 5_000
    assert elapsed < _budget(), message
