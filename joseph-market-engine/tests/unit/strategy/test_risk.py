"""Unit tests for the projected stop/target/reward-to-risk model."""

from __future__ import annotations

import copy
import decimal
from collections.abc import Sequence

import pytest

from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import risk
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing

D = decimal.Decimal
TF = timeframes.Timeframe


def _definition(
    direction: str = "LONG", **risk_fields: str
) -> ast.StrategyDefinition:
    """Builds a Long A variant with a custom risk model.

    Args:
        direction: ``LONG`` or ``SHORT``.
        **risk_fields: Risk model fields.

    Returns:
        Definition.
    """
    raw = copy.deepcopy(
        fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()
    )
    raw["direction"] = direction
    raw["risk"] = {
        "min_rr": "2",
        "stop_model": "AOI_INVALIDATION",
        "target_model": "NEAREST_OPPOSING_LIQUIDITY",
        **risk_fields,
    }
    return ast.parse_definition(raw)


def _frames(
    count: int = 20,
    *,
    close: str = "100",
    pools: Sequence[tuple[str, str]] = (("BSL", "110"), ("SSL", "90")),
) -> list[structures.BarFrame]:
    """Builds frames with constant 2-point ranges and liquidity pools.

    Args:
        count: Number of frames.
        close: Close of the last bar.
        pools: ``(kind, level)`` liquidity lines.

    Returns:
        Frames.
    """
    factory = frame_testing.FrameFactory()
    result = []
    for i in range(count):
        spec = frame_testing.bar("100", "101", "99", "100")
        if i == count - 1:
            spec = frame_testing.bar(
                close, str(D(close) + 1), str(D(close) - 1), close
            )
        aois = [
            factory.aoi(
                kind,
                "neutral",
                level,
                level,
                confirmed_index=0,
                aoi_id=f"{kind}-{level}",
            )
            for kind, level in pools
        ]
        snap = factory.snapshot("liquidity_sweeps", TF.M5, i, aois=aois)
        result.append(factory.frame(i, spec, [snap]))
    return result


def _touch(
    lower: str, upper: str, side: str = "bullish", close_index: int = 19
) -> risk.TouchedAoi:
    """Builds a touched AOI.

    Args:
        lower: Lower bound.
        upper: Upper bound.
        side: AOI side.
        close_index: Touching bar index.

    Returns:
        Touch.
    """
    factory = frame_testing.FrameFactory()
    aoi = factory.aoi(
        "FVG", side, lower, upper, confirmed_index=1, aoi_id=f"fvg-{lower}"
    )
    return risk.TouchedAoi(aoi=aoi, bar_close_ms=factory.close(close_index))


def test_wilder_atr_matches_hand_computation() -> None:
    """Constant 2-point ranges yield ATR 2; a spike is smoothed by 1/14."""
    frames = _frames(16)
    candles = [f.candle for f in frames[:15]]
    assert risk.wilder_atr(candles) == D(2)
    factory = frame_testing.FrameFactory()
    spike = factory.candle(15, frame_testing.bar("100", "116", "100", "100"))
    assert risk.wilder_atr([*candles, spike]) == (D(2) * 13 + D(16)) / 14
    assert risk.wilder_atr(candles[:5]) is None


def test_long_invalidation_stop_and_liquidity_target() -> None:
    """Long: stop under the AOI, target at the nearest BSL above."""
    assessment = risk.assess(_definition(), _frames(), [_touch("95", "97")])
    assert assessment.entry == D(100)
    assert assessment.stop == D(95)
    assert assessment.target == D(110)
    assert assessment.gross_rr == D(2)
    assert assessment.rr == D(2)
    assert assessment.anchor_aoi_id == "fvg-95"


def test_short_invalidation_stop_and_liquidity_target() -> None:
    """Short: stop above the AOI, target at the nearest SSL below."""
    assessment = risk.assess(
        _definition("SHORT"), _frames(), [_touch("103", "104", "bearish")]
    )
    assert assessment.stop == D(104)
    assert assessment.target == D(90)
    assert assessment.rr == D("2.5")


def test_fees_reduce_net_reward_to_risk() -> None:
    """Round-trip fees lower the net ratio below the gross ratio."""
    assessment = risk.assess(
        _definition(fee_bps_estimate="10"), _frames(), [_touch("95", "97")]
    )
    assert assessment.gross_rr == D(2)
    assert assessment.rr is not None
    assert assessment.rr < D(2)
    assert assessment.rr == ((D(10) - D("0.2")) / (D(5) + D("0.2"))).quantize(
        D("0.0001"), rounding=decimal.ROUND_FLOOR
    )


def test_atr_buffer_widens_the_stop() -> None:
    """stop_buffer_atr moves the stop by ATR multiples."""
    assessment = risk.assess(
        _definition(stop_buffer_atr="0.5"), _frames(), [_touch("95", "97")]
    )
    assert assessment.atr == D(2)
    assert assessment.stop == D(94)


def test_swing_and_atr_stops_and_fixed_r() -> None:
    """Swing and ATR stop models with fixed-R targets."""
    swing = risk.assess(
        _definition(stop_model="SWING", target_model="FIXED_R", fixed_r="3"),
        _frames(),
        [],
    )
    assert swing.stop == D(99)
    assert swing.target == D(103)
    assert swing.rr == D(3)
    atr = risk.assess(
        _definition(
            stop_model="ATR",
            stop_buffer_atr="1.5",
            target_model="FIXED_R",
            fixed_r="2",
        ),
        _frames(),
        [],
    )
    assert atr.stop == D(97)
    assert atr.target == D(106)


@pytest.mark.parametrize(
    ("touched", "pools", "reason"),
    [
        ([], (("BSL", "110"),), "no touched AOI"),
        ([_touch("95", "97")], (("SSL", "90"),), "no opposing liquidity"),
        ([_touch("101", "103")], (("BSL", "110"),), "stop is not beyond"),
    ],
)
def test_not_computable_cases(
    touched: list[risk.TouchedAoi],
    pools: tuple[tuple[str, str], ...],
    reason: str,
) -> None:
    """Missing inputs leave rr empty with an explanation."""
    assessment = risk.assess(_definition(), _frames(pools=pools), touched)
    assert assessment.rr is None
    assert not assessment.computable
    assert any(reason in text for text in assessment.reasons)


def test_atr_needed_but_missing() -> None:
    """A buffer without enough bars for ATR is not computable."""
    assessment = risk.assess(
        _definition(stop_buffer_atr="1"),
        _frames(5),
        [_touch("95", "97", close_index=4)],
    )
    assert assessment.rr is None
    assert any("ATR" in r for r in assessment.reasons)


def test_anchor_prefers_recent_then_quality() -> None:
    """The latest touch anchors; ties go to higher quality."""
    old = _touch("95", "97", close_index=10)
    new = _touch("96", "98", close_index=12)
    assert risk.choose_anchor([old, new]) is new
    assert risk.choose_anchor([]) is None
