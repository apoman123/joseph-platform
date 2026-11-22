"""Market-structure outputs shared by indicators and the strategy engine.

Indicator plugins publish ``Aoi`` records, ``StructureEvent`` occurrences and
scalar ``values`` inside an ``IndicatorSnapshot``. The strategy engine reads
only these published types, never a plugin's private state.

Published values contract (``IndicatorSnapshot.values`` keys per plugin; all
decimals are ``Decimal`` objects in-process and strings on the wire):

Every plugin is an adapter around the external ``smc_indicators`` library
(``apoman123/PineScripts``), a line-by-line port of the Pine sources.

``daily_bias`` (run on each timeframe it is configured for)
    ``trend``: ``"BULLISH" | "BEARISH" | "NEUTRAL"``
    ``last_event``: ``"BOS" | "CHOCH" | "INITIAL" | "NONE"``
    ``last_event_direction``: ``"BULLISH" | "BEARISH" | None``
    ``event_age``: bars since the last event, ``int | None``
    ``last_pivot_high`` / ``last_pivot_low``: ``Decimal | None``
    ``pdh`` / ``pdl`` / ``previous_day_mid``: previous 1D bar, ``Decimal |
    None``; ``range_location``: ``"ABOVE" | "BELOW" | "PREMIUM" |
    "DISCOUNT" | None`` of the close versus that range
    ``session``: ``"ASIA" | "LONDON" | "NEW_YORK" | "NONE"`` for the bar
    (New York windows 20:00-00:00, 02:00-05:00, 08:30-11:00)
    ``bias``: 1D/4H/1H consensus ``"BULLISH" | "BEARISH" | "NEUTRAL"``;
    ``agreement``: dashboard agreement code; ``d1_trend`` / ``h4_trend`` /
    ``h1_trend``: each timeframe's structure as seen by the dashboard
``market_structure``
    ``trend``, ``last_event`` (``"BOS" | "MSS" | "INITIAL" | "NONE"``),
    ``last_event_direction``, ``event_age``, ``last_pivot_high`` /
    ``last_pivot_low``, ``high_type`` / ``low_type`` (``H HH LH EQH`` /
    ``L HL LL EQL``), ``swing_alignment``, ``agreement``,
    ``pending_high`` / ``pending_low`` (unbroken latest pivots).
    AOIs of kind SWING_HIGH / SWING_LOW (lines).
``fvg_ifvg``
    ``active_count``: ``int``; ``atr``. AOIs of kind FVG / IFVG.
``order_breaker_blocks``
    ``active_count``: ``int``; ``atr``. AOIs of kind OB / BREAKER.
``liquidity_sweeps``
    ``active_count``: ``int``. AOIs of kind BSL / SSL / EQH / EQL (lines).
``order_flow_pressure``
    ``delta_pct``: ``Decimal | None``; ``method``: ``"taker_volume"`` (true
    delta from the venue's taker buy volume; snapshot method ``trade``) or
    ``"candle_direction"`` (Pine intrabar estimate)
    ``buy_volume`` / ``sell_volume``: ``Decimal | None`` (base units)
    ``coverage_pct``: ``Decimal | None``; ``intrabar_count``: ``int``
    ``rel_volume``: ``Decimal | None``
    ``prior_high`` / ``prior_low``: the 20 bars before this one
``auction_value_areas``
    ``vah`` / ``poc`` / ``val``: chart-timeframe profile, ``Decimal | None``
    ``vah_1h`` / ``poc_1h`` / ``val_1h``: 1H profile, ``Decimal | None``
``premium_discount_ote``
    ``range_id``: ``str | None``; ``direction``: ``"BULLISH" | "BEARISH" |
    None``; ``range_high`` / ``range_low`` / ``eq`` / ``ote_lower`` /
    ``ote_upper``: ``Decimal | None``; ``in_premium`` / ``in_discount`` /
    ``in_ote``: ``bool | None`` for the bar close; ``position_pct``,
    ``zone`` and ``read`` mirror the Pine dashboard.
    AOIs of kind PREMIUM / DISCOUNT / EQUILIBRIUM / OTE carry ``range_id``.
``anchored_vwap`` (Python-native study, TradingView VWAP definition)
    ``vwap`` / ``stdev`` / ``upper_1..3`` / ``lower_1..3``: ``Decimal |
    None`` (a band is ``None`` when its multiplier is 0); ``anchor_ms``:
    ``int | None`` (current anchor bar); ``bars``: ``int``;
    ``anchor_in_window``: ``bool`` (False: the anchor bar was not computed,
    every value is ``None``)
``regression_channel`` (Python-native study, least squares)
    ``start_ms``: ``int | None`` (oldest bar of the fit); ``start`` /
    ``end`` / ``upper_start`` / ``upper_end`` / ``lower_start`` /
    ``lower_end`` / ``slope`` (price per bar) / ``stdev`` (sample stdev of
    residuals) / ``max_up`` / ``max_down``: ``Decimal | None``;
    ``pearson_r``: ``Decimal | None``; ``bars``: ``int``;
    ``anchor_in_window``: ``bool``
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Mapping

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes

ValueType = str | int | bool | decimal.Decimal | None


class ZoneSide(enum.StrEnum):
    """Directional side of a structure (contract ``ZoneSide``)."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class AoiKind(enum.StrEnum):
    """Area-of-interest kinds (contract ``AoiKind``)."""

    FVG = "FVG"
    IFVG = "IFVG"
    OB = "OB"
    BREAKER = "BREAKER"
    BSL = "BSL"
    SSL = "SSL"
    EQH = "EQH"
    EQL = "EQL"
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"
    OTE = "OTE"
    VAH = "VAH"
    POC = "POC"
    VAL = "VAL"
    PDH = "PDH"
    PDL = "PDL"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


