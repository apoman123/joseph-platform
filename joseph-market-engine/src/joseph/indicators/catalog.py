"""Build-time composition of the first-party indicator plugins.

Adding a plugin means adding one import and one ``register`` line here plus
the plugin package, schema fixtures and tests; no existing plugin changes.
"""

from __future__ import annotations

from joseph.indicators.plugins.auction_value_areas import (
    plugin as auction_value_areas,
)
from joseph.indicators.plugins.daily_bias import plugin as daily_bias
from joseph.indicators.plugins.fvg_ifvg import plugin as fvg_ifvg
from joseph.indicators.plugins.liquidity_sweeps import (
    plugin as liquidity_sweeps,
)
from joseph.indicators.plugins.order_breaker_blocks import (
    plugin as order_breaker_blocks,
)
from joseph.indicators.plugins.order_flow_pressure import (
    plugin as order_flow_pressure,
)
from joseph.indicators.plugins.premium_discount_ote import (
    plugin as premium_discount_ote,
)
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib


def default_plugins() -> tuple[protocol.IndicatorPlugin, ...]:
    """Instantiates the seven first-party plugins.

    Returns:
        Plugins in display order.
    """
    return (
        daily_bias.DailyBiasPlugin(),
        fvg_ifvg.FvgIfvgPlugin(),
        order_breaker_blocks.OrderBreakerBlocksPlugin(),
        liquidity_sweeps.LiquiditySweepsPlugin(),
        order_flow_pressure.OrderFlowPressurePlugin(),
        auction_value_areas.AuctionValueAreasPlugin(),
        premium_discount_ote.PremiumDiscountOtePlugin(),
    )


def build_default_registry() -> registry_lib.PluginRegistry:
    """Builds a registry containing every first-party plugin.

    Returns:
        Populated registry.
    """
    registry = registry_lib.PluginRegistry()
    for plugin in default_plugins():
        registry.register(plugin)
    return registry
