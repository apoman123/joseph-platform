"""Static mapping from rule vocabulary to indicator plugins.

The strategy engine only reads published indicator outputs. This catalog
states which plugin publishes each AOI kind and structure event, so the
compiler can report missing plugins and the indicator engine can be told
exactly which series to compute.
"""

from __future__ import annotations

from collections.abc import Mapping

from joseph.domain import structures

DAILY_BIAS = "daily_bias"
FVG_IFVG = "fvg_ifvg"
ORDER_BREAKER_BLOCKS = "order_breaker_blocks"
LIQUIDITY_SWEEPS = "liquidity_sweeps"
ORDER_FLOW_PRESSURE = "order_flow_pressure"
AUCTION_VALUE_AREAS = "auction_value_areas"
PREMIUM_DISCOUNT_OTE = "premium_discount_ote"
MARKET_STRUCTURE = "market_structure"
ANCHORED_VWAP = "anchored_vwap"
REGRESSION_CHANNEL = "regression_channel"

DEFAULT_PLUGINS: frozenset[str] = frozenset(
    {
        DAILY_BIAS,
        FVG_IFVG,
        ORDER_BREAKER_BLOCKS,
        LIQUIDITY_SWEEPS,
        ORDER_FLOW_PRESSURE,
        AUCTION_VALUE_AREAS,
        PREMIUM_DISCOUNT_OTE,
        MARKET_STRUCTURE,
        ANCHORED_VWAP,
        REGRESSION_CHANNEL,
    }
)
"""First-party plugins assumed available when no registry is supplied."""

_K = structures.AoiKind
_E = structures.StructureEventName

AOI_KIND_PLUGIN: Mapping[structures.AoiKind, str] = {
    _K.FVG: FVG_IFVG,
    _K.IFVG: FVG_IFVG,
    _K.OB: ORDER_BREAKER_BLOCKS,
    _K.BREAKER: ORDER_BREAKER_BLOCKS,
    _K.BSL: LIQUIDITY_SWEEPS,
    _K.SSL: LIQUIDITY_SWEEPS,
    _K.EQH: LIQUIDITY_SWEEPS,
    _K.EQL: LIQUIDITY_SWEEPS,
    _K.PREMIUM: PREMIUM_DISCOUNT_OTE,
    _K.DISCOUNT: PREMIUM_DISCOUNT_OTE,
    _K.EQUILIBRIUM: PREMIUM_DISCOUNT_OTE,
    _K.OTE: PREMIUM_DISCOUNT_OTE,
    _K.VAH: AUCTION_VALUE_AREAS,
    _K.POC: AUCTION_VALUE_AREAS,
    _K.VAL: AUCTION_VALUE_AREAS,
    _K.PDH: DAILY_BIAS,
    _K.PDL: DAILY_BIAS,
    _K.SESSION_HIGH: DAILY_BIAS,
    _K.SESSION_LOW: DAILY_BIAS,
    _K.SWING_HIGH: MARKET_STRUCTURE,
    _K.SWING_LOW: MARKET_STRUCTURE,
}
"""Plugin publishing each AOI kind."""

