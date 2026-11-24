# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Composition of the indicator plugins.

The built-in indicators (``joseph.indicators.builtin``) always register
first, in a fixed order; plugin packages installed in the environment add
more at run time through the ``joseph.indicator_plugins`` entry-point group
(``joseph.indicators.discovery``). The application builds its registry with
``build_registry`` and its discovery configuration; tests and tools that
need only the built-ins use ``build_default_registry``.
"""

from __future__ import annotations

from joseph.indicators import builtin
from joseph.indicators import discovery
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib


def builtin_plugins() -> tuple[protocol.IndicatorPlugin, ...]:
    """Instantiates every built-in plugin.

    Returns:
        Plugins in registration (display) order.
    """
    return builtin.builtin_plugins()


def build_default_registry() -> registry_lib.PluginRegistry:
    """Builds a registry holding the built-in plugins only.

    Returns:
        Populated registry; no plugin package is loaded.
    """
    registry = registry_lib.PluginRegistry()
    for plugin in builtin_plugins():
        registry.register(plugin)
    return registry


def build_registry(
    config: discovery.DiscoveryConfig,
) -> tuple[registry_lib.PluginRegistry, discovery.LoadReport]:
    """Builds the registry of the built-ins and the configured packages.

    Plugin packages that fail to load are logged, reported and skipped;
    plugins listed in ``config.disabled`` keep their metadata but are
    disabled.

    Args:
        config: Discovery configuration (see ``platform.config.Settings``
            for the environment variables that set it).

    Returns:
        ``(registry, report)``.

    Raises:
        PluginLoadError: If a plugin in ``config.required`` is not
            installed or not enabled.
    """
    return discovery.build_registry(config)
