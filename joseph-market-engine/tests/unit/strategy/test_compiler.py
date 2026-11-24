# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for semantic validation and compilation.

AOI is an area of interest: the price zone that an ``AOI_TOUCH`` node tests.
Rules read the generic test vocabulary (``testing.vocabulary``).
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Collection
from typing import TypeAlias

import pytest

from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import catalog
from joseph.strategy.domain import compiler
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary

V = vocabulary
Make: TypeAlias = Callable[..., ast.StrategyDefinition]
"""The ``make_definition`` fixture: ``make(when=None, **overrides)``."""

_TOUCH = {
    "op": "AOI_TOUCH",
    "kinds": [V.ZONE_UP],
    "side": "bullish",
    "within_bars": 3,
}
_FIXED = {
    "min_rr": "1",
    "stop_model": "SWING",
    "target_model": "FIXED_R",
    "fixed_r": "2",
}


def _compile(
    definition: ast.StrategyDefinition,
    vocab: catalog.Vocabulary = V.VOCABULARY,
    *,
    unsupported_ops: Collection[str] = (),
) -> compiler.CompileResult:
    """Compiles against the test vocabulary.

    Args:
        definition: Definition.
        vocab: Vocabulary to compile against.
        unsupported_ops: Operators removed from the build.

    Returns:
        The compile result.
    """
    return compiler.compile_strategy(
        definition, vocabulary=vocab, unsupported_ops=unsupported_ops
    )


def _codes(result: compiler.CompileResult) -> set[str]:
    """Collects error codes.

    Args:
        result: Result of ``compile_strategy``.

    Returns:
        The codes of its validation errors.
    """
    return {issue.code for issue in result.validation.errors}


def _warnings(result: compiler.CompileResult) -> set[str]:
    """Collects warning codes.

    Args:
        result: Result of ``compile_strategy``.

    Returns:
        The codes of its validation warnings.
    """
    return {issue.code for issue in result.validation.warnings}


def _issue(
    result: compiler.CompileResult, code: str
) -> compiler.ValidationIssue:
    """Finds the first error or warning with a code.

    Args:
        result: Result of ``compile_strategy``.
        code: Issue code.

    Returns:
        The issue.
    """
    issues = (*result.validation.errors, *result.validation.warnings)
    return next(issue for issue in issues if issue.code == code)


def _with_touch(*nodes: dict[str, object]) -> dict[str, object]:
    """Builds an ALL root of the anchoring touch and other nodes.

    Args:
        *nodes: Other nodes.

    Returns:
        The root node.
    """
    return {"op": "ALL", "children": [_TOUCH, *nodes]}


def test_templates_compile(
    definitions: dict[str, ast.StrategyDefinition],
) -> None:
    """The three example templates are valid without warnings."""
    assert set(definitions) == {
        fixtures.EMA_TREND_LONG,
        fixtures.RSI_REVERSION_SHORT,
        fixtures.PIVOT_BOUNCE_LONG,
    }
    for definition in definitions.values():
        result = _compile(definition)
        assert result.plan is not None, result.validation.errors
        assert not result.validation.warnings
    ema = _compile(definitions[fixtures.EMA_TREND_LONG]).validation
    assert set(ema.required_plugins) == {"ema", "rsi", "volume_delta"}
    assert [tf.value for tf in ema.required_timeframes] == ["15m", "1h"]
    pivot = _compile(definitions[fixtures.PIVOT_BOUNCE_LONG])
    assert pivot.plan is not None
    assert pivot.validation.required_plugins == (
        "pivot_points",
        "volume_delta",
    )
    assert pivot.plan.target_plugins == ("pivot_points",)
    assert pivot.plan.has_aoi_touch


