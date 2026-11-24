# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Instrument tick sizes for indicator settings (Pine ``syminfo.mintick``).

The ported Pine indicators compare and round prices in ticks. Plugins only
receive settings, so engine builders resolve the instrument's tick size
through the ``TickSizeSource`` port and inject it as the ``tick_size``
setting of every plugin that declares one, unless the caller overrode it.
"""

from __future__ import annotations

import decimal
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Protocol

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.indicators.sdk import registry as registry_lib

TICK_SETTING = "tick_size"
"""Setting name shared by every packaged plugin."""


class TickSizeSource(Protocol):
    """Port resolving an instrument's minimum price increment."""

    async def tick_size(
        self, instrument_id: instruments.InstrumentId
    ) -> decimal.Decimal | None:
        """Returns the tick size, or ``None`` when unknown.

        Args:
            instrument_id: Exact venue instrument.
        """
        ...


def _declares_tick(
    registry: registry_lib.PluginRegistry, plugin_id: str
) -> bool:
    """Returns whether a plugin accepts the ``tick_size`` setting.

    Args:
        registry: Plugin registry.
        plugin_id: Plugin id.

    Returns:
        True when its input schema lists ``tick_size``.
    """
    schema = registry.get(plugin_id).input_schema()
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and TICK_SETTING in properties


def _unset(value: object) -> bool:
    """Returns whether a ``tick_size`` override means "not provided".

    Args:
        value: Raw override value.

    Returns:
        True for missing, empty or zero values.
    """
    if value is None or value == "":
        return True
    try:
        return decimal.Decimal(str(value)) == 0
    except decimal.InvalidOperation:
        return False


def with_tick_size(
    registry: registry_lib.PluginRegistry,
    plugin_ids: Iterable[str],
    settings: Mapping[str, Mapping[str, object]],
    tick: decimal.Decimal | None,
) -> dict[str, dict[str, object]]:
    """Adds the instrument tick size to per-plugin settings.

    Args:
        registry: Plugin registry.
        plugin_ids: Plugins that will run.
        settings: Per-plugin overrides (not modified).
        tick: Instrument tick size, or ``None`` when unknown.

    Returns:
        New per-plugin overrides with ``tick_size`` filled where missing.
    """
    merged = {key: dict(value) for key, value in settings.items()}
    if tick is None or tick <= 0:
        return merged
    for plugin_id in plugin_ids:
        if not _declares_tick(registry, plugin_id):
            continue
        overrides = merged.setdefault(plugin_id, {})
        if _unset(overrides.get(TICK_SETTING)):
            overrides[TICK_SETTING] = decimals.format_decimal(tick)
    return merged


async def resolve(
    source: TickSizeSource | None,
    instrument_id: instruments.InstrumentId,
) -> decimal.Decimal | None:
    """Looks up a tick size, tolerating a missing source or lookup failure.

    Args:
        source: Optional port.
        instrument_id: Exact venue instrument.

    Returns:
        Tick size or ``None`` (plugins then infer it from prices).
    """
    if source is None:
        return None
    try:
        return await source.tick_size(instrument_id)
    except Exception:
        return None
