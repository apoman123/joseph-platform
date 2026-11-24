# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the indicator SDK primitives and registry."""

from __future__ import annotations

import decimal
import json

import pytest

from joseph.domain import errors
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.builtin import moving_average
from joseph.indicators.builtin import oscillators
from joseph.indicators.sdk import base
from joseph.indicators.sdk import metadata
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import state_codec

D = decimal.Decimal
M5 = timeframes.Timeframe.M5


def test_settings_resolve_defaults_and_validate() -> None:
    """Overrides are typed and validated against the declaration."""
    specs = oscillators.AtrPlugin.SETTINGS
    resolved = settings_lib.resolve(specs, {"tick_size": "0.5"})
    assert resolved["tick_size"] == D("0.5")
    assert resolved["length"] == 14
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"unknown": 1})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"length": "14"})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"length": 0})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"tick_size": True})


def test_input_schema_lists_every_setting() -> None:
    """The published JSON Schema mirrors the declarations."""
    plugin = moving_average.EmaPlugin()
    schema = plugin.input_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {s.name for s in plugin.SETTINGS}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "plugin",
    [
        *catalog.builtin_plugins(),
        testing.EchoPlugin(),
        testing.ZoneEchoPlugin(),
    ],
    ids=lambda plugin: plugin.plugin_id,
)
def test_json_checkpoints_restore_and_continue(
    plugin: protocol.IndicatorPlugin,
) -> None:
    """A JSON round-tripped checkpoint continues exactly like the original."""
    candles = testing.random_walk(260, seed=3, timeframe=M5)
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    for candle in candles[:200]:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    blob = plugin.dump_state(state)
    restored = plugin.restore_state(json.loads(json.dumps(blob)))
    assert restored == state
    assert plugin.dump_state(restored) == blob
    for candle in candles[200:]:
        bar = protocol.BarInput(candle)
        state, expected = plugin.on_bar_closed(state, bar)
        restored, got = plugin.on_bar_closed(restored, bar)
        assert got == expected
        assert (
            plugin.snapshot(restored).to_contract()
            == plugin.snapshot(state).to_contract()
        )


def test_state_codec_keeps_string_enums() -> None:
    """StrEnum members survive JSON with their type, not as plain strings."""
    blob = json.loads(json.dumps(state_codec.dump((M5, "5m"))))
    restored = state_codec.load(blob, (timeframes.Timeframe,))
    assert restored[0] is M5
    assert type(restored[1]) is str


def test_state_codec_rejects_unknown_types() -> None:
    """Unregistered classes cannot be smuggled into a checkpoint."""
    with pytest.raises(TypeError):
        state_codec.load({"__dc__": "Evil", "f": {}}, ())
    with pytest.raises(TypeError):
        state_codec.dump(object())


class _FutureSchema(moving_average.SmaPlugin):
    """Plugin declaring an unsupported schema version.

    Attributes:
        PLUGIN_ID: Registry id, distinct from the parent's.
        SCHEMA_VERSION: Plugin schema version, outside the supported set.
    """

    PLUGIN_ID = "future"
    SCHEMA_VERSION = 99


class _BadId(moving_average.SmaPlugin):
    """Plugin whose id breaks the pattern.

    Attributes:
        PLUGIN_ID: Registry id with upper-case letters.
    """

    PLUGIN_ID = "Bad-Id"


class _BadDisplay(moving_average.SmaPlugin):
    """Plugin plotting a value it does not declare.

    Attributes:
        PLUGIN_ID: Registry id.
        DISPLAY: Display with a plot of an undeclared value.
    """

    PLUGIN_ID = "bad_display"
    DISPLAY = metadata.Display(plots=(metadata.PlotStyle("nope", "Nope"),))


def test_registry_rejects_duplicates_versions_and_bad_metadata() -> None:
    """Duplicate ids, unsupported versions and bad declarations fail."""
    registry = registry_lib.PluginRegistry()
    registry.register(moving_average.SmaPlugin())
    with pytest.raises(errors.ConflictError):
        registry.register(moving_average.SmaPlugin())
    with pytest.raises(errors.UnsupportedError):
        registry.register(_FutureSchema())
    with pytest.raises(errors.ValidationFailedError, match="lower snake"):
        registry.register(_BadId())
    with pytest.raises(errors.ValidationFailedError, match="nope"):
        registry.register(_BadDisplay())
    assert registry.ids() == ("sma",)


def test_kinds_are_unique_across_plugins() -> None:
    """A second plugin declaring a registered kind is refused."""
    registry = registry_lib.PluginRegistry()
    registry.register(testing.ZoneEchoPlugin("zones_a"))
    with pytest.raises(errors.ConflictError, match="ECHO_ZONE"):
        registry.register(testing.ZoneEchoPlugin("zones_b"))
    registry.register(testing.ZoneEchoPlugin("zones_c", kind="OTHER_ZONE"))
    assert registry.kind_owner("ECHO_ZONE") == "zones_a"
    assert registry.kind_owner("OTHER_ZONE") == "zones_c"
    assert registry.kind_owner("MISSING") is None
    registry.disable("zones_a", "test")
    assert registry.kind_owner("ECHO_ZONE") == "zones_a"


def test_event_names_may_repeat_across_plugins() -> None:
    """Several plugins may publish one event name."""
    registry = registry_lib.PluginRegistry()
    registry.register(testing.EchoPlugin("first"))
    registry.register(testing.EchoPlugin("second"))
    assert registry.ids() == ("first", "second")


