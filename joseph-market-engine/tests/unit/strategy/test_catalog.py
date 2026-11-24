# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the rule vocabulary built from plugin metadata."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from joseph.domain import structures
from joseph.strategy.domain import catalog
from joseph.strategy.testing import vocabulary


def _info(
    plugin_id: object,
    *,
    kinds: list[str] | None = None,
    events: list[str] | None = None,
    values: list[object] | None = None,
) -> dict[str, object]:
    """Builds the vocabulary-relevant part of a plugin info.

    Args:
        plugin_id: Plugin id (malformed values are allowed for tests).
        kinds: Declared AOI kinds.
        events: Declared events.
        values: Declared ``output_values`` entries.

    Returns:
        The partial info dict.
    """
    return {
        "plugin_id": plugin_id,
        "output_kinds": kinds or [],
        "output_events": events or [],
        "output_values": values or [],
    }


_INFOS = [
    _info(
        "alpha",
        kinds=["BOX_A", "SHARED_KIND"],
        events=["PING", "AOI_EXPIRED"],
        values=[
            {"key": "mode", "type": "string", "choices": ["on", "off"]},
            {"key": "flag", "type": "boolean"},
            {"key": "level", "type": "decimal"},
            {"type": "string"},
            "not-a-spec",
        ],
    ),
    _info("beta", kinds=["SHARED_KIND", "BOX_B"], events=["PING", "PONG"]),
    _info("gamma", events=["PING"]),
    _info("alpha", kinds=["IGNORED"]),
    _info(7),
]


@pytest.fixture
def vocab() -> catalog.Vocabulary:
    """Builds a vocabulary of three plugins, ``gamma`` disabled.

    Returns:
        The vocabulary.
    """
    return catalog.Vocabulary.from_plugin_infos(_INFOS, ["alpha", "beta"])


def test_plugins_are_known_in_order_and_usable_by_lifecycle(
    vocab: catalog.Vocabulary,
) -> None:
    """Every declared plugin is known; only the usable ids may run."""
    assert vocab.order == ("alpha", "beta", "gamma")
    assert vocab.usable == frozenset({"alpha", "beta"})
    assert vocab.known("gamma")
    assert not vocab.known("delta")


def test_kinds_keep_their_first_publisher(vocab: catalog.Vocabulary) -> None:
    """A repeated kind keeps its first plugin; repeated ids are ignored."""
    assert dict(vocab.kind_plugin) == {
        "BOX_A": "alpha",
        "SHARED_KIND": "alpha",
        "BOX_B": "beta",
    }
    assert vocab.kind_plugins(["BOX_B", "BOX_A", "UNKNOWN"]) == (
        "alpha",
        "beta",
    )
    assert vocab.kind_plugins([]) == ()


def test_events_list_publishers_in_registration_order(
    vocab: catalog.Vocabulary,
) -> None:
    """Publishers keep registration order; reserved events are not listed."""
    assert dict(vocab.event_plugins) == {
        "PING": ("alpha", "beta", "gamma"),
        "PONG": ("beta",),
    }
    assert vocab.event_known("PONG")
    assert vocab.event_known(structures.AOI_INVALIDATED)
    assert not vocab.event_known("NOPE")


@pytest.mark.parametrize(
    ("name", "source", "expected"),
    [
        ("PING", None, "alpha"),
        ("PING", "gamma", "gamma"),
        ("PONG", None, "beta"),
        ("NOPE", None, None),
        ("NOPE", "beta", "beta"),
        (structures.AOI_EXPIRED, None, None),
        (structures.AOI_EXPIRED, "alpha", None),
    ],
)
def test_event_source_resolution(
    vocab: catalog.Vocabulary,
    name: str,
    source: str | None,
    expected: str | None,
) -> None:
    """Reserved: none; a given source wins; otherwise the first publisher."""
    assert vocab.event_source(name, source) == expected


def test_value_infos(vocab: catalog.Vocabulary) -> None:
    """Value specs keep type and choices; malformed entries are skipped."""
    alpha = vocab.values["alpha"]
    assert dict(alpha) == {
        "mode": catalog.ValueInfo("string", ("on", "off")),
        "flag": catalog.ValueInfo("boolean"),
        "level": catalog.ValueInfo("decimal"),
    }
    assert dict(vocab.values["beta"]) == {}


def test_empty_and_malformed_infos() -> None:
    """The empty vocabulary knows nothing; non-list fields are ignored."""
    empty = catalog.Vocabulary.empty()
    assert empty.order == ()
    assert empty.usable == frozenset()
    assert empty.event_source("PING", None) is None
    odd = catalog.Vocabulary.from_plugin_infos(
        [
            {
                "plugin_id": "odd",
                "output_kinds": "BOX",
                "output_events": None,
                "output_values": {"key": "x"},
            }
        ],
        ["odd"],
    )
    assert odd.known("odd")
    assert dict(odd.kind_plugin) == {}
    assert dict(odd.values["odd"]) == {}


def test_test_vocabulary_covers_builtins_and_test_plugins() -> None:
    """The generic test vocabulary resolves every test kind and event."""
    vocab = vocabulary.VOCABULARY
    assert vocab.order[:9] == (
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
    assert vocab.kind_plugin[vocabulary.ZONE_UP] == vocabulary.TEST_ZONES
    assert vocab.kind_plugin[vocabulary.LEVEL_HIGH] == vocabulary.TEST_LEVELS
    assert vocab.kind_plugin["PIVOT_S1"] == "pivot_points"
    assert vocab.kind_plugin["ECHO_ZONE"] == "echo_zones"
    assert vocab.event_plugins[vocabulary.SIGNAL_SHARED] == (
        vocabulary.TEST_SIGNALS,
        vocabulary.TEST_STATE,
    )
    assert vocab.event_source("ECHO_UP", None) == "echo"
    trend = vocab.values[vocabulary.TEST_STATE]["trend"]
    assert trend.choices == vocabulary.TREND_CHOICES
    assert vocab.values["volume_delta"]["method"].type == catalog.STRING
    disabled = vocabulary.vocabulary(disabled={"ema"}, without={"rsi"})
    assert disabled.known("ema")
    assert "ema" not in disabled.usable
    assert not disabled.known("rsi")


def test_declared_infos_conform_to_the_contract(
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """The declared-only test plugin infos are valid IndicatorPluginInfo."""
    infos = vocabulary.declared_plugin_infos()
    assert [info["plugin_id"] for info in infos] == [
        vocabulary.TEST_ZONES,
        vocabulary.TEST_LEVELS,
        vocabulary.TEST_SIGNALS,
        vocabulary.TEST_STATE,
    ]
    for info in infos:
        contract_validate("indicators.schema.json", "IndicatorPluginInfo", info)