def test_zone_rule_lists_its_requirements() -> None:
    """The zone-touch rule compiles and lists plugins per timeframe."""
    result = _compile(scenarios.zone_long_definition())
    assert result.plan is not None, result.validation.errors
    assert not result.validation.warnings
    assert set(result.validation.required_plugins) == {
        V.TEST_STATE,
        V.TEST_ZONES,
        V.TEST_SIGNALS,
        V.TEST_LEVELS,
    }
    assert [tf.value for tf in result.validation.required_timeframes] == [
        "5m",
        "1h",
        "4h",
    ]
    plugins = result.plan.requirements.plugins
    assert plugins[V.TEST_STATE] == frozenset({timeframes.Timeframe.H4})
    assert plugins[V.TEST_ZONES] == frozenset(
        {timeframes.Timeframe.M5, timeframes.Timeframe.H1}
    )
    assert result.plan.gate_count == 6
    assert result.plan.vocabulary is V.VOCABULARY
    assert result.plan.target_plugins == (V.TEST_LEVELS,)


def test_unavailable_plugin_fails_with_actionable_error(
    definitions: dict[str, ast.StrategyDefinition],
) -> None:
    """A disabled or missing plugin is an error, never a skipped condition."""
    definition = definitions[fixtures.EMA_TREND_LONG]
    disabled = _compile(definition, V.vocabulary(disabled={"volume_delta"}))
    assert disabled.plan is None
    issue = _issue(disabled, "PLUGIN_UNAVAILABLE")
    assert "'volume_delta' is disabled or removed" in issue.message
    assert issue.path.startswith("/when/children/2")
    missing = _compile(definition, V.vocabulary(without={"rsi"}))
    issue = _issue(missing, "PLUGIN_UNAVAILABLE")
    assert "'rsi' is not installed" in issue.message
    assert issue.path == "/when/children/1/left"


def test_removed_operator_fails(make_definition: Make) -> None:
    """Disabled rule operators fail validation."""
    result = _compile(make_definition(), unsupported_ops={"SEQUENCE"})
    assert "OP_UNSUPPORTED" in _codes(result)


def test_nested_lower_timeframe_is_a_warning(make_definition: Make) -> None:
    """A nesting lower timeframe is allowed with a warning."""
    when = _with_touch(
        {"op": "EVENT", "name": V.SIGNAL_UP, "tf": "1m", "within_bars": 0}
    )
    result = _compile(make_definition(when=when))
    assert result.validation.valid
    assert "LOWER_TIMEFRAME_INPUT" in _warnings(result)


def test_non_nesting_timeframe_is_rejected(make_definition: Make) -> None:
    """3m bars do not nest inside 5m bars."""
    when = _with_touch(
        {"op": "EVENT", "name": V.SIGNAL_UP, "tf": "3m", "within_bars": 0}
    )
    assert "TIMEFRAME_NOT_NESTED" in _codes(
        _compile(make_definition(when=when))
    )


def _state(**fields: object) -> dict[str, object]:
    """Builds a STATE node over ``test_state.trend``.

    Args:
        **fields: Field overrides.

    Returns:
        The node in contract form.
    """
    return {
        "op": "STATE",
        "plugin_id": V.TEST_STATE,
        "timeframes": ["1h", "4h"],
        "where": [{"field": "trend", "any_of": ["BULLISH"]}],
        **fields,
    }


@pytest.mark.parametrize(
    ("node", "code"),
    [
        (_state(match="K_OF_N"), "STATE_K_REQUIRED"),
        (_state(match="K_OF_N", k=3), "STATE_K_EXCEEDS"),
        (_state(timeframes=["1h", "1h"]), "STATE_DUPLICATE_TF"),
        (_state(timeframes=["3m"]), "TIMEFRAME_NOT_NESTED"),
    ],
)
def test_state_errors(
    make_definition: Make, node: dict[str, object], code: str
) -> None:
    """K-of-N, duplicate and nesting timeframe rules are enforced."""
    assert code in _codes(_compile(make_definition(when=_with_touch(node))))


def test_state_lower_timeframe_is_allowed(make_definition: Make) -> None:
    """STATE may read a nesting lower timeframe, like indicator operands."""
    result = _compile(
        make_definition(when=_with_touch(_state(timeframes=["1m"])))
    )
    assert result.validation.valid
    assert "LOWER_TIMEFRAME_INPUT" in _warnings(result)


