# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tests for the adapters around the external ``smc_indicators`` library."""

from __future__ import annotations

import decimal
import os
import pickle
import zoneinfo
from collections.abc import Mapping
from collections.abc import Sequence

import pytest
from smc_indicators import fvg_ifvg
from smc_indicators import liquidity_sweeps
from smc_indicators import market_structure
from smc_indicators import types as smc_types

from joseph.domain import errors
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import testing
from joseph.indicators import ticks
from joseph.indicators.packaged import bridge
from joseph.indicators.packaged import codec
from joseph.indicators.packaged import convert
from joseph.indicators.packaged import plugins as packaged
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import settings as settings_lib
from joseph.strategy.domain import catalog as strategy_catalog

D = decimal.Decimal
M5 = timeframes.Timeframe.M5
_AOI = structures.AoiState


class _Scripted:
    """Library stand-in that replays scripted zones and events."""

    def __init__(
        self,
        script: Sequence[
            tuple[Sequence[smc_types.Zone], Sequence[smc_types.Event]]
        ],
    ) -> None:
        """Initializes the script.

        Args:
            script: ``(zones, events)`` per bar.
        """
        self.script = list(script)
        self.index = 0

    def update(self, bar: smc_types.Bar) -> smc_types.Result:
        """Returns the next scripted result.

        Args:
            bar: Ignored library bar.

        Returns:
            Scripted result.
        """
        zones, events = self.script[self.index]
        self.index += 1
        return smc_types.Result(
            bar_index=self.index - 1,
            time_ms=bar.time_ms,
            zones=tuple(zones),
            events=tuple(events),
        )


_SCRIPT: list[tuple[list[smc_types.Zone], list[smc_types.Event]]] = []
"""Script consumed by ``_ScriptedPlugin`` (set per test)."""


class _ScriptedPlugin(bridge.PackagedPlugin):
    """Adapter around ``_Scripted`` for testing the generic bridge."""

    PLUGIN_ID = "scripted"
    DISPLAY_NAME = "Scripted"
    DESCRIPTION = "Test double."
    SETTINGS = (bridge.tick_spec(),)

    def _build(
        self,
        settings: Mapping[str, settings_lib.SettingValue],
        tick: decimal.Decimal,
        timeframe: timeframes.Timeframe,
    ) -> dict[str, object]:
        """Creates the scripted object.

        Args:
            settings: Ignored.
            tick: Ignored.
            timeframe: Ignored.

        Returns:
            Objects by role.
        """
        del settings, tick, timeframe
        return {"main": _Scripted(_SCRIPT)}

    def _advance(
        self,
        payload: bridge.Payload,
        bar: protocol.BarInput,
        settings: Mapping[str, settings_lib.SettingValue],
        step: decimal.Decimal,
    ) -> bridge.LibraryStep:
        """Replays one scripted bar.

        Args:
            payload: Live payload.
            bar: Bar input.
            settings: Ignored.
            step: Ignored.

        Returns:
            Library step.
        """
        del settings, step
        scripted = payload.objects["main"]
        assert isinstance(scripted, _Scripted)
        result = scripted.update(convert.to_bar(bar.candle))
        return bridge.LibraryStep(zones=result.zones, events=result.events)


def _zone(
    zone_id: str, kind: str, side: int = 1, **meta: object
) -> smc_types.Zone:
    """Builds a library zone.

    Args:
        zone_id: Library id.
        kind: Library kind.
        side: Side.
        **meta: Zone meta.

    Returns:
        Zone.
    """
    return smc_types.Zone(
        zone_id=zone_id,
        kind=kind,
        side=side,
        top=101.0,
        bottom=100.0,
        origin_time_ms=testing.DEFAULT_START_MS,
        confirmed_time_ms=testing.DEFAULT_START_MS,
        meta=meta,  # type: ignore[arg-type]
    )


