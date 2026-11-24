# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the projected stop/target/reward-to-risk model.

AOI is an area of interest, here a ``ZONE_UP`` test zone; ``LEVEL_HIGH``
and ``LEVEL_LOW`` are test levels used as ``NEAREST_AOI`` targets; ATR is
Wilder's average true range.
"""

from __future__ import annotations

import copy
import dataclasses
import decimal
from collections.abc import Sequence

import pytest

from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import risk
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary

D = decimal.Decimal
TF = timeframes.Timeframe
V = vocabulary
_TARGETS = (V.TEST_LEVELS,)


def _definition(
    direction: str = "LONG", **risk_fields: str | list[str]
) -> ast.StrategyDefinition:
    """Builds a zone-touch variant with a custom risk model.

    Args:
        direction: ``LONG`` or ``SHORT``.
        **risk_fields: Risk model fields that override the default AOI
            invalidation stop, nearest level target (``LEVEL_HIGH`` for a
            long, ``LEVEL_LOW`` for a short) and 2R minimum.

    Returns:
        The parsed definition.
    """
    raw = copy.deepcopy(scenarios.ZONE_LONG)
    raw["direction"] = direction
    target = V.LEVEL_HIGH if direction == "LONG" else V.LEVEL_LOW
    raw["risk"] = {
        "min_rr": "2",
        "stop_model": "AOI_INVALIDATION",
        "target_model": "NEAREST_AOI",
        "target_kinds": [target],
        **risk_fields,
    }
    return ast.parse_definition(raw)


def _frames(
    count: int = 20,
    *,
    close: str = "100",
    pools: Sequence[tuple[str, str]] = (
        (V.LEVEL_HIGH, "110"),
        (V.LEVEL_LOW, "90"),
    ),
    tf: timeframes.Timeframe = TF.M5,
) -> list[structures.BarFrame]:
    """Builds 5m frames with constant 2-point ranges and target levels.

    Args:
        count: Number of frames.
        close: Open and close of the last bar, whose range is one point
            either side; earlier bars span 99-101 around 100.
        pools: ``(kind, level)`` lines in every frame's ``test_levels``
            snapshot.
        tf: Timeframe of the ``test_levels`` snapshot.

    Returns:
        The frames in bar order.
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
                tf=tf,
                aoi_id=f"{kind}-{level}",
            )
            for kind, level in pools
        ]
        snap = factory.snapshot(
            V.TEST_LEVELS, tf, i, aois=aois, as_of_ms=factory.close(i)
        )
        result.append(factory.frame(i, spec, [snap]))
    return result


def _touch(
    lower: str, upper: str, side: str = "bullish", close_index: int = 19
) -> risk.TouchedAoi:
    """Builds a touched zone.

    Args:
        lower: Lower bound of the zone.
        upper: Upper bound of the zone.
        side: ``bullish`` or ``bearish``.
        close_index: Index of the bar whose close touched the zone.

    Returns:
        The touch; the zone's id is ``zone-<lower>``.
    """
    factory = frame_testing.FrameFactory()
    aoi = factory.aoi(
        V.ZONE_UP, side, lower, upper, confirmed_index=1, aoi_id=f"zone-{lower}"
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


def test_long_invalidation_stop_and_nearest_aoi_target() -> None:
    """Long: stop under the AOI, target at the nearest level above."""
    assessment = risk.assess(
        _definition(), _frames(), [_touch("95", "97")], _TARGETS
    )
    assert assessment.entry == D(100)
    assert assessment.stop == D(95)
    assert assessment.target == D(110)
    assert assessment.gross_rr == D(2)
    assert assessment.rr == D(2)
    assert assessment.anchor_aoi_id == "zone-95"


def test_short_invalidation_stop_and_nearest_aoi_target() -> None:
    """Short: stop above the AOI, target at the nearest level below."""
    assessment = risk.assess(
        _definition("SHORT"),
        _frames(),
        [_touch("103", "104", "bearish")],
        _TARGETS,
    )
    assert assessment.stop == D(104)
    assert assessment.target == D(90)
    assert assessment.rr == D("2.5")


def test_fees_reduce_net_reward_to_risk() -> None:
    """Round-trip fees lower the net ratio below the gross ratio."""
    assessment = risk.assess(
        _definition(fee_bps_estimate="10"),
        _frames(),
        [_touch("95", "97")],
        _TARGETS,
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
        _definition(stop_buffer_atr="0.5"),
        _frames(),
        [_touch("95", "97")],
        _TARGETS,
    )
    assert assessment.atr == D(2)
    assert assessment.stop == D(94)


def test_swing_and_atr_stops_and_fixed_r() -> None:
    """Swing and ATR stops put fixed-R targets at R multiples of the risk."""
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
        ([], ((V.LEVEL_HIGH, "110"),), "no touched AOI"),
        (
            [_touch("95", "97")],
            ((V.LEVEL_LOW, "90"), (V.LEVEL_HIGH, "99")),
            "no LEVEL_HIGH zone beyond the entry",
        ),
        ([_touch("101", "103")], ((V.LEVEL_HIGH, "110"),), "stop is not"),
    ],
)
def test_not_computable_cases(
    touched: list[risk.TouchedAoi],
    pools: tuple[tuple[str, str], ...],
    reason: str,
) -> None:
    """Missing inputs leave the ratio empty, with the reason recorded."""
    assessment = risk.assess(
        _definition(), _frames(pools=pools), touched, _TARGETS
    )
    assert assessment.rr is None
    assert not assessment.computable
    assert any(reason in text for text in assessment.reasons)


