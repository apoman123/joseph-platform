"""Point-in-time indicator queries for the API layer.

``IndicatorQueryService`` fetches closed candles through the
``CandleSource`` port (implemented by the market-data context), runs the
requested plugins with the same engine used for strategies, and returns
contract dicts (``IndicatorSnapshotList`` / ``AoiList`` / ``IndicatorSeries``).
Callers may pass per-plugin setting overrides; the instrument's tick size is
injected through the ``TickSizeSource`` port.

Windows are counted in bars of the instrument's session calendar
(``sessions.for_instrument``), so on an exchange "400 bars back" skips nights,
weekends and holidays.

Anchored studies (a plugin whose settings include ``anchor_ms``, with
``anchor = "time"`` when it also has an ``anchor`` setting, i.e.
``anchored_vwap`` and the anchored ``regression_channel``) are computed from
the anchor bar onward whenever they are requested alone, however far back
the anchor lies, up to ``QueryConfig.max_anchored_bars`` chart bars (50,000
by default). A more distant anchor is refused with a validation error
instead of being drawn from the wrong bar. Anchored series are kept
incrementally between requests (a bounded LRU of runs, at most
``anchored_cache_points`` points in all): a repeated request, e.g. after a
bar closed, only feeds the newly closed bars to the cached engine.
"""

from __future__ import annotations

