# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Rule vocabulary built from the metadata of installed indicator plugins.

The strategy engine only reads published indicator outputs. Each plugin
declares, in its contract ``IndicatorPluginInfo``, the AOI kinds, events
and values it publishes; this module turns those declarations into a
``Vocabulary`` the compiler and evaluator use to find the plugin behind
each kind and event, report missing plugins and tell the indicator engine
exactly which series to compute.

The domain depends only on the contract dict shape, never on the indicator
package itself: bootstrap passes ``registry.infos(include_unavailable=True)``
and the usable plugin ids.
"""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Iterable
from collections.abc import Mapping

from joseph.domain import structures

STRING = "string"
"""``ValueSpec.type`` of string values."""

BOOLEAN = "boolean"
"""``ValueSpec.type`` of boolean values."""


@dataclasses.dataclass(frozen=True, slots=True)
class ValueInfo:
    """Declared type of one published value.

    Attributes:
        type: Contract ``ValueSpec.type`` (``decimal``, ``integer``,
            ``boolean``, ``string`` or ``timestamp_ms``).
        choices: Declared string choices; empty when open-ended.
    """

    type: str
    choices: tuple[str, ...] = ()


def _strings(value: object) -> tuple[str, ...]:
    """Reads a list of strings from a contract dict field.

    Args:
        value: Raw field value.

    Returns:
        The strings, in order; non-string items and non-lists are ignored.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _value_infos(value: object) -> dict[str, ValueInfo]:
    """Reads ``output_values`` of a contract plugin info.

    Args:
        value: Raw ``output_values`` list.

    Returns:
        Value key to declared type.
    """
    result: dict[str, ValueInfo] = {}
    if not isinstance(value, (list, tuple)):
        return result
    for spec in value:
        if not isinstance(spec, Mapping):
            continue
        key = spec.get("key")
        kind = spec.get("type")
        if isinstance(key, str) and isinstance(kind, str):
            result[key] = ValueInfo(kind, _strings(spec.get("choices")))
    return result


@dataclasses.dataclass(frozen=True, slots=True)
class Vocabulary:
    """Kinds, events and values published by the installed plugins.

    Attributes:
        usable: Plugin ids that may be evaluated (enabled or deprecated).
        kind_plugin: AOI kind to the plugin declaring it, whatever its
            lifecycle.
        event_plugins: Event name to every plugin declaring it, in
            registration order; reserved events are not listed.
        values: Plugin id to published value key to its declared type.
        order: Every known plugin id, in registration order.
    """

    usable: frozenset[str]
    kind_plugin: Mapping[str, str]
    event_plugins: Mapping[str, tuple[str, ...]]
    values: Mapping[str, Mapping[str, ValueInfo]]
    order: tuple[str, ...]

    @classmethod
    def from_plugin_infos(
        cls,
        infos: Iterable[Mapping[str, object]],
        usable: Iterable[str],
    ) -> Vocabulary:
        """Builds the vocabulary from contract plugin infos.

        Args:
            infos: ``IndicatorPluginInfo`` dicts in registration order,
                including disabled plugins so rules that use them get an
                "unavailable" error instead of "unknown".
            usable: Ids of the enabled or deprecated plugins.

        Returns:
            The vocabulary. A kind declared twice keeps its first plugin.
        """
        order: list[str] = []
        kind_plugin: dict[str, str] = {}
        event_plugins: dict[str, list[str]] = {}
        values: dict[str, Mapping[str, ValueInfo]] = {}
        for info in infos:
            plugin_id = info.get("plugin_id")
            if not isinstance(plugin_id, str) or plugin_id in values:
                continue
            order.append(plugin_id)
            for kind in _strings(info.get("output_kinds")):
                kind_plugin.setdefault(kind, plugin_id)
            for event in _strings(info.get("output_events")):
                if event in structures.RESERVED_EVENTS:
                    continue
                publishers = event_plugins.setdefault(event, [])
                if plugin_id not in publishers:
                    publishers.append(plugin_id)
            values[plugin_id] = types.MappingProxyType(
                _value_infos(info.get("output_values"))
            )
        return cls(
            usable=frozenset(usable),
            kind_plugin=types.MappingProxyType(kind_plugin),
            event_plugins=types.MappingProxyType(
                {name: tuple(ids) for name, ids in event_plugins.items()}
            ),
            values=types.MappingProxyType(values),
            order=tuple(order),
        )

    @classmethod
    def empty(cls) -> Vocabulary:
        """Returns a vocabulary without any plugin.

        Returns:
            The empty vocabulary.
        """
        return cls.from_plugin_infos((), ())

    def known(self, plugin_id: str) -> bool:
        """Checks whether a plugin is installed, whatever its lifecycle.

        Args:
            plugin_id: Plugin id.

        Returns:
            True when the plugin declared metadata.
        """
        return plugin_id in self.values

    def event_known(self, name: str) -> bool:
        """Checks whether any plugin may publish an event.

        Args:
            name: Event name.

        Returns:
            True for reserved events and declared ones.
        """
        return name in structures.RESERVED_EVENTS or name in self.event_plugins

    def event_source(self, name: str, source: str | None) -> str | None:
        """Resolves the plugin whose snapshot proves an event's absence.

        Args:
            name: Event name.
            source: Plugin named by the rule, if any.

        Returns:
            ``None`` for reserved events (any plugin may emit them);
            ``source`` when given; otherwise the first registered
            publisher, or ``None`` when no plugin declares the name.
        """
        if name in structures.RESERVED_EVENTS:
            return None
        if source is not None:
            return source
        publishers = self.event_plugins.get(name, ())
        return publishers[0] if publishers else None

    def kind_plugins(self, kinds: Iterable[str]) -> tuple[str, ...]:
        """Returns the plugins declaring some kinds, unknown kinds skipped.

        Args:
            kinds: AOI kinds.

        Returns:
            Sorted distinct plugin ids.
        """
        return tuple(
            sorted(
                {self.kind_plugin[k] for k in kinds if k in self.kind_plugin}
            )
        )