def _run_script(
    script: list[tuple[list[smc_types.Zone], list[smc_types.Event]]],
) -> list[tuple[structures.IndicatorSnapshot, protocol.PluginOutput]]:
    """Runs the scripted plugin over random bars.

    Args:
        script: Per-bar library output.

    Returns:
        ``(snapshot, output)`` per bar.
    """
    _SCRIPT[:] = script
    candles = testing.random_walk(len(script), seed=1, timeframe=M5)
    return testing.run_bars(_ScriptedPlugin(), candles)


def test_inverted_zone_ends_once_and_the_new_kind_starts() -> None:
    """A kind change of one library zone is an inversion."""
    results = _run_script(
        [
            ([_zone("z1", "FVG")], []),
            ([_zone("z1", "IFVG", side=-1)], []),
            ([_zone("z1", "IFVG", side=-1)], []),
        ]
    )
    second = results[1][0]
    states = {(aoi.kind, aoi.state) for aoi in second.aois}
    assert (structures.AoiKind.FVG, _AOI.INVERTED) in states
    assert (structures.AoiKind.IFVG, _AOI.ACTIVE) in states
    third = results[2][0]
    assert [aoi.kind for aoi in third.aois] == [structures.AoiKind.IFVG]
    assert results[2][1].updated_aois == ()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("AOI_INVALIDATED", _AOI.INVALIDATED),
        ("AOI_EXPIRED", _AOI.EXPIRED),
        ("SSL_SWEEP", _AOI.SWEPT),
        ("BSL_BREAK", _AOI.INVALIDATED),
        ("MIDPOINT_TESTED", _AOI.EXPIRED),
    ],
)
def test_vanished_zone_state_follows_the_bar_event(
    event: str, expected: structures.AoiState
) -> None:
    """The terminal state is inferred from the event naming the zone."""
    results = _run_script(
        [
            ([_zone("z1", "SSL")], []),
            ([], [smc_types.Event(event, 0, 100.0, zone_id="z1")]),
        ]
    )
    ended = results[1][0].aois
    assert [aoi.state for aoi in ended] == [expected]
    contract_events = results[1][1].events
    if event != "MIDPOINT_TESTED":
        assert contract_events[0].aoi_id == ended[0].aoi_id
    else:
        assert contract_events == ()


def test_unchanged_zones_are_not_republished() -> None:
    """Only created or changed AOIs appear in ``updated_aois``."""
    results = _run_script(
        [
            ([_zone("z1", "OB")], []),
            ([_zone("z1", "OB")], []),
            ([_zone("z1", "OB", touched=True)], []),
        ]
    )
    assert len(results[0][1].updated_aois) == 1
    assert results[1][1].updated_aois == ()
    assert len(results[2][1].updated_aois) == 1


def test_unknown_library_kinds_stay_private() -> None:
    """Kinds outside the contract are not published."""
    results = _run_script([([_zone("z1", "PRIVATE_KIND")], [])])
    assert results[0][0].aois == ()


def test_range_id_meta_moves_to_the_aoi_field() -> None:
    """``range_id`` is a first-class AOI field."""
    results = _run_script([([_zone("z1", "OTE", range_id="r-1", mid=1.5)], [])])
    (aoi,) = results[0][0].aois
    assert aoi.range_id == "r-1"
    assert aoi.meta == {"mid": D("1.5")}


