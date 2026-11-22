"""Point-in-time indicator queries for the API layer.

``IndicatorQueryService`` fetches closed candles through the
``CandleSource`` port (implemented by the market-data context), runs the
requested plugins with the same engine used for strategies, and returns
contract dicts (``IndicatorSnapshotList`` / ``AoiList`` / ``IndicatorSeries``).
Callers may pass per-plugin setting overrides; the instrument's tick size is
injected through the ``TickSizeSource`` port.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import ticks as ticks_lib
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import registry as registry_lib


class CandleSource(Protocol):
    """Port supplying historical candles (market-data context)."""

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns candles whose start lies in ``[start_ms, end_ms)``.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Bar resolution.
            start_ms: Inclusive window start.
            end_ms: Exclusive window end.
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True)
class QueryConfig:
    """Query tuning.

    Attributes:
        lookback_bars: Chart bars computed before the requested time.
        intrabar_lookback_bars: Chart bars for which intrabars are fetched.
        aux_lookback_bars: Extra higher-timeframe bars fetched before the
            chart window.
        max_events: Maximum events returned by ``aois``.
        max_series_bars: Maximum chart bars computed for ``series``.
        settings: Per-plugin setting overrides.
    """

    lookback_bars: int = 400
    intrabar_lookback_bars: int = 60
    aux_lookback_bars: int = 64
    max_events: int = 500
    max_series_bars: int = 2000
    settings: Mapping[str, Mapping[str, object]] = dataclasses.field(
        default_factory=dict
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _Computation:
    """Result of one point-in-time run.

    Attributes:
        frames: Frames produced.
        plugin_ids: Plugins included.
    """

    frames: tuple[structures.BarFrame, ...]
    plugin_ids: tuple[str, ...]


class IndicatorQueryService:
    """Computes indicator snapshots and AOIs on demand."""

    def __init__(
        self,
        candle_source: CandleSource,
        registry: registry_lib.PluginRegistry,
        clock: clock_lib.Clock,
        config: QueryConfig | None = None,
        tick_sizes: ticks_lib.TickSizeSource | None = None,
    ) -> None:
        """Creates the service.

        Args:
            candle_source: Historical candle port.
            registry: Plugin registry.
            clock: Time source for "latest" queries.
            config: Tuning (defaults when omitted).
            tick_sizes: Optional instrument tick-size port.
        """
        self._source = candle_source
        self._registry = registry
        self._clock = clock
        self._config = config or QueryConfig()
        self._ticks = tick_sizes

    def plugins(self) -> dict[str, object]:
        """Returns the contract ``IndicatorPluginList``.

        Returns:
            Usable plugins.
        """
        return self._registry.list_infos()

    async def snapshots(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_ids: Sequence[str] | None = None,
        at_ms: int | None = None,
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        """Computes snapshots as of a point in time.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_ids: Plugins to include (all usable when omitted).
            at_ms: Point in time; latest closed bar when omitted.
            settings: Optional per-plugin setting overrides.

        Returns:
            Contract ``IndicatorSnapshotList``.
        """
        result = await self._compute(
            instrument_id, timeframe, plugin_ids, at_ms, settings
        )
        last = result.frames[-1]
        snapshots = [
            snapshot.to_contract()
            for plugin_id in result.plugin_ids
            if (snapshot := last.snapshot(plugin_id, timeframe)) is not None
        ]
        return {"snapshots": snapshots, "as_of_ms": last.close_ms}

    async def aois(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_ids: Sequence[str] | None = None,
        at_ms: int | None = None,
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        """Computes AOIs and recent events as of a point in time.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_ids: Plugins to include (all usable when omitted).
            at_ms: Point in time; latest closed bar when omitted.
            settings: Optional per-plugin setting overrides.

        Returns:
            Contract ``AoiList``.
        """
        result = await self._compute(
            instrument_id, timeframe, plugin_ids, at_ms, settings
        )
        last = result.frames[-1]
        aois: list[dict[str, object]] = []
        for plugin_id in result.plugin_ids:
            snapshot = last.snapshot(plugin_id, timeframe)
            if snapshot is not None:
                aois.extend(aoi.to_contract() for aoi in snapshot.aois)
        events = [
            event.to_contract()
            for frame in result.frames
            for event in frame.events
            if event.timeframe is timeframe
        ][-self._config.max_events :]
        return {
            "instrument_id": str(instrument_id),
            "timeframe": timeframe.value,
            "aois": aois,
            "events": events,
            "as_of_ms": last.close_ms,
            "feed_state": last.feed_state.value,
        }

    async def series(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_id: str,
        from_ms: int | None = None,
        settings: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Computes one plugin's published values on every closed bar.

        Each point is what the plugin published when that bar closed, so a
        sub-pane drawn from it never shows values that were unknown then.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_id: Plugin to compute.
            from_ms: Optional first bar start; the default lookback window
                otherwise (capped at ``max_series_bars``).
            settings: Optional setting overrides for the plugin.

        Returns:
            Contract ``IndicatorSeries``.
        """
        end_ms = self._clock.now_ms()
        bars = self._config.lookback_bars
        if from_ms is not None:
            wanted = (timeframe.floor(end_ms) - from_ms) // (
                timeframe.duration_ms
            )
            bars = max(bars, min(int(wanted) + 1, self._config.max_series_bars))
        result = await self._compute(
            instrument_id,
            timeframe,
            [plugin_id],
            None,
            {plugin_id: settings or {}},
            lookback_bars=bars,
            intrabar_bars=bars,
        )
        plugin = self._registry.get(plugin_id)
        points = [
            {
                "bar_start_ms": frame.candle.start_ms,
                "values": snapshot.to_contract()["values"],
            }
            for frame in result.frames
            if (from_ms is None or frame.candle.start_ms >= from_ms)
            and (snapshot := frame.snapshot(plugin_id, timeframe)) is not None
            and snapshot.warmup_complete
        ]
        last = result.frames[-1]
        return {
            "instrument_id": str(instrument_id),
            "timeframe": timeframe.value,
            "plugin_id": plugin_id,
            "indicator_version": plugin.indicator_version,
            "points": points,
            "as_of_ms": last.close_ms,
            "feed_state": last.feed_state.value,
        }

    async def _compute(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_ids: Sequence[str] | None,
        at_ms: int | None,
        settings: Mapping[str, Mapping[str, object]] | None = None,
        *,
        lookback_bars: int | None = None,
        intrabar_bars: int | None = None,
    ) -> _Computation:
        """Runs the requested plugins over a lookback window.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_ids: Requested plugins.
            at_ms: Point in time or ``None``.
            settings: Per-request overrides layered over the configured ones.
            lookback_bars: Chart bars to compute (configured default when
                omitted).
            intrabar_bars: Chart bars for which intrabars are fetched
                (configured default when omitted); series use the whole
                window so every point has its lower-timeframe data.

        Returns:
            Frames and the included plugin ids.

        Raises:
            ValidationFailedError: If no plugin is requested/usable.
            MissingDataError: If no closed candle is available.
        """
        ids = tuple(plugin_ids) if plugin_ids else self._registry.ids()
        if not ids:
            raise errors.ValidationFailedError("no indicator plugins selected")
        for plugin_id in ids:
            self._registry.get(plugin_id)
        merged = {
            plugin_id: {
                **self._config.settings.get(plugin_id, {}),
                **(settings or {}).get(plugin_id, {}),
            }
            for plugin_id in ids
        }
        tick = await ticks_lib.resolve(self._ticks, instrument_id)
        merged = ticks_lib.with_tick_size(self._registry, ids, merged, tick)
        config = engine_lib.EngineConfig(
            signal_timeframe=timeframe,
            plugins={
                plugin_id: engine_lib.PluginSpec(
                    timeframes=(timeframe,),
                    settings=merged.get(plugin_id, {}),
                )
                for plugin_id in ids
            },
        )
        engine = engine_lib.IndicatorEngine(
            self._registry, config, instrument_id
        )
        end_ms = self._clock.now_ms() if at_ms is None else at_ms
        chart_start = timeframe.floor(end_ms) - (
            lookback_bars or self._config.lookback_bars
        ) * (timeframe.duration_ms)
        windows: dict[timeframes.Timeframe, int] = {}
        for tf in engine.required_timeframes:
            if tf is timeframe:
                windows[tf] = chart_start
            elif tf.duration_ms < timeframe.duration_ms:
                windows[tf] = (
                    timeframe.floor(end_ms)
                    - (intrabar_bars or self._config.intrabar_lookback_bars)
                    * timeframe.duration_ms
                )
            else:
                windows[tf] = (
                    chart_start
                    - self._config.aux_lookback_bars * tf.duration_ms
                )
        ordered = sorted(windows, key=lambda tf: tf.duration_ms)
        fetched = await asyncio.gather(
            *(
                self._source.get_candles(instrument_id, tf, windows[tf], end_ms)
                for tf in ordered
            )
        )
        candles_by_tf = {
            tf: [
                candle
                for candle in candles
                if candle.is_final
                and candle.end_ms <= end_ms
                and candle.start_ms >= windows[tf]
            ]
            for tf, candles in zip(ordered, fetched, strict=True)
        }
        frames = engine.run(candles_by_tf)
        if not frames:
            raise errors.MissingDataError(
                f"no closed {timeframe.value} candles for {instrument_id}"
            )
        return _Computation(frames=tuple(frames), plugin_ids=ids)
