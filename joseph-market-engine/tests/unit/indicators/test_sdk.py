"""Unit tests for the indicator SDK primitives and registry."""

from __future__ import annotations

import decimal
import json

import pytest

from joseph.domain import errors
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators.plugins.fvg_ifvg import plugin as fvg
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib
from joseph.indicators.sdk import settings as settings_lib
from joseph.indicators.sdk import state_codec
from joseph.indicators.sdk import ta

D = decimal.Decimal
M5 = timeframes.Timeframe.M5


def test_atr_seeds_with_simple_average_then_rma() -> None:
    """ATR is None until seeded, then follows Wilder smoothing."""
    atr = ta.Atr(length=3)
    rows = [
        ("10", "11", "9", "10"),
        ("10", "12", "10", "11"),
        ("11", "11.5", "10.5", "11"),
        ("11", "14", "11", "13"),
    ]
    values = []
    for candle in testing.candles_from_rows(rows):
        atr = atr.update(candle)
        values.append(atr.value)
    assert values[:2] == [None, None]
    assert values[2] == (D(2) + D(2) + D(1)) / 3
    assert values[3] == (values[2] * 2 + D(3)) / 3


def test_first_true_range_is_high_minus_low() -> None:
    """Without a previous close, true range is the bar range."""
    atr = ta.Atr(length=1).update(
        testing.make_candle(M5, testing.DEFAULT_START_MS, 10, 15, 8, 12)
    )
    assert atr.value == D(7)


def test_pivot_confirms_only_after_right_bars() -> None:
    """A pivot is reported at the close of the right-th following bar."""
    highs = ["10", "11", "15", "11", "10"]
    rows = [(h, h, str(D(h) - 1), h) for h in highs]
    tracker = ta.PivotTracker(left=2, right=2)
    reports = []
    candles = testing.candles_from_rows(rows)
    for candle in candles:
        tracker, high, _ = tracker.update(ta.BarPoint.of(candle))
        reports.append(high)
    assert reports[:4] == [None, None, None, None]
    assert reports[4] is not None
    assert reports[4].price == D(15)
    assert reports[4].start_ms == candles[2].start_ms
    assert reports[4].confirmed_at_ms == candles[4].end_ms


def test_equal_highs_do_not_form_a_pivot() -> None:
    """Strict comparison: an equal neighbor blocks the pivot."""
    rows = [(h, h, str(D(h) - 1), h) for h in ["10", "15", "15", "11", "10"]]
    tracker = ta.PivotTracker(left=1, right=1)
    found = []
    for candle in testing.candles_from_rows(rows):
        tracker, high, _ = tracker.update(ta.BarPoint.of(candle))
        found.append(high)
    assert all(high is None for high in found)


def test_score_helpers_clamp_and_round() -> None:
    """Scores are integers in 0..100."""
    assert ta.saturate(D(3), D(1)) == D(1)
    assert ta.saturate(D(-1), D(1)) == D(0)
    assert ta.score((D(55), D(1)), (D(30), D(1)), (D(15), D(1))) == 100
    assert ta.score((D(60), D("0.5"))) == 30
    assert ta.decay(68, D("0.70")) == 48


def test_settings_resolve_defaults_and_validate() -> None:
    """Overrides are typed and validated against the declaration."""
    specs = fvg.FvgIfvgPlugin.SETTINGS
    resolved = settings_lib.resolve(specs, {"min_gap_atr": "0.2"})
    assert resolved["min_gap_atr"] == D("0.2")
    assert resolved["atr_length"] == 14
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"unknown": 1})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"atr_length": "14"})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"atr_length": 0})
    with pytest.raises(errors.ValidationFailedError):
        settings_lib.resolve(specs, {"min_gap_atr": True})


def test_input_schema_lists_every_setting() -> None:
    """The published JSON Schema mirrors the declarations."""
    schema = fvg.FvgIfvgPlugin().input_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {s.name for s in fvg.FvgIfvgPlugin.SETTINGS}
    assert schema["additionalProperties"] is False


def test_state_codec_round_trips_plugin_state() -> None:
    """Checkpoints are JSON-compatible and restore to an equal state."""
    plugin = fvg.FvgIfvgPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    for candle in testing.random_walk(300, seed=3, timeframe=M5):
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    blob = plugin.dump_state(state)
    text = json.dumps(blob)
    restored = plugin.restore_state(json.loads(text))
    assert restored == state
    assert state_codec.dump(restored) == blob


def test_state_codec_rejects_unknown_types() -> None:
    """Unregistered classes cannot be smuggled into a checkpoint."""
    with pytest.raises(TypeError):
        state_codec.load({"__dc__": "Evil", "f": {}}, ())
    with pytest.raises(TypeError):
        state_codec.dump(object())


def test_registry_rejects_duplicates_and_bad_versions() -> None:
    """Duplicate ids and unsupported schema versions are refused."""
    registry = registry_lib.PluginRegistry()
    registry.register(fvg.FvgIfvgPlugin())
    with pytest.raises(errors.ConflictError):
        registry.register(fvg.FvgIfvgPlugin())

    class FutureSchema(fvg.FvgIfvgPlugin):
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


def test_default_registry_has_the_seven_plugins() -> None:
    """All first-party plugins are registered in display order."""
    assert catalog.build_default_registry().ids() == (
        "daily_bias",
        "fvg_ifvg",
        "order_breaker_blocks",
        "liquidity_sweeps",
        "order_flow_pressure",
        "auction_value_areas",
        "premium_discount_ote",
    )


def test_plugins_reject_bars_from_other_series() -> None:
    """Headers refuse foreign, non-final or non-increasing bars."""
    plugin = fvg.FvgIfvgPlugin()
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
        before = state_codec.dump(state)
        for candle in candles[60:]:
            plugin.on_bar_closed(state, protocol.BarInput(candle))
        assert state_codec.dump(state) == before, plugin.plugin_id