class _Evil:
    """Pickles to a call of an arbitrary function."""

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Reduces to ``os.getcwd()``.

        Returns:
            Reduce tuple.
        """
        return os.getcwd, ()


def test_restricted_codec_refuses_foreign_globals() -> None:
    """A tampered checkpoint cannot run code or reach modules."""
    with pytest.raises(pickle.UnpicklingError, match="forbidden"):
        codec.loads(pickle.dumps(_Evil()))
    smuggled = b"\x80\x05csmc_indicators.base\ncollections\n."
    with pytest.raises(pickle.UnpicklingError, match="forbidden"):
        codec.loads(smuggled)
    payload = bridge.Payload(tick=D("0.01"), objects={})
    assert codec.loads(codec.dumps(payload)) == payload
    assert codec.decode(codec.encode(b"abc")) == b"abc"
    zone = zoneinfo.ZoneInfo("America/New_York")
    assert codec.loads(codec.dumps({"zone": zone})) == {"zone": zone}


def test_price_conversion_hides_float_noise() -> None:
    """Prices keep tick precision plus four places, without float noise."""
    step = convert.quantum(D("0.01"))
    assert convert.price(0.1 + 0.2, step) == D("0.3")
    assert convert.price(100.125, step) == D("100.125")
    assert convert.price(float("nan"), step) is None
    assert convert.price(None, step) is None
    assert convert.ratio(12.345678, 2) == D("12.35")


def test_tick_is_inferred_from_price_precision() -> None:
    """Without an instrument tick, the first bar's decimals decide."""
    candle = testing.make_candle(
        M5, testing.DEFAULT_START_MS, "1.25", "1.3", 1, "1.255"
    )
    assert bridge.infer_tick(candle) == D("0.001")


def test_tick_size_is_injected_unless_overridden() -> None:
    """Every plugin declaring ``tick_size`` receives the instrument tick."""
    registry = catalog.build_default_registry()
    merged = ticks.with_tick_size(
        registry,
        ["fvg_ifvg", "liquidity_sweeps"],
        {"liquidity_sweeps": {"tick_size": "0.5"}},
        D("0.01"),
    )
    assert merged["fvg_ifvg"]["tick_size"] == "0.01"
    assert merged["liquidity_sweeps"]["tick_size"] == "0.5"
    assert ticks.with_tick_size(registry, ["fvg_ifvg"], {}, None) == {}


def test_published_values_match_the_strategy_catalog() -> None:
    """Strategies may reference every value a plugin publishes."""
    m1 = testing.random_walk(2400, seed=5, timeframe=timeframes.Timeframe.M1)
    aux = {
        tf: testing.aggregate(m1, tf)
        for tf in (
            timeframes.Timeframe.H1,
            timeframes.Timeframe.H4,
            timeframes.Timeframe.D1,
        )
    }
    candles = testing.aggregate(m1, M5)
    for plugin in catalog.default_plugins():
        results = testing.run_bars(plugin, candles[:60])
        keys = set(results[-1][0].values)
        published = strategy_catalog.PUBLISHED_VALUES[plugin.plugin_id]
        assert keys == set(published), plugin.plugin_id
    del aux


def test_every_output_kind_and_event_is_in_the_strategy_catalog() -> None:
    """The compiler can map every kind and event to its plugin."""
    for plugin in catalog.default_plugins():
        for kind in plugin.output_kinds:
            assert strategy_catalog.AOI_KIND_PLUGIN[kind] == plugin.plugin_id
        for name in plugin.output_events:
            owner = strategy_catalog.EVENT_PLUGIN[name]
            assert owner in (None, plugin.plugin_id, "daily_bias"), name


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fvg_adapter_publishes_exactly_the_library_zones(seed: int) -> None:
    """The adapter neither adds, drops nor moves library zones."""
    candles = testing.random_walk(400, seed=seed, timeframe=M5)
    library = fvg_ifvg.FvgIfvg(0.01)
    expected = library.run(convert.to_bar(c) for c in candles)[-1].zones
    plugin = packaged.FvgIfvgPlugin()
    results = testing.run_bars(plugin, candles, settings={"tick_size": "0.01"})
    live = [a for a in results[-1][0].aois if a.invalidated_at_ms is None]
    assert [
        (a.kind.value, a.lower, a.upper, a.quality_score) for a in live
    ] == [
        (
            z.kind,
            D(str(z.bottom)).quantize(D("0.000001")).normalize(),
            D(str(z.top)).quantize(D("0.000001")).normalize(),
            z.score,
        )
        for z in expected
    ]


