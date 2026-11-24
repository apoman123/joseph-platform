# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Reusable base class implementing the plugin contract boilerplate.

Concrete plugins declare class-level metadata (id, settings, what they
publish and how it is displayed) and implement three hooks:
``_initial_state``, ``_step`` and ``_publish``. The base class handles
settings resolution, header bookkeeping, snapshot assembly, checkpointing
and the batch ``compute`` helper, so every plugin behaves identically in
those respects. The state must be a frozen dataclass of tuples, scalars,
decimals, enums and domain value objects, so the default ``state_codec``
checkpoints it.
"""

from __future__ import annotations

import abc
import dataclasses
import decimal
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import ClassVar
from typing import TypeAlias

from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes as tfs
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import state_codec

LICENSING_CLEAN_ROOM = "CLEAN_ROOM_FROM_SPEC"
"""Default contract ``LicensingStatus``; ports of other code override it."""

PARITY_SPEC_ONLY = "SPEC_ONLY"
"""Default contract ``ParityStatus``: no reference export to compare."""

ALL_TIMEFRAMES: tuple[tfs.Timeframe, ...] = tuple(tfs.Timeframe)
"""Every supported timeframe, the default of ``BasePlugin.TIMEFRAMES``."""


_COMMON_STATE_TYPES: tuple[type, ...] = (
    protocol.SeriesHeader,
    structures.Aoi,
    structures.StructureEvent,
    structures.AoiState,
    structures.ZoneSide,
    structures.IndicatorMethod,
    tfs.Timeframe,
    quality.FeedState,
)


@dataclasses.dataclass(frozen=True, slots=True)
class StepResult[StateT]:
    """Result of a plugin's ``_step`` hook.

    Attributes:
        state: New plugin state (header excluded; the base sets it).
        events: Events confirmed on the bar.
        updated: AOIs created or changed on the bar.
        ended: AOIs that stopped being live on the bar.
        coverage_pct: Optional coverage of sub-data for data quality.
    """

    state: StateT
    events: tuple[structures.StructureEvent, ...] = ()
    updated: tuple[structures.Aoi, ...] = ()
    ended: tuple[structures.Aoi, ...] = ()
    coverage_pct: str | None = None


class BasePlugin[StateT](abc.ABC):
    """Shared implementation of ``protocol.IndicatorPlugin``.

    Subclasses set the ``ClassVar`` metadata and implement the hooks.
    The state type must be a frozen dataclass with a ``header`` field of
    type ``protocol.SeriesHeader``. Everything the base class publishes
    goes through the properties (``plugin_id``, ``output_kinds`` and so
    on), so a subclass may override a property instead of its ``ClassVar``.

    Attributes:
        PLUGIN_ID: Unique plugin id (lower snake case), e.g. ``"ema"``;
            also the ``source`` of every published AOI and event.
        VERSION: Semantic version of the algorithm (``indicator_version``).
        SCHEMA_VERSION: Output schema version; the registry accepts only
            supported ones.
        DISPLAY_NAME: Human-readable name.
        DESCRIPTION: One-sentence description.
        TIMEFRAMES: Series timeframes ``initialize`` accepts (all by
            default).
        OUTPUT_KINDS: AOI kinds the plugin can publish (upper snake case,
            unique across installed plugins).
        OUTPUT_EVENTS: Event names the plugin can publish (upper snake
            case; ``structures.RESERVED_EVENTS`` need no declaration).
        OUTPUT_VALUES: Values the plugin publishes in its snapshots.
        DISPLAY: Display hints of the terminal.
        SETTINGS: Setting specifications, which define the input schema,
            the defaults and override validation.
        STATE_TYPES: Dataclass and enum types of the plugin state beyond
            the common SDK ones, needed to restore checkpoints.
        METHOD: Calculation method of published AOIs and, unless
            ``snapshot_method`` says otherwise, of snapshots.
    """

    PLUGIN_ID: ClassVar[str]
    VERSION: ClassVar[str] = "1.0.0"
    SCHEMA_VERSION: ClassVar[int] = 2
    DISPLAY_NAME: ClassVar[str]
    DESCRIPTION: ClassVar[str]
    TIMEFRAMES: ClassVar[tuple[tfs.Timeframe, ...]] = ALL_TIMEFRAMES
    OUTPUT_KINDS: ClassVar[tuple[structures.AoiKind, ...]] = ()
    OUTPUT_EVENTS: ClassVar[tuple[structures.StructureEventName, ...]] = ()
    OUTPUT_VALUES: ClassVar[tuple[metadata.ValueSpec, ...]] = ()
    DISPLAY: ClassVar[metadata.Display] = metadata.Display()
    SETTINGS: ClassVar[tuple[settings_lib.SettingSpec, ...]] = ()
    STATE_TYPES: ClassVar[tuple[type, ...]] = ()
    METHOD: ClassVar[structures.IndicatorMethod] = (
        structures.IndicatorMethod.PINE_PARITY
    )

    @property
    def plugin_id(self) -> str:
        """The unique plugin id, ``PLUGIN_ID``."""
        return self.PLUGIN_ID

    @property
    def indicator_version(self) -> str:
        """The algorithm version, ``VERSION``."""
        return self.VERSION

    @property
    def schema_version(self) -> int:
        """The output schema version, ``SCHEMA_VERSION``."""
        return self.SCHEMA_VERSION

    @property
    def display_name(self) -> str:
        """The display name, ``DISPLAY_NAME``."""
        return self.DISPLAY_NAME

    @property
    def description(self) -> str:
        """The description, ``DESCRIPTION``."""
        return self.DESCRIPTION

    @property
    def warmup_bars(self) -> int:
        """The warm-up bar count for the default settings."""
        return self.warmup_for(self.default_settings())

    @property
    def timeframes(self) -> tuple[tfs.Timeframe, ...]:
        """The supported series timeframes, ``TIMEFRAMES``."""
        return self.TIMEFRAMES

    @property
    def output_kinds(self) -> tuple[structures.AoiKind, ...]:
        """The publishable AOI kinds, ``OUTPUT_KINDS``."""
        return self.OUTPUT_KINDS

    @property
    def output_events(self) -> tuple[structures.StructureEventName, ...]:
        """The publishable event names, ``OUTPUT_EVENTS``."""
        return self.OUTPUT_EVENTS

    @property
    def output_values(self) -> tuple[metadata.ValueSpec, ...]:
        """The published values, ``OUTPUT_VALUES``."""
        return self.OUTPUT_VALUES

    @property
    def display(self) -> metadata.Display:
        """The display hints, ``DISPLAY``."""
        return self.DISPLAY

    @property
    def licensing_status(self) -> str:
        """The contract ``LicensingStatus`` (clean-room by default)."""
        return LICENSING_CLEAN_ROOM

    @property
    def parity_status(self) -> str:
        """The contract ``ParityStatus`` (``SPEC_ONLY`` by default)."""
        return PARITY_SPEC_ONLY

    def snapshot_method(
        self, values: Mapping[str, structures.ValueType]
    ) -> structures.IndicatorMethod:
        """Returns the calculation method of one published snapshot.

        Plugins whose bars may switch to venue trade data (for example true
        volume delta) override this so such outputs are never labelled as
        TradingView parity.

        Args:
            values: The snapshot's published values.

        Returns:
            ``METHOD`` by default.
        """
        del values  # Unused.
        return self.METHOD

    def input_schema(self) -> dict[str, object]:
        """Returns the JSON Schema of the settings."""
        return settings_lib.json_schema(self.SETTINGS)

    def default_settings(self) -> dict[str, settings_lib.SettingValue]:
        """Returns default settings."""
        return settings_lib.defaults(self.SETTINGS)

    def resolve_settings(
        self, overrides: Mapping[str, object] | None
    ) -> dict[str, settings_lib.SettingValue]:
        """Validates overrides and merges them onto the defaults.

        Args:
            overrides: User-supplied settings.

        Returns:
            Resolved settings.
        """
        return settings_lib.resolve(self.SETTINGS, overrides)

    def warmup_for(
        self, settings: Mapping[str, settings_lib.SettingValue]
    ) -> int:
        """Returns warmup bars for resolved settings.

        Args:
            settings: Resolved settings.

        Returns:
            Number of bars before outputs are meaningful.
        """
        del settings  # Unused.
        return 1

    def required_streams(
        self,
        settings: Mapping[str, object] | None,
        *,
        timeframe: tfs.Timeframe | None = None,
    ) -> frozenset[str]:
        """Returns needed data streams (chart bars only by default).

        Args:
            settings: Overrides.
            timeframe: Series timeframe, when known.

        Returns:
            Stream names.
        """
        del settings, timeframe  # Unused.
        return frozenset({protocol.CHART_STREAM})

    def lookback(
        self, settings: Mapping[str, object] | None, stream: str
    ) -> int:
        """Returns the aux-stream lookback (none by default).

        Args:
            settings: Overrides.
            stream: Stream name.

        Returns:
            Number of completed bars to supply.
        """
        del settings, stream  # Unused.
        return 0

    def initialize(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: tfs.Timeframe,
        settings: Mapping[str, object] | None,
    ) -> StateT:
        """Creates the initial state.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            settings: Overrides.

        Returns:
            Initial immutable state.

        Raises:
            ValueError: If the timeframe is unsupported.
        """
        if timeframe not in self.timeframes:
            raise ValueError(
                f"{self.plugin_id} does not support timeframe {timeframe}"
            )
        resolved = self.resolve_settings(settings)
        header = protocol.SeriesHeader(
            instrument_id=instrument_id,
            timeframe=timeframe,
            settings=tuple(sorted(resolved.items())),
        )
        return self._initial_state(header, resolved)

    def on_bar_closed(
        self, state: StateT, bar: protocol.BarInput
    ) -> tuple[StateT, protocol.PluginOutput]:
        """Applies one closed bar without mutating ``state``.

        Args:
            state: Previous state.
            bar: Closed bar input.

        Returns:
            ``(new_state, output)``.
        """
        header = self._header(state).advance(bar.candle)
        result = self._step(state, header, bar)
        header = dataclasses.replace(
            header,
            events=result.events,
            ended=result.ended,
            coverage_pct=result.coverage_pct,
        )
        new_state = self._with_header(result.state, header)
        output = protocol.PluginOutput(
            events=result.events, updated_aois=result.updated + result.ended
        )
        return new_state, output

    def snapshot(self, state: StateT) -> structures.IndicatorSnapshot:
        """Publishes the state.

        Args:
            state: Current state.

        Returns:
            Indicator snapshot as of the last processed bar.
        """
        header = self._header(state)
        live, values = self._publish(state)
        warmup = self.warmup_for(header.settings_map())
        return structures.IndicatorSnapshot(
            instrument_id=header.instrument_id,
            timeframe=header.timeframe,
            plugin_id=self.plugin_id,
            indicator_version=self.indicator_version,
            method=self.snapshot_method(values),
            bar_start_ms=header.bar_start_ms,
            as_of_ms=header.as_of_ms,
            aois=tuple(live) + header.ended,
            events=header.events,
            values=dict(values),
            warmup_complete=header.bars >= warmup,
            data_quality=quality.DataQuality(
                feed_state=header.feed_state,
                missing_bars=header.missing_bars,
                coverage_pct=header.coverage_pct,
            ),
        )

    def dump_state(self, state: StateT) -> object:
        """Encodes a JSON-compatible checkpoint.

        Args:
            state: Current state.

        Returns:
            Encoded state.
        """
        return state_codec.dump(state)

    def restore_state(self, blob: object) -> StateT:
        """Restores a checkpoint.

        Args:
            blob: Output of ``dump_state``.

        Returns:
            The restored state.
        """
        restored: StateT = state_codec.load(
            blob, _COMMON_STATE_TYPES + self.STATE_TYPES
        )
        return restored

    def info(self) -> dict[str, object]:
        """Returns the contract ``IndicatorPluginInfo`` object."""
        return {
            "plugin_id": self.plugin_id,
            "indicator_version": self.indicator_version,
            "schema_version": self.schema_version,
            "display_name": self.display_name,
            "description": self.description,
            "timeframes": [tf.value for tf in self.timeframes],
            "warmup_bars": self.warmup_bars,
            "required_streams": sorted(self.required_streams(None)),
            "input_schema": self.input_schema(),
            "output_kinds": list(self.output_kinds),
            "output_events": list(self.output_events),
            "output_values": [
                spec.to_contract() for spec in self.output_values
            ],
            "display": self.display.to_contract(),
            "licensing_status": self.licensing_status,
            "parity_status": self.parity_status,
        }

    def compute(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: tfs.Timeframe,
        bars: Sequence[protocol.BarInput],
        settings: Mapping[str, object] | None = None,
    ) -> list[structures.IndicatorSnapshot]:
        """Batch helper: runs every bar and snapshots after each.

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Series timeframe.
            bars: Closed bars in ascending order.
            settings: Overrides.

        Returns:
            One snapshot per input bar.
        """
        state = self.initialize(instrument_id, timeframe, settings)
        snapshots: list[structures.IndicatorSnapshot] = []
        for bar in bars:
            state, _ = self.on_bar_closed(state, bar)
            snapshots.append(self.snapshot(state))
        return snapshots

    # ----------------------------------------------------------- helpers

    def _header(self, state: StateT) -> protocol.SeriesHeader:
        """Reads the header field of a state.

        Args:
            state: Plugin state.

        Returns:
            The header.
        """
        header: protocol.SeriesHeader = state.header  # type: ignore[attr-defined]  # StateT is unbounded; the class contract requires a header field.
        return header

    def _with_header(
        self, state: StateT, header: protocol.SeriesHeader
    ) -> StateT:
        """Returns a copy of ``state`` with a new header.

        Args:
            state: Plugin state (frozen dataclass).
            header: New header.

        Returns:
            Updated state.
        """
        replaced: StateT = dataclasses.replace(
            state,
            header=header,  # type: ignore[type-var]  # StateT is unbounded; the class contract makes it a dataclass.
        )
        return replaced

    def event(
        self,
        header: protocol.SeriesHeader,
        name: structures.StructureEventName,
        *,
        price: decimal.Decimal | None = None,
        aoi_id: str | None = None,
        score: int | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> structures.StructureEvent:
        """Builds an event confirmed at the header's current bar.

        Args:
            header: Header already advanced to the current bar.
            name: Event name.
            price: Optional reference price.
            aoi_id: Optional related AOI.
            score: Optional 0-100 score.
            meta: Extra published fields.

        Returns:
            The event with a deterministic id.
        """
        event_id = ids.stable_id(
            f"{self.plugin_id}-evt",
            header.instrument_id,
            header.timeframe.value,
            name,
            header.bar_start_ms,
            aoi_id or "",
            price if price is not None else "",
        )
        return structures.StructureEvent(
            event_id=event_id,
            instrument_id=header.instrument_id,
            timeframe=header.timeframe,
            source=self.plugin_id,
            name=name,
            bar_start_ms=header.bar_start_ms,
            confirmed_at_ms=header.as_of_ms,
            price=price,
            aoi_id=aoi_id,
            score=score,
            meta=dict(meta or {}),
        )

    def aoi(
        self,
        header: protocol.SeriesHeader,
        *,
        aoi_id: str,
        kind: structures.AoiKind,
        side: structures.ZoneSide,
        lower: decimal.Decimal,
        upper: decimal.Decimal,
        created_at_ms: int,
        state: structures.AoiState = structures.AoiState.ACTIVE,
        quality_score: int | None = None,
        group_id: str | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> structures.Aoi:
        """Builds an AOI confirmed at the header's current bar.

        Args:
            header: Header advanced to the confirming bar.
            aoi_id: Deterministic id.
            kind: AOI kind.
            side: Directional side.
            lower: Lower bound.
            upper: Upper bound.
            created_at_ms: Start of the originating bar.
            state: Initial lifecycle state.
            quality_score: Optional 0-100 score.
            group_id: Optional plugin-defined id of the structure the AOI
                belongs to.
            meta: Extra published fields.

        Returns:
            The AOI.
        """
        return structures.Aoi(
            aoi_id=aoi_id,
            instrument_id=header.instrument_id,
            timeframe=header.timeframe,
            source=self.plugin_id,
            kind=kind,
            side=side,
            state=state,
            lower=lower,
            upper=upper,
            created_at_ms=created_at_ms,
            confirmed_at_ms=header.as_of_ms,
            as_of_ms=header.as_of_ms,
            indicator_version=self.indicator_version,
            method=self.METHOD,
            quality_score=quality_score,
            group_id=group_id,
            meta=dict(meta or {}),
        )

    # ------------------------------------------------------------- hooks

    @abc.abstractmethod
    def _initial_state(
        self,
        header: protocol.SeriesHeader,
        settings: Mapping[str, settings_lib.SettingValue],
    ) -> StateT:
        """Builds the initial plugin state.

        Args:
            header: Fresh header.
            settings: Resolved settings.

        Returns:
            The state before any bar, holding ``header``.
        """

    @abc.abstractmethod
    def _step(
        self,
        state: StateT,
        header: protocol.SeriesHeader,
        bar: protocol.BarInput,
    ) -> StepResult[StateT]:
        """Applies one bar.

        Args:
            state: Previous state (must not be mutated).
            header: Header advanced to this bar.
            bar: Bar input.

        Returns:
            The new state and the bar's events and AOI changes; the base
            class then stores the advanced header in the state.
        """

    @abc.abstractmethod
    def _publish(
        self, state: StateT
    ) -> tuple[Sequence[structures.Aoi], Mapping[str, structures.ValueType]]:
        """Returns live AOIs and published values.

        Args:
            state: Current state.

        Returns:
            ``(live_aois, values)``: the AOIs still live and the published
            scalar values, keyed as ``OUTPUT_VALUES`` declares.
        """


def end_aoi(
    aoi: structures.Aoi,
    header: protocol.SeriesHeader,
    state: structures.AoiState,
    reason: str | None = None,
) -> structures.Aoi:
    """Marks an AOI as no longer live at the current bar.

    Args:
        aoi: The AOI.
        header: Header advanced to the current bar.
        state: Terminal state, ``INVALIDATED`` or ``EXPIRED``.
        reason: Optional display-only end reason in the plugin's words
            (lower snake case, labelled by ``Display.end_reasons``).

    Returns:
        Updated AOI.

    Raises:
        ValueError: If ``state`` is live or ``reason`` is malformed.
    """
    if state.is_live:
        raise ValueError(f"{state} is not a terminal AOI state")
    return dataclasses.replace(
        aoi,
        state=state,
        invalidated_at_ms=header.as_of_ms,
        as_of_ms=header.as_of_ms,
        end_reason=reason,
    )


def touch_aoi(
    aoi: structures.Aoi,
    header: protocol.SeriesHeader,
    quality_score: int | None,
) -> structures.Aoi:
    """Marks an AOI as tested with a decayed score.

    Args:
        aoi: The AOI.
        header: Header advanced to the current bar.
        quality_score: New score.

    Returns:
        Updated AOI.
    """
    return dataclasses.replace(
        aoi,
        state=structures.AoiState.TESTED,
        quality_score=quality_score,
        as_of_ms=header.as_of_ms,
    )


AnyPlugin: TypeAlias = BasePlugin[Any]
"""Type alias for heterogeneous plugin collections."""
