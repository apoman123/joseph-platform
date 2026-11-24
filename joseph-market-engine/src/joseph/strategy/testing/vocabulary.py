# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Generic rule vocabulary for strategy tests.

``vocabulary()`` combines the built-in indicator plugins, the predictable
test plugins of ``joseph.indicators.testing`` (``echo`` and ``echo_zones``)
and four declared-only test plugins whose outputs strategy tests build by
hand with ``frames.FrameFactory``:

- ``test_zones`` publishes the zone kinds ``ZONE_UP`` and ``ZONE_DOWN``;
- ``test_levels`` publishes the level kinds ``LEVEL_HIGH`` and
  ``LEVEL_LOW``;
- ``test_signals`` publishes the events ``SIGNAL_UP``, ``SIGNAL_DOWN`` and
  ``SIGNAL_SHARED``;
- ``test_state`` publishes the values ``trend`` (``BULLISH``, ``BEARISH``
  or ``NEUTRAL``), ``armed`` (boolean) and ``score`` (decimal), and the
  event ``SIGNAL_SHARED`` too, so one event name has two publishers.

The declared-only plugins register after every real one, so
``test_signals`` is the default source of ``SIGNAL_SHARED``.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import timeframes
from joseph.indicators import catalog as indicator_catalog
from joseph.indicators import testing as indicator_testing
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import registry as registry_lib
from joseph.strategy.domain import catalog

TEST_ZONES = "test_zones"
"""Declared-only plugin publishing ``ZONE_UP`` and ``ZONE_DOWN``."""

TEST_LEVELS = "test_levels"
"""Declared-only plugin publishing ``LEVEL_HIGH`` and ``LEVEL_LOW``."""

TEST_SIGNALS = "test_signals"
"""Declared-only plugin publishing ``SIGNAL_UP``, ``SIGNAL_DOWN`` and
``SIGNAL_SHARED``."""

TEST_STATE = "test_state"
"""Declared-only plugin publishing ``trend``, ``armed``, ``score`` and
``SIGNAL_SHARED``."""

ZONE_UP = "ZONE_UP"
"""Bullish zone kind of ``test_zones``."""

ZONE_DOWN = "ZONE_DOWN"
"""Bearish zone kind of ``test_zones``."""

LEVEL_HIGH = "LEVEL_HIGH"
"""Level kind of ``test_levels`` above price."""

LEVEL_LOW = "LEVEL_LOW"
"""Level kind of ``test_levels`` below price."""

SIGNAL_UP = "SIGNAL_UP"
"""Event of ``test_signals``."""

SIGNAL_DOWN = "SIGNAL_DOWN"
"""Event of ``test_signals``."""

SIGNAL_SHARED = "SIGNAL_SHARED"
"""Event published by both ``test_signals`` and ``test_state``."""

TREND_CHOICES = ("BULLISH", "BEARISH", "NEUTRAL")
"""Declared choices of ``test_state.trend``."""

_ALL_TIMEFRAMES = [tf.value for tf in timeframes.Timeframe]


def _info(
    plugin_id: str,
    display_name: str,
    *,
    kinds: Sequence[str] = (),
    events: Sequence[str] = (),
    values: Sequence[metadata.ValueSpec] = (),
) -> dict[str, object]:
    """Builds a contract ``IndicatorPluginInfo`` of a declared-only plugin.

    Args:
        plugin_id: Plugin id.
        display_name: Display name.
        kinds: Declared AOI kinds.
        events: Declared events.
        values: Declared published values.

    Returns:
        The info dict.
    """
    return {
        "plugin_id": plugin_id,
        "indicator_version": "1.0.0",
        "schema_version": 2,
        "display_name": display_name,
        "description": f"Declared-only test plugin {display_name}.",
        "timeframes": list(_ALL_TIMEFRAMES),
        "warmup_bars": 1,
        "required_streams": ["candles:chart"],
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "output_kinds": list(kinds),
        "output_events": list(events),
        "output_values": [value.to_contract() for value in values],
        "display": metadata.Display(layer=False).to_contract(),
        "licensing_status": "CLEAN_ROOM_FROM_SPEC",
        "parity_status": "SPEC_ONLY",
    }


def declared_plugin_infos() -> list[dict[str, object]]:
    """Returns the infos of the four declared-only test plugins.

    Returns:
        ``test_zones``, ``test_levels``, ``test_signals`` and
        ``test_state``, in that order.
    """
    count = metadata.ValueSpec(
        "active_count", metadata.ValueType.INTEGER, "Live zones."
    )
    return [
        _info(
            TEST_ZONES,
            "Test zones",
            kinds=(ZONE_UP, ZONE_DOWN),
            values=(count,),
        ),
        _info(
            TEST_LEVELS,
            "Test levels",
            kinds=(LEVEL_HIGH, LEVEL_LOW),
            values=(count,),
        ),
        _info(
            TEST_SIGNALS,
            "Test signals",
            events=(SIGNAL_UP, SIGNAL_DOWN, SIGNAL_SHARED),
        ),
        _info(
            TEST_STATE,
            "Test state",
            events=(SIGNAL_SHARED,),
            values=(
                metadata.ValueSpec(
                    "trend",
                    metadata.ValueType.STRING,
                    "Trend direction.",
                    choices=TREND_CHOICES,
                ),
                metadata.ValueSpec(
                    "armed", metadata.ValueType.BOOLEAN, "Whether armed."
                ),
                metadata.ValueSpec(
                    "score",
                    metadata.ValueType.DECIMAL,
                    "Score.",
                    metadata.Unit.NONE,
                ),
            ),
        ),
    ]


def installed_registry() -> registry_lib.PluginRegistry:
    """Builds a registry of the built-ins and the echo test plugins.

    Returns:
        The registry, built-ins first.
    """
    registry = indicator_catalog.build_default_registry()
    for plugin in indicator_testing.echo_plugins():
        registry.register(plugin)
    return registry


def vocabulary(
    *,
    disabled: Iterable[str] = (),
    without: Iterable[str] = (),
) -> catalog.Vocabulary:
    """Builds the generic test vocabulary.

    Args:
        disabled: Plugin ids kept in the metadata but not usable.
        without: Plugin ids left out entirely (not installed).

    Returns:
        Vocabulary of the built-ins, ``echo``, ``echo_zones`` and the
        declared-only test plugins.
    """
    removed = frozenset(without)
    infos = [
        info
        for info in [
            *installed_registry().infos(include_unavailable=True),
            *declared_plugin_infos(),
        ]
        if info["plugin_id"] not in removed
    ]
    off = frozenset(disabled)
    usable = [
        str(info["plugin_id"]) for info in infos if info["plugin_id"] not in off
    ]
    return catalog.Vocabulary.from_plugin_infos(infos, usable)


VOCABULARY = vocabulary()
"""The generic test vocabulary with every plugin usable."""
