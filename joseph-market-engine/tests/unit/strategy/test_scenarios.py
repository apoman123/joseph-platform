# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Generic worlds end to end: each fires once and each condition matters.

The worlds are the zone-touch rule over the test plugins and the three
example templates of the contract fixture over the built-in plugin ids.
"""

from __future__ import annotations

import decimal
from collections.abc import Sequence
from typing import Any

import pytest

from joseph.domain import quality
from joseph.domain import structures
from joseph.strategy.app import processor
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary

D = decimal.Decimal


def _market(
    frames: Sequence[structures.BarFrame], oi_change: str
) -> evaluator.MarketContext:
    """Builds a market context with a constant open-interest (OI) change.

    Args:
        frames: Frames that get one market point each, at their close.
        oi_change: OI change in percent, as a decimal string.

    Returns:
        The context.
    """
    return evaluator.MarketContext(
        points=tuple(
            evaluator.MarketPoint(ts_ms=f.close_ms, oi_change_pct=D(oi_change))
            for f in frames
        )
    )


def _plan(definition: ast.StrategyDefinition) -> compiler.CompiledStrategy:
    """Compiles a definition against the test vocabulary.

    Args:
        definition: Definition that must compile.

    Returns:
        The plan.
    """
    result = compiler.compile_strategy(
        definition, vocabulary=vocabulary.VOCABULARY
    )
    assert result.plan is not None, result.validation.errors
    return result.plan


def _signals(
    definition: ast.StrategyDefinition,
    frames: Sequence[structures.BarFrame],
    market: evaluator.MarketContext | None = None,
) -> list[tuple[int, str]]:
    """Runs a strategy over frames in paper mode.

    Args:
        definition: Strategy definition.
        frames: Frames in bar order.
        market: Market context, or ``None`` when no market data is known.

    Returns:
        ``(bar index, kind)`` of the signals emitted, in order.
    """
    version = fixtures.make_version(definition)
    proc = processor.InstanceProcessor(
        _plan(definition),
        version,
        setup_fsm.InstanceKey(
            "tenant-01",
            version.strategy_version_id,
            "*",
            str(frames[0].instrument_id),
            definition.direction,
        ),
        mode="PAPER",
    )
    signals: list[tuple[int, str]] = []
    for index, frame in enumerate(frames):
        signals.extend(
            (index, s.kind)
            for s in proc.step(
                frame, evaluated_at_ms=frame.close_ms, market=market
            ).signals
        )
    return signals


def _kinds(
    definition: ast.StrategyDefinition,
    frames: Sequence[structures.BarFrame],
    market: evaluator.MarketContext | None = None,
) -> list[str]:
    """Runs a strategy over frames and keeps the signal kinds.

    Args:
        definition: Strategy definition.
        frames: Frames in bar order.
        market: Market context.

    Returns:
        The kinds of the signals emitted, in order.
    """
    return [kind for _, kind in _signals(definition, frames, market)]


def _after_first_bar(
    definition: ast.StrategyDefinition,
    frames: Sequence[structures.BarFrame],
    market: evaluator.MarketContext | None = None,
) -> list[tuple[int, str]]:
    """Runs a template and drops the signals of bar 0.

    Bar 0 has no previous bar, so a CROSSES or lookback condition there is
    UNKNOWN and the templates report ENTRY_BLOCKED once.

    Args:
        definition: Strategy definition.
        frames: Frames in bar order.
        market: Market context.

    Returns:
        ``(bar index, kind)`` of the signals of bars 1 and later.
    """
    return [
        (index, kind)
        for index, kind in _signals(definition, frames, market)
        if index > 0
    ]


def _template(strategy_id: str) -> ast.StrategyDefinition:
    """Returns a template definition of the contract fixture.

    Args:
        strategy_id: Template strategy id.

    Returns:
        The definition.
    """
    return fixtures.strategy_definitions()[strategy_id]


def _true_bars(
    definition: ast.StrategyDefinition,
    frames: Sequence[structures.BarFrame],
    market: evaluator.MarketContext | None = None,
) -> list[int]:
    """Lists the bars on which the rule evaluates TRUE.

    Args:
        definition: Strategy definition.
        frames: Frames in bar order.
        market: Market context.

    Returns:
        Indices of the TRUE bars.
    """
    plan = _plan(definition)
    return [
        index
        for index in range(len(frames))
        if evaluator.evaluate(plan, frames[: index + 1], market).result
        is tristate.TriState.TRUE
    ]


# ------------------------------------------------------------- zone touch


_ZONE_REMOVALS: dict[str, dict[str, Any]] = {
    "trend reversed": {"trend": "BEARISH"},
    "price outside the zone": {"in_zone": False},
    "no zone touch": {"touch": False},
    "no first signal": {"first_signal": False},
    "no final signal": {"final_signal": False},
    "no target level": {"target": False},
    "anchor invalidated": {"aoi_state": structures.AoiState.INVALIDATED},
    "stale entry bar": {"entry_feed": quality.FeedState.STALE},
}


def test_zone_rule_fires_once_on_the_entry_bar() -> None:
    """Every condition holds on bar 30 only: one entry."""
    frames = scenarios.zone_long_frames()
    definition = scenarios.zone_long_definition()
    assert _true_bars(definition, frames) == [30]
    assert _signals(definition, frames) == [(30, "ENTRY_CONFIRMED")]


@pytest.mark.parametrize(
    "change", list(_ZONE_REMOVALS), ids=list(_ZONE_REMOVALS)
)
def test_zone_rule_each_condition_is_necessary(change: str) -> None:
    """Removing any single condition prevents ENTRY_CONFIRMED."""
    opts = scenarios.WorldOptions(**_ZONE_REMOVALS[change])
    assert "ENTRY_CONFIRMED" not in _kinds(
        scenarios.zone_long_definition(), scenarios.zone_long_frames(opts)
    )


def test_zone_rule_binds_one_group_and_targets_the_level() -> None:
    """The entry binds the 1h group and targets the level above."""
    frames = scenarios.zone_long_frames()
    evaluation = evaluator.evaluate(
        _plan(scenarios.zone_long_definition()), frames[:31]
    )
    assert evaluation.group_ids == ("grp-1h-1",)
    assert evaluation.aoi_ids == ("zone-5m-long",)
    assert evaluation.risk.stop == D(92)
    assert evaluation.risk.target == D(110)
    state = evaluation.reason_tree.children[0]
    assert state.evidence == {"4h": {"trend": "BULLISH"}}


def test_zone_rule_works_on_an_exchange_calendar() -> None:
    """The same world on a session instrument fires once."""
    frames = scenarios.zone_long_frames(
        scenarios.WorldOptions(bars=40, entry_index=30),
        instrument_id="mock:stock:ACME",
    )
    definition = scenarios.zone_long_definition(
        universe={"qualification": "ANY", "venues": ["mock"]}
    )
    assert _true_bars(definition, frames) == [30]


# ---------------------------------------------------------------- templates


def test_template_fixtures_compile_and_evaluate() -> None:
    """valid[0..2] of the contract fixture compile and evaluate."""
    valid = fixtures.load_fixture("strategy")["valid"]
    assert isinstance(valid, list)
    ids = [ast.parse_definition(item).strategy_id for item in valid[:3]]
    assert ids == [
        fixtures.EMA_TREND_LONG,
        fixtures.RSI_REVERSION_SHORT,
        fixtures.PIVOT_BOUNCE_LONG,
    ]
    worlds = {
        fixtures.EMA_TREND_LONG: scenarios.ema_trend_frames(),
        fixtures.RSI_REVERSION_SHORT: scenarios.rsi_reversion_frames(),
        fixtures.PIVOT_BOUNCE_LONG: scenarios.pivot_bounce_frames(),
    }
    for strategy_id, frames in worlds.items():
        evaluation = evaluator.evaluate(
            _plan(_template(strategy_id)), frames, _market(frames, "0")
        )
        assert evaluation.reason_tree.children


def test_ema_trend_fires_once() -> None:
    """The EMA trend template fires on its entry bar only."""
    definition = _template(fixtures.EMA_TREND_LONG)
    frames = scenarios.ema_trend_frames()
    assert _true_bars(definition, frames) == [30]
    assert _signals(definition, frames) == [(30, "ENTRY_CONFIRMED")]


@pytest.mark.parametrize(
    "options",
    [
        scenarios.EmaTrendOptions(cross=False),
        scenarios.EmaTrendOptions(rsi="50"),
        scenarios.EmaTrendOptions(delta_pct="-0.5"),
    ],
    ids=["no cross", "rsi not above 50", "negative delta"],
)
def test_ema_trend_each_condition_is_necessary(
    options: scenarios.EmaTrendOptions,
) -> None:
    """Removing any single condition prevents ENTRY_CONFIRMED."""
    assert "ENTRY_CONFIRMED" not in _kinds(
        _template(fixtures.EMA_TREND_LONG), scenarios.ema_trend_frames(options)
    )


def test_rsi_reversion_fires_once_with_market_data() -> None:
    """The RSI reversion template fires when OI is not falling over 5%."""
    definition = _template(fixtures.RSI_REVERSION_SHORT)
    frames = scenarios.rsi_reversion_frames()
    market = _market(frames, "-1")
    assert _true_bars(definition, frames, market) == [30]
    assert _after_first_bar(definition, frames, market) == [
        (30, "ENTRY_CONFIRMED")
    ]
    assert _signals(definition, frames, market)[0] == (0, "ENTRY_BLOCKED")


@pytest.mark.parametrize(
    "options",
    [
        scenarios.RsiReversionOptions(cross=False),
        scenarios.RsiReversionOptions(band_break=False),
    ],
    ids=["no cross", "no band break"],
)
def test_rsi_reversion_each_condition_is_necessary(
    options: scenarios.RsiReversionOptions,
) -> None:
    """Removing any single condition prevents an entry."""
    frames = scenarios.rsi_reversion_frames(options)
    assert "ENTRY_CONFIRMED" not in _kinds(
        _template(fixtures.RSI_REVERSION_SHORT), frames, _market(frames, "-1")
    )


def test_rsi_reversion_falling_open_interest_vetoes() -> None:
    """OI falling more than 5% makes the NOT condition false."""
    frames = scenarios.rsi_reversion_frames()
    assert "ENTRY_CONFIRMED" not in _kinds(
        _template(fixtures.RSI_REVERSION_SHORT), frames, _market(frames, "-6")
    )


def test_rsi_reversion_without_market_data_is_blocked() -> None:
    """Unknown OI data blocks rather than silently passing."""
    assert _after_first_bar(
        _template(fixtures.RSI_REVERSION_SHORT),
        scenarios.rsi_reversion_frames(),
    ) == [(30, "ENTRY_BLOCKED")]


def test_pivot_bounce_fires_once_inside_the_window() -> None:
    """The pivot bounce template fires on its 11:00 entry bar only."""
    definition = _template(fixtures.PIVOT_BOUNCE_LONG)
    frames = scenarios.pivot_bounce_frames()
    assert _true_bars(definition, frames) == [18]
    assert _after_first_bar(definition, frames) == [(18, "ENTRY_CONFIRMED")]
    evaluation = evaluator.evaluate(_plan(definition), frames[:19])
    assert evaluation.aoi_ids == ("pivot-s1",)
    assert evaluation.risk.target == D(100)


@pytest.mark.parametrize(
    "options",
    [
        scenarios.PivotBounceOptions(touch=False),
        scenarios.PivotBounceOptions(method="candle_direction"),
        scenarios.PivotBounceOptions(target=False),
        scenarios.PivotBounceOptions(aoi_state=structures.AoiState.EXPIRED),
        scenarios.PivotBounceOptions(entry_index=26),
    ],
    ids=[
        "no touch",
        "estimated delta",
        "no target",
        "level expired",
        "outside the window",
    ],
)
def test_pivot_bounce_each_condition_is_necessary(
    options: scenarios.PivotBounceOptions,
) -> None:
    """Removing any single condition prevents ENTRY_CONFIRMED."""
    assert "ENTRY_CONFIRMED" not in _kinds(
        _template(fixtures.PIVOT_BOUNCE_LONG),
        scenarios.pivot_bounce_frames(options),
    )
