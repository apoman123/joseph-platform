"""Builds ``BarFrame``s with the production indicator engine.

Used by backtests (``FrameProvider``) and by the live pipeline for warm-up,
so historical and live evaluation share the same closed-bar visibility rules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import service as indicator_service
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import registry as registry_lib
from joseph.strategy.domain import compiler

_AUX_LOOKBACK_BARS = 64
"""Extra higher-timeframe bars fetched before the window (pivot warm-up)."""


def merge_requirements(
    requirements: Iterable[compiler.EngineRequirements],
) -> dict[timeframes.Timeframe, compiler.EngineRequirements]:
    """Unions requirements per signal timeframe.

    One engine per (instrument, signal timeframe) must compute every plugin
    any active strategy needs, otherwise the first frame delivered to a
    strategy could lack a snapshot it depends on.

    Args:
        requirements: Requirements of active plans.

    Returns:
        Merged requirements keyed by signal timeframe.
    """
    merged: dict[timeframes.Timeframe, compiler.EngineRequirements] = {}
    for req in requirements:
        current = merged.get(req.signal_timeframe)
        if current is None:
            merged[req.signal_timeframe] = req
            continue
        plugins = {k: set(v) for k, v in current.plugins.items()}
        for plugin_id, tfs in req.plugins.items():
            plugins.setdefault(plugin_id, set()).update(tfs)
        merged[req.signal_timeframe] = compiler.EngineRequirements(
            signal_timeframe=req.signal_timeframe,
            plugins={k: frozenset(v) for k, v in plugins.items()},
            history_bars=max(current.history_bars, req.history_bars),
        )
    return merged


def engine_config(
    requirements: compiler.EngineRequirements,
    settings: Mapping[str, Mapping[str, object]] | None = None,
) -> engine_lib.EngineConfig:
    """Translates strategy requirements into an indicator engine config.

    Args:
        requirements: Plugins and timeframes to compute.
        settings: Optional per-plugin setting overrides.

    Returns:
        Engine configuration.
    """
    overrides = settings or {}
    return engine_lib.EngineConfig(
        signal_timeframe=requirements.signal_timeframe,
        plugins={
            plugin_id: engine_lib.PluginSpec(
                timeframes=tuple(sorted(tfs, key=lambda tf: tf.duration_ms)),
                settings=overrides.get(plugin_id, {}),
            )
            for plugin_id, tfs in sorted(requirements.plugins.items())
        },
    )


def fetch_windows(
    required: Iterable[timeframes.Timeframe],
    signal_tf: timeframes.Timeframe,
    start_ms: int,
) -> dict[timeframes.Timeframe, int]:
    """Chooses the fetch start for every required timeframe.

    Args:
        required: Timeframes the engine consumes.
        signal_tf: Signal timeframe.
        start_ms: First signal bar (including warm-up) to compute.

    Returns:
        Fetch start per timeframe.
    """
    windows: dict[timeframes.Timeframe, int] = {}
    for tf in required:
        if tf.duration_ms <= signal_tf.duration_ms:
            windows[tf] = start_ms
        else:
            windows[tf] = tf.floor(start_ms) - _AUX_LOOKBACK_BARS * (
                tf.duration_ms
            )
    return windows


class EngineFrameProvider:
    """``FrameProvider`` backed by the indicator engine and a candle source.

    Attributes:
        registry: Indicator plugin registry.
    """

    def __init__(
        self,
        candle_source: indicator_service.CandleSource,
        registry: registry_lib.PluginRegistry,
        *,
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        """Initializes the provider.

        Args:
            candle_source: Final-candle source (market-data service).
            registry: Plugin registry.
            settings: Optional per-plugin setting overrides.
        """
        self._source = candle_source
        self.registry = registry
        self._settings = settings

    def new_engine(
        self,
        instrument_id: instruments.InstrumentId,
        requirements: compiler.EngineRequirements,
    ) -> engine_lib.IndicatorEngine:
        """Creates an engine for one instrument.

        Args:
            instrument_id: Exact venue instrument.
            requirements: Plugins and timeframes to compute.

        Returns:
            Fresh engine.
        """
        return engine_lib.IndicatorEngine(
            self.registry,
            engine_config(requirements, self._settings),
            instrument_id,
        )

    async def candles(
        self,
        engine: engine_lib.IndicatorEngine,
        instrument_id: instruments.InstrumentId,
        signal_tf: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> dict[timeframes.Timeframe, list[market.Candle]]:
        """Fetches final candles for every timeframe the engine needs.

        Args:
            engine: Engine whose ``required_timeframes`` drive the fetch.
            instrument_id: Exact venue instrument.
            signal_tf: Signal timeframe.
            start_ms: First signal bar start (warm-up included).
            end_ms: Exclusive end.

        Returns:
            Candles per timeframe, final only, ending at or before
            ``end_ms``.
        """
        windows = fetch_windows(engine.required_timeframes, signal_tf, start_ms)
        ordered = sorted(windows, key=lambda tf: tf.duration_ms)
        fetched = await asyncio.gather(
            *(
                self._source.get_candles(instrument_id, tf, windows[tf], end_ms)
                for tf in ordered
            )
        )
        return {
            tf: [c for c in rows if c.is_final and c.end_ms <= end_ms]
            for tf, rows in zip(ordered, fetched, strict=True)
        }

    async def frames(
        self,
        instrument_id: instruments.InstrumentId,
        signal_tf: timeframes.Timeframe,
        requirements: compiler.EngineRequirements,
        from_ms: int,
        to_ms: int,
    ) -> Sequence[structures.BarFrame]:
        """Returns closed frames for a range plus warm-up history.

        Args:
            instrument_id: Exact venue instrument.
            signal_tf: Signal timeframe.
            requirements: Plugins and timeframes to compute.
            from_ms: Inclusive start of the evaluated range.
            to_ms: Exclusive end.

        Returns:
            Frames, oldest first; warm-up frames precede ``from_ms``.
        """
        engine = self.new_engine(instrument_id, requirements)
        warmup = max(requirements.history_bars, 1) * signal_tf.duration_ms
        start = signal_tf.floor(from_ms) - warmup
        candles = await self.candles(
            engine, instrument_id, signal_tf, start, to_ms
        )
        return [
            frame
            for frame in engine.run(candles)
            if frame.candle.start_ms < to_ms
        ]
