"""Performance smoke tests: every plugin over 5k bars; 50k anchored bars."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Sequence

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import service
from joseph.indicators import testing
from joseph.indicators.sdk import engine as engine_lib

TF = timeframes.Timeframe

_BUDGET_S = 30.0
"""Wall-clock budget without instrumentation."""

_TRACED_FACTOR = 3.0
"""Coverage tracing slows pure-Python code roughly 2-3x."""


_ANCHORED_BUDGET_S = 30.0
"""Cold 50,000-bar anchored series (computed from the anchor bar)."""

_INCREMENTAL_BUDGET_S = 2.0
"""The same series re-requested after a bar closed (incremental)."""


def _budget(seconds: float = _BUDGET_S) -> float:
    """Returns a time budget, scaled when coverage tracing is active.

    Args:
        seconds: Uninstrumented budget.

    Returns:
        Seconds allowed for the run.
    """
    coverage = sys.modules.get("coverage")
    traced = coverage is not None and coverage.Coverage.current() is not None
    return seconds * (_TRACED_FACTOR if traced else 1.0)


class _Source:
    """In-memory 1m candle source."""

    def __init__(self, candles: list[market.Candle]) -> None:
        """Stores candles.

        Args:
            candles: 1m candles.
        """
        self.candles = candles

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: TF,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns the candles in the window.

        Args:
            instrument_id: Ignored.
            timeframe: Ignored (1m only).
            start_ms: Window start.
            end_ms: Window end.

        Returns:
            Candles.
        """
        del instrument_id, timeframe
        return [c for c in self.candles if start_ms <= c.start_ms < end_ms]


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


@pytest.mark.slow
def test_fifty_thousand_bar_anchored_vwap_within_budget() -> None:
    """An anchored VWAP runs cold, then incrementally up to the 50k cap."""
    bars = testing.random_walk(50_000, seed=17)
    source = _Source(bars[:-1])
    clock = clock_lib.ManualClock(bars[-2].end_ms)
    query = service.IndicatorQueryService(
        source, catalog.build_default_registry(), clock
    )
    anchor = bars[0].start_ms
    settings = {"anchor": "time", "anchor_ms": anchor}

    def request() -> dict[str, object]:
        """Requests the series from the anchor.

        Returns:
            Contract ``IndicatorSeries``.
        """
        return asyncio.run(
            query.series(
                testing.DEFAULT_INSTRUMENT,
                TF.M1,
                "anchored_vwap",
                from_ms=anchor,
                settings=settings,
            )
        )

    started = time.perf_counter()
    cold = request()
    cold_s = time.perf_counter() - started
    source.candles = bars
    clock.advance(bars[-1].end_ms - clock.now_ms())
    started = time.perf_counter()
    warm = request()
    warm_s = time.perf_counter() - started
    message = f"[perf] anchored 50k cold {cold_s:.2f}s, next bar {warm_s:.2f}s"
    print(message)  # noqa: T201
    assert len(cold["points"]) == 49_999  # type: ignore[arg-type]
    assert len(warm["points"]) == 50_000  # type: ignore[arg-type]
    assert cold_s < _budget(_ANCHORED_BUDGET_S), message
    assert warm_s < _budget(_INCREMENTAL_BUDGET_S), message
