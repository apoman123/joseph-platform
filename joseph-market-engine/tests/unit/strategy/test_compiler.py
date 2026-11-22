"""Unit tests for semantic validation and compilation."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.testing import fixtures

Make = Callable[..., ast.StrategyDefinition]

_TOUCH = {
    "op": "AOI_TOUCH",
    "kinds": ["FVG"],
    "side": "bullish",
    "within_bars": 3,
}


def _codes(result: compiler.CompileResult) -> set[str]:
    """Collects error codes.

    Args:
        result: Compile result.

    Returns:
        Error codes.
    """
    return {issue.code for issue in result.validation.errors}


def _warnings(result: compiler.CompileResult) -> set[str]:
    """Collects warning codes.

    Args:
        result: Compile result.

    Returns:
        Warning codes.
    """
    return {issue.code for issue in result.validation.warnings}


def test_fixtures_compile(
    definitions: dict[str, ast.StrategyDefinition],
) -> None:
    """Long A and Short B are valid and list their requirements."""
    long_a = compiler.compile_strategy(definitions[fixtures.LONG_A])
    assert long_a.validation.valid
    assert long_a.plan is not None
    assert set(long_a.validation.required_plugins) == {
        "daily_bias",
        "premium_discount_ote",
        "fvg_ifvg",
        "order_breaker_blocks",
        "liquidity_sweeps",
        "order_flow_pressure",
    }
    assert [tf.value for tf in long_a.validation.required_timeframes] == [
        "5m",
        "1h",
        "4h",
    ]
    plugins = long_a.plan.requirements.plugins
    assert plugins["daily_bias"] == frozenset({timeframes.Timeframe.H4})
    assert long_a.plan.gate_count == 6
    assert long_a.plan.has_aoi_touch
    assert compiler.compile_strategy(definitions[fixtures.SHORT_B]).plan


def test_removed_plugin_fails_with_actionable_error(
    definitions: dict[str, ast.StrategyDefinition],
) -> None:
    """A removed plugin is an error, never a skipped condition."""
    available = {
        "daily_bias",
        "premium_discount_ote",
        "fvg_ifvg",
        "order_breaker_blocks",
        "liquidity_sweeps",
    }
    result = compiler.compile_strategy(
        definitions[fixtures.LONG_A], available_plugins=available
    )
    assert result.plan is None
    issue = next(
        i for i in result.validation.errors if i.code == "PLUGIN_UNAVAILABLE"
    )
    assert "order_flow_pressure" in issue.message
    assert issue.path.startswith("/when/children/4")


def test_removed_operator_fails(make_definition: Make) -> None:
    """Disabled rule operators fail validation."""
    result = compiler.compile_strategy(
        make_definition(), unsupported_ops={"SEQUENCE"}
    )
    assert "OP_UNSUPPORTED" in _codes(result)


def test_bias_finer_than_signal_is_rejected(make_definition: Make) -> None:
    """HTF bias cannot use a finer timeframe than the signal."""
    when = {
        "op": "ALL",
        "children": [
            {"op": "BIAS", "timeframes": ["1m"], "value": "BULLISH"},
            _TOUCH,
        ],
    }
    assert "TIMEFRAME_FINER_THAN_SIGNAL" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_nested_lower_timeframe_is_a_warning(make_definition: Make) -> None:
    """A nesting lower timeframe is allowed with a warning."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "EVENT",
                "name": "SELLER_FAILURE",
                "tf": "1m",
                "within_bars": 0,
            },
        ],
    }
    result = compiler.compile_strategy(make_definition(when=when))
    assert result.validation.valid
    assert "LOWER_TIMEFRAME_INPUT" in _warnings(result)