EVENT_PLUGIN: Mapping[structures.StructureEventName, str | None] = {
    _E.BOS_BULL: DAILY_BIAS,
    _E.BOS_BEAR: DAILY_BIAS,
    _E.CHOCH_BULL: DAILY_BIAS,
    _E.CHOCH_BEAR: DAILY_BIAS,
    _E.INITIAL_BREAK_BULL: DAILY_BIAS,
    _E.INITIAL_BREAK_BEAR: DAILY_BIAS,
    _E.FVG_FORMED: FVG_IFVG,
    _E.IFVG_FORMED: FVG_IFVG,
    _E.OB_FORMED: ORDER_BREAKER_BLOCKS,
    _E.BREAKER_FORMED: ORDER_BREAKER_BLOCKS,
    _E.AOI_INVALIDATED: None,
    _E.AOI_EXPIRED: None,
    _E.SSL_SWEEP: LIQUIDITY_SWEEPS,
    _E.BSL_SWEEP: LIQUIDITY_SWEEPS,
    _E.SSL_BREAK: LIQUIDITY_SWEEPS,
    _E.BSL_BREAK: LIQUIDITY_SWEEPS,
    _E.BUYER_FAILURE: ORDER_FLOW_PRESSURE,
    _E.SELLER_FAILURE: ORDER_FLOW_PRESSURE,
    _E.BUYER_BREAKOUT: ORDER_FLOW_PRESSURE,
    _E.SELLER_BREAKOUT: ORDER_FLOW_PRESSURE,
    _E.RANGE_RESET: PREMIUM_DISCOUNT_OTE,
    _E.VALUE_AREA_UPDATED: AUCTION_VALUE_AREAS,
    _E.SWING_HH: MARKET_STRUCTURE,
    _E.SWING_HL: MARKET_STRUCTURE,
    _E.SWING_LH: MARKET_STRUCTURE,
    _E.SWING_LL: MARKET_STRUCTURE,
    _E.SWING_EQH: MARKET_STRUCTURE,
    _E.SWING_EQL: MARKET_STRUCTURE,
}
"""Plugin publishing each event; ``None`` means any plugin may emit it."""

PUBLISHED_VALUES: Mapping[str, frozenset[str]] = {
    DAILY_BIAS: frozenset(
        {
            "trend",
            "last_event",
            "last_event_direction",
            "last_pivot_high",
            "last_pivot_low",
            "pdh",
            "pdl",
            "session",
            "event_age",
            "previous_day_mid",
            "range_location",
            "bias",
            "agreement",
            "d1_trend",
            "h4_trend",
            "h1_trend",
        }
    ),
    MARKET_STRUCTURE: frozenset(
        {
            "trend",
            "last_event",
            "last_event_direction",
            "event_age",
            "last_pivot_high",
            "last_pivot_low",
            "high_type",
            "low_type",
            "swing_alignment",
            "agreement",
            "pending_high",
            "pending_low",
        }
    ),
    FVG_IFVG: frozenset({"active_count", "atr"}),
    ORDER_BREAKER_BLOCKS: frozenset({"active_count", "atr"}),
    LIQUIDITY_SWEEPS: frozenset({"active_count"}),
    ORDER_FLOW_PRESSURE: frozenset(
        {
            "delta_pct",
            "coverage_pct",
            "intrabar_count",
            "rel_volume",
            "method",
            "buy_volume",
            "sell_volume",
            "prior_high",
            "prior_low",
        }
    ),
    AUCTION_VALUE_AREAS: frozenset(
        {"vah", "poc", "val", "vah_1h", "poc_1h", "val_1h"}
    ),
    PREMIUM_DISCOUNT_OTE: frozenset(
        {
            "range_id",
            "direction",
            "range_high",
            "range_low",
            "eq",
            "ote_lower",
            "ote_upper",
            "in_premium",
            "in_discount",
            "in_ote",
            "position_pct",
            "zone",
            "read",
        }
    ),
    ANCHORED_VWAP: frozenset(
        {
            "vwap",
            "stdev",
            "upper_1",
            "lower_1",
            "upper_2",
            "lower_2",
            "upper_3",
            "lower_3",
            "anchor_ms",
            "bars",
            "anchor_in_window",
        }
    ),
    REGRESSION_CHANNEL: frozenset(
        {
            "start_ms",
            "start",
            "end",
            "upper_start",
            "upper_end",
            "lower_start",
            "lower_end",
            "slope",
            "stdev",
            "pearson_r",
            "max_up",
            "max_down",
            "bars",
            "anchor_in_window",
        }
    ),
}
"""Published ``IndicatorSnapshot.values`` keys (see domain.structures)."""

BULLISH_LIQUIDITY_TARGETS: frozenset[structures.AoiKind] = frozenset(
    {_K.BSL, _K.EQH}
)
"""Buy-side pools above price: long targets."""

BEARISH_LIQUIDITY_TARGETS: frozenset[structures.AoiKind] = frozenset(
    {_K.SSL, _K.EQL}
)
"""Sell-side pools below price: short targets."""