@pytest.mark.parametrize(
    ("node", "code", "path"),
    [
        (_state(k=1), "STATE_K_IGNORED", "/when/children/1"),
        (
            _state(where=[{"field": "missing", "any_of": ["x"]}]),
            "UNKNOWN_INDICATOR_FIELD",
            "/when/children/1/where/0/field",
        ),
        (
            _state(where=[{"field": "score", "any_of": ["1"]}]),
            "STATE_VALUE_NOT_PUBLISHED",
            "/when/children/1/where/0/field",
        ),
        (
            _state(where=[{"field": "trend", "any_of": ["BULLISH", "UP"]}]),
            "STATE_VALUE_NOT_PUBLISHED",
            "/when/children/1/where/0/any_of/1",
        ),
        (
            _state(where=[{"field": "trend", "any_of": [True]}]),
            "STATE_VALUE_NOT_PUBLISHED",
            "/when/children/1/where/0/any_of/0",
        ),
        (
            _state(where=[{"field": "armed", "any_of": ["true"]}]),
            "STATE_VALUE_NOT_PUBLISHED",
            "/when/children/1/where/0/any_of/0",
        ),
    ],
    ids=[
        "k-ignored",
        "unknown-field",
        "decimal-value",
        "not-a-choice",
        "bool-for-string",
        "string-for-bool",
    ],
)
def test_state_warnings(
    make_definition: Make, node: dict[str, object], code: str, path: str
) -> None:
    """STATE clauses that can never match warn but stay valid."""
    result = _compile(make_definition(when=_with_touch(node)))
    assert result.validation.valid
    assert _issue(result, code).path == path


def test_state_with_matching_values_has_no_warning(
    make_definition: Make,
) -> None:
    """Declared choices and boolean values compile cleanly."""
    node = _state(
        where=[
            {"field": "trend", "any_of": ["BULLISH", "NEUTRAL"]},
            {"field": "armed", "any_of": [True]},
        ]
    )
    result = _compile(make_definition(when=_with_touch(node)))
    assert result.validation.valid
    assert not result.validation.warnings


def test_unknown_kinds_are_rejected(make_definition: Make) -> None:
    """Kinds no installed plugin declares are errors wherever they appear."""
    when = _with_touch(
        {"op": "IN_ZONE", "kinds": [V.ZONE_UP, "NOPE"], "tf": "1h"},
        {
            "op": "AOI_TOUCH",
            "kinds": ["MISSING_KIND"],
            "side": "bullish",
            "within_bars": 0,
        },
        {
            "op": "AOI_OVERLAP",
            "kinds_a": [V.ZONE_UP],
            "kinds_b": ["OTHER_KIND"],
            "side": "bullish",
        },
    )
    result = _compile(
        make_definition(
            when=when,
            risk={
                "min_rr": "2",
                "stop_model": "AOI_INVALIDATION",
                "target_model": "NEAREST_AOI",
                "target_kinds": [V.LEVEL_HIGH, "GONE"],
            },
        )
    )
    paths = {
        issue.path
        for issue in result.validation.errors
        if issue.code == "KIND_UNKNOWN"
    }
    assert paths == {
        "/when/children/1/kinds/1",
        "/when/children/2/kinds/0",
        "/when/children/3/kinds_b/0",
        "/risk/target_kinds/1",
    }


def test_kinds_of_disabled_plugins_are_unavailable(
    make_definition: Make,
) -> None:
    """A known kind of a disabled plugin reports the plugin, not the kind."""
    result = _compile(
        make_definition(when=_with_touch()),
        V.vocabulary(disabled={V.TEST_ZONES}),
    )
    assert "PLUGIN_UNAVAILABLE" in _codes(result)
    assert "KIND_UNKNOWN" not in _codes(result)


