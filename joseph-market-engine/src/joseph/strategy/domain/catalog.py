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

DEFAULT_PLUGINS: frozenset[str] = frozenset(
    {
        DAILY_BIAS,
        FVG_IFVG,
        ORDER_BREAKER_BLOCKS,
        LIQUIDITY_SWEEPS,
        ORDER_FLOW_PRESSURE,
        AUCTION_VALUE_AREAS,
        PREMIUM_DISCOUNT_OTE,
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
        }
    ),
    FVG_IFVG: frozenset({"active_count"}),
    ORDER_BREAKER_BLOCKS: frozenset({"active_count"}),
    LIQUIDITY_SWEEPS: frozenset({"active_count"}),
    ORDER_FLOW_PRESSURE: frozenset(
        {"delta_pct", "coverage_pct", "intrabar_count", "rel_volume", "method"}
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