def test_non_nesting_timeframe_is_rejected(make_definition: Make) -> None:
    """3m bars do not nest inside 5m bars."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "EVENT",
                "name": "SELLER_FAILURE",
                "tf": "3m",
                "within_bars": 0,
            },
        ],
    }
    assert "TIMEFRAME_NOT_NESTED" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


@pytest.mark.parametrize(
    ("bias", "code"),
    [
        (
            {
                "op": "BIAS",
                "timeframes": ["1h", "4h"],
                "value": "BULLISH",
                "match": "K_OF_N",
            },
            "BIAS_K_REQUIRED",
        ),
        (
            {
                "op": "BIAS",
                "timeframes": ["1h", "4h"],
                "value": "BULLISH",
                "match": "K_OF_N",
                "k": 3,
            },
            "BIAS_K_EXCEEDS",
        ),
        (
            {"op": "BIAS", "timeframes": ["1h", "1h"], "value": "BULLISH"},
            "BIAS_DUPLICATE_TF",
        ),
    ],
)
def test_bias_semantics(
    make_definition: Make, bias: dict[str, object], code: str
) -> None:
    """K-of-N and duplicate timeframe rules are enforced."""
    when = {"op": "ALL", "children": [bias, _TOUCH]}
    assert code in _codes(compiler.compile_strategy(make_definition(when=when)))


def test_sequence_window_must_fit_steps(make_definition: Make) -> None:
    """Steps need distinct bars inside the window."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "SEQUENCE",
                "within_bars": 2,
                "steps": [
                    {"name": "SSL_SWEEP"},
                    {"name": "CHOCH_BULL"},
                    {"name": "SELLER_FAILURE"},
                ],
            },
        ],
    }
    assert "SEQUENCE_WINDOW_TOO_SHORT" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_duplicate_node_ids_are_rejected(make_definition: Make) -> None:
    """Editor node ids must be unique."""
    when = {"op": "ALL", "id": "x", "children": [{**_TOUCH, "id": "x"}]}
    assert "DUPLICATE_NODE_ID" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_range_group_members_share_timeframe(make_definition: Make) -> None:
    """A range group cannot mix dealing-range timeframes."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "PRICE_IN_ZONE",
                "zone": "DISCOUNT",
                "tf": "1h",
                "range_group": "A",
            },
            {
                "op": "PRICE_IN_ZONE",
                "zone": "OTE",
                "tf": "4h",
                "range_group": "A",
            },
        ],
    }
    assert "RANGE_GROUP_TF_MISMATCH" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_contradictory_range_group_warns(make_definition: Make) -> None:
    """Premium and discount in one group can never both hold."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "PRICE_IN_ZONE",
                "zone": "DISCOUNT",
                "tf": "1h",
                "range_group": "A",
            },
            {
                "op": "PRICE_IN_ZONE",
                "zone": "PREMIUM",
                "tf": "1h",
                "range_group": "A",
            },
        ],
    }
    assert "RANGE_GROUP_CONTRADICTORY" in _warnings(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_risk_model_requirements(make_definition: Make) -> None:
    """FIXED_R needs fixed_r, ATR stops need a buffer, AOI stops a touch."""
    fixed = make_definition(
        risk={
            "min_rr": "2",
            "stop_model": "AOI_INVALIDATION",
            "target_model": "FIXED_R",
        }
    )
    assert "FIXED_R_REQUIRED" in _codes(compiler.compile_strategy(fixed))
    atr = make_definition(
        risk={
            "min_rr": "2",
            "stop_model": "ATR",
            "target_model": "FIXED_R",
            "fixed_r": "2",
        }
    )
    assert "ATR_BUFFER_REQUIRED" in _codes(compiler.compile_strategy(atr))
    no_touch = make_definition(
        when={"op": "EVENT", "name": "SSL_SWEEP", "within_bars": 0}
    )
    assert "AOI_STOP_REQUIRES_TOUCH" in _codes(
        compiler.compile_strategy(no_touch)
    )


def test_negated_touch_does_not_anchor_a_stop(make_definition: Make) -> None:
    """An AOI_TOUCH under NOT cannot anchor an invalidation stop."""
    when = {
        "op": "ALL",
        "children": [
            {"op": "NOT", "child": _TOUCH},
            {"op": "EVENT", "name": "SSL_SWEEP", "within_bars": 0},
        ],
    }
    assert "AOI_STOP_REQUIRES_TOUCH" in _codes(
        compiler.compile_strategy(make_definition(when=when))
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
    codes = _codes(compiler.compile_strategy(bad))
    assert {"UNIVERSE_VENUE_MISMATCH", "UNIVERSE_OVERLAP"} <= codes


def test_candle_operand_on_other_timeframe_is_rejected(
    make_definition: Make,
) -> None:
    """Candle operands read the signal timeframe only."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "COMPARE",
                "left": {"kind": "candle", "tf": "1h", "field": "close"},
                "cmp": "gt",
                "right": {"kind": "const", "value": "1"},
            },
        ],
    }
    assert "CANDLE_TF_UNSUPPORTED" in _codes(
        compiler.compile_strategy(make_definition(when=when))
    )


def test_warnings_for_rr_floor_auto_live_and_fields(
    make_definition: Make,
) -> None:
    """Non-blocking issues are reported as warnings."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {"op": "RISK_REWARD", "min_rr": "1"},
            {
                "op": "COMPARE",
                "left": {
                    "kind": "indicator",
                    "plugin_id": "order_flow_pressure",
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
        ],
    }
    result = compiler.compile_strategy(
        make_definition(when=when, execution_mode="AUTO_LIVE")
    )
    assert result.validation.valid
    assert {
        "RR_BELOW_FLOOR",
        "AUTO_LIVE_REQUIRES_DEPLOYMENT",
        "UNKNOWN_INDICATOR_FIELD",
        "CONSTANT_COMPARISON",
    } <= _warnings(result)


def test_lookback_and_window(make_definition: Make) -> None:
    """Lookback sums nested temporal operators; window covers risk."""
    when = {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "WITHIN_BARS",
                "bars": 5,
                "child": {
                    "op": "PERSIST_FOR",
                    "bars": 3,
                    "child": {
                        "op": "EVENT",
                        "name": "SSL_SWEEP",
                        "within_bars": 2,
                    },
                },
            },
        ],
    }
    result = compiler.compile_strategy(make_definition(when=when))
    assert result.plan is not None
    assert result.plan.lookback_bars == 5 + 2 + 2
    assert result.plan.window_bars == compiler.RISK_WINDOW_BARS
    deep = {"op": "WITHIN_BARS", "bars": 150, "child": _TOUCH}
    plan = compiler.compile_strategy(
        make_definition(when={"op": "ALL", "children": [deep]})
    ).plan
    assert plan is not None
    assert plan.window_bars == 154


def test_depth_and_size_limits(make_definition: Make) -> None:
    """Deep trees are rejected."""
    node: dict[str, object] = _TOUCH
    for _ in range(compiler.MAX_DEPTH):
        node = {"op": "NOT", "child": {"op": "NOT", "child": node}}
    assert "TOO_DEEP" in _codes(
        compiler.compile_strategy(make_definition(when=node))
    )


def test_validation_contract_shape(
    definitions: dict[str, ast.StrategyDefinition],
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Validation results conform to the schema."""
    result = compiler.compile_strategy(definitions[fixtures.SHORT_B])
    contract_validate(
        "strategy.schema.json",
        "ValidationResult",
        result.validation.to_contract(),
    )