def test_in_zone_groups(make_definition: Make) -> None:
    """A group must read one timeframe and should have several members."""
    mixed = _with_touch(
        {"op": "IN_ZONE", "kinds": [V.ZONE_UP], "tf": "1h", "group": "A"},
        {"op": "IN_ZONE", "kinds": [V.ZONE_DOWN], "tf": "4h", "group": "A"},
    )
    assert "GROUP_TF_MISMATCH" in _codes(_compile(make_definition(when=mixed)))
    single = _with_touch(
        {"op": "IN_ZONE", "kinds": [V.ZONE_UP], "tf": "1h", "group": "A"}
    )
    result = _compile(make_definition(when=single))
    assert result.validation.valid
    assert "GROUP_SINGLE" in _warnings(result)


def test_in_zone_requires_the_kinds_plugin(make_definition: Make) -> None:
    """Each kind requires its publishing plugin on the zone timeframe."""
    when = _with_touch(
        {"op": "IN_ZONE", "kinds": [V.ZONE_UP, V.LEVEL_LOW], "tf": "4h"}
    )
    plan = _compile(make_definition(when=when)).plan
    assert plan is not None
    plugins = plan.requirements.plugins
    assert timeframes.Timeframe.H4 in plugins[V.TEST_ZONES]
    assert timeframes.Timeframe.H4 in plugins[V.TEST_LEVELS]


def _event(**fields: object) -> dict[str, object]:
    """Builds an EVENT node.

    Args:
        **fields: Field overrides.

    Returns:
        The node in contract form.
    """
    return {"op": "EVENT", "name": V.SIGNAL_UP, "within_bars": 0, **fields}


def test_event_vocabulary(make_definition: Make) -> None:
    """Unknown events and mismatched sources are errors."""
    unknown = _with_touch(_event(name="NO_SUCH_EVENT"))
    result = _compile(make_definition(when=unknown))
    assert _issue(result, "EVENT_UNKNOWN").path == "/when/children/1/name"
    mismatch = _with_touch(_event(source=V.TEST_STATE))
    result = _compile(make_definition(when=mismatch))
    assert _issue(result, "EVENT_SOURCE_MISMATCH").path == (
        "/when/children/1/source"
    )
    missing_source = _with_touch(_event(source="not_installed"))
    assert "PLUGIN_UNAVAILABLE" in _codes(
        _compile(make_definition(when=missing_source))
    )


def test_event_sources_and_requirements(make_definition: Make) -> None:
    """Shared names warn without a source and require the resolved one."""
    implicit = _compile(
        make_definition(when=_with_touch(_event(name=V.SIGNAL_SHARED)))
    )
    assert implicit.plan is not None
    assert "EVENT_SOURCE_IMPLICIT" in _warnings(implicit)
    assert V.TEST_SIGNALS in implicit.plan.requirements.plugins
    assert V.TEST_STATE not in implicit.plan.requirements.plugins
    explicit = _compile(
        make_definition(
            when=_with_touch(_event(name=V.SIGNAL_SHARED, source=V.TEST_STATE))
        )
    )
    assert explicit.plan is not None
    assert not explicit.validation.warnings
    assert V.TEST_STATE in explicit.plan.requirements.plugins
    reserved = _compile(
        make_definition(when=_with_touch(_event(name="AOI_EXPIRED")))
    )
    assert reserved.validation.valid
    assert V.TEST_SIGNALS not in reserved.validation.required_plugins


def test_sequence_steps_are_checked(make_definition: Make) -> None:
    """Sequence steps get the same event checks with step paths."""
    when = _with_touch(
        {
            "op": "SEQUENCE",
            "within_bars": 3,
            "steps": [
                {"name": V.SIGNAL_DOWN, "source": "echo"},
                {"name": "UNHEARD_OF"},
            ],
        }
    )
    result = _compile(make_definition(when=when))
    assert _issue(result, "EVENT_SOURCE_MISMATCH").path == (
        "/when/children/1/steps/0/source"
    )
    assert _issue(result, "EVENT_UNKNOWN").path == (
        "/when/children/1/steps/1/name"
    )


