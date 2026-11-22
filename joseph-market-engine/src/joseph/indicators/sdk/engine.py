"""Multi-timeframe indicator engine producing no-look-ahead ``BarFrame``s.

The engine runs configured plugins on each requested timeframe and builds
one ``BarFrame`` per closed signal-timeframe bar.

Scheduling is event-time based and watermark gated:

* Each plugin series has its own FIFO of closed bars. A bar ending at ``E``
  on timeframe ``X`` runs once every dependency timeframe ``D`` (intrabar or
  aux stream) has delivered data up to ``D.floor(E)``: lower-timeframe
  intrabars are complete and higher-timeframe bars are visible only once
  closed. Series are independent, so one stalled series never blocks the
  others (their intrabars are consumed promptly).
* A frame for the signal bar ending at ``E`` is a barrier: it is built only
  after every series ``T`` has received and processed its bars through
  ``T.floor(E)``.
* Events inside a frame are ordered canonically by ``(confirmed_at_ms,
  timeframe, series order, sequence)``, independent of arrival order.

Feeding the same candles in any per-timeframe-ordered interleaving (batch,
streaming, or replay from a checkpoint) therefore yields identical frames.
``flush`` forces pending work when a dependency feed stalls; that degraded
path is counted in ``stats``.
"""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib
from joseph.indicators.sdk import state_codec

_MIN_STORE = 64
_NO_LIMIT = 2**62

_SNAPSHOT_TYPES: tuple[type, ...] = (
    structures.IndicatorSnapshot,
    structures.Aoi,
    structures.StructureEvent,
    structures.AoiKind,
    structures.AoiState,
    structures.ZoneSide,
    structures.IndicatorMethod,
    structures.StructureEventName,
    quality.DataQuality,
    quality.FeedState,
    timeframes.Timeframe,
)

EventKey = tuple[int, int, int, int, int]
"""Canonical event order: confirmed, duration, series, bar, index."""


@dataclasses.dataclass(frozen=True, slots=True)
class PluginSpec:
    """How one plugin runs inside an engine.

    Attributes:
        timeframes: Series timeframes the plugin runs on.
        settings: Setting overrides (validated by the plugin).
    """

    timeframes: tuple[timeframes.Timeframe, ...]
    settings: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class EngineConfig:
    """Engine configuration.

    Attributes:
        signal_timeframe: Timeframe whose closed bars produce frames.
        plugins: Plugin id to run specification.
    """

    signal_timeframe: timeframes.Timeframe
    plugins: Mapping[str, PluginSpec]


@dataclasses.dataclass(slots=True)
class EngineStats:
    """Counters describing degraded or rejected input.

    Attributes:
        accepted: Final candles accepted.
        ignored_non_final: Non-final candles ignored.
        duplicates: Exact re-deliveries ignored.
        revisions_ignored: Corrections of already-accepted bars ignored
            (fired history is never rewritten).
        out_of_order: Older candles rejected.
        forced_items: Work items processed by ``flush`` before their
            dependencies arrived.
    """

    accepted: int = 0
    ignored_non_final: int = 0
    duplicates: int = 0
    revisions_ignored: int = 0
    out_of_order: int = 0
    forced_items: int = 0


@dataclasses.dataclass(slots=True)
class _Series:
    """Runtime state of one plugin on one timeframe.

    Attributes:
        index: Position in configuration order (event tie-breaker).
        plugin: The plugin.
        timeframe: Series timeframe.
        state: Plugin state.
        intrabar_tfs: Intrabar dependency timeframes.
        aux: Aux dependency timeframe to lookback.
        queue: Closed bars waiting to run, oldest first.
        snapshot: Cached snapshot after the last processed bar.
        history: Snapshots not yet superseded for pending frames; a series
            may run ahead of the frame barrier, so frames pick the latest
            snapshot whose ``as_of_ms`` is at or before their close.
        processed: Number of processed bars.
    """

    index: int
    plugin: protocol.IndicatorPlugin
    timeframe: timeframes.Timeframe
    state: Any
    intrabar_tfs: tuple[timeframes.Timeframe, ...]
    aux: dict[timeframes.Timeframe, int]
    queue: collections.deque[market.Candle] = dataclasses.field(
        default_factory=collections.deque
    )
    snapshot: structures.IndicatorSnapshot | None = None
    history: collections.deque[structures.IndicatorSnapshot] = (
        dataclasses.field(default_factory=collections.deque)
    )
    processed: int = 0

    def visible_at(self, end_ms: int) -> structures.IndicatorSnapshot | None:
        """Returns the latest snapshot known at ``end_ms``.

        Args:
            end_ms: Frame close.

        Returns:
            Snapshot with the greatest ``as_of_ms <= end_ms``, or ``None``.
        """
        for snapshot in reversed(self.history):
            if snapshot.as_of_ms <= end_ms:
                return snapshot
        return None

    def prune_before(self, end_ms: int) -> None:
        """Drops snapshots superseded at ``end_ms``.

        Keeps the latest snapshot visible at ``end_ms`` (later frames may
        still need it) and everything newer.

        Args:
            end_ms: Close of the frame just built.
        """
        while len(self.history) > 1 and self.history[1].as_of_ms <= end_ms:
            self.history.popleft()