import asyncio
import bisect
import collections
import contextlib
import dataclasses
import json
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import ticks as ticks_lib
from joseph.indicators.sdk import engine as engine_lib
from joseph.indicators.sdk import protocol
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
        max_series_bars: Maximum chart bars computed for ``series`` of a
            study without an anchor.
        settings: Per-plugin setting overrides.
        max_anchored_bars: Hard cap of chart bars computed from an anchor
            bar to now; an older anchor is refused.
        anchored_cache_points: Points kept across all cached anchored runs
            (least recently used runs are dropped; 0 disables the cache).
    """

    lookback_bars: int = 400
    intrabar_lookback_bars: int = 60
    aux_lookback_bars: int = 64
    max_events: int = 500
    max_series_bars: int = 2000
    settings: Mapping[str, Mapping[str, object]] = dataclasses.field(
        default_factory=dict
    )
    max_anchored_bars: int = 50_000
    anchored_cache_points: int = 200_000


@dataclasses.dataclass(frozen=True, slots=True)
class _Computation:
    """Result of one point-in-time run.

    Attributes:
        frames: Frames produced.
        plugin_ids: Plugins included.
    """

    frames: tuple[structures.BarFrame, ...]
    plugin_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Plan:
    """Engine and fetch windows of one computation.

    Attributes:
        engine: Fresh engine.
        timeframe: Chart timeframe.
        ids: Plugins computed.
        windows: First bar start fetched per timeframe.
        end_ms: Evaluation time.
        anchored: Whether the window was widened to an anchor bar.
        settings: Merged settings per plugin.
    """

    engine: engine_lib.IndicatorEngine
    timeframe: timeframes.Timeframe
    ids: tuple[str, ...]
    windows: dict[timeframes.Timeframe, int]
    end_ms: int
    anchored: bool
    settings: Mapping[str, Mapping[str, object]]


class _SeriesRun:
    """Published values of one plugin series, extendable bar by bar.

    Attributes:
        engine: Engine positioned after the last fed bar.
        chart_start: First chart bar computed.
        starts: Bar start of every warm point, ascending.
        rows: Contract values per point (tuples in ``keys`` order).
        keys: Value keys of the rows.
        last_start: Newest fed candle start per timeframe.
        as_of_ms: Close of the newest frame.
        feed_state: Provenance of the newest frame.
    """

    def __init__(
        self, engine: engine_lib.IndicatorEngine, chart_start: int
    ) -> None:
        """Initializes an empty run.

        Args:
            engine: Engine.
            chart_start: First chart bar computed.
        """
        self.engine = engine
        self.chart_start = chart_start
        self.starts: list[int] = []
        self.rows: list[tuple[object, ...]] = []
        self.keys: tuple[str, ...] | None = None
        self.last_start: dict[timeframes.Timeframe, int] = {}
        self.as_of_ms = 0
        self.feed_state = "UNAVAILABLE"

    def record(
        self,
        frames: Sequence[structures.BarFrame],
        plugin_id: str,
        timeframe: timeframes.Timeframe,
    ) -> None:
        """Appends the warm points of new frames.

        Args:
            frames: New frames, oldest first.
            plugin_id: Plugin.
            timeframe: Chart timeframe.
        """
        for frame in frames:
            snapshot = frame.snapshot(plugin_id, timeframe)
            if snapshot is None or not snapshot.warmup_complete:
                continue
            values = snapshot.values_contract()
            if self.keys is None:
                self.keys = tuple(values)
            if tuple(values) != self.keys:
                self.keys = tuple(dict.fromkeys((*self.keys, *values)))
            self.starts.append(frame.candle.start_ms)
            self.rows.append(tuple(values.get(key) for key in self.keys))
        if frames:
            self.as_of_ms = frames[-1].close_ms
            self.feed_state = frames[-1].feed_state.value

    def note(
        self, candles: Mapping[timeframes.Timeframe, Sequence[market.Candle]]
    ) -> None:
        """Records the newest candle fed per timeframe.

        Args:
            candles: Candles fed, per timeframe.
        """
        for tf, rows in candles.items():
            if rows:
                newest = max(candle.start_ms for candle in rows)
                self.last_start[tf] = max(
                    newest, self.last_start.get(tf, newest)
                )

    def points(self, first_ms: int) -> list[dict[str, object]]:
        """Returns the contract points from a bar start on.

        Args:
            first_ms: First bar start returned.

        Returns:
            ``IndicatorSeries`` points.
        """
        keys = self.keys or ()
        index = bisect.bisect_left(self.starts, first_ms)
        return [
            {
                "bar_start_ms": start,
                "values": dict(zip(keys, row, strict=False)),
            }
            for start, row in zip(
                self.starts[index:], self.rows[index:], strict=True
            )
        ]


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
        self._runs: collections.OrderedDict[tuple[str, ...], _SeriesRun] = (
            collections.OrderedDict()
        )
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

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
        An anchored study is computed from its anchor bar onward and kept
        incrementally: a repeated request only feeds the bars closed since.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_id: Plugin to compute.
            from_ms: Optional first bar start; the default lookback window
                otherwise (capped at ``max_series_bars`` unless anchored).
            settings: Optional setting overrides for the plugin.

        Returns:
            Contract ``IndicatorSeries``.
        """
        end_ms = self._clock.now_ms()
        calendar = sessions.for_instrument(instrument_id)
        bars = self._config.lookback_bars
        if from_ms is not None:
            with _calendar_errors(instrument_id):
                wanted = calendar.bars_between(
                    timeframe,
                    calendar.floor(timeframe, from_ms),
                    calendar.floor(timeframe, end_ms),
                )
            bars = max(bars, min(wanted + 1, self._config.max_series_bars))
        plan = await self._plan(
            instrument_id,
            timeframe,
            [plugin_id],
            end_ms,
            {plugin_id: settings or {}},
            lookback_bars=bars,
            intrabar_bars=bars,
        )
        first = plan.windows[timeframe]
        if from_ms is not None:
            first = max(first, from_ms)
        if plan.anchored and self._config.anchored_cache_points > 0:
            run = await self._anchored_run(plan, plugin_id)
        else:
            run = await self._series_run(plan, plugin_id)
        plugin = self._registry.get(plugin_id)
        return {
            "instrument_id": str(instrument_id),
            "timeframe": timeframe.value,
            "plugin_id": plugin_id,
            "indicator_version": plugin.indicator_version,
            "points": run.points(first),
            "as_of_ms": run.as_of_ms,
            "feed_state": run.feed_state,
        }

    async def _compute(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_ids: Sequence[str] | None,
        at_ms: int | None,
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> _Computation:
        """Runs the requested plugins over the default lookback window.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_ids: Requested plugins.
            at_ms: Point in time or ``None``.
            settings: Per-request overrides layered over the configured ones.

        Returns:
            Frames and the included plugin ids.
        """
        end_ms = self._clock.now_ms() if at_ms is None else at_ms
        plan = await self._plan(
            instrument_id, timeframe, plugin_ids, end_ms, settings
        )
        frames, _ = await self._batch(plan)
        return _Computation(frames=tuple(frames), plugin_ids=plan.ids)

    async def _plan(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        plugin_ids: Sequence[str] | None,
        end_ms: int,
        settings: Mapping[str, Mapping[str, object]] | None = None,
        *,
        lookback_bars: int | None = None,
        intrabar_bars: int | None = None,
    ) -> _Plan:
        """Builds the engine and the fetch window of every timeframe.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            plugin_ids: Requested plugins (all usable when empty).
            end_ms: Evaluation time.
            settings: Per-request overrides layered over the configured ones.
            lookback_bars: Chart bars to compute (configured default when
                omitted); widened to an anchored study's anchor bar.
            intrabar_bars: Chart bars for which intrabars are fetched
                (configured default when omitted); series use the whole
                window so every point has its lower-timeframe data.

        Returns:
            The plan.

        Raises:
            ValidationFailedError: If no plugin is requested/usable, or an
                anchor lies beyond ``max_anchored_bars``.
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
        calendar = engine.calendar
        lookback = lookback_bars or self._config.lookback_bars
        with _calendar_errors(instrument_id):
            anchored = self._anchored_bars(
                ids, merged, timeframe, calendar, end_ms
            )
            if anchored is not None:
                lookback = max(lookback, anchored)
            current = calendar.floor(timeframe, end_ms)
            chart_start = calendar.shift(timeframe, current, -lookback)
            windows: dict[timeframes.Timeframe, int] = {}
            for tf in engine.required_timeframes:
                if tf is timeframe:
                    windows[tf] = chart_start
                elif tf.duration_ms < timeframe.duration_ms:
                    windows[tf] = calendar.shift(
                        timeframe,
                        current,
                        -(intrabar_bars or self._config.intrabar_lookback_bars),
                    )
                else:
                    windows[tf] = calendar.shift(
                        tf,
                        calendar.floor(tf, chart_start),
                        -self._config.aux_lookback_bars,
                    )
        return _Plan(
            engine=engine,
            timeframe=timeframe,
            ids=ids,
            windows=windows,
            end_ms=end_ms,
            anchored=anchored is not None,
            settings=merged,
        )

    async def _fetch(
        self, plan: _Plan, since: Mapping[timeframes.Timeframe, int]
    ) -> dict[timeframes.Timeframe, list[market.Candle]]:
        """Fetches the final candles a plan consumes.

        Args:
            plan: Computation plan.
            since: First bar start per timeframe.

        Returns:
            Final candles closed by ``plan.end_ms`` per timeframe.
        """
        instrument_id = plan.engine.instrument_id
        ordered = sorted(since, key=lambda tf: tf.duration_ms)
        fetched = await asyncio.gather(
            *(
                self._source.get_candles(
                    instrument_id, tf, since[tf], plan.end_ms
                )
                for tf in ordered
            )
        )
        return {
            tf: [
                candle
                for candle in candles
                if candle.is_final
                and candle.end_ms <= plan.end_ms
                and candle.start_ms >= since[tf]
            ]
            for tf, candles in zip(ordered, fetched, strict=True)
        }

    async def _batch(
        self, plan: _Plan
    ) -> tuple[
        list[structures.BarFrame],
        dict[timeframes.Timeframe, list[market.Candle]],
    ]:
        """Fetches a plan's candles and runs its engine in batch.

        Args:
            plan: Computation plan.

        Returns:
            Frames (oldest first) and the candles fed.

        Raises:
            MissingDataError: If no closed candle is available.
        """
        candles = await self._fetch(plan, plan.windows)
        frames = plan.engine.run(candles)
        if not frames:
            raise errors.MissingDataError(
                f"no closed {plan.timeframe.value} candles for "
                f"{plan.engine.instrument_id}"
            )
        return frames, candles

    async def _series_run(self, plan: _Plan, plugin_id: str) -> _SeriesRun:
        """Computes a series run in batch.

        Args:
            plan: Computation plan.
            plugin_id: Plugin.

        Returns:
            The run.
        """
        frames, candles = await self._batch(plan)
        run = _SeriesRun(plan.engine, plan.windows[plan.timeframe])
        run.record(frames, plugin_id, plan.timeframe)
        run.note(candles)
        return run

    async def _anchored_run(self, plan: _Plan, plugin_id: str) -> _SeriesRun:
        """Returns the cached run of an anchored study, brought up to date.

        The run continues incrementally (identical to a batch run, which the
        engine guarantees for any per-timeframe-ordered feed); a revised bar
        or an out-of-order input discards it and recomputes from the anchor.

        Args:
            plan: Plan of the request.
            plugin_id: Anchored plugin.

        Returns:
            The run.
        """
        tf = plan.timeframe
        key = (
            str(plan.engine.instrument_id),
            tf.value,
            plugin_id,
            json.dumps(plan.settings, sort_keys=True, default=str),
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            run = self._runs.pop(key, None)
            if (
                run is None
                or run.chart_start > plan.windows[tf]
                or not await self._advance(run, plan, plugin_id)
            ):
                run = await self._series_run(plan, plugin_id)
            self._runs[key] = run
            self._evict()
            return run

    async def _advance(
        self, run: _SeriesRun, plan: _Plan, plugin_id: str
    ) -> bool:
        """Feeds the bars closed since a cached run's last one.

        Args:
            run: Cached run.
            plan: Plan of the request.
            plugin_id: Anchored plugin.

        Returns:
            False when the run is stale (a revised or out-of-order bar).
        """
        engine = run.engine
        since = {
            tf: run.last_start.get(tf, start)
            for tf, start in plan.windows.items()
        }
        fetched = await self._fetch(plan, since)
        before = (engine.stats.revisions_ignored, engine.stats.out_of_order)
        candles = sorted(
            (c for rows in fetched.values() for c in rows),
            key=lambda c: (c.end_ms, c.timeframe.duration_ms, c.start_ms),
        )
        frames = engine.feed(candles)
        frames.extend(engine.flush())
        if (
            engine.stats.revisions_ignored,
            engine.stats.out_of_order,
        ) != before:
            return False
        run.record(frames, plugin_id, plan.timeframe)
        run.note(fetched)
        return True

    def _evict(self) -> None:
        """Drops the least recently used anchored runs over the budget."""
        budget = self._config.anchored_cache_points
        while self._runs and (
            sum(len(run.starts) for run in self._runs.values()) > budget
        ):
            self._runs.popitem(last=False)
        for key in [
            key
            for key, lock in self._locks.items()
            if key not in self._runs and not lock.locked()
        ]:
            del self._locks[key]

    def _anchored_bars(
        self,
        ids: Sequence[str],
        settings: Mapping[str, Mapping[str, object]],
        timeframe: timeframes.Timeframe,
        calendar: sessions.BarCalendar,
        end_ms: int,
    ) -> int | None:
        """Returns the chart bars from the oldest anchor bar to ``end_ms``.

        Only a request made of anchored studies is widened, so a full
        snapshot set never computes every plugin over 50,000 bars.

        Args:
            ids: Requested plugins.
            settings: Merged settings per plugin.
            timeframe: Chart timeframe.
            calendar: Instrument calendar.
            end_ms: Evaluation time.

        Returns:
            Bar count (anchor bar through the bar at ``end_ms``, which may
            still be open), or ``None`` when some requested plugin has no
            time anchor.

        Raises:
            ValidationFailedError: If the anchor lies more than
                ``max_anchored_bars`` bars back.
            ValueError: If the anchor precedes the calendar.
        """
        anchors: list[int] = []
        for plugin_id in ids:
            anchor = anchor_time_ms(
                self._registry.get(plugin_id), settings.get(plugin_id, {})
            )
            if anchor is None:
                return None
            anchors.append(anchor)
        oldest = min(anchors)
        if oldest >= end_ms:
            return None
        start = calendar.floor(timeframe, oldest)
        bars = calendar.bars_between(timeframe, start, end_ms)
        current = calendar.floor(timeframe, end_ms)
        # ``bars`` counts the bar at ``end_ms`` only when it started before
        # ``end_ms``; on an exact bar boundary every counted bar is closed.
        still_open = current < end_ms < calendar.end_of(timeframe, current)
        closed = bars - still_open
        cap = self._config.max_anchored_bars
        if closed > cap:
            raise errors.ValidationFailedError(
                f"the anchor is {closed} closed {timeframe.value} bars back; "
                f"anchored studies are computed for at most {cap} bars. Move "
                "the anchor closer or use a higher timeframe."
            )
        return bars


def anchor_time_ms(
    plugin: protocol.IndicatorPlugin,
    overrides: Mapping[str, object],
) -> int | None:
    """Returns the time anchor of an anchored study, if it has one.

    A plugin is anchored when its settings include ``anchor_ms`` and, if it
    also has an ``anchor`` setting, that setting is ``"time"``.

    Args:
        plugin: Plugin.
        overrides: Setting overrides (merged over the defaults).

    Returns:
        Positive anchor time, or ``None`` for an unanchored study.
    """
    defaults = plugin.default_settings()
    if "anchor_ms" not in defaults:
        return None
    resolved = {**defaults, **overrides}
    if "anchor" in defaults and resolved.get("anchor") != "time":
        return None
    value = resolved.get("anchor_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


@contextlib.contextmanager
def _calendar_errors(
    instrument_id: instruments.InstrumentId,
) -> Iterator[None]:
    """Turns times outside an instrument's calendar into validation errors.

    Args:
        instrument_id: Instrument whose calendar is used.

    Yields:
        Nothing.

    Raises:
        errors.ValidationFailedError: If the calendar rejected a time.
    """
    try:
        yield
    except ValueError as err:
        raise errors.ValidationFailedError(f"{instrument_id}: {err}") from err