def test_sequence_window_must_fit_steps(make_definition: Make) -> None:
    """Steps need distinct bars inside the window."""
    when = _with_touch(
        {
            "op": "SEQUENCE",
            "within_bars": 2,
            "steps": [
                {"name": V.SIGNAL_DOWN},
                {"name": "ECHO_UP"},
                {"name": V.SIGNAL_UP},
            ],
        }
    )
    assert "SEQUENCE_WINDOW_TOO_SHORT" in _codes(
        _compile(make_definition(when=when))
    )


@pytest.mark.parametrize(
    ("fields", "code", "path"),
    [
        (
            {"time_zone": "Mars/Olympus"},
            "TIME_ZONE_UNKNOWN",
            "/when/children/1/time_zone",
        ),
        (
            {
                "windows": [
                    {"start": "09:30", "end": "11:30"},
                    {"start": "12:00", "end": "12:00"},
                ]
            },
            "TIME_WINDOW_EMPTY",
            "/when/children/1/windows/1",
        ),
    ],
)
def test_time_window_errors(
    make_definition: Make, fields: dict[str, object], code: str, path: str
) -> None:
    """Unknown zones and empty windows are errors."""
    node = {
        "op": "TIME_WINDOW",
        "time_zone": "America/New_York",
        "windows": [{"start": "09:30", "end": "11:30"}],
        **fields,
    }
    result = _compile(make_definition(when=_with_touch(node)))
    assert _issue(result, code).path == path


def test_time_window_wrapping_is_valid(make_definition: Make) -> None:
    """A window ending before it starts wraps past midnight."""
    node: dict[str, object] = {
        "op": "TIME_WINDOW",
        "time_zone": "Asia/Taipei",
        "windows": [{"start": "22:00", "end": "02:00"}],
        "weekdays": [1, 2, 3, 4, 5],
    }
    result = _compile(make_definition(when=_with_touch(node)))
    assert result.validation.valid
    assert not result.validation.warnings


def test_duplicate_node_ids_are_rejected(make_definition: Make) -> None:
    """Editor node ids must be unique."""
    when = {"op": "ALL", "id": "x", "children": [{**_TOUCH, "id": "x"}]}
    assert "DUPLICATE_NODE_ID" in _codes(_compile(make_definition(when=when)))


def test_risk_model_requirements(make_definition: Make) -> None:
    """FIXED_R needs fixed_r, ATR stops need a buffer, AOI stops a touch."""
    fixed = make_definition(
        risk={
            "min_rr": "2",
            "stop_model": "AOI_INVALIDATION",
            "target_model": "FIXED_R",
        }
    )
    assert "FIXED_R_REQUIRED" in _codes(_compile(fixed))
    atr = make_definition(
        risk={
            "min_rr": "2",
            "stop_model": "ATR",
            "target_model": "FIXED_R",
            "fixed_r": "2",
        }
    )
    assert "ATR_BUFFER_REQUIRED" in _codes(_compile(atr))
    no_touch = make_definition(when=_event())
    assert "AOI_STOP_REQUIRES_TOUCH" in _codes(_compile(no_touch))


def test_target_kinds(make_definition: Make) -> None:
    """NEAREST_AOI needs kinds; FIXED_R ignores them with a warning."""
    nearest = {
        "min_rr": "2",
        "stop_model": "AOI_INVALIDATION",
        "target_model": "NEAREST_AOI",
    }
    result = _compile(make_definition(risk=nearest))
    assert _issue(result, "TARGET_KINDS_REQUIRED").path == (
        "/risk/target_kinds"
    )
    ignored = _compile(
        make_definition(risk={**_FIXED, "target_kinds": [V.LEVEL_HIGH]})
    )
    assert ignored.validation.valid
    assert _issue(ignored, "TARGET_KINDS_IGNORED").path == "/risk/target_kinds"
    plan = _compile(
        make_definition(
            risk={**nearest, "target_kinds": [V.LEVEL_HIGH, "PIVOT_R1"]}
        )
    ).plan
    assert plan is not None
    assert plan.target_plugins == ("pivot_points", V.TEST_LEVELS)
    assert plan.requirements.plugins["pivot_points"] == frozenset(
        {timeframes.Timeframe.M5}
    )