class AoiState(enum.StrEnum):
    """Lifecycle state of an AOI (contract ``AoiState``)."""

    ACTIVE = "active"
    TESTED = "tested"
    INVERTED = "inverted"
    SWEPT = "swept"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"

    @property
    def is_live(self) -> bool:
        """Whether the AOI can still be touched by new price action."""
        return self in (AoiState.ACTIVE, AoiState.TESTED)


class IndicatorMethod(enum.StrEnum):
    """Calculation method; ``TRADE`` outputs are never parity outputs."""

    PINE_PARITY = "pine_parity"
    TRADE = "trade"


class StructureEventName(enum.StrEnum):
    """Market-structure event names (contract ``StructureEventName``)."""

    BOS_BULL = "BOS_BULL"
    BOS_BEAR = "BOS_BEAR"
    CHOCH_BULL = "CHOCH_BULL"
    CHOCH_BEAR = "CHOCH_BEAR"
    INITIAL_BREAK_BULL = "INITIAL_BREAK_BULL"
    INITIAL_BREAK_BEAR = "INITIAL_BREAK_BEAR"
    FVG_FORMED = "FVG_FORMED"
    IFVG_FORMED = "IFVG_FORMED"
    OB_FORMED = "OB_FORMED"
    BREAKER_FORMED = "BREAKER_FORMED"
    AOI_INVALIDATED = "AOI_INVALIDATED"
    AOI_EXPIRED = "AOI_EXPIRED"
    SSL_SWEEP = "SSL_SWEEP"
    BSL_SWEEP = "BSL_SWEEP"
    SSL_BREAK = "SSL_BREAK"
    BSL_BREAK = "BSL_BREAK"
    BUYER_FAILURE = "BUYER_FAILURE"
    SELLER_FAILURE = "SELLER_FAILURE"
    BUYER_BREAKOUT = "BUYER_BREAKOUT"
    SELLER_BREAKOUT = "SELLER_BREAKOUT"
    RANGE_RESET = "RANGE_RESET"
    VALUE_AREA_UPDATED = "VALUE_AREA_UPDATED"
    SWING_HH = "SWING_HH"
    SWING_HL = "SWING_HL"
    SWING_LH = "SWING_LH"
    SWING_LL = "SWING_LL"
    SWING_EQH = "SWING_EQH"
    SWING_EQL = "SWING_EQL"


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

    Lines (liquidity pools, PDH/PDL, VAH/POC/VAL) use ``lower == upper``.

    Attributes:
        aoi_id: Deterministic identifier (see ``ids.stable_id``).
        instrument_id: Exact venue instrument.
        timeframe: Timeframe on which the AOI was detected.
        source: Plugin id that produced it.
        kind: AOI kind.
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
        range_id: Dealing range id for premium/discount/OTE zones.
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
    range_id: str | None = None
    meta: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates bounds and timing.

        Raises:
            ValueError: If bounds are inverted or confirmation precedes
                creation.
        """
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
            "kind": self.kind.value,
            "side": self.side.value,
            "state": self.state.value,
            "lower": decimals.format_decimal(self.lower),
            "upper": decimals.format_decimal(self.upper),
            "created_at_ms": self.created_at_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "invalidated_at_ms": self.invalidated_at_ms,
            "as_of_ms": self.as_of_ms,
            "quality_score": self.quality_score,
            "range_id": self.range_id,
            "indicator_version": self.indicator_version,
            "method": self.method.value,
            "meta": _contract_value(dict(self.meta)),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class StructureEvent:
    """Market-structure event confirmed at a closed bar.

    Attributes:
        event_id: Deterministic identifier.
        instrument_id: Exact venue instrument.
        timeframe: Timeframe of the confirming bar.
        source: Plugin id.
        name: Event name.
        bar_start_ms: Start of the confirming bar.
        confirmed_at_ms: Close of the confirming bar (``bar_start + tf``).
        price: Optional reference price (e.g. swept level).
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
            "name": self.name.value,
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
        as_of_ms: Close of that bar (``bar_start_ms + timeframe``).
        aois: AOIs known at ``as_of_ms`` (live ones plus recently ended).
        events: Events confirmed on the last processed bar only.
        values: Published scalar values (see module docstring).
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
            "values": {
                key: _contract_value(value)
                for key, value in self.values.items()
            },
            "warmup_complete": self.warmup_complete,
            "data_quality": self.data_quality.to_contract(),
        }


SnapshotKey = tuple[str, timeframes.Timeframe]
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
        """Returns the exclusive end (close time) of the signal bar."""
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