def test_atr_needed_but_missing() -> None:
    """A buffer without enough bars for ATR is not computable."""
    assessment = risk.assess(
        _definition(stop_buffer_atr="1"),
        _frames(5),
        [_touch("95", "97", close_index=4)],
        _TARGETS,
    )
    assert assessment.rr is None
    assert any("ATR" in r for r in assessment.reasons)


def test_anchor_prefers_recent_then_quality() -> None:
    """The latest touch anchors; ties go to higher quality."""
    old = _touch("95", "97", close_index=10)
    new = _touch("96", "98", close_index=12)
    assert risk.choose_anchor([old, new]) is new
    assert risk.choose_anchor([]) is None


def test_nearest_aoi_target_needs_the_target_plugins() -> None:
    """Without a snapshot of the target plugins the target is missing."""
    assessment = risk.assess(_definition(), _frames(), [_touch("95", "97")])
    assert assessment.target is None
    assert "target zone snapshot unavailable" in assessment.reasons


def test_nearest_aoi_target_picks_the_nearest_live_kind() -> None:
    """Only live AOIs of the target kinds beyond the entry count."""
    pools = (
        (V.LEVEL_HIGH, "120"),
        (V.LEVEL_HIGH, "108"),
        (V.LEVEL_LOW, "104"),
        (V.LEVEL_HIGH, "99"),
    )
    assessment = risk.assess(
        _definition(), _frames(pools=pools), [_touch("95", "97")], _TARGETS
    )
    assert assessment.target == D(108)
    both = risk.assess(
        _definition(target_kinds=[V.LEVEL_HIGH, V.LEVEL_LOW]),
        _frames(pools=pools),
        [_touch("95", "97")],
        _TARGETS,
    )
    assert both.target == D(104)


def test_nearest_aoi_target_searches_every_timeframe() -> None:
    """Snapshots of the target plugins on any timeframe are searched."""
    assessment = risk.assess(
        _definition(),
        _frames(tf=TF.H1),
        [_touch("95", "97")],
        _TARGETS,
    )
    assert assessment.target == D(110)


def test_ended_target_levels_are_ignored() -> None:
    """An expired level is not a target."""
    frames = _frames()
    last = frames[-1]
    snapshot = last.snapshots[(V.TEST_LEVELS, TF.M5)]
    ended = dataclasses.replace(
        snapshot,
        aois=tuple(
            dataclasses.replace(aoi, state=structures.AoiState.EXPIRED)
            for aoi in snapshot.aois
        ),
    )
    frames[-1] = dataclasses.replace(
        last, snapshots={(V.TEST_LEVELS, TF.M5): ended}
    )
    assessment = risk.assess(
        _definition(), frames, [_touch("95", "97")], _TARGETS
    )
    assert assessment.target is None
