# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Build-time composition of the indicator plugins.

Every indicator algorithm lives in the external ``smc_indicators`` library
(``apoman123/PineScripts``, ``python_indicators/``); the platform registers
thin adapters around it. Adding an indicator means porting it in that
repository, then adding one adapter in ``joseph.indicators.packaged``.
"""

from __future__ import annotations

from joseph.indicators.packaged import plugins as packaged
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib


def default_plugins() -> tuple[protocol.IndicatorPlugin, ...]:
    """Instantiates every packaged plugin.

    Returns:
        Plugins in display order.
    """
    return packaged.default_plugins()


def build_default_registry() -> registry_lib.PluginRegistry:
    """Builds a registry containing every packaged plugin.

    Returns:
        Populated registry.
    """
    registry = registry_lib.PluginRegistry()
    for plugin in default_plugins():
        registry.register(plugin)
    return registry
