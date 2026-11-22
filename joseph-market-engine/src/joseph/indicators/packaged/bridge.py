"""Generic adapter from a ``smc_indicators`` object to the plugin SDK.

A ``PackagedPlugin`` owns one or more mutable library objects per series.
The SDK requires pure transitions (``on_bar_closed`` never mutates its
input) and JSON checkpoints, so the adapter keeps the library objects in a
*cell* shared by successive states. Each state also records how to rebuild
its objects: a pickled *base* taken every ``SNAPSHOT_EVERY`` bars plus a
persistent log of the bar inputs applied since then:

* advancing the newest state reuses the cell's live objects (fast path, no
  pickling except at snapshot bars);
* advancing an older state rebuilds its objects from its base and log (the
  library is deterministic), so earlier states stay valid and replayable;
* ``dump_state`` materializes the objects the same way and emits them as
  base64 next to the SDK-encoded header.

Library zones become ``Aoi`` records with ids derived from the library's
deterministic zone ids; zones that disappear are published once as ended
AOIs whose terminal state is inferred from that bar's library events.
"""

from __future__ import annotations

import abc
import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence
from importlib import metadata
from typing import ClassVar

from smc_indicators import types as smc_types

from joseph.domain import errors
from joseph.domain import ids
from joseph.domain import market
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.packaged import codec
from joseph.indicators.packaged import convert
from joseph.indicators.sdk import base
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import state_codec

LIBRARY_DISTRIBUTION = "joseph-smc-indicators"
"""Distribution name of the external indicator library."""

LICENSING_PINE_PORT = "PINE_PORT_PENDING_REVIEW"
"""Contract licensing status of line-by-line Pine ports."""

TICK_SETTING = "tick_size"
"""Setting every packaged plugin accepts; ``0`` means "from the bars"."""

SNAPSHOT_EVERY = 32
"""Bars between pickled bases; older states replay fewer bars than this."""

_STATE_TYPES: tuple[type, ...] = (
    protocol.SeriesHeader,
    structures.Aoi,
    structures.StructureEvent,
    structures.AoiKind,
    structures.AoiState,
    structures.ZoneSide,
    structures.IndicatorMethod,
    structures.StructureEventName,
    tfs.Timeframe,
)

_END_BY_EVENT: Mapping[str, structures.AoiState] = {
    "AOI_INVALIDATED": structures.AoiState.INVALIDATED,
    "AOI_EXPIRED": structures.AoiState.EXPIRED,
    "BSL_SWEEP": structures.AoiState.SWEPT,
    "SSL_SWEEP": structures.AoiState.SWEPT,
    "BSL_BREAK": structures.AoiState.INVALIDATED,
    "SSL_BREAK": structures.AoiState.INVALIDATED,
}
"""Terminal AOI state implied by a library event naming the zone."""


