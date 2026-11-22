"""Unit tests for the indicator SDK primitives and registry."""

from __future__ import annotations

import decimal
import json

import pytest

from joseph.domain import errors
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.packaged import plugins as packaged
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import state_codec

D = decimal.Decimal
M5 = timeframes.Timeframe.M5


def test_settings_resolve_defaults_and_validate() -> None:
    """Overrides are typed and validated against the declaration."""
    specs = packaged.FvgIfvgPlugin.SETTINGS
    resolved = settings_lib.resolve(specs, {"min_gap_atr": "0.2"})
    assert resolved["min_gap_atr"] == D("0.2")
    assert resolved["max_zones"] == 30
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"unknown": 1})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"max_zones": "30"})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"max_zones": 0})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"min_gap_atr": True})


def test_input_schema_lists_every_setting() -> None:
    """The published JSON Schema mirrors the declarations."""
    schema = packaged.FvgIfvgPlugin().input_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {s.name for s in packaged.FvgIfvgPlugin.SETTINGS}
    assert schema["additionalProperties"] is False


def test_state_codec_round_trips_plugin_state() -> None:
    """Checkpoints are JSON-compatible and restore to an equal state."""
    plugin = packaged.FvgIfvgPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    for candle in testing.random_walk(300, seed=3, timeframe=M5):
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    blob = plugin.dump_state(state)
    text = json.dumps(blob)
    restored = plugin.restore_state(json.loads(text))
    assert restored == state
    assert plugin.dump_state(restored) == blob


def test_state_codec_rejects_unknown_types() -> None:
    """Unregistered classes cannot be smuggled into a checkpoint."""
    with pytest.raises(TypeError):
        state_codec.load({"__dc__": "Evil", "f": {}}, ())
    with pytest.raises(TypeError):
        state_codec.dump(object())


def test_registry_rejects_duplicates_and_bad_versions() -> None:
    """Duplicate ids and unsupported schema versions are refused."""
    registry = registry_lib.PluginRegistry()
    registry.register(packaged.FvgIfvgPlugin())
    with pytest.raises(errors.ConflictError):
        registry.register(packaged.FvgIfvgPlugin())

    class FutureSchema(packaged.FvgIfvgPlugin):
        """Plugin declaring an unsupported schema version."""

        PLUGIN_ID = "future"
        SCHEMA_VERSION = 99

    with pytest.raises(errors.UnsupportedError):
        registry.register(FutureSchema())


def test_removed_plugin_keeps_metadata_and_fails_actionably() -> None:
    """Removing a plugin preserves metadata and explains the failure."""
    registry = catalog.build_default_registry()
    registry.remove("fvg_ifvg", "replaced by fvg_v2")
    assert not registry.is_available("fvg_ifvg")
    assert "fvg_ifvg" not in registry.ids()
    assert "fvg_ifvg" in registry.ids(include_unavailable=True)
    metadata = registry.metadata("fvg_ifvg")
    assert metadata is not None
    assert metadata["lifecycle"] == "REMOVED"
    with pytest.raises(errors.UnsupportedError, match="replaced by fvg_v2"):
        registry.get("fvg_ifvg")
    listed = registry.list_infos()["plugins"]
    assert isinstance(listed, list)
    assert all(info["plugin_id"] != "fvg_ifvg" for info in listed)
    registry.enable("fvg_ifvg")
    assert registry.get("fvg_ifvg").plugin_id == "fvg_ifvg"


def test_unknown_plugin_is_not_found() -> None:
    """Unknown ids raise NotFoundError."""
    with pytest.raises(errors.NotFoundError):
        catalog.build_default_registry().get("nope")


def test_default_registry_has_the_ten_plugins() -> None:
    """Every packaged plugin is registered in display order."""
    assert catalog.build_default_registry().ids() == (
        "daily_bias",
        "market_structure",
        "fvg_ifvg",
        "order_breaker_blocks",
        "liquidity_sweeps",
        "order_flow_pressure",
        "auction_value_areas",
        "premium_discount_ote",
        "anchored_vwap",
        "regression_channel",
    )


def test_plugins_reject_bars_from_other_series() -> None:
    """Headers refuse foreign, non-final or non-increasing bars."""
    plugin = packaged.FvgIfvgPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    candle = testing.make_candle(M5, testing.DEFAULT_START_MS, 1, 2, 0.5, 1.5)
    state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    with pytest.raises(ValueError):
        plugin.on_bar_closed(state, protocol.BarInput(candle))
    unfinished = testing.make_candle(
        M5,
        testing.DEFAULT_START_MS + 300_000,
        1,
        2,
        0.5,
        1.5,
        is_final=False,
    )
    with pytest.raises(ValueError):
        plugin.on_bar_closed(state, protocol.BarInput(unfinished))


def test_on_bar_closed_does_not_mutate_the_input_state() -> None:
    """Plugins are pure: the previous state object is unchanged."""
    for plugin in catalog.default_plugins():
        candles = testing.random_walk(80, seed=11, timeframe=M5)
        state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
        for candle in candles[:60]:
            state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
        before = plugin.dump_state(state)
        for candle in candles[60:]:
            plugin.on_bar_closed(state, protocol.BarInput(candle))
        assert plugin.dump_state(state) == before, plugin.plugin_id