def test_negated_touch_does_not_anchor_a_stop(make_definition: Make) -> None:
    """An AOI_TOUCH under NOT cannot anchor an invalidation stop."""
    when = {"op": "ALL", "children": [{"op": "NOT", "child": _TOUCH}, _event()]}
    assert "AOI_STOP_REQUIRES_TOUCH" in _codes(
        _compile(make_definition(when=when))
    )


def test_universe_consistency(make_definition: Make) -> None:
    """Listed instruments must belong to listed venues and not overlap."""
    bad = make_definition(
        universe={
            "qualification": "ANY",
            "venues": ["binance"],
            "whitelist": ["bitget:perp:BTCUSDT", "binance:perp:ETHUSDT"],
            "blacklist": ["binance:perp:ETHUSDT"],
        }
    )
    codes = _codes(_compile(bad))
    assert {"UNIVERSE_VENUE_MISMATCH", "UNIVERSE_OVERLAP"} <= codes


def test_candle_operand_on_other_timeframe_is_rejected(
    make_definition: Make,
) -> None:
    """Candle operands read the signal timeframe only."""
    when = _with_touch(
        {
            "op": "COMPARE",
            "left": {"kind": "candle", "tf": "1h", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "1"},
        }
    )
    assert "CANDLE_TF_UNSUPPORTED" in _codes(
        _compile(make_definition(when=when))
    )


def test_warnings_for_rr_floor_auto_live_and_fields(
    make_definition: Make,
) -> None:
    """A low risk-reward floor, AUTO_LIVE, unknown fields and constants warn."""
    when = _with_touch(
        {"op": "RISK_REWARD", "min_rr": "1"},
        {
            "op": "COMPARE",
            "left": {
                "kind": "indicator",
                "plugin_id": "volume_delta",
                "field": "nonexistent",
            },
            "cmp": "gt",
            "right": {"kind": "const", "value": "1"},
        },
        {
            "op": "COMPARE",
            "left": {"kind": "const", "value": "1"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "0"},
        },
    )
    result = _compile(make_definition(when=when, execution_mode="AUTO_LIVE"))
    assert result.validation.valid
    assert {
        "RR_BELOW_FLOOR",
        "AUTO_LIVE_REQUIRES_DEPLOYMENT",
        "UNKNOWN_INDICATOR_FIELD",
        "CONSTANT_COMPARISON",
    } <= _warnings(result)


def test_lookback_and_window(make_definition: Make) -> None:
    """Lookback sums nested temporal operators; window covers risk."""
    when = _with_touch(
        {
            "op": "WITHIN_BARS",
            "bars": 5,
            "child": {
                "op": "PERSIST_FOR",
                "bars": 3,
                "child": _event(name=V.SIGNAL_DOWN, within_bars=2),
            },
        }
    )
    result = _compile(make_definition(when=when))
    assert result.plan is not None
    assert result.plan.lookback_bars == 5 + 2 + 2
    assert result.plan.window_bars == compiler.RISK_WINDOW_BARS
    deep = {"op": "WITHIN_BARS", "bars": 150, "child": _TOUCH}
    plan = _compile(
        make_definition(when={"op": "ALL", "children": [deep]})
    ).plan
    assert plan is not None
    assert plan.window_bars == 154


def test_depth_and_size_limits(make_definition: Make) -> None:
    """Deep trees are rejected."""
    node: dict[str, object] = _TOUCH
    for _ in range(compiler.MAX_DEPTH):
        node = {"op": "NOT", "child": {"op": "NOT", "child": node}}
    assert "TOO_DEEP" in _codes(_compile(make_definition(when=node)))


def test_validation_contract_shape(
    make_definition: Make,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Validation results, with errors and warnings, conform to the schema."""
    when = _with_touch(
        _event(name=V.SIGNAL_SHARED), _event(name="NO_SUCH_EVENT")
    )
    result = _compile(make_definition(when=when))
    assert result.validation.errors
    assert result.validation.warnings
    contract_validate(
        "strategy.schema.json",
        "ValidationResult",
        result.validation.to_contract(),
    )
