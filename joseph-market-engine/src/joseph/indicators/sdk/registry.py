# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Compile-time plugin registry with lifecycle (V5 §10).

Plugins are vetted modules registered at build time, never fetched
remotely. Disabling or removing a plugin keeps its metadata so that saved
strategies referencing it fail validation with an actionable message
instead of silently changing meaning.
"""

from __future__ import annotations

import dataclasses
import enum

from joseph.domain import errors
from joseph.indicators.sdk import protocol

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class Lifecycle(enum.StrEnum):
    """Plugin lifecycle states."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"
    REMOVED = "REMOVED"


@dataclasses.dataclass(slots=True)
class _Entry:
    """Registry entry.

    Attributes:
        plugin: The plugin instance.
        lifecycle: Current lifecycle state.
        reason: Why the plugin is not enabled, if applicable.
        info: Cached contract info captured at registration.
    """

    plugin: protocol.IndicatorPlugin
    lifecycle: Lifecycle
    reason: str | None
    info: dict[str, object]


class PluginRegistry:
    """Holds indicator plugins keyed by ``plugin_id``."""

    def __init__(self) -> None:
        """Creates an empty registry."""
        self._entries: dict[str, _Entry] = {}

    def register(self, plugin: protocol.IndicatorPlugin) -> None:
        """Registers a plugin.

        Args:
            plugin: Plugin instance.

        Raises:
            ConflictError: If the id is already registered.
            UnsupportedError: If the plugin's schema version is unsupported.
        """
        if plugin.plugin_id in self._entries:
            raise errors.ConflictError(
                f"indicator plugin {plugin.plugin_id!r} already registered"
            )
        if plugin.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise errors.UnsupportedError(
                f"plugin {plugin.plugin_id!r} schema_version "
                f"{plugin.schema_version} is not supported"
            )
        self._entries[plugin.plugin_id] = _Entry(
            plugin=plugin,
            lifecycle=Lifecycle.ENABLED,
            reason=None,
            info=plugin.info(),
        )

    def get(self, plugin_id: str) -> protocol.IndicatorPlugin:
        """Returns an enabled or deprecated plugin.

        Args:
            plugin_id: Plugin id.

        Returns:
            The plugin.

        Raises:
            NotFoundError: If the id was never registered.
            UnsupportedError: If the plugin is disabled or removed.
        """
        entry = self._entries.get(plugin_id)
        if entry is None:
            raise errors.NotFoundError(
                f"unknown indicator plugin {plugin_id!r}"
            )
        if entry.lifecycle in (Lifecycle.DISABLED, Lifecycle.REMOVED):
            raise errors.UnsupportedError(self.describe_unavailable(plugin_id))
        return entry.plugin

    def is_available(self, plugin_id: str) -> bool:
        """Checks whether a plugin can be used.

        Args:
            plugin_id: Plugin id.

        Returns:
            True when registered and enabled or deprecated.
        """
        entry = self._entries.get(plugin_id)
        return entry is not None and entry.lifecycle in (
            Lifecycle.ENABLED,
            Lifecycle.DEPRECATED,
        )

    def lifecycle(self, plugin_id: str) -> Lifecycle | None:
        """Returns the lifecycle of a plugin.

        Args:
            plugin_id: Plugin id.

        Returns:
            Lifecycle, or ``None`` if never registered.
        """
        entry = self._entries.get(plugin_id)
        return None if entry is None else entry.lifecycle

    def describe_unavailable(self, plugin_id: str) -> str:
        """Builds an actionable message for an unusable plugin.

        Args:
            plugin_id: Plugin id.

        Returns:
            Message suitable for strategy validation errors.
        """
        entry = self._entries.get(plugin_id)
        if entry is None:
            return f"indicator plugin {plugin_id!r} is not installed"
        reason = f": {entry.reason}" if entry.reason else ""
        return (
            f"indicator plugin {plugin_id!r} is {entry.lifecycle.value}"
            f"{reason}; strategies using it cannot be evaluated until it "
            "is re-enabled or the rule is migrated"
        )

    def set_lifecycle(
        self, plugin_id: str, lifecycle: Lifecycle, reason: str | None = None
    ) -> None:
        """Changes a plugin's lifecycle, keeping its metadata.

        Args:
            plugin_id: Plugin id.
            lifecycle: New lifecycle.
            reason: Explanation shown to users.

        Raises:
            NotFoundError: If the id was never registered.
        """
        entry = self._entries.get(plugin_id)
        if entry is None:
            raise errors.NotFoundError(
                f"unknown indicator plugin {plugin_id!r}"
            )
        entry.lifecycle = lifecycle
        entry.reason = reason

    def disable(self, plugin_id: str, reason: str) -> None:
        """Disables a plugin.

        Args:
            plugin_id: Plugin id.
            reason: Explanation shown to users.
        """
        self.set_lifecycle(plugin_id, Lifecycle.DISABLED, reason)

    def remove(self, plugin_id: str, reason: str) -> None:
        """Marks a plugin removed while preserving its metadata.

        Args:
            plugin_id: Plugin id.
            reason: Explanation shown to users.
        """
        self.set_lifecycle(plugin_id, Lifecycle.REMOVED, reason)

    def enable(self, plugin_id: str) -> None:
        """Re-enables a plugin.

        Args:
            plugin_id: Plugin id.
        """
        self.set_lifecycle(plugin_id, Lifecycle.ENABLED, None)

    def ids(self, *, include_unavailable: bool = False) -> tuple[str, ...]:
        """Lists plugin ids in registration order.

        Args:
            include_unavailable: Include disabled and removed plugins.

        Returns:
            Plugin ids.
        """
        return tuple(
            plugin_id
            for plugin_id, entry in self._entries.items()
            if include_unavailable
            or entry.lifecycle
            in (
                Lifecycle.ENABLED,
                Lifecycle.DEPRECATED,
            )
        )

    def list_infos(self) -> dict[str, object]:
        """Returns the contract ``IndicatorPluginList`` of usable plugins.

        Returns:
            ``{"plugins": [IndicatorPluginInfo, ...]}``.
        """
        return {
            "plugins": [
                dict(self._entries[plugin_id].info) for plugin_id in self.ids()
            ]
        }

    def metadata(self, plugin_id: str) -> dict[str, object] | None:
        """Returns stored metadata even for disabled/removed plugins.

        Args:
            plugin_id: Plugin id.

        Returns:
            Info dict plus ``lifecycle``/``reason``, or ``None``.
        """
        entry = self._entries.get(plugin_id)
        if entry is None:
            return None
        return {
            **entry.info,
            "lifecycle": entry.lifecycle.value,
            "reason": entry.reason,
        }