def library_version() -> str:
    """Returns the installed library version.

    Returns:
        Version string, or ``"0.0.0"`` when metadata is unavailable.
    """
    try:
        return metadata.version(LIBRARY_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "0.0.0"


def tick_spec(description: str | None = None) -> settings_lib.SettingSpec:
    """Builds the shared ``tick_size`` setting.

    Args:
        description: Optional override text.

    Returns:
        Decimal setting; the platform fills it from the instrument.
    """
    return settings_lib.SettingSpec(
        TICK_SETTING,
        settings_lib.SettingKind.DECIMAL,
        decimal.Decimal(0),
        description
        or (
            "Symbol tick size (Pine syminfo.mintick). 0 = filled from the "
            "instrument, else inferred from the first bar's price precision."
        ),
        minimum=decimal.Decimal(0),
    )


def infer_tick(candle: market.Candle) -> decimal.Decimal:
    """Infers a tick size from the decimals of one candle's prices.

    Args:
        candle: First candle of a series.

    Returns:
        ``10 ** -places`` for the most precise OHLC price.
    """
    places = 0
    for value in (candle.open, candle.high, candle.low, candle.close):
        exponent = value.normalize().as_tuple().exponent
        if isinstance(exponent, int):
            places = max(places, -exponent)
    return decimal.Decimal(1).scaleb(-min(places, 12))


@dataclasses.dataclass(slots=True)
class Payload:
    """Mutable library state of one series; pickled for checkpoints.

    Attributes:
        tick: Tick size used by the library objects.
        objects: Library objects by role (e.g. ``"main"``).
        aux_seen: Start of the last auxiliary bar pushed, per timeframe.
        memo: Small plugin-specific memory (JSON scalars only).
    """

    tick: decimal.Decimal
    objects: dict[str, object]
    aux_seen: dict[str, int] = dataclasses.field(default_factory=dict)
    memo: dict[str, object] = dataclasses.field(default_factory=dict)


class _Cell:
    """Live payload shared by the newest state of a series.

    Attributes:
        payload: Live library state, or ``None`` before the first bar.
        head: Version of the state the payload corresponds to.
    """

    __slots__ = ("head", "payload")

    def __init__(self, payload: Payload | None, head: int) -> None:
        """Initializes the cell.

        Args:
            payload: Live payload.
            head: Version it corresponds to.
        """
        self.payload = payload
        self.head = head


@dataclasses.dataclass(frozen=True, slots=True)
class _Log:
    """Persistent (shared-tail) list of bar inputs, newest first.

    Attributes:
        bar: Bar input applied.
        previous: Older entries, or ``None``.
        length: Entries including this one.
    """

    bar: protocol.BarInput
    previous: _Log | None
    length: int

    def oldest_first(self) -> list[protocol.BarInput]:
        """Returns the logged bar inputs in application order.

        Returns:
            Bar inputs, oldest first.
        """
        entries: list[protocol.BarInput] = []
        node: _Log | None = self
        while node is not None:
            entries.append(node.bar)
            node = node.previous
        entries.reverse()
        return entries


@dataclasses.dataclass(frozen=True, slots=True)
class PackagedState:
    """Immutable plugin state wrapping library objects.

    Equality covers what the plugin publishes (header, version, AOIs and
    values); how the objects are rebuilt is an implementation detail.

    Attributes:
        header: SDK bookkeeping.
        version: Number of bars applied (identifies the cell head).
        live: Live AOIs after the last bar.
        zone_ids: Library zone id per live AOI.
        keys: Library identity key per live AOI.
        values: Published values after the last bar.
        cell: Shared live payload.
        sources: Library zone per live AOI; lets an unchanged zone reuse
            its AOI without conversion (a cache, not checkpointed).
        base: Pickled ``Payload`` at ``base_version`` (empty = build fresh).
        base_version: Bars applied when ``base`` was taken.
        log: Bar inputs applied after ``base``, or ``None``.
    """

    header: protocol.SeriesHeader
    version: int
    live: tuple[structures.Aoi, ...]
    zone_ids: tuple[str, ...]
    keys: tuple[str, ...]
    values: tuple[tuple[str, structures.ValueType], ...]
    cell: _Cell = dataclasses.field(compare=False, repr=False)
    sources: tuple[smc_types.Zone, ...] = dataclasses.field(
        default=(), compare=False, repr=False
    )
    base: bytes = dataclasses.field(default=b"", compare=False, repr=False)
    base_version: int = dataclasses.field(default=0, compare=False)
    log: _Log | None = dataclasses.field(
        default=None, compare=False, repr=False
    )


@dataclasses.dataclass(frozen=True, slots=True)
class LibraryStep:
    """What the library objects produced on one bar.

    Attributes:
        zones: Live library zones after the bar.
        events: Library events raised on the bar.
        values: Published platform values.
        coverage_pct: Optional sub-data coverage for data quality.
    """

    zones: Sequence[smc_types.Zone] = ()
    events: Sequence[smc_types.Event] = ()
    values: Mapping[str, structures.ValueType] = dataclasses.field(
        default_factory=dict
    )
    coverage_pct: str | None = None


class PackagedPlugin(base.BasePlugin[PackagedState]):
    """Base class of every adapter around a library indicator.

    Subclasses declare metadata and settings and implement ``_build`` and
    ``_advance``; optional hooks customize kinds, identities and events.
    """

    VERSION: ClassVar[str] = library_version()
    PINE_SOURCE: ClassVar[str] = ""
    EVENT_ALIASES: ClassVar[Mapping[str, str]] = {}
    KIND_ALIASES: ClassVar[Mapping[str, str]] = {}

    @property
    def licensing_status(self) -> str:
        """Returns the licensing status of a Pine port."""
        return LICENSING_PINE_PORT

    def info(self) -> dict[str, object]:
        """Returns the plugin info including its Pine source path."""
        info = super().info()
        if self.PINE_SOURCE:
            info["description"] = (
                f"{self.DESCRIPTION} Port of {self.PINE_SOURCE}."
            )
        return info

    # ------------------------------------------------------------ hooks

    @abc.abstractmethod
    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: tfs.Timeframe,
    ) -> dict[str, object]:
        """Creates the library objects of a new series.

        Args:
            settings: Resolved settings.
            tick: Tick size.
            timeframe: Series timeframe.

        Returns:
            Library objects by role.
        """

    @abc.abstractmethod
    def _advance(
        self,
        payload: Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> LibraryStep:
        """Feeds one closed bar to the library objects.

        Args:
            payload: Live payload (may be mutated).
            bar: SDK bar input.
            settings: Resolved settings.
            step: Price quantum for decimal conversion.

        Returns:
            Library outputs and published values.
        """

    def _kind(self, zone: smc_types.Zone) -> structures.AoiKind | None:
        """Maps a library zone kind to an AOI kind.

        Args:
            zone: Library zone.

        Returns:
            AOI kind, or ``None`` to keep the zone private.
        """
        name = self.KIND_ALIASES.get(zone.kind, zone.kind)
        try:
            return structures.AoiKind(name)
        except ValueError:
            return None

    def _key(self, zone: smc_types.Zone) -> str:
        """Returns the identity of a zone (kind changes start a new AOI).

        Args:
            zone: Library zone.

        Returns:
            Identity key.
        """
        return f"{zone.zone_id}:{zone.kind}"

    def _event_name(
        self, event: smc_types.Event
    ) -> structures.StructureEventName | None:
        """Maps a library event to a contract event name.

        Args:
            event: Library event.

        Returns:
            Contract name, or ``None`` for library-only events.
        """
        name = self.EVENT_ALIASES.get(event.name, event.name)
        try:
            return structures.StructureEventName(name)
        except ValueError:
            return None

    def _end_state(
        self,
        zone_id: str,
        events: Sequence[smc_types.Event],
        current_zone_ids: frozenset[str],
    ) -> structures.AoiState:
        """Infers why a live zone disappeared.

        Args:
            zone_id: Library zone id of the vanished AOI.
            events: Library events of the bar.
            current_zone_ids: Library ids still live.

        Returns:
            Terminal AOI state.
        """
        if zone_id in current_zone_ids:
            return structures.AoiState.INVERTED
        for event in events:
            if event.zone_id == zone_id and event.name in _END_BY_EVENT:
                return _END_BY_EVENT[event.name]
        return structures.AoiState.EXPIRED

    # ------------------------------------------------- SDK implementation

    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> PackagedState:
        """Creates the empty state (objects are built on the first bar).

        The library objects are trial-built here so that settings the
        library rejects fail the request instead of the first bar.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            Initial state.

        Raises:
            ValidationFailedError: If the library rejects the settings.
        """
        tick = settings_lib.get_decimal(settings, TICK_SETTING)
        try:
            self._build(
                settings,
                tick if tick > 0 else decimal.Decimal("0.01"),
                header.timeframe,
            )
        except ValueError as err:
            raise errors.ValidationFailedError(
                f"{self.PLUGIN_ID}: {err}"
            ) from err
        return PackagedState(
            header=header,
            version=0,
            live=(),
            zone_ids=(),
            keys=(),
            values=(),
            cell=_Cell(None, 0),
        )

    def _fresh(
        self, header: protocol.SeriesHeader, candle: market.Candle
    ) -> Payload:
        """Builds the library objects for the first bar of a series.

        Args:
            header: Series header.
            candle: First bar (used to infer the tick when unset).

        Returns:
            New payload.
        """
        settings = header.settings_map()
        tick = settings_lib.get_decimal(settings, TICK_SETTING)
        if tick <= 0:
            tick = infer_tick(candle)
        return Payload(
            tick=tick,
            objects=self._build(settings, tick, header.timeframe),
        )

    def _materialize(self, state: PackagedState) -> Payload | None:
        """Rebuilds the library objects of ``state`` without touching others.

        Args:
            state: Any state of this plugin.

        Returns:
            A private payload positioned at ``state``, or ``None`` before
            the first bar.
        """
        logged = state.log.oldest_first() if state.log is not None else []
        if state.base:
            restored = codec.loads(state.base)
            assert isinstance(restored, Payload)
            payload = restored
        elif logged:
            payload = self._fresh(state.header, logged[0].candle)
        else:
            return None
        settings = state.header.settings_map()
        step = convert.quantum(payload.tick)
        for bar in logged:
            self._advance(payload, bar, settings, step)
        return payload

    def _payload(
        self,
        state: PackagedState,
        header: protocol.SeriesHeader,
        candle: market.Candle,
    ) -> tuple[Payload, _Cell]:
        """Returns a payload that may be mutated for the next bar.

        Args:
            state: Previous state.
            header: Advanced header.
            candle: Current bar.

        Returns:
            ``(payload, cell)`` positioned at ``state``.
        """
        cell = state.cell
        if cell.head == state.version and cell.payload is not None:
            return cell.payload, cell
        payload = self._materialize(state)
        if payload is None:
            payload = self._fresh(header, candle)
        return payload, _Cell(payload, state.version)

    def _step(
        self,
        state: PackagedState,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> base.StepResult[PackagedState]:
        """Feeds the bar to the library and translates its outputs.

        Args:
            state: Previous state (never mutated).
            header: Header advanced to this bar.
            bar: SDK bar input.

        Returns:
            New state and published changes.
        """
        payload, cell = self._payload(state, header, bar.candle)
        step = convert.quantum(payload.tick)
        try:
            out = self._advance(payload, bar, header.settings_map(), step)
        except BaseException:
            # The live objects may be half-updated: force a rebuild from
            # ``state.blob`` on the next call instead of reusing them.
            cell.head = -1
            raise
        live, zone_ids, keys, sources = self._aois(
            state, header, out.zones, step
        )
        previous = dict(zip(state.keys, state.live, strict=True))
        updated = tuple(
            aoi
            for key, aoi in zip(keys, live, strict=True)
            if previous.get(key) is not aoi
        )
        current = frozenset(zone_ids)
        alive = frozenset(keys)
        ended = tuple(
            self.end_aoi(
                aoi, header, self._end_state(zone_id, out.events, current)
            )
            for zone_id, key, aoi in zip(
                state.zone_ids, state.keys, state.live, strict=True
            )
            if key not in alive
        )
        events = self._events(
            header, out.events, state, live=live, zone_ids=zone_ids, step=step
        )
        version = state.version + 1
        length = (state.log.length if state.log is not None else 0) + 1
        if length >= SNAPSHOT_EVERY:
            snapshot, snapshot_version, log = (
                codec.dumps(payload),
                version,
                None,
            )
        else:
            snapshot, snapshot_version = state.base, state.base_version
            log = _Log(bar, state.log, length)
        cell.head = version
        new_state = PackagedState(
            header=header,
            version=version,
            live=live,
            zone_ids=zone_ids,
            keys=keys,
            values=tuple(out.values.items()),
            cell=cell,
            sources=sources,
            base=snapshot,
            base_version=snapshot_version,
            log=log,
        )
        return base.StepResult(
            state=new_state,
            events=events,
            updated=updated,
            ended=ended,
            coverage_pct=out.coverage_pct,
        )

    def _aois(
        self,
        state: PackagedState,
        header: protocol.SeriesHeader,
        zones: Sequence[smc_types.Zone],
        step: decimal.Decimal,
    ) -> tuple[
        tuple[structures.Aoi, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[smc_types.Zone, ...],
    ]:
        """Converts live library zones, reusing unchanged AOIs.

        A zone equal to the one behind the previous AOI keeps that AOI
        without conversion; otherwise the converted AOI still replaces the
        old one only when something other than ``as_of_ms`` changed.

        Args:
            state: Previous state.
            header: Advanced header.
            zones: Live library zones.
            step: Price quantum.

        Returns:
            ``(aois, zone_ids, keys, sources)`` in library order.
        """
        previous = dict(zip(state.keys, state.live, strict=True))
        cached = (
            dict(zip(state.keys, state.sources, strict=True))
            if len(state.sources) == len(state.keys)
            else {}
        )
        aois: list[structures.Aoi] = []
        zone_ids: list[str] = []
        keys: list[str] = []
        sources: list[smc_types.Zone] = []
        for zone in zones:
            kind = self._kind(zone)
            if kind is None:
                continue
            key = self._key(zone)
            old = previous.get(key)
            if old is not None and cached.get(key) == zone:
                fresh = old
            else:
                fresh = self._zone_aoi(header, zone, kind, key, step)
                if old is not None and _same(old, fresh):
                    fresh = old
            aois.append(fresh)
            zone_ids.append(zone.zone_id)
            keys.append(key)
            sources.append(zone)
        return tuple(aois), tuple(zone_ids), tuple(keys), tuple(sources)

    def _zone_aoi(
        self,
        header: protocol.SeriesHeader,
        zone: smc_types.Zone,
        kind: structures.AoiKind,
        key: str,
        step: decimal.Decimal,
    ) -> structures.Aoi:
        """Converts one library zone.

        Args:
            header: Advanced header.
            zone: Library zone.
            kind: Mapped kind.
            key: Identity key.
            step: Price quantum.

        Returns:
            The AOI as of this bar.
        """
        lower = convert.price(min(zone.bottom, zone.top), step)
        upper = convert.price(max(zone.bottom, zone.top), step)
        assert lower is not None and upper is not None
        meta = {
            name: convert.scalar(value, step)
            for name, value in zone.meta.items()
            if name != "range_id"
        }
        range_id = zone.meta.get("range_id")
        aoi = self.aoi(
            header,
            aoi_id=ids.stable_id(
                self.PLUGIN_ID,
                header.instrument_id,
                header.timeframe.value,
                key,
            ),
            kind=kind,
            side=convert.side(zone.side),
            lower=lower,
            upper=upper,
            created_at_ms=zone.origin_time_ms,
            state=convert.state(zone.state),
            quality_score=zone.score,
            range_id=str(range_id) if range_id is not None else None,
            meta=meta,
        )
        confirmed = min(
            zone.confirmed_time_ms + header.timeframe.duration_ms,
            header.as_of_ms,
        )
        return dataclasses.replace(aoi, confirmed_at_ms=confirmed)

    def _events(
        self,
        header: protocol.SeriesHeader,
        events: Sequence[smc_types.Event],
        state: PackagedState,
        *,
        live: Sequence[structures.Aoi],
        zone_ids: Sequence[str],
        step: decimal.Decimal,
    ) -> tuple[structures.StructureEvent, ...]:
        """Converts library events to contract events.

        Args:
            header: Advanced header.
            events: Library events.
            state: Previous state (for AOIs that just ended).
            live: Current AOIs.
            zone_ids: Library id per current AOI.
            step: Price quantum.

        Returns:
            Contract events in library order.
        """
        by_zone = dict(zip(state.zone_ids, state.live, strict=True))
        by_zone.update(zip(zone_ids, live, strict=True))
        converted: list[structures.StructureEvent] = []
        for event in events:
            name = self._event_name(event)
            if name is None:
                continue
            aoi = by_zone.get(event.zone_id) if event.zone_id else None
            converted.append(
                self.event(
                    header,
                    name,
                    price=convert.price(event.price, step),
                    aoi_id=aoi.aoi_id if aoi is not None else None,
                    score=event.score,
                    meta={
                        key: convert.scalar(value, step)
                        for key, value in event.meta.items()
                    },
                )
            )
        return tuple(converted)

    def _publish(
        self, state: PackagedState
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Returns the live AOIs and values of the last bar.

        Args:
            state: Current state.

        Returns:
            ``(live AOIs, values)``.
        """
        return state.live, dict(state.values)

    def dump_state(self, state: PackagedState) -> object:
        """Encodes a JSON-compatible checkpoint.

        Args:
            state: Current state.

        The library objects are rebuilt from the state's own base and log
        (never read from the shared cell), so the result depends only on the
        state and is identical however often it is taken.

        Returns:
            Checkpoint data.
        """
        if state.log is None:
            blob = state.base
        else:
            payload = self._materialize(state)
            blob = codec.dumps(payload) if payload is not None else b""
        return {
            "header": state_codec.dump(state.header),
            "blob": codec.encode(blob),
            "version": state.version,
            "live": state_codec.dump(state.live),
            "zone_ids": list(state.zone_ids),
            "keys": list(state.keys),
            "values": state_codec.dump(state.values),
        }

    def restore_state(self, blob: object) -> PackagedState:
        """Restores a checkpoint produced by ``dump_state``.

        Args:
            blob: Checkpoint data.

        Returns:
            Restored state (its live objects are rebuilt lazily).

        Raises:
            TypeError: If the checkpoint is malformed.
        """
        if not isinstance(blob, Mapping):
            raise TypeError("packaged plugin checkpoint must be a mapping")
        version = int(blob["version"])
        return PackagedState(
            header=state_codec.load(blob["header"], _STATE_TYPES),
            version=version,
            live=state_codec.load(blob["live"], _STATE_TYPES),
            zone_ids=tuple(str(item) for item in blob["zone_ids"]),
            keys=tuple(str(item) for item in blob["keys"]),
            values=state_codec.load(blob["values"], _STATE_TYPES),
            cell=_Cell(None, -1),
            base=codec.decode(str(blob["blob"])),
            base_version=version,
        )

    # ------------------------------------------------------------ helpers

    def new_aux(
        self,
        payload: Payload,
        bar: protocol.BarInput,
        timeframe: tfs.Timeframe,
    ) -> list[smc_types.Bar]:
        """Returns auxiliary bars not yet pushed to the library.

        The engine supplies every completed aux bar visible at the chart
        bar's close; the library then applies Pine's ``lookahead_on`` +
        ``[1]`` visibility on top.

        Args:
            payload: Live payload (its cursor is advanced).
            bar: SDK bar input.
            timeframe: Aux timeframe.

        Returns:
            New library bars, oldest first.
        """
        seen = payload.aux_seen.get(timeframe.value, -1)
        fresh = [
            convert.to_bar(candle)
            for candle in bar.aux.get(timeframe, ())
            if candle.start_ms > seen
        ]
        if fresh:
            payload.aux_seen[timeframe.value] = fresh[-1].time_ms
        return fresh


def _same(old: structures.Aoi, new: structures.Aoi) -> bool:
    """Returns whether an AOI did not change apart from its as-of time.

    Args:
        old: Previously published AOI.
        new: Freshly converted AOI.

    Returns:
        True when the AOI can be republished unchanged.
    """
    return dataclasses.replace(new, as_of_ms=old.as_of_ms) == old


def float_setting(
    settings: Mapping[str, settings_lib.SettingValue], name: str
) -> float:
    """Reads a decimal setting as a float for the library.

    Args:
        settings: Resolved settings.
        name: Setting name.

    Returns:
        Float value.
    """
    return float(settings_lib.get_decimal(settings, name))