def series_key(plugin_id: str, timeframe: timeframes.Timeframe) -> str:
    """Builds the internal key of a plugin series.

    Args:
        plugin_id: Plugin id.
        timeframe: Series timeframe.

    Returns:
        ``"plugin_id@tf"``.
    """
    return f"{plugin_id}@{timeframe.value}"


class IndicatorEngine:
    """Runs plugins across timeframes for one instrument."""

    def __init__(
        self,
        registry: registry_lib.PluginRegistry,
        config: EngineConfig,
        instrument_id: instruments.InstrumentId,
    ) -> None:
        """Creates the engine and initializes every plugin series.

        Args:
            registry: Plugin registry.
            config: Engine configuration.
            instrument_id: Exact venue instrument.

        Raises:
            ValidationFailedError: If a plugin/timeframe combination or a
                stream dependency is invalid.
        """
        self._config = config
        self._instrument_id = instrument_id
        self._series: dict[str, _Series] = {}
        self._store: dict[
            timeframes.Timeframe, collections.deque[market.Candle]
        ] = {}
        self._watermark: dict[timeframes.Timeframe, int] = {}
        self._last_start: dict[timeframes.Timeframe, int] = {}
        self._frames: collections.deque[market.Candle] = collections.deque()
        self._events: list[tuple[EventKey, structures.StructureEvent]] = []
        self._signal_missing = 0
        self.stats = EngineStats()
        capacity: dict[timeframes.Timeframe, int] = collections.defaultdict(
            lambda: _MIN_STORE
        )
        for plugin_id, spec in config.plugins.items():
            plugin = registry.get(plugin_id)
            for tf in spec.timeframes:
                self._add_series(plugin, tf, spec.settings, capacity)
        self._timeframes = frozenset(
            {config.signal_timeframe}
            | {s.timeframe for s in self._series.values()}
            | {tf for s in self._series.values() for tf in s.intrabar_tfs}
            | {tf for s in self._series.values() for tf in s.aux}
        )
        for tf in self._timeframes:
            self._store[tf] = collections.deque(maxlen=capacity[tf])
            self._watermark[tf] = -1

    def _add_series(
        self,
        plugin: protocol.IndicatorPlugin,
        timeframe: timeframes.Timeframe,
        settings: Mapping[str, object],
        capacity: dict[timeframes.Timeframe, int],
    ) -> None:
        """Initializes one plugin series and records its dependencies.

        Args:
            plugin: Plugin.
            timeframe: Series timeframe.
            settings: Overrides.
            capacity: Per-timeframe store capacity being accumulated.

        Raises:
            ValidationFailedError: On invalid combinations.
        """
        if timeframe not in plugin.timeframes:
            raise errors.ValidationFailedError(
                f"{plugin.plugin_id} does not support {timeframe}"
            )
        try:
            state = plugin.initialize(self._instrument_id, timeframe, settings)
        except ValueError as err:
            raise errors.ValidationFailedError(str(err)) from err
        intrabar_tfs: list[timeframes.Timeframe] = []
        aux: dict[timeframes.Timeframe, int] = {}
        for stream in sorted(plugin.required_streams(settings)):
            kind, tf = protocol.parse_stream(stream)
            if kind == "intrabars" and tf is not None:
                if not timeframe.contains(tf):
                    raise errors.ValidationFailedError(
                        f"{plugin.plugin_id}: {tf} intrabars do not nest in "
                        f"{timeframe}"
                    )
                intrabar_tfs.append(tf)
                per_bar = timeframe.duration_ms // tf.duration_ms
                capacity[tf] = max(capacity[tf], 4 * per_bar + 16)
            elif kind == "aux" and tf is not None:
                lookback = max(1, plugin.lookback(settings, stream))
                aux[tf] = lookback
                capacity[tf] = max(capacity[tf], lookback + 16)
        self._series[series_key(plugin.plugin_id, timeframe)] = _Series(
            index=len(self._series),
            plugin=plugin,
            timeframe=timeframe,
            state=state,
            intrabar_tfs=tuple(intrabar_tfs),
            aux=aux,
        )

    @property
    def required_timeframes(self) -> frozenset[timeframes.Timeframe]:
        """Returns every timeframe the engine consumes."""
        return self._timeframes

    @property
    def instrument_id(self) -> instruments.InstrumentId:
        """Returns the engine's instrument."""
        return self._instrument_id

    def on_candle(self, candle: market.Candle) -> list[structures.BarFrame]:
        """Feeds one candle and returns frames that became complete.

        Args:
            candle: Candle of any consumed timeframe.

        Returns:
            Newly completed frames, oldest first.

        Raises:
            ValueError: If the candle belongs to another instrument.
        """
        if candle.instrument_id != self._instrument_id:
            raise ValueError("candle belongs to another instrument")
        tf = candle.timeframe
        if tf not in self._timeframes:
            return []
        if not candle.is_final:
            self.stats.ignored_non_final += 1
            return []
        last = self._last_start.get(tf)
        if last is not None and candle.start_ms <= last:
            if candle.start_ms == last:
                stored = self._store[tf][-1]
                if candle.revision > stored.revision:
                    self.stats.revisions_ignored += 1
                else:
                    self.stats.duplicates += 1
            else:
                self.stats.out_of_order += 1
            return []
        if (
            tf is self._config.signal_timeframe
            and last is not None
            and candle.start_ms > last + tf.duration_ms
        ):
            self._signal_missing += (
                candle.start_ms - last - tf.duration_ms
            ) // tf.duration_ms
        self._last_start[tf] = candle.start_ms
        self._store[tf].append(candle)
        self._watermark[tf] = max(self._watermark[tf], candle.end_ms)
        self.stats.accepted += 1
        for series in self._series.values():
            if series.timeframe is tf:
                series.queue.append(candle)
        if tf is self._config.signal_timeframe:
            self._frames.append(candle)
        return self._drain(force_until=None)

    def run(
        self,
        candles_by_tf: Mapping[timeframes.Timeframe, Sequence[market.Candle]],
    ) -> list[structures.BarFrame]:
        """Batch mode: feeds every candle in event-time order, then flushes.

        Args:
            candles_by_tf: Candles per timeframe (any order).

        Returns:
            All frames, oldest first.
        """
        ordered = sorted(
            (c for series in candles_by_tf.values() for c in series),
            key=lambda c: (c.end_ms, c.timeframe.duration_ms, c.start_ms),
        )
        frames = self.feed(ordered)
        frames.extend(self.flush())
        return frames

    def feed(
        self, candles: Iterable[market.Candle]
    ) -> list[structures.BarFrame]:
        """Feeds candles in the given order (streaming/replay helper).

        Args:
            candles: Candles in arrival order.

        Returns:
            Frames completed while feeding, oldest first.
        """
        frames: list[structures.BarFrame] = []
        for candle in candles:
            frames.extend(self.on_candle(candle))
        return frames

    def flush(self, up_to_ms: int | None = None) -> list[structures.BarFrame]:
        """Processes pending work regardless of missing dependencies.

        Use when a dependency feed stalls; inputs that arrive later for
        already-processed times are not applied retroactively.

        Args:
            up_to_ms: Only force work ending at or before this time
                (``None`` forces everything).

        Returns:
            Frames produced.
        """
        return self._drain(
            force_until=_NO_LIMIT if up_to_ms is None else up_to_ms
        )

    def snapshot(
        self, plugin_id: str, timeframe: timeframes.Timeframe
    ) -> structures.IndicatorSnapshot | None:
        """Returns the latest snapshot of one series.

        Args:
            plugin_id: Plugin id.
            timeframe: Series timeframe.

        Returns:
            Snapshot, or ``None`` before the first processed bar.
        """
        series = self._series.get(series_key(plugin_id, timeframe))
        return None if series is None else series.snapshot

    # --------------------------------------------------------- scheduling

    def _series_ready(self, series: _Series, candle: market.Candle) -> bool:
        """Checks whether a series bar's dependencies have arrived.

        Args:
            series: The series.
            candle: Its next bar.

        Returns:
            True when it may run.
        """
        for tf in series.intrabar_tfs:
            if self._watermark[tf] < candle.end_ms:
                return False
        return all(
            self._watermark[tf] >= tf.floor(candle.end_ms) for tf in series.aux
        )

    def _series_blocking(self, end_ms: int) -> bool:
        """Checks whether any series still holds a bar ending by ``end_ms``.

        Args:
            end_ms: Frame close.

        Returns:
            True when some series has unprocessed bars up to the close.
        """
        return any(
            series.queue and series.queue[0].end_ms <= end_ms
            for series in self._series.values()
        )

    def _frame_ready(self, candle: market.Candle) -> bool:
        """Checks the frame barrier for a signal bar.

        Args:
            candle: The signal bar.

        Returns:
            True when every series has processed through the bar close.
        """
        end = candle.end_ms
        if self._series_blocking(end):
            return False
        return all(
            self._watermark[s.timeframe] >= s.timeframe.floor(end)
            for s in self._series.values()
        )

    def _drain(self, force_until: int | None) -> list[structures.BarFrame]:
        """Runs ready (or forced) work until no progress is possible.

        Args:
            force_until: Force work ending at or before this time.

        Returns:
            Frames produced.
        """
        frames: list[structures.BarFrame] = []
        progress = True
        while progress:
            progress = False
            for series in self._series.values():
                while series.queue:
                    candle = series.queue[0]
                    if not self._series_ready(series, candle):
                        if force_until is None or candle.end_ms > force_until:
                            break
                        self.stats.forced_items += 1
                    series.queue.popleft()
                    self._run_series(series, candle)
                    progress = True
            while self._frames:
                candle = self._frames[0]
                if not self._frame_ready(candle):
                    if (
                        force_until is None
                        or candle.end_ms > force_until
                        or self._series_blocking(candle.end_ms)
                    ):
                        break
                    self.stats.forced_items += 1
                self._frames.popleft()
                frames.append(self._build_frame(candle))
                progress = True
        return frames

    # ------------------------------------------------------------ running

    def _intrabars(
        self, tf: timeframes.Timeframe, start_ms: int, end_ms: int
    ) -> tuple[market.Candle, ...]:
        """Collects stored lower-timeframe bars inside ``[start, end)``.

        Args:
            tf: Lower timeframe.
            start_ms: Parent bar start.
            end_ms: Parent bar end.

        Returns:
            Bars oldest first.
        """
        found: list[market.Candle] = []
        for candle in reversed(self._store[tf]):
            if candle.start_ms < start_ms:
                break
            if candle.end_ms <= end_ms:
                found.append(candle)
        found.reverse()
        return tuple(found)

    def _aux(
        self, tf: timeframes.Timeframe, end_ms: int, count: int
    ) -> tuple[market.Candle, ...]:
        """Collects the latest completed bars visible at ``end_ms``.

        Args:
            tf: Aux timeframe.
            end_ms: Visibility time (bar close).
            count: Maximum bars.

        Returns:
            Bars oldest first.
        """
        found: list[market.Candle] = []
        for candle in reversed(self._store[tf]):
            if candle.end_ms > end_ms:
                continue
            found.append(candle)
            if len(found) >= count:
                break
        found.reverse()
        return tuple(found)

    def _run_series(self, series: _Series, candle: market.Candle) -> None:
        """Runs one plugin bar.

        Args:
            series: Series to advance.
            candle: Its closed bar.
        """
        bar = protocol.BarInput(
            candle=candle,
            intrabars={
                tf: self._intrabars(tf, candle.start_ms, candle.end_ms)
                for tf in series.intrabar_tfs
            },
            aux={
                tf: self._aux(tf, candle.end_ms, count)
                for tf, count in series.aux.items()
            },
        )
        series.state, output = series.plugin.on_bar_closed(series.state, bar)
        series.snapshot = series.plugin.snapshot(series.state)
        series.history.append(series.snapshot)
        series.processed += 1
        for position, event in enumerate(output.events):
            key = (
                event.confirmed_at_ms,
                series.timeframe.duration_ms,
                series.index,
                series.processed,
                position,
            )
            self._events.append((key, event))

    def _build_frame(self, candle: market.Candle) -> structures.BarFrame:
        """Builds the frame for one closed signal bar.

        Args:
            candle: The signal bar.

        Returns:
            The frame.
        """
        start, end = candle.start_ms, candle.end_ms
        window = sorted(
            (item for item in self._events if start < item[0][0] <= end),
            key=lambda item: item[0],
        )
        self._events = [item for item in self._events if item[0][0] > end]
        snapshots: dict[
            structures.SnapshotKey, structures.IndicatorSnapshot
        ] = {}
        for series in self._series.values():
            visible = series.visible_at(end)
            if visible is not None:
                snapshots[(series.plugin.plugin_id, series.timeframe)] = visible
            series.prune_before(end)
        return structures.BarFrame(
            instrument_id=self._instrument_id,
            timeframe=self._config.signal_timeframe,
            candle=candle,
            snapshots=snapshots,
            events=tuple(event for _, event in window),
            feed_state=candle.feed_state,
            missing_bars=self._signal_missing,
        )

    # --------------------------------------------------------- checkpoint

    def checkpoint(self) -> dict[str, object]:
        """Encodes the full engine state as JSON-compatible data.

        Returns:
            Checkpoint usable with ``restore``.
        """
        return {
            "series": {
                key: {
                    "state": series.plugin.dump_state(series.state),
                    "processed": series.processed,
                    "queue": state_codec.dump(tuple(series.queue)),
                    "history": state_codec.dump(tuple(series.history)),
                }
                for key, series in self._series.items()
            },
            "store": {
                tf.value: state_codec.dump(tuple(candles))
                for tf, candles in self._store.items()
            },
            "watermark": {tf.value: wm for tf, wm in self._watermark.items()},
            "last_start": {
                tf.value: start for tf, start in self._last_start.items()
            },
            "frames": state_codec.dump(tuple(self._frames)),
            "events": [
                [list(key), state_codec.dump(event)]
                for key, event in self._events
            ],
            "signal_missing": self._signal_missing,
        }

    def restore(self, blob: Mapping[str, Any]) -> None:
        """Restores a checkpoint produced by ``checkpoint``.

        The engine must have been built with the same configuration.

        Args:
            blob: Checkpoint data.

        Raises:
            ValueError: If the checkpoint does not match the configuration.
        """
        if set(blob["series"]) != set(self._series):
            raise ValueError("checkpoint was taken with another configuration")
        candle_types = (market.Candle, timeframes.Timeframe, quality.FeedState)
        for key, data in blob["series"].items():
            series = self._series[key]
            series.state = series.plugin.restore_state(data["state"])
            series.processed = int(data["processed"])
            series.queue = collections.deque(
                state_codec.load(data["queue"], candle_types)
            )
            series.history = collections.deque(
                state_codec.load(data["history"], _SNAPSHOT_TYPES)
            )
            series.snapshot = (
                series.plugin.snapshot(series.state)
                if series.processed
                else None
            )
        for tf_text, encoded in blob["store"].items():
            store = self._store[timeframes.parse(tf_text)]
            store.clear()
            store.extend(state_codec.load(encoded, candle_types))
        self._watermark = {
            timeframes.parse(tf): int(wm)
            for tf, wm in blob["watermark"].items()
        }
        self._last_start = {
            timeframes.parse(tf): int(start)
            for tf, start in blob["last_start"].items()
        }
        self._frames = collections.deque(
            state_codec.load(blob["frames"], candle_types)
        )
        event_types = (
            structures.StructureEvent,
            structures.StructureEventName,
            timeframes.Timeframe,
        )
        self._events = [
            (
                (int(k[0]), int(k[1]), int(k[2]), int(k[3]), int(k[4])),
                state_codec.load(event, event_types),
            )
            for k, event in blob["events"]
        ]
        self._signal_missing = int(blob["signal_missing"])
