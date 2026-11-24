# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Indicator outputs shared by indicators and the strategy engine.

Indicator plugins publish ``Aoi`` (area of interest, AOI) records,
``StructureEvent`` occurrences and scalar ``values`` inside an
``IndicatorSnapshot``. The strategy engine reads only these published types,
never a plugin's private state.

AOI kinds and event names are strings matching ``NAME_PATTERN``. Each plugin
declares the kinds, events and values it publishes in its contract
``IndicatorPluginInfo`` (``output_kinds``, ``output_events`` and
``output_values``): a kind belongs to exactly one installed plugin, several
plugins may publish one event name, and ``AOI_INVALIDATED`` /
``AOI_EXPIRED`` (``RESERVED_EVENTS``) are open to every plugin. Published
decimals are ``Decimal`` objects in-process and strings on the wire.

Bars, anchors and higher-timeframe visibility follow the instrument's session
calendar (``joseph.domain.sessions``).
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
import re
from collections.abc import Mapping
from typing import TypeAlias

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes

ValueType: TypeAlias = str | int | bool | decimal.Decimal | None
"""Type of one published scalar in ``IndicatorSnapshot.values``."""


class ZoneSide(enum.StrEnum):
    """Directional side of a structure (contract ``ZoneSide``)."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


AoiKind: TypeAlias = str
"""AOI kind (contract ``AoiKind``), declared by the publishing plugin."""

StructureEventName: TypeAlias = str
"""Event name (contract ``StructureEventName``), declared by its plugins."""

NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,47}$")
"""Pattern of AOI kinds and event names (upper snake case)."""

END_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
"""Pattern of ``Aoi.end_reason`` (contract ``EndReason``)."""

AOI_INVALIDATED = "AOI_INVALIDATED"
"""Reserved event: an AOI stopped being live before it expired."""

AOI_EXPIRED = "AOI_EXPIRED"
"""Reserved event: an AOI reached the end of its lifetime."""

RESERVED_EVENTS: frozenset[str] = frozenset({AOI_INVALIDATED, AOI_EXPIRED})
"""Event names any plugin may publish without declaring them."""


class AoiState(enum.StrEnum):
    """Lifecycle state of an AOI (contract ``AoiState``).

    ``ACTIVE`` and ``TESTED`` are live. ``INVALIDATED`` and ``EXPIRED`` have
    ended; ``Aoi.end_reason`` may say why in the plugin's own words.
    """

    ACTIVE = "active"
    TESTED = "tested"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"

    @property
    def is_live(self) -> bool:
        """Whether the AOI can still be touched by new price action."""
        return self in (AoiState.ACTIVE, AoiState.TESTED)


class IndicatorMethod(enum.StrEnum):
    """Calculation method (contract ``IndicatorMethod``).

    ``TRADE`` and ``TRADE_CLASSIFICATION`` outputs are never parity outputs.
    """

    PINE_PARITY = "pine_parity"
    TRADE = "trade"
    """Venue trade data with a published aggressor side (true delta)."""
    TRADE_CLASSIFICATION = "trade_classification"
    """Trades whose side was classified against the quote (Lee-Ready /
    quote rule) on stock exchanges: an estimate, not true taker delta."""


def _contract_value(value: object) -> object:
    """Converts in-process values to JSON-compatible contract values.

    Args:
        value: Scalar, decimal, mapping or sequence.

    Returns:
        The JSON-compatible equivalent with decimals as strings.
    """
    if isinstance(value, decimal.Decimal):
        return decimals.format_decimal(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _contract_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_contract_value(v) for v in value]
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class Aoi:
    """Area of interest produced by an indicator plugin.

    Zones span ``[lower, upper]``; levels (lines) use ``lower == upper``.

    Attributes:
        aoi_id: Deterministic identifier (see ``ids.stable_id``).
        instrument_id: Exact venue instrument.
        timeframe: Timeframe on which the AOI was detected.
        source: Plugin id that produced it.
        kind: AOI kind declared in the plugin's ``output_kinds``.
        side: Directional side.
        state: Lifecycle state as of ``as_of_ms``.
        lower: Lower price bound.
        upper: Upper price bound.
        created_at_ms: Start of the originating bar.
        confirmed_at_ms: Close time of the confirming bar.
        as_of_ms: Time of this record version.
        indicator_version: Plugin semantic version.
        method: Calculation method.
        invalidated_at_ms: When it stopped being live, if it has.
        quality_score: Plugin-specific 0-100 formation score. Not a win rate.
        group_id: Plugin-defined id of the structure the AOI belongs to
            (``IN_ZONE`` groups bind on it), if any.
        end_reason: Display-only reason why a non-live AOI ended, in the
            plugin's words (contract ``EndReason``), if any.
        meta: Extra plugin-published fields (JSON-compatible).
    """

    aoi_id: str
    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    source: str
    kind: AoiKind
    side: ZoneSide
    state: AoiState
    lower: decimal.Decimal
    upper: decimal.Decimal
    created_at_ms: int
    confirmed_at_ms: int
    as_of_ms: int
    indicator_version: str
    method: IndicatorMethod = IndicatorMethod.PINE_PARITY
    invalidated_at_ms: int | None = None
    quality_score: int | None = None
    group_id: str | None = None
    end_reason: str | None = None
    meta: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates names, bounds and timing.

        Raises:
            ValueError: If the kind or end reason breaks its pattern, lower
                exceeds upper or confirmation precedes creation.
        """
        if not NAME_PATTERN.match(self.kind):
            raise ValueError(f"AOI kind {self.kind!r} is not upper snake case")
        if self.end_reason is not None and not END_REASON_PATTERN.match(
            self.end_reason
        ):
            raise ValueError(f"end_reason {self.end_reason!r} is malformed")
        if self.lower > self.upper:
            raise ValueError("lower must be <= upper")
        if self.confirmed_at_ms < self.created_at_ms:
            raise ValueError("confirmed_at_ms precedes created_at_ms")
        if self.quality_score is not None and not (
            0 <= self.quality_score <= 100
        ):
            raise ValueError("quality_score must be within 0..100")

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``AoiRecord`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "schema_version": 1,
            "type": "aoi.updated",
            "instrument_id": str(self.instrument_id),
            "timeframe": self.timeframe.value,
            "aoi_id": self.aoi_id,
            "source": self.source,
            "kind": self.kind,
            "side": self.side.value,
            "state": self.state.value,
            "lower": decimals.format_decimal(self.lower),
            "upper": decimals.format_decimal(self.upper),
            "created_at_ms": self.created_at_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "invalidated_at_ms": self.invalidated_at_ms,
            "as_of_ms": self.as_of_ms,
            "quality_score": self.quality_score,
            "group_id": self.group_id,
            "end_reason": self.end_reason,
            "indicator_version": self.indicator_version,
            "method": self.method.value,
            "meta": _contract_value(dict(self.meta)),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class StructureEvent:
    """Event a plugin confirmed at a closed bar.

    Attributes:
        event_id: Deterministic identifier.
        instrument_id: Exact venue instrument.
        timeframe: Timeframe of the confirming bar.
        source: Plugin id.
        name: Event name declared in the plugin's ``output_events`` (or a
            reserved one).
        bar_start_ms: Start of the confirming bar.
        confirmed_at_ms: Close (exclusive ``end_ms``) of the confirming bar.
        price: Optional reference price (e.g. the level crossed).
        aoi_id: Optional related AOI.
        score: Optional 0-100 event score.
        meta: Extra plugin-published fields.
    """

    event_id: str
    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    source: str
    name: StructureEventName
    bar_start_ms: int
    confirmed_at_ms: int
    price: decimal.Decimal | None = None
    aoi_id: str | None = None
    score: int | None = None
    meta: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates the event name.

        Raises:
            ValueError: If the name is not upper snake case.
        """
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"event name {self.name!r} is not upper snake case"
            )

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``StructureEvent`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "event_id": self.event_id,
            "instrument_id": str(self.instrument_id),
            "timeframe": self.timeframe.value,
            "source": self.source,
            "name": self.name,
            "bar_start_ms": self.bar_start_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "price": decimals.format_optional(self.price),
            "aoi_id": self.aoi_id,
            "score": self.score,
            "meta": _contract_value(dict(self.meta)),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    """Published state of one plugin on one series as of a closed bar.

    Attributes:
        instrument_id: Exact venue instrument.
        timeframe: Series timeframe.
        plugin_id: Producing plugin.
        indicator_version: Plugin semantic version.
        method: Calculation method.
        bar_start_ms: Start of the last processed closed bar.
        as_of_ms: Close of that bar (its exclusive ``end_ms``, which the
            session calendar may cut short at a session close).
        aois: AOIs known at ``as_of_ms`` (live ones plus recently ended).
        events: Events confirmed on the last processed bar only.
        values: Published scalar values, keyed as the plugin's
            ``output_values`` declare.
        warmup_complete: False until ``warmup_bars`` have been processed.
        data_quality: Quality of the inputs.
    """

    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    plugin_id: str
    indicator_version: str
    method: IndicatorMethod
    bar_start_ms: int
    as_of_ms: int
    aois: tuple[Aoi, ...]
    events: tuple[StructureEvent, ...]
    values: Mapping[str, ValueType]
    warmup_complete: bool
    data_quality: quality.DataQuality

    def values_contract(self) -> dict[str, object]:
        """Serializes the published values alone (contract ``values``).

        Returns:
            JSON-compatible dict with decimals as strings.
        """
        return {
            key: _contract_value(value) for key, value in self.values.items()
        }

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``IndicatorSnapshot`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "schema_version": 1,
            "instrument_id": str(self.instrument_id),
            "timeframe": self.timeframe.value,
            "plugin_id": self.plugin_id,
            "indicator_version": self.indicator_version,
            "method": self.method.value,
            "as_of_ms": self.as_of_ms,
            "bar_start_ms": self.bar_start_ms,
            "aois": [aoi.to_contract() for aoi in self.aois],
            "events": [event.to_contract() for event in self.events],
            "values": self.values_contract(),
            "warmup_complete": self.warmup_complete,
            "data_quality": self.data_quality.to_contract(),
        }


SnapshotKey: TypeAlias = tuple[str, timeframes.Timeframe]
"""Key of ``BarFrame.snapshots``: ``(plugin_id, timeframe)``."""


@dataclasses.dataclass(frozen=True, slots=True)
class BarFrame:
    """Everything visible at the close of one signal-timeframe bar.

    Built by the indicator engine with strict no-look-ahead visibility: a
    higher-timeframe snapshot appears only after that higher bar closed at or
    before ``candle.end_ms``. The strategy evaluator consumes a sequence of
    frames and nothing else, so backtest, replay, paper and live share one
    evaluation path.

    Attributes:
        instrument_id: Exact venue instrument.
        timeframe: Signal timeframe.
        candle: The closed signal-timeframe candle (``is_final`` is True).
        snapshots: Latest visible snapshot per ``(plugin_id, timeframe)``.
        events: Events whose ``confirmed_at_ms`` falls in
            ``(candle.start_ms, candle.end_ms]`` across every timeframe.
        feed_state: Provenance of the signal candle.
        missing_bars: Gaps detected up to this frame.
    """

    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    candle: market.Candle
    snapshots: Mapping[SnapshotKey, IndicatorSnapshot]
    events: tuple[StructureEvent, ...]
    feed_state: quality.FeedState
    missing_bars: int = 0

    @property
    def close_ms(self) -> int:
        """The exclusive end (close time) of the signal bar."""
        return self.candle.end_ms

    def snapshot(
        self, plugin_id: str, timeframe: timeframes.Timeframe
    ) -> IndicatorSnapshot | None:
        """Looks up the visible snapshot for a plugin and timeframe.

        Args:
            plugin_id: Plugin identifier.
            timeframe: Series timeframe.

        Returns:
            The snapshot, or ``None`` if not computed or not yet visible.
        """
        return self.snapshots.get((plugin_id, timeframe))
