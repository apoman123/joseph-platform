# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Long A and Short B end-to-end: each condition is necessary."""

from __future__ import annotations

import decimal
from collections.abc import Sequence
from typing import Any

import pytest

from joseph.domain import quality
from joseph.domain import structures
from joseph.strategy.app import processor
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import scenarios

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


def _kinds(
    strategy_id: str,
    frames: Sequence[structures.BarFrame],
    market: evaluator.MarketContext | None = None,
) -> list[str]:
    """Runs a fixture strategy over frames in paper mode.

    Args:
        strategy_id: Fixture strategy id.
        frames: Frames in bar order.
        market: Market context, or ``None`` when no market data is known.

    Returns:
        The kinds of the signals emitted, in order.
    """
    definition = fixtures.strategy_definitions()[strategy_id]
    plan = compiler.compile_strategy(definition).plan
    assert plan is not None
    version = fixtures.make_version(definition)
    proc = processor.InstanceProcessor(
        plan,
        version,
        setup_fsm.InstanceKey(
            "tenant-01",
            version.strategy_version_id,
            "*",
            "binance:perp:BTCUSDT",
            definition.direction,
        ),
        mode="PAPER",
    )
    kinds: list[str] = []
    for frame in frames:
        kinds.extend(
            s.kind
            for s in proc.step(
                frame, evaluated_at_ms=frame.close_ms, market=market
            ).signals
        )
    return kinds


_REMOVALS: dict[str, dict[str, Any]] = {
    "bias reversed": {"bias": "BEARISH"},
    "price outside zones": {"in_zone": False},
    "no AOI touch": {"touch": False},
    "no sweep": {"sweep": False},
    "no failure": {"failure": False},
    "AOI invalidated": {"aoi_state": structures.AoiState.INVALIDATED},
    "stale entry bar": {"entry_feed": quality.FeedState.STALE},
}


def test_long_a_positive_case() -> None:
    """Every Long A condition holds on bar 30: one entry."""
    assert _kinds(fixtures.LONG_A, scenarios.long_a_frames()) == [
        "ENTRY_CONFIRMED"
    ]


@pytest.mark.parametrize("change", list(_REMOVALS), ids=list(_REMOVALS))
def test_long_a_each_condition_is_necessary(change: str) -> None:
    """Removing any single condition prevents ENTRY_CONFIRMED."""
    opts = scenarios.WorldOptions(**_REMOVALS[change])
    assert "ENTRY_CONFIRMED" not in _kinds(
        fixtures.LONG_A, scenarios.long_a_frames(opts)
    )


def test_short_b_positive_case() -> None:
    """Short B fires once when OI is not falling more than 5%."""
    frames = scenarios.short_b_frames()
    assert _kinds(fixtures.SHORT_B, frames, _market(frames, "-1")) == [
        "ENTRY_CONFIRMED"
    ]


@pytest.mark.parametrize("change", list(_REMOVALS), ids=list(_REMOVALS))
def test_short_b_each_condition_is_necessary(change: str) -> None:
    """Removing any single Short B condition prevents an entry."""
    overrides = dict(_REMOVALS[change])
    if change == "bias reversed":
        overrides["bias"] = "BULLISH"
    opts = scenarios.WorldOptions(**overrides)
    frames = scenarios.short_b_frames(opts)
    assert "ENTRY_CONFIRMED" not in _kinds(
        fixtures.SHORT_B, frames, _market(frames, "-1")
    )


def test_short_b_falling_open_interest_vetoes() -> None:
    """OI falling more than 5% makes the NOT condition false."""
    frames = scenarios.short_b_frames()
    assert "ENTRY_CONFIRMED" not in _kinds(
        fixtures.SHORT_B, frames, _market(frames, "-6")
    )


def test_short_b_without_market_data_is_blocked() -> None:
    """Unknown OI data blocks rather than silently passing."""
    assert _kinds(fixtures.SHORT_B, scenarios.short_b_frames()) == [
        "ENTRY_BLOCKED"
    ]


def test_short_b_k_of_n_bias_uses_two_of_three() -> None:
    """Short B's two-of-three bias holds with 1d and 4h bearish, 1h bullish."""
    frames = scenarios.short_b_frames()
    evaluation = evaluator.evaluate(
        compiler.compile_strategy(
            fixtures.strategy_definitions()[fixtures.SHORT_B]
        ).plan,  # type: ignore[arg-type]  # Short B compiles, so plan is set
        frames[:31],
        _market(frames, "-1"),
    )
    bias = evaluation.reason_tree.children[0]
    assert bias.result.value == "TRUE"
    assert bias.evidence == {"1d": "BEARISH", "4h": "BEARISH", "1h": "BULLISH"}
