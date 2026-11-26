# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Rule AST v2 models mirroring ``strategy.schema.json``.

The visual builder emits this typed tree; nothing is ever evaluated as code.
Models are immutable Pydantic v2 classes with ``extra="forbid"`` so unknown
or misspelled fields are rejected instead of silently ignored. Decimal
fields accept contract ``DecimalString`` text only and serialize back to
canonical strings.
"""

from __future__ import annotations

import decimal
import json
import re
from typing import Annotated
from typing import Literal
from typing import TypeAlias

import pydantic

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import timeframes

_DECIMAL_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_INSTRUMENT_PATTERN = (
    r"^(binance|hyperliquid|lighter|bitget|mexc|twse|tpex|nasdaq|nyse|cboe"
    r"|treasury|mock):(perp|spot|future|stock|etf|rate)"
    r":[A-Za-z0-9._/-]{1,64}$"
)
"""Contract ``InstrumentId`` (rate series parse, then fail compilation)."""


def _parse_decimal_string(value: object) -> decimal.Decimal:
    """Parses a contract ``DecimalString``.

    Args:
        value: Raw input; must be a string (or an already parsed Decimal).

    Returns:
        The exact decimal.

    Raises:
        ValueError: If the value is not a canonical decimal string.
    """
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise ValueError("decimal must be finite")
        return value
    if not isinstance(value, str) or not _DECIMAL_PATTERN.match(value):
        raise ValueError("expected a decimal string such as '2.0'")
    return decimal.Decimal(value)


def _parse_non_negative(value: object) -> decimal.Decimal:
    """Parses a non-negative contract decimal string.

    Args:
        value: Raw input.

    Returns:
        The exact non-negative decimal.

    Raises:
        ValueError: If the value is negative or not a decimal string.
    """
    result = _parse_decimal_string(value)
    if result < 0:
        raise ValueError("decimal must be non-negative")
    return result


DecimalStr: TypeAlias = Annotated[
    decimal.Decimal,
    pydantic.BeforeValidator(_parse_decimal_string),
    pydantic.PlainSerializer(decimals.format_decimal, return_type=str),
]
"""Decimal transported as a contract ``DecimalString``."""

NonNegativeDecimalStr: TypeAlias = Annotated[
    decimal.Decimal,
    pydantic.BeforeValidator(_parse_non_negative),
    pydantic.PlainSerializer(decimals.format_decimal, return_type=str),
]
"""Non-negative decimal transported as a string."""

StrictInt: TypeAlias = Annotated[int, pydantic.Strict()]
"""Integer that rejects strings and floats."""

StrictBool: TypeAlias = Annotated[bool, pydantic.Strict()]
"""Boolean that rejects truthy strings and numbers."""

NodeId: TypeAlias = Annotated[str, pydantic.Field(max_length=64)]
"""Editor id of a node, echoed in the reason tree."""

NodeLabel: TypeAlias = Annotated[str, pydantic.Field(max_length=200)]
"""Display label of a node."""

Bars200: TypeAlias = Annotated[
    int, pydantic.Strict(), pydantic.Field(ge=0, le=200)
]
"""Bar count from 0 to 200."""

Bars200Min1: TypeAlias = Annotated[
    int, pydantic.Strict(), pydantic.Field(ge=1, le=200)
]
"""Bar count from 1 to 200."""

InstrumentIdStr: TypeAlias = Annotated[
    str, pydantic.Field(pattern=_INSTRUMENT_PATTERN)
]
"""Canonical instrument id text, e.g. ``binance:perp:BTCUSDT``."""

PluginId: TypeAlias = Annotated[
    str, pydantic.Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
]
"""Indicator plugin id (contract ``PluginId``)."""

AoiKindName: TypeAlias = Annotated[
    str, pydantic.Field(pattern=r"^[A-Z][A-Z0-9_]{0,47}$")
]
"""AOI kind declared by a plugin (contract ``AoiKind``)."""

EventName: TypeAlias = Annotated[
    str, pydantic.Field(pattern=r"^[A-Z][A-Z0-9_]{0,47}$")
]
"""Event name declared by a plugin (contract ``StructureEventName``)."""

LocalTime: TypeAlias = Annotated[
    str, pydantic.Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
]
"""Local wall-clock time ``HH:MM``."""

Side: TypeAlias = Literal["bullish", "bearish"]
"""Required AOI side."""


class _Model(pydantic.BaseModel):
    """Immutable model base that rejects unknown fields."""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)


class AllNode(_Model):
    """TRUE when every child is TRUE (Kleene conjunction).

    Attributes:
        op: Discriminator ``ALL``.
        id: Stable editor id echoed in the reason tree.
        label: Optional display label.
        children: 1-32 child nodes.
    """

    op: Literal["ALL"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    children: Annotated[
        list[RuleNode], pydantic.Field(min_length=1, max_length=32)
    ]


class AnyNode(_Model):
    """TRUE when any child is TRUE (Kleene disjunction).

    Attributes:
        op: Discriminator ``ANY``.
        id: Stable editor id.
        label: Optional display label.
        children: 1-32 child nodes.
    """

    op: Literal["ANY"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    children: Annotated[
        list[RuleNode], pydantic.Field(min_length=1, max_length=32)
    ]


class NotNode(_Model):
    """Kleene negation of one child.

    Attributes:
        op: Discriminator ``NOT``.
        id: Stable editor id.
        label: Optional display label.
        child: Negated node.
    """

    op: Literal["NOT"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    child: RuleNode


def _unique_weekdays(value: list[int]) -> list[int]:
    """Rejects repeated weekdays.

    Args:
        value: ISO weekdays.

    Returns:
        The weekdays unchanged.

    Raises:
        ValueError: If a weekday repeats.
    """
    if len(set(value)) != len(value):
        raise ValueError("weekdays must be unique")
    return value


class ValueMatch(_Model):
    """One STATE clause: a published value equals one of ``any_of``.

    Values compare with the same JSON type, so a string never equals a
    boolean.

    Attributes:
        field: Published value key.
        any_of: Accepted values.
    """

    field: Annotated[str, pydantic.Field(max_length=64)]
    any_of: Annotated[
        list[Annotated[str, pydantic.Strict()] | StrictBool],
        pydantic.Field(min_length=1, max_length=16),
    ]


class StateNode(_Model):
    """Published indicator state on one or more timeframes.

    Attributes:
        op: Discriminator ``STATE``.
        id: Stable editor id.
        label: Optional display label.
        plugin_id: Plugin whose latest warm snapshot is read.
        timeframes: Timeframes checked.
        where: Clauses that must all match on a timeframe.
        match: ``ALL`` or ``K_OF_N``.
        k: Threshold for ``K_OF_N``.
    """

    op: Literal["STATE"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    plugin_id: PluginId
    timeframes: Annotated[
        list[timeframes.Timeframe], pydantic.Field(min_length=1, max_length=6)
    ]
    where: Annotated[
        list[ValueMatch], pydantic.Field(min_length=1, max_length=8)
    ]
    match: Literal["ALL", "K_OF_N"] = "ALL"
    k: Annotated[int, pydantic.Strict(), pydantic.Field(ge=1, le=6)] | None = (
        None
    )


class InZoneNode(_Model):
    """Price inside a live AOI of the given kinds on ``tf``.

    Attributes:
        op: Discriminator ``IN_ZONE``.
        id: Stable editor id.
        label: Optional display label.
        kinds: Acceptable AOI kinds.
        side: Required AOI side, if any.
        tf: AOI detection timeframe.
        price_source: Candle price compared with the zone.
        group: Nodes sharing a group must agree on the AOI ``group_id``.
    """

    op: Literal["IN_ZONE"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    kinds: Annotated[
        list[AoiKindName], pydantic.Field(min_length=1, max_length=8)
    ]
    side: Side | None = None
    tf: timeframes.Timeframe
    price_source: Literal["close", "high", "low", "hl2"] = "close"
    group: Annotated[str, pydantic.Field(max_length=32)] | None = None


class AoiTouchNode(_Model):
    """A recent closed bar touched a previously confirmed AOI.

    Attributes:
        op: Discriminator ``AOI_TOUCH``.
        id: Stable editor id.
        label: Optional display label.
        kinds: Acceptable AOI kinds.
        side: Required AOI side.
        tf: AOI detection timeframe (defaults to the signal timeframe).
        within_bars: Lookback in closed signal bars (0 = current bar).
        touch: Intersection rule.
        require_active: AOI must still be live at evaluation time.
        min_quality: Minimum plugin quality score.
    """

    op: Literal["AOI_TOUCH"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    kinds: Annotated[
        list[AoiKindName], pydantic.Field(min_length=1, max_length=8)
    ]
    side: Side
    tf: timeframes.Timeframe | None = None
    within_bars: Bars200
    touch: Literal["WICK", "BODY", "CLOSE"] = "WICK"
    require_active: StrictBool = True
    min_quality: (
        Annotated[int, pydantic.Strict(), pydantic.Field(ge=0, le=100)] | None
    ) = None


class AoiOverlapNode(_Model):
    """Two live AOIs of different kind sets overlap.

    Attributes:
        op: Discriminator ``AOI_OVERLAP``.
        id: Stable editor id.
        label: Optional display label.
        kinds_a: First kind set.
        kinds_b: Second kind set.
        side: Required side for both AOIs.
        tf: Detection timeframe (defaults to the signal timeframe).
        min_overlap_pct: Minimum overlap as percent of the smaller zone.
    """

    op: Literal["AOI_OVERLAP"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    kinds_a: Annotated[list[AoiKindName], pydantic.Field(min_length=1)]
    kinds_b: Annotated[list[AoiKindName], pydantic.Field(min_length=1)]
    side: Side
    tf: timeframes.Timeframe | None = None
    min_overlap_pct: NonNegativeDecimalStr | None = None


class EventRef(_Model):
    """Reference to an indicator event on a timeframe.

    Attributes:
        name: Event name.
        source: Plugin that must publish the event; any plugin's events of
            that name count when omitted.
        tf: Event timeframe (defaults to the signal timeframe).
    """

    name: EventName
    source: PluginId | None = None
    tf: timeframes.Timeframe | None = None


class EventNode(_Model):
    """An indicator event confirmed within the lookback.

    Attributes:
        op: Discriminator ``EVENT``.
        id: Stable editor id.
        label: Optional display label.
        name: Event name.
        source: Plugin that must publish the event; any plugin's events of
            that name count when omitted.
        tf: Event timeframe (defaults to the signal timeframe).
        within_bars: Lookback in closed signal bars (0 = current bar).
    """

    op: Literal["EVENT"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    name: EventName
    source: PluginId | None = None
    tf: timeframes.Timeframe | None = None
    within_bars: Bars200


class SequenceNode(_Model):
    """Ordered events on distinct bars ending on the evaluated bar.

    Attributes:
        op: Discriminator ``SEQUENCE``.
        id: Stable editor id.
        label: Optional display label.
        within_bars: Window length in bars, including the evaluated bar.
        steps: 2-8 ordered event references.
    """

    op: Literal["SEQUENCE"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    within_bars: Bars200Min1
    steps: Annotated[list[EventRef], pydantic.Field(min_length=2, max_length=8)]


class ConstOperand(_Model):
    """Constant decimal operand.

    Attributes:
        kind: Discriminator ``const``.
        value: The constant.
    """

    kind: Literal["const"]
    value: DecimalStr


class CandleOperand(_Model):
    """Signal-candle field operand.

    Attributes:
        kind: Discriminator ``candle``.
        tf: Candle timeframe (signal timeframe only in this version).
        field: OHLCV field.
        bars_ago: Offset in closed signal bars.
    """

    kind: Literal["candle"]
    tf: timeframes.Timeframe | None = None
    field: Literal["open", "high", "low", "close", "base_volume"]
    bars_ago: Bars200 = 0


class IndicatorOperand(_Model):
    """Published indicator value operand.

    Attributes:
        kind: Discriminator ``indicator``.
        plugin_id: Producing plugin.
        tf: Series timeframe (defaults to the signal timeframe).
        field: Published value key.
        bars_ago: Offset in closed signal bars.
    """

    kind: Literal["indicator"]
    plugin_id: str
    tf: timeframes.Timeframe | None = None
    field: Annotated[str, pydantic.Field(max_length=64)]
    bars_ago: Bars200 = 0


class MarketOperand(_Model):
    """Market context operand (funding, open interest, spread, turnover).

    Attributes:
        kind: Discriminator ``market``.
        field: Market field.
    """

    kind: Literal["market"]
    field: Literal[
        "funding_8h_pct",
        "open_interest_usd",
        "oi_change_pct",
        "spread_pct",
        "turnover_24h_quote",
    ]


Operand: TypeAlias = Annotated[
    ConstOperand | CandleOperand | IndicatorOperand | MarketOperand,
    pydantic.Field(discriminator="kind"),
]
"""Discriminated operand union."""


class CompareNode(_Model):
    """Binary comparison of two operands.

    Attributes:
        op: Discriminator ``COMPARE``.
        id: Stable editor id.
        label: Optional display label.
        left: Left operand.
        cmp: Comparator.
        right: Right operand.
    """

    op: Literal["COMPARE"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    left: Operand
    cmp: Literal["gt", "gte", "lt", "lte", "eq", "neq"]
    right: Operand


class InRangeNode(_Model):
    """Operand inside ``[low, high]`` (or open interval when exclusive).

    Attributes:
        op: Discriminator ``IN_RANGE``.
        id: Stable editor id.
        label: Optional display label.
        value: Tested operand.
        low: Lower bound operand.
        high: Upper bound operand.
        inclusive: Whether bounds are inclusive.
    """

    op: Literal["IN_RANGE"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    value: Operand
    low: Operand
    high: Operand
    inclusive: StrictBool = True


class CrossesNode(_Model):
    """``left`` crossed ``right`` between the previous and current bar.

    ``ABOVE``: at or below on the previous bar, above on the current one;
    ``BELOW`` mirrors it.

    Attributes:
        op: Discriminator ``CROSSES``.
        id: Stable editor id.
        label: Optional display label.
        left: Moving operand.
        right: Reference operand.
        direction: ``ABOVE`` or ``BELOW``.
    """

    op: Literal["CROSSES"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    left: Operand
    right: Operand
    direction: Literal["ABOVE", "BELOW"]


class PersistForNode(_Model):
    """Child TRUE on each of the last ``bars`` closed bars.

    Attributes:
        op: Discriminator ``PERSIST_FOR``.
        id: Stable editor id.
        label: Optional display label.
        bars: Number of consecutive bars.
        child: Persisting node.
    """

    op: Literal["PERSIST_FOR"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    bars: Bars200Min1
    child: RuleNode


class WithinBarsNode(_Model):
    """Child TRUE on at least one of the last ``bars`` + 1 bars.

    Attributes:
        op: Discriminator ``WITHIN_BARS``.
        id: Stable editor id.
        label: Optional display label.
        bars: Lookback in bars.
        child: Tested node.
    """

    op: Literal["WITHIN_BARS"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    bars: Bars200
    child: RuleNode


class LocalTimeWindow(_Model):
    """Local-time window ``[start, end)``; ``end <= start`` wraps midnight.

    Attributes:
        start: Inclusive start ``HH:MM``.
        end: Exclusive end ``HH:MM``.
    """

    start: LocalTime
    end: LocalTime


class TimeWindowNode(_Model):
    """Evaluated bar starts inside a local-time window.

    Attributes:
        op: Discriminator ``TIME_WINDOW``.
        id: Stable editor id.
        label: Optional display label.
        time_zone: IANA time zone of the windows.
        windows: Accepted windows.
        weekdays: Accepted ISO local weekdays (1 = Monday); every day when
            omitted.
    """

    op: Literal["TIME_WINDOW"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    time_zone: Annotated[str, pydantic.Field(min_length=1, max_length=64)]
    windows: Annotated[
        list[LocalTimeWindow], pydantic.Field(min_length=1, max_length=8)
    ]
    weekdays: (
        Annotated[
            list[Annotated[int, pydantic.Strict(), pydantic.Field(ge=1, le=7)]],
            pydantic.Field(min_length=1, max_length=7),
            pydantic.AfterValidator(_unique_weekdays),
        ]
        | None
    ) = None


class RiskRewardNode(_Model):
    """Projected net reward/risk is at least ``min_rr``.

    Attributes:
        op: Discriminator ``RISK_REWARD``.
        id: Stable editor id.
        label: Optional display label.
        min_rr: Minimum net reward/risk.
    """

    op: Literal["RISK_REWARD"]
    id: NodeId | None = None
    label: NodeLabel | None = None
    min_rr: NonNegativeDecimalStr


RuleNode: TypeAlias = Annotated[
    AllNode
    | AnyNode
    | NotNode
    | StateNode
    | InZoneNode
    | AoiTouchNode
    | AoiOverlapNode
    | EventNode
    | SequenceNode
    | CompareNode
    | InRangeNode
    | CrossesNode
    | PersistForNode
    | WithinBarsNode
    | TimeWindowNode
    | RiskRewardNode,
    pydantic.Field(discriminator="op"),
]
"""Discriminated union of every rule node on ``op``."""

AnyRuleNode: TypeAlias = (
    AllNode
    | AnyNode
    | NotNode
    | StateNode
    | InZoneNode
    | AoiTouchNode
    | AoiOverlapNode
    | EventNode
    | SequenceNode
    | CompareNode
    | InRangeNode
    | CrossesNode
    | PersistForNode
    | WithinBarsNode
    | TimeWindowNode
    | RiskRewardNode
)
"""Plain union of node classes for type annotations."""


class Universe(_Model):
    """Instruments a strategy may evaluate.

    Attributes:
        qualification: ``QUALIFIED_ONLY`` or ``ANY``.
        venues: Allowed venues.
        whitelist: If non-empty, only these instruments.
        blacklist: Excluded instruments.
        min_qualified_minutes: Minimum continuous qualification time.
    """

    qualification: Literal["QUALIFIED_ONLY", "ANY"]
    venues: Annotated[list[instruments.Venue], pydantic.Field(min_length=1)]
    whitelist: list[InstrumentIdStr] = pydantic.Field(default_factory=list)
    blacklist: list[InstrumentIdStr] = pydantic.Field(default_factory=list)
    min_qualified_minutes: Annotated[
        int, pydantic.Strict(), pydantic.Field(ge=0)
    ] = 0


class RiskModel(_Model):
    """Stop, target and minimum reward/risk policy.

    Attributes:
        min_rr: Strategy-level floor for net reward/risk on every entry.
        stop_model: How the protective stop is placed.
        stop_buffer_atr: Stop buffer in ATR(14) multiples.
        target_model: How the target is placed.
        target_kinds: AOI kinds ``NEAREST_AOI`` may target.
        fixed_r: Reward multiple for ``FIXED_R`` targets.
        fee_bps_estimate: Estimated cost per side in basis points.
        max_risk_pct_equity: Sizing hint for the execution engine.
    """

    min_rr: NonNegativeDecimalStr
    stop_model: Literal["AOI_INVALIDATION", "SWING", "ATR"]
    stop_buffer_atr: NonNegativeDecimalStr | None = None
    target_model: Literal["NEAREST_AOI", "FIXED_R"]
    target_kinds: (
        Annotated[list[AoiKindName], pydantic.Field(min_length=1, max_length=8)]
        | None
    ) = None
    fixed_r: NonNegativeDecimalStr | None = None
    fee_bps_estimate: NonNegativeDecimalStr | None = None
    max_risk_pct_equity: NonNegativeDecimalStr | None = None


ChannelName: TypeAlias = Literal[
    "UI", "TELEGRAM", "WHATSAPP", "WEB_PUSH", "LINE", "EMAIL", "WEBHOOK"
]
"""Notification channels selectable in a strategy."""


def _default_channels() -> list[ChannelName]:
    """Returns the default notification channels.

    Returns:
        ``["UI"]``.
    """
    return ["UI"]


SignalKindName: TypeAlias = Literal[
    "WATCHING_AOI",
    "PRE_SIGNAL",
    "ENTRY_CONFIRMED",
    "ENTRY_BLOCKED",
    "AOI_INVALIDATED",
    "SETUP_EXPIRED",
    "QUALIFICATION_CHANGED",
    "POSITION_EXITED",
]
"""Contract ``SignalKind`` values."""

DEFAULT_EMIT: tuple[SignalKindName, ...] = ("ENTRY_CONFIRMED", "ENTRY_BLOCKED")
"""Kinds emitted when ``notification.emit`` is omitted."""


class NotificationPolicy(_Model):
    """Notification and de-duplication policy.

    Attributes:
        cooldown_bars: Bars after a fired entry during which new entries
            on the same instrument/direction are blocked.
        once_per_aoi: Fire at most once per AOI.
        emit: Signal kinds the user wants to receive.
        channels: Notification channels allowed for this strategy.
    """

    cooldown_bars: Annotated[
        int, pydantic.Strict(), pydantic.Field(ge=0, le=10000)
    ]
    once_per_aoi: StrictBool
    emit: list[SignalKindName] = pydantic.Field(
        default_factory=lambda: list(DEFAULT_EMIT)
    )
    channels: list[ChannelName] = pydantic.Field(
        default_factory=_default_channels
    )


class SetupPolicy(_Model):
    """Setup lifetime policy.

    Attributes:
        expire_after_bars: Bars after which an unfired setup expires.
    """

    expire_after_bars: Annotated[
        int, pydantic.Strict(), pydantic.Field(ge=1, le=1000)
    ] = 24


class DataPolicy(_Model):
    """Input freshness policy.

    Attributes:
        max_data_age_ms: Maximum time from the evaluated bar's close to the
            evaluation; an older bar evaluates to UNKNOWN.
        allow_rest_poll: Whether polled (POLL) candles are acceptable.
    """

    max_data_age_ms: Annotated[int, pydantic.Strict(), pydantic.Field(ge=0)] = (
        120_000
    )
    allow_rest_poll: StrictBool = True


class StrategyDefinition(_Model):
    """User-authored strategy (Rule AST v2).

    Attributes:
        schema_version: Always 2.
        strategy_id: Slug unique per tenant.
        name: Display name.
        description: Optional description.
        direction: ``LONG`` or ``SHORT``.
        signal_timeframe: Timeframe whose closed bars trigger evaluation.
        universe: Instrument universe.
        when: Root rule node.
        risk: Risk model.
        notification: Notification policy.
        setup: Setup policy.
        data_policy: Freshness policy.
        execution_mode: Requested mode (AUTO_LIVE additionally needs an
            execution-engine deployment with consent).
    """

    schema_version: Literal[2]
    strategy_id: Annotated[
        str, pydantic.Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    ]
    name: Annotated[str, pydantic.Field(min_length=1, max_length=200)]
    description: Annotated[str, pydantic.Field(max_length=2000)] | None = None
    direction: Literal["LONG", "SHORT"]
    signal_timeframe: timeframes.Timeframe
    universe: Universe
    when: RuleNode
    risk: RiskModel
    notification: NotificationPolicy
    setup: SetupPolicy = pydantic.Field(default_factory=SetupPolicy)
    data_policy: DataPolicy = pydantic.Field(default_factory=DataPolicy)
    execution_mode: Literal["ALERT_ONLY", "PAPER", "SHADOW", "AUTO_LIVE"]

    def to_contract(self) -> dict[str, object]:
        """Serializes to a contract ``StrategyDefinition`` dict.

        Defaults are materialized and absent optionals omitted, so the
        output is the canonical form used for hashing.

        Returns:
            JSON-compatible dict.
        """
        dumped: dict[str, object] = self.model_dump(
            mode="json", exclude_none=True
        )
        return dumped

    def canonical_json(self) -> str:
        """Returns canonical JSON (sorted keys, no whitespace).

        Returns:
            Canonical text; equal definitions always produce equal text.
        """
        return json.dumps(
            self.to_contract(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def definition_hash(self) -> str:
        """Returns ``sha256:<hex>`` of the canonical JSON.

        Returns:
            Content hash identifying the trading meaning of the rule.
        """
        digest = ids.sha256_hex(self.canonical_json().encode("utf-8"))
        return f"sha256:{digest}"


def parse_definition(payload: object) -> StrategyDefinition:
    """Validates a JSON-compatible payload into a definition.

    Args:
        payload: Parsed JSON object.

    Returns:
        The validated definition.

    Raises:
        pydantic.ValidationError: If the payload violates the schema.
    """
    return StrategyDefinition.model_validate(payload)


# The recursive models refer to ``RuleNode`` before it is defined; resolve
# those forward references once every node class exists.
for _model in (
    AllNode,
    AnyNode,
    NotNode,
    PersistForNode,
    WithinBarsNode,
    StrategyDefinition,
):
    _model.model_rebuild()