def test_removed_plugin_keeps_metadata_and_fails_actionably() -> None:
    """Removing a plugin preserves metadata and explains the failure."""
    registry = catalog.build_default_registry()
    registry.remove("ema", "replaced by ema_v2")
    assert not registry.is_available("ema")
    assert "ema" not in registry.ids()
    assert "ema" in registry.ids(include_unavailable=True)
    stored = registry.metadata("ema")
    assert stored is not None
    assert stored["lifecycle"] == "REMOVED"
    with pytest.raises(errors.UnsupportedError, match="replaced by ema_v2"):
        registry.get("ema")
    listed = registry.list_infos()["plugins"]
    assert isinstance(listed, list)
    assert all(info["plugin_id"] != "ema" for info in listed)
    registry.enable("ema")
    assert registry.get("ema").plugin_id == "ema"


def test_infos_include_unavailable_plugins_on_request() -> None:
    """``infos`` returns copies, with disabled plugins only on request."""
    registry = catalog.build_default_registry()
    registry.disable("rsi", "test")
    usable = [
        info["plugin_id"] for info in registry.infos(include_unavailable=False)
    ]
    every = [
        info["plugin_id"] for info in registry.infos(include_unavailable=True)
    ]
    assert "rsi" not in usable
    assert every == list(registry.ids(include_unavailable=True))
    first = registry.infos(include_unavailable=True)[0]
    first["display"]["z_index"] = 99  # type: ignore[index]  # contract payloads are typed dict[str, object]
    assert registry.infos(include_unavailable=True)[0] != first


def test_unknown_plugin_is_not_found() -> None:
    """Unknown ids raise NotFoundError."""
    with pytest.raises(errors.NotFoundError):
        catalog.build_default_registry().get("nope")


def test_default_registry_has_the_nine_builtins() -> None:
    """Every built-in is registered, in display order."""
    assert catalog.build_default_registry().ids() == (
        "sma",
        "ema",
        "anchored_vwap",
        "regression_channel",
        "bollinger_bands",
        "rsi",
        "atr",
        "volume_delta",
        "pivot_points",
    )


def test_builtins_declare_valid_metadata() -> None:
    """Every built-in and test plugin passes the metadata rules."""
    plugins = [
        *catalog.builtin_plugins(),
        testing.EchoPlugin(),
        testing.ZoneEchoPlugin(),
    ]
    for plugin in plugins:
        assert metadata.validate(plugin) == [], plugin.plugin_id
        info = plugin.info()
        assert info["schema_version"] == 2
        assert info["licensing_status"] == "CLEAN_ROOM_FROM_SPEC"
        assert info["parity_status"] == "SPEC_ONLY"


def test_plugins_reject_bars_from_other_series() -> None:
    """Plugins refuse a repeated bar and a non-final bar."""
    plugin = moving_average.EmaPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    candle = testing.make_candle(
        M5, testing.DEFAULT_START_MS, 1, 2, "0.5", "1.5"
    )
    state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    with pytest.raises(ValueError):
        plugin.on_bar_closed(state, protocol.BarInput(candle))
    unfinished = testing.make_candle(
        M5,
        testing.DEFAULT_START_MS + 300_000,
        1,
        2,
        "0.5",
        "1.5",
        is_final=False,
    )
    with pytest.raises(ValueError):
        plugin.on_bar_closed(state, protocol.BarInput(unfinished))


def test_on_bar_closed_does_not_mutate_the_input_state() -> None:
    """Plugins are pure: the previous state object is unchanged."""
    plugins = [
        *catalog.builtin_plugins(),
        testing.EchoPlugin(),
        testing.ZoneEchoPlugin(),
    ]
    candles = testing.random_walk(80, seed=11, timeframe=M5)
    for plugin in plugins:
        state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
        for candle in candles[:60]:
            state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
        before = plugin.dump_state(state)
        for candle in candles[60:]:
            plugin.on_bar_closed(state, protocol.BarInput(candle))
        assert plugin.dump_state(state) == before, plugin.plugin_id


def test_end_aoi_records_a_reason_and_refuses_live_states() -> None:
    """``end_aoi`` sets the end reason; a live state is not an end."""
    plugin = testing.ZoneEchoPlugin()
    candle = testing.make_candle(
        M5, testing.DEFAULT_START_MS, 1, 2, "0.5", "1.5"
    )
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    state, output = plugin.on_bar_closed(state, protocol.BarInput(candle))
    aoi = output.updated_aois[0]
    header = state.header
    ended = base.end_aoi(
        aoi, header, structures.AoiState.INVALIDATED, "broken_through"
    )
    assert ended.end_reason == "broken_through"
    assert ended.to_contract()["end_reason"] == "broken_through"
    with pytest.raises(ValueError, match="terminal"):
        base.end_aoi(aoi, header, structures.AoiState.TESTED)
    with pytest.raises(ValueError, match="end_reason"):
        base.end_aoi(aoi, header, structures.AoiState.EXPIRED, "Bad Reason")


def test_aoi_and_event_names_follow_the_pattern() -> None:
    """Kinds and event names must be upper snake case."""
    plugin = testing.EchoPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    with pytest.raises(ValueError, match="upper snake case"):
        plugin.event(state.header, "lower_case")
    with pytest.raises(ValueError, match="upper snake case"):
        plugin.aoi(
            state.header,
            aoi_id="zone-1",
            kind="Zone",
            side=structures.ZoneSide.NEUTRAL,
            lower=D(1),
            upper=D(2),
            created_at_ms=0,
        )
