# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Plugin registry with lifecycle.

The built-in plugins and the plugins of installed plugin packages
(``joseph.indicators.discovery``) register once, at startup; nothing is
fetched remotely. ``register`` rejects a plugin whose id is taken, whose
schema version is unsupported, whose declarations break the metadata rules
(``metadata.validate``) or which declares an AOI kind another plugin
already declared.

Disabling or removing a plugin keeps its metadata so that saved strategies
referencing it fail validation with an actionable message instead of
silently changing meaning.
"""

from __future__ import annotations

import copy
import dataclasses
import enum

from joseph.domain import errors
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol

SUPPORTED_SCHEMA_VERSIONS = frozenset({2})
"""Plugin output schema versions that ``register`` accepts."""


class Lifecycle(enum.StrEnum):
    """Plugin lifecycle states."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"
    REMOVED = "REMOVED"


_USABLE = (Lifecycle.ENABLED, Lifecycle.DEPRECATED)
"""Lifecycles whose plugins can run."""


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
    """Holds indicator plugins keyed by ``plugin_id``, in registration order."""

    def __init__(self) -> None:
        """Creates an empty registry."""
        self._entries: dict[str, _Entry] = {}
        self._kind_owners: dict[str, str] = {}

    def register(self, plugin: protocol.IndicatorPlugin) -> None:
        """Registers a plugin after checking its declarations.

        Args:
            plugin: Plugin instance.

        Raises:
            ConflictError: If the id is already registered or a declared
                AOI kind belongs to another plugin.
            UnsupportedError: If the plugin's schema version is unsupported.
            ValidationFailedError: If the plugin id or its declarations
                break the metadata rules.
        """
        plugin_id = plugin.plugin_id
        if plugin_id in self._entries:
            raise errors.ConflictError(
                f"indicator plugin {plugin_id!r} already registered"
            )
        if plugin.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise errors.UnsupportedError(
                f"plugin {plugin_id!r} schema_version "
                f"{plugin.schema_version} is not supported"
            )
        problems = metadata.validate(plugin)
        if problems:
            raise errors.ValidationFailedError(
                f"indicator plugin {plugin_id!r}: {'; '.join(problems)}"
            )
        taken = {
            kind: self._kind_owners[kind]
            for kind in plugin.output_kinds
            if kind in self._kind_owners
        }
        if taken:
            owners = ", ".join(f"{k} ({o})" for k, o in sorted(taken.items()))
            raise errors.ConflictError(
                f"indicator plugin {plugin_id!r} declares AOI kinds already "
                f"declared by other plugins: {owners}"
            )
        info = plugin.info()
        self._entries[plugin_id] = _Entry(
            plugin=plugin,
            lifecycle=Lifecycle.ENABLED,
            reason=None,
            info=info,
        )
        for kind in plugin.output_kinds:
            self._kind_owners[kind] = plugin_id

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
        if entry.lifecycle not in _USABLE:
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
        return entry is not None and entry.lifecycle in _USABLE

    def lifecycle(self, plugin_id: str) -> Lifecycle | None:
        """Returns the lifecycle of a plugin.

        Args:
            plugin_id: Plugin id.

        Returns:
            Lifecycle, or ``None`` if never registered.
        """
        entry = self._entries.get(plugin_id)
        return None if entry is None else entry.lifecycle

    def kind_owner(self, kind: str) -> str | None:
        """Returns the plugin that declares an AOI kind.

        Args:
            kind: AOI kind.

        Returns:
            The plugin id, whatever its lifecycle, or ``None`` when no
            registered plugin declares the kind.
        """
        return self._kind_owners.get(kind)

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
            if include_unavailable or entry.lifecycle in _USABLE
        )

    def infos(self, *, include_unavailable: bool) -> list[dict[str, object]]:
        """Returns the contract ``IndicatorPluginInfo`` of plugins.

        The strategy engine builds its vocabulary from these (kinds and
        events of disabled plugins included, so rules that use them get an
        actionable "unavailable" error instead of "unknown").

        Args:
            include_unavailable: Include disabled and removed plugins.

        Returns:
            Copies of the infos captured at registration, in registration
            order.
        """
        return [
            copy.deepcopy(self._entries[plugin_id].info)
            for plugin_id in self.ids(include_unavailable=include_unavailable)
        ]

    def list_infos(self) -> dict[str, object]:
        """Returns the contract ``IndicatorPluginList`` of usable plugins.

        Returns:
            ``{"plugins": [IndicatorPluginInfo, ...]}``.
        """
        return {"plugins": self.infos(include_unavailable=False)}

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
            **copy.deepcopy(entry.info),
            "lifecycle": entry.lifecycle.value,
            "reason": entry.reason,
        }