@pytest.mark.parametrize("seed", [4, 5])
def test_structure_and_liquidity_adapters_match_the_library(seed: int) -> None:
    """Event counts of the adapters equal the library's."""
    candles = testing.random_walk(500, seed=seed, timeframe=M5)
    bars = [convert.to_bar(c) for c in candles]
    for library, plugin in (
        (
            market_structure.MarketStructure(0.01),
            packaged.MarketStructurePlugin(),
        ),
        (
            liquidity_sweeps.LiquiditySweeps(0.01),
            packaged.LiquiditySweepsPlugin(),
        ),
    ):
        names = [
            event.name
            for result in library.run(bars)
            for event in result.events
            if plugin._event_name(event) is not None
        ]
        got = testing.event_names(
            testing.run_bars(plugin, candles, settings={"tick_size": "0.01"})
        )
        aliases = dict(plugin.EVENT_ALIASES)
        assert [name for _, name in got] == [aliases.get(n, n) for n in names]


def test_settings_outside_the_pine_bounds_are_rejected_up_front() -> None:
    """Out-of-range inputs fail at initialize with a validation error."""
    plugin = packaged.FvgIfvgPlugin()
    with pytest.raises(errors.ValidationFailedError):
        plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, {"max_zones": 101})
    with pytest.raises(ValueError, match="does not support timeframe"):
        packaged.AuctionValueAreasPlugin().initialize(
            testing.DEFAULT_INSTRUMENT, timeframes.Timeframe.H1, None
        )


def test_library_rejections_become_validation_errors() -> None:
    """A rule the settings schema cannot express still yields a 422."""

    class _Strict(_ScriptedPlugin):
        """Adapter whose library refuses every configuration."""

        PLUGIN_ID = "strict"

        def _build(
            self,
            settings: Mapping[str, settings_lib.SettingValue],
            tick: decimal.Decimal,
            timeframe: timeframes.Timeframe,
        ) -> dict[str, object]:
            """Rejects the configuration.

            Args:
                settings: Ignored.
                tick: Ignored.
                timeframe: Ignored.

            Raises:
                ValueError: Always.
            """
            del settings, tick, timeframe
            raise ValueError("nope")

    with pytest.raises(errors.ValidationFailedError, match="strict: nope"):
        _Strict().initialize(testing.DEFAULT_INSTRUMENT, M5, None)


def test_a_failed_bar_does_not_corrupt_the_previous_state() -> None:
    """After a library error, retrying from the same head state is exact."""
    candles = testing.random_walk(40, seed=9, timeframe=M5)
    plugin = packaged.FvgIfvgPlugin()
    state = plugin.initialize(testing.DEFAULT_INSTRUMENT, M5, None)
    for candle in candles[:30]:
        state, _ = plugin.on_bar_closed(state, protocol.BarInput(candle))
    copy = plugin.restore_state(plugin.dump_state(state))
    expected, _ = plugin.on_bar_closed(copy, protocol.BarInput(candles[30]))
    payload = state.cell.payload
    assert payload is not None and state.cell.head == state.version
    indicator = payload.objects["main"]
    assert isinstance(indicator, fvg_ifvg.FvgIfvg)

    def _explode(bar: smc_types.Bar) -> smc_types.Result:
        """Fails like a library bug would.

        Args:
            bar: Library bar.

        Raises:
            RuntimeError: Always.
        """
        del bar
        raise RuntimeError("library failure")

    indicator.update = _explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        plugin.on_bar_closed(state, protocol.BarInput(candles[30]))
    assert state.cell.head == -1
    retried, _ = plugin.on_bar_closed(state, protocol.BarInput(candles[30]))
    assert plugin.snapshot(retried).to_contract() == (
        plugin.snapshot(expected).to_contract()
    )
