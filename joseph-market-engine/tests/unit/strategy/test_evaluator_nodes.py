# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the tri-state evaluator's semantics, node by node.

The rules read the declared-only test plugins of ``testing.vocabulary``:
``test_state`` (values), ``test_zones`` and ``test_levels`` (AOIs) and
``test_signals`` (events).
"""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Callable
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import tristate
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import vocabulary

T = tristate.TriState
TF = timeframes.Timeframe
V = vocabulary
Make: TypeAlias = Callable[..., ast.StrategyDefinition]
Plan: TypeAlias = Callable[[ast.StrategyDefinition], compiler.CompiledStrategy]
Snaps: TypeAlias = Callable[
    [frame_testing.FrameFactory, int], list[structures.IndicatorSnapshot]
]
Events: TypeAlias = Callable[
    [frame_testing.FrameFactory, int], list[structures.StructureEvent]
]
_RISK = {
    "min_rr": "0",
    "stop_model": "SWING",
    "target_model": "FIXED_R",
    "fixed_r": "2",
}
_FLAT = frame_testing.bar("100", "101", "99", "100")


@pytest.fixture
def plan_for(
    make_definition: Make, compile_plan: Plan
) -> Callable[..., compiler.CompiledStrategy]:
    """Compiles a single-node rule with a neutral risk model.

    Args:
        make_definition: Definition builder fixture.
        compile_plan: Compiler fixture.

    Returns:
        ``plan_for(node, **overrides)``, which compiles a definition whose
        ``when`` is ``node``; ``risk`` defaults to a neutral model.
    """

    def build(
        node: dict[str, object], **overrides: object
    ) -> compiler.CompiledStrategy:
        """Builds the plan.

        Args:
            node: Root rule node.
            **overrides: Definition field overrides.

        Returns:
            The compiled strategy.
        """
        overrides.setdefault("risk", _RISK)
        return compile_plan(make_definition(when=node, **overrides))

    return build


def _series(
    count: int,
    snapshots: Callable[
        [frame_testing.FrameFactory, int],
        Sequence[structures.IndicatorSnapshot],
    ] = lambda f, i: (),
    events: Callable[
        [frame_testing.FrameFactory, int], Sequence[structures.StructureEvent]
    ] = lambda f, i: (),
    specs: dict[int, frame_testing.BarSpec] | None = None,
    feeds: dict[int, quality.FeedState] | None = None,
) -> list[structures.BarFrame]:
    """Builds a frame series of flat, live bars.

    Args:
        count: Number of bars.
        snapshots: Builds the snapshots of bar ``i``; none by default.
        events: Builds the events of bar ``i``; none by default.
        specs: OHLC overrides by bar index.
        feeds: Feed state overrides by bar index.

    Returns:
        Frames 0 to ``count - 1`` of one series.
    """
    factory = frame_testing.FrameFactory()
    return [
        factory.frame(
            i,
            (specs or {}).get(i, _FLAT),
            snapshots(factory, i),
            events(factory, i),
            feed_state=(feeds or {}).get(i, quality.FeedState.LIVE),
        )
        for i in range(count)
    ]


def _result(
    plan: compiler.CompiledStrategy,
    frames: Sequence[structures.BarFrame],
    **kwargs: object,
) -> tristate.TriState:
    """Evaluates and returns the root result.

    Args:
        plan: Compiled strategy.
        frames: Frames, oldest first.
        **kwargs: Extra ``evaluate`` arguments.

    Returns:
        The root node's tri-state result.
    """
    return evaluator.evaluate(plan, frames, **kwargs).result  # type: ignore[arg-type]  # extras are typed object; tests pass evaluate's real types


def _merge(*builders: Snaps) -> Snaps:
    """Combines snapshot builders.

    Args:
        *builders: Builders whose snapshots are concatenated.

    Returns:
        A builder of every builder's snapshots.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds the combined snapshots of one bar.

        Args:
            factory: Frame factory of the series.
            index: Bar index.

        Returns:
            The bar's snapshots.
        """
        snaps: list[structures.IndicatorSnapshot] = []
        for builder in builders:
            snaps.extend(builder(factory, index))
        return snaps

    return build


# -------------------------------------------------------------------- STATE


def _state_snaps(
    values: dict[TF, dict[str, structures.ValueType] | None],
    *,
    warm: bool = True,
) -> Snaps:
    """Returns a snapshot builder publishing ``test_state`` values.

    Args:
        values: Published values per timeframe (``None`` omits the
            snapshot).
        warm: Whether the snapshots report warm-up complete.

    Returns:
        A builder of each bar's ``test_state`` snapshots.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds snapshots for one bar.

        Args:
            factory: Frame factory of the series.
            index: Bar index.

        Returns:
            The bar's snapshots.
        """
        return [
            factory.snapshot(
                V.TEST_STATE,
                tf,
                index,
                warm=warm,
                as_of_ms=tf.floor(factory.close(index)),
                values=published,
            )
            for tf, published in values.items()
            if published is not None
        ]

    return build


def _state(
    timeframes_: list[str], where: list[dict[str, object]], **extra: object
) -> dict[str, object]:
    """Builds a STATE node over ``test_state``.

    Args:
        timeframes_: Timeframes checked.
        where: Clauses.
        **extra: Further node fields (``match``, ``k``).

    Returns:
        The node in contract form.
    """
    return {
        "op": "STATE",
        "plugin_id": V.TEST_STATE,
        "timeframes": timeframes_,
        "where": where,
        **extra,
    }


_UP: list[dict[str, object]] = [{"field": "trend", "any_of": ["BULLISH"]}]


def test_state_all_requires_every_timeframe(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """ALL is FALSE when any timeframe disagrees."""
    plan = plan_for(_state(["4h", "1h"], _UP))
    agree = _series(
        3,
        _state_snaps(
            {TF.H4: {"trend": "BULLISH"}, TF.H1: {"trend": "BULLISH"}}
        ),
    )
    mixed = _series(
        3,
        _state_snaps(
            {TF.H4: {"trend": "BULLISH"}, TF.H1: {"trend": "BEARISH"}}
        ),
    )
    assert _result(plan, agree) is T.TRUE
    assert _result(plan, mixed) is T.FALSE


def test_state_missing_snapshot_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A missing or warming-up snapshot yields UNKNOWN, not FALSE."""
    plan = plan_for(_state(["4h", "1h"], _UP))
    missing = _state_snaps({TF.H4: {"trend": "BULLISH"}, TF.H1: None})
    cold = _state_snaps(
        {TF.H4: {"trend": "BULLISH"}, TF.H1: {"trend": "BULLISH"}}, warm=False
    )
    assert _result(plan, _series(3, missing)) is T.UNKNOWN
    assert _result(plan, _series(3, cold)) is T.UNKNOWN


def test_state_k_of_n(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """K_OF_N counts matching timeframes."""
    plan = plan_for(
        _state(
            ["1d", "4h", "1h"],
            [{"field": "trend", "any_of": ["BEARISH"]}],
            match="K_OF_N",
            k=2,
        )
    )
    bear: dict[str, structures.ValueType] = {"trend": "BEARISH"}
    bull: dict[str, structures.ValueType] = {"trend": "BULLISH"}
    two = _state_snaps({TF.D1: bear, TF.H4: bear, TF.H1: bull})
    one = _state_snaps({TF.D1: bear, TF.H4: bull, TF.H1: bull})
    undecided = _state_snaps({TF.D1: bear, TF.H4: None, TF.H1: bull})
    assert _result(plan, _series(2, two)) is T.TRUE
    assert _result(plan, _series(2, one)) is T.FALSE
    assert _result(plan, _series(2, undecided)) is T.UNKNOWN


@pytest.mark.parametrize(
    ("published", "where", "expected"),
    [
        ({"trend": "BULLISH", "armed": True}, _UP, T.TRUE),
        ({"trend": None}, _UP, T.FALSE),
        ({"armed": True}, _UP, T.FALSE),
        ({"armed": True}, [{"field": "armed", "any_of": [True]}], T.TRUE),
        ({"armed": True}, [{"field": "armed", "any_of": ["true"]}], T.FALSE),
        ({"armed": "true"}, [{"field": "armed", "any_of": [True]}], T.FALSE),
        (
            {"trend": "NEUTRAL"},
            [{"field": "trend", "any_of": ["BULLISH", "NEUTRAL"]}],
            T.TRUE,
        ),
        (
            {"trend": "BULLISH", "armed": False},
            [*_UP, {"field": "armed", "any_of": [True]}],
            T.FALSE,
        ),
    ],
    ids=[
        "match",
        "null",
        "missing",
        "bool",
        "string-vs-bool",
        "bool-vs-string",
        "any-of",
        "every-clause",
    ],
)
def test_state_clause_matching(
    plan_for: Callable[..., compiler.CompiledStrategy],
    published: dict[str, structures.ValueType],
    where: list[dict[str, object]],
    expected: tristate.TriState,
) -> None:
    """Clauses compare type-strictly; null or missing values never match."""
    plan = plan_for(_state(["4h"], where))
    frames = _series(2, _state_snaps({TF.H4: published}))
    assert _result(plan, frames) is expected


def _future_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds a 4h state snapshot stamped one 4h bar after the frame close.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's look-ahead snapshot.
    """
    return [
        factory.snapshot(
            V.TEST_STATE,
            TF.H4,
            index,
            as_of_ms=factory.close(index) + TF.H4.duration_ms,
            values={"trend": "BULLISH"},
        )
    ]


def test_snapshot_from_the_future_is_rejected(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A snapshot stamped after the frame close is never read."""
    plan = plan_for(_state(["4h"], _UP))
    evaluation = evaluator.evaluate(plan, _series(2, _future_snaps))
    assert evaluation.result is T.UNKNOWN
    assert "LOOKAHEAD_REJECTED" in evaluation.data_flags


# ------------------------------------------------------------------ IN_ZONE


_DEFAULT_ZONES: tuple[tuple[str, str, str, str, str | None], ...] = (
    (V.ZONE_UP, "bullish", "80", "100", "g1"),
    (V.ZONE_DOWN, "bearish", "88", "95", "g1"),
    (V.ZONE_DOWN, "bearish", "100", "120", None),
)
"""Default 1h zones: ``(kind, side, lower, upper, group_id)``."""


def _zone_snaps(
    zones: Sequence[tuple[str, str, str, str, str | None]] | None = None,
    *,
    per_bar_group: dict[int, str] | None = None,
    state: structures.AoiState = structures.AoiState.ACTIVE,
) -> Snaps:
    """Returns a builder for 1h ``test_zones`` snapshots.

    Args:
        zones: ``(kind, side, lower, upper, group_id)`` per AOI; by default
            a bullish ``ZONE_UP`` 80-100 and a bearish ``ZONE_DOWN`` 88-95
            in group ``g1`` and a bearish ``ZONE_DOWN`` 100-120 without a
            group.
        per_bar_group: Group id replacing every non-null group by bar index.
        state: State of every AOI.

    Returns:
        A builder of each bar's ``test_zones`` snapshot.
    """
    spec = _DEFAULT_ZONES if zones is None else zones

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds the zone snapshot of one bar.

        Args:
            factory: Frame factory of the series.
            index: Bar index.

        Returns:
            The bar's snapshots.
        """
        aois = []
        for number, (kind, side, lower, upper, declared) in enumerate(spec):
            group = declared
            if declared is not None:
                group = (per_bar_group or {}).get(index, declared)
            aois.append(
                factory.aoi(
                    kind,
                    side,
                    lower,
                    upper,
                    confirmed_index=0,
                    tf=TF.H1,
                    group_id=group,
                    aoi_id=f"z{number}-{group}",
                    state=state,
                )
            )
        return [
            factory.snapshot(
                V.TEST_ZONES,
                TF.H1,
                index,
                as_of_ms=TF.H1.floor(factory.close(index)),
                aois=aois,
            )
        ]

    return build


def _in_zone(kinds: list[str], **extra: object) -> dict[str, object]:
    """Builds a 1h IN_ZONE node.

    Args:
        kinds: AOI kinds.
        **extra: Further node fields.

    Returns:
        The node in contract form.
    """
    return {"op": "IN_ZONE", "kinds": kinds, "tf": "1h", **extra}


@pytest.mark.parametrize(
    ("node", "spec", "expected"),
    [
        (
            _in_zone([V.ZONE_UP]),
            frame_testing.bar("95", "96", "90", "94"),
            T.TRUE,
        ),
        (
            _in_zone([V.ZONE_UP]),
            frame_testing.bar("101", "104", "100", "102"),
            T.FALSE,
        ),
        (
            _in_zone([V.ZONE_DOWN]),
            frame_testing.bar("101", "104", "100", "102"),
            T.TRUE,
        ),
        (
            _in_zone([V.ZONE_UP, V.ZONE_DOWN], side="bearish"),
            frame_testing.bar("97", "98", "96", "97"),
            T.FALSE,
        ),
        (
            _in_zone([V.ZONE_DOWN], price_source="low"),
            frame_testing.bar("97", "98", "94", "97"),
            T.TRUE,
        ),
        (
            _in_zone([V.ZONE_DOWN]),
            frame_testing.bar("97", "98", "94", "97"),
            T.FALSE,
        ),
        (
            _in_zone([V.ZONE_DOWN], price_source="hl2"),
            frame_testing.bar("97", "98", "88", "97"),
            T.TRUE,
        ),
        (
            _in_zone([V.ZONE_DOWN], price_source="high"),
            frame_testing.bar("80", "89", "78", "79"),
            T.TRUE,
        ),
    ],
    ids=[
        "inside",
        "outside",
        "other-kind",
        "side",
        "low",
        "close",
        "hl2",
        "high",
    ],
)
def test_in_zone(
    plan_for: Callable[..., compiler.CompiledStrategy],
    node: dict[str, object],
    spec: frame_testing.BarSpec,
    expected: tristate.TriState,
) -> None:
    """Kinds, side and price source select the live AOIs tested."""
    frames = _series(3, _zone_snaps(), specs={2: spec})
    assert _result(plan_for(node), frames) is expected


def test_in_zone_ignores_ended_zones(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """An invalidated or expired AOI is not a zone any more."""
    plan = plan_for(_in_zone([V.ZONE_UP]))
    spec = {1: frame_testing.bar("95", "96", "90", "94")}
    for state in (structures.AoiState.INVALIDATED, structures.AoiState.EXPIRED):
        frames = _series(2, _zone_snaps(state=state), specs=spec)
        assert _result(plan, frames) is T.FALSE
    tested = _series(
        2, _zone_snaps(state=structures.AoiState.TESTED), specs=spec
    )
    assert _result(plan, tested) is T.TRUE


def test_in_zone_missing_snapshot_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """No snapshot of the publishing plugin on ``tf`` means UNKNOWN."""
    plan = plan_for(_in_zone([V.ZONE_UP]))
    evaluation = evaluator.evaluate(plan, _series(2))
    assert evaluation.result is T.UNKNOWN
    assert "unavailable" in evaluation.reason_tree.detail
    other = plan_for(_in_zone([V.ZONE_UP], tf="4h"))
    assert _result(other, _series(2, _zone_snaps())) is T.UNKNOWN


def _grouped(second: dict[str, object]) -> dict[str, object]:
    """Builds an ALL of a grouped ``ZONE_UP`` member and another member.

    Args:
        second: The other member (or a node wrapping it).

    Returns:
        The root node.
    """
    return {
        "op": "ALL",
        "children": [_in_zone([V.ZONE_UP], id="up", group="G"), second],
    }


def test_group_binds_the_same_group(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Members evaluated on earlier bars use the current bar's binding."""
    plan = plan_for(
        _grouped(
            {
                "op": "WITHIN_BARS",
                "bars": 2,
                "child": _in_zone([V.ZONE_DOWN], group="G", price_source="low"),
            }
        )
    )
    specs = {
        1: frame_testing.bar("93", "94", "90", "93"),
        3: frame_testing.bar("99", "99.5", "97", "99"),
    }
    same = evaluator.evaluate(plan, _series(4, _zone_snaps(), specs=specs))
    assert same.result is T.TRUE
    assert same.group_ids == ("g1",)
    switched = evaluator.evaluate(
        plan, _series(4, _zone_snaps(per_bar_group={3: "g2"}), specs=specs)
    )
    assert switched.result is T.FALSE
    assert switched.group_ids == ("g2",)


def test_group_member_outside_the_binding_is_false(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A containing AOI whose group is not bound does not count."""
    zones = (
        (V.ZONE_UP, "bullish", "80", "100", "g1"),
        (V.ZONE_UP, "bullish", "101", "105", "g9"),
        (V.ZONE_DOWN, "bearish", "88", "95", "g1"),
    )
    spec = {1: frame_testing.bar("103", "104", "102", "103")}
    frames = _series(2, _zone_snaps(zones), specs=spec)
    grouped = plan_for(
        _grouped(_in_zone([V.ZONE_DOWN], group="G", price_source="high"))
    )
    trace = evaluator.evaluate(grouped, frames).reason_tree
    assert trace.children[0].result is T.FALSE
    assert trace.children[0].evidence["binding"] == ["g1"]
    assert _result(plan_for(_in_zone([V.ZONE_UP])), frames) is T.TRUE


def _level_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds a 1h ``test_levels`` snapshot with a grouped level.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [
        factory.snapshot(
            V.TEST_LEVELS,
            TF.H1,
            index,
            as_of_ms=TF.H1.floor(factory.close(index)),
            aois=[
                factory.aoi(
                    V.LEVEL_LOW,
                    "bullish",
                    "90",
                    "90",
                    confirmed_index=0,
                    tf=TF.H1,
                    group_id="g1",
                )
            ],
        )
    ]


def test_group_binding_unknown_when_a_member_source_is_missing(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A member whose snapshot is missing makes the binding UNKNOWN."""
    plan = plan_for(_grouped(_in_zone([V.LEVEL_LOW], group="G")))
    spec = {1: frame_testing.bar("95", "96", "90", "94")}
    trace = evaluator.evaluate(
        plan, _series(2, _zone_snaps(), specs=spec)
    ).reason_tree
    assert trace.children[0].result is T.UNKNOWN
    assert trace.children[0].detail == "group binding unknown"
    both = _series(2, _merge(_zone_snaps(), _level_snaps), specs=spec)
    trace = evaluator.evaluate(plan, both).reason_tree
    assert trace.children[0].result is T.TRUE
    assert trace.children[0].evidence["binding"] == ["g1"]


def test_group_ids_record_true_zones_at_the_current_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Only zones true at the evaluated bar add their group ids."""
    plan = plan_for(_in_zone([V.ZONE_UP]))
    inside = _series(
        2,
        _zone_snaps(),
        specs={1: frame_testing.bar("95", "96", "90", "94")},
    )
    assert evaluator.evaluate(plan, inside).group_ids == ("g1",)
    earlier = _series(
        2,
        _zone_snaps(),
        specs={
            0: frame_testing.bar("95", "96", "90", "94"),
            1: frame_testing.bar("101", "104", "100", "102"),
        },
    )
    within = plan_for(
        {"op": "WITHIN_BARS", "bars": 1, "child": _in_zone([V.ZONE_UP])}
    )
    evaluation = evaluator.evaluate(within, earlier)
    assert evaluation.result is T.TRUE
    assert evaluation.group_ids == ()


# ---------------------------------------------------------------- AOI_TOUCH


def _zone_touch_snaps(
    *,
    lower: str = "92",
    upper: str = "94",
    confirmed_index: int = 1,
    quality_score: int | None = 70,
    state: structures.AoiState = structures.AoiState.ACTIVE,
    invalidated_at_index: int | None = None,
    include_levels: bool = False,
) -> Snaps:
    """Returns a builder for 5m ``test_zones`` (and optional level) snapshots.

    Args:
        lower: Zone lower bound.
        upper: Zone upper bound.
        confirmed_index: Bar whose close confirmed the zone.
        quality_score: Zone score.
        state: Zone state from ``invalidated_at_index`` on (from the start
            when that is None); ACTIVE before it.
        invalidated_at_index: Bar whose close invalidated it, or None.
        include_levels: Also publish an empty ``test_levels`` snapshot.

    Returns:
        A builder of each bar's snapshots; the bullish ``ZONE_UP`` zone
        ``zone-a`` appears from ``confirmed_index`` on.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds the snapshots of one bar.

        Args:
            factory: Frame factory of the series.
            index: Bar index.

        Returns:
            The bar's snapshots.
        """
        aois = []
        if index >= confirmed_index:
            aoi_state = state
            invalidated_at_ms = None
            if invalidated_at_index is not None:
                if index >= invalidated_at_index:
                    invalidated_at_ms = factory.close(invalidated_at_index)
                else:
                    aoi_state = structures.AoiState.ACTIVE
            aois.append(
                factory.aoi(
                    V.ZONE_UP,
                    "bullish",
                    lower,
                    upper,
                    confirmed_index=confirmed_index,
                    aoi_id="zone-a",
                    quality_score=quality_score,
                    state=aoi_state,
                    invalidated_at_ms=invalidated_at_ms,
                )
            )
        snaps = [factory.snapshot(V.TEST_ZONES, TF.M5, index, aois=aois)]
        if include_levels:
            snaps.append(factory.snapshot(V.TEST_LEVELS, TF.M5, index))
        return snaps

    return build


_TOUCH_BAR = frame_testing.bar("96", "97", "93.5", "95")


def _touch(**extra: object) -> dict[str, object]:
    """Builds a bullish ``ZONE_UP`` AOI_TOUCH node on the current bar.

    Args:
        **extra: Field overrides.

    Returns:
        The node in contract form.
    """
    return {
        "op": "AOI_TOUCH",
        "kinds": [V.ZONE_UP],
        "side": "bullish",
        "within_bars": 0,
        **extra,
    }


@pytest.mark.parametrize(
    ("mode", "spec", "expected"),
    [
        ("WICK", frame_testing.bar("96", "97", "93.5", "95"), T.TRUE),
        ("BODY", frame_testing.bar("96", "97", "93.5", "95"), T.FALSE),
        ("BODY", frame_testing.bar("93.8", "97", "93", "95"), T.TRUE),
        ("CLOSE", frame_testing.bar("96", "97", "93", "93.6"), T.TRUE),
        ("CLOSE", frame_testing.bar("96", "97", "93", "95"), T.FALSE),
    ],
)
def test_touch_modes(
    plan_for: Callable[..., compiler.CompiledStrategy],
    mode: str,
    spec: frame_testing.BarSpec,
    expected: tristate.TriState,
) -> None:
    """WICK, BODY and CLOSE intersections differ as documented."""
    plan = plan_for(_touch(touch=mode))
    frames = _series(4, _zone_touch_snaps(), specs={3: spec})
    assert _result(plan, frames) is expected


def test_touch_requires_confirmation_before_the_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """An AOI confirmed at the touching bar's own close does not count."""
    plan = plan_for(_touch())
    frames = _series(
        4, _zone_touch_snaps(confirmed_index=3), specs={3: _TOUCH_BAR}
    )
    assert _result(plan, frames) is T.FALSE


def test_touch_lookback_window(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """within_bars counts closed bars back from the current one."""
    plan = plan_for(_touch(within_bars=2))
    near = _series(6, _zone_touch_snaps(), specs={3: _TOUCH_BAR})
    far = _series(7, _zone_touch_snaps(), specs={3: _TOUCH_BAR})
    assert _result(plan, near) is T.TRUE
    assert _result(plan, far) is T.FALSE


def test_touch_require_active_and_quality(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Invalidated or low-quality AOIs do not count when required."""
    invalid = _series(
        4,
        _zone_touch_snaps(
            state=structures.AoiState.INVALIDATED, invalidated_at_index=3
        ),
        specs={3: _TOUCH_BAR},
    )
    assert _result(plan_for(_touch()), invalid) is T.FALSE
    assert _result(plan_for(_touch(require_active=False)), invalid) is T.TRUE
    frames = _series(4, _zone_touch_snaps(), specs={3: _TOUCH_BAR})
    assert _result(plan_for(_touch(min_quality=80)), frames) is T.FALSE


def test_touch_side_must_match(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A bullish AOI never satisfies a bearish touch."""
    plan = plan_for(_touch(side="bearish"))
    frames = _series(4, _zone_touch_snaps(), specs={3: _TOUCH_BAR})
    assert _result(plan, frames) is T.FALSE


def test_touch_with_missing_source_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Kinds whose plugin snapshot is missing make the touch UNKNOWN."""
    plan = plan_for(_touch(kinds=[V.ZONE_UP, V.LEVEL_LOW]))
    frames = _series(4, _zone_touch_snaps(), specs={3: _FLAT})
    assert _result(plan, frames) is T.UNKNOWN
    with_levels = _series(
        4, _zone_touch_snaps(include_levels=True), specs={3: _TOUCH_BAR}
    )
    assert _result(plan, with_levels) is T.TRUE


def _overlap_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds a zone at 92-94 and a level band at 93.5-96 that overlap.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [
        factory.snapshot(
            V.TEST_ZONES,
            TF.M5,
            index,
            aois=[
                factory.aoi(V.ZONE_UP, "bullish", "92", "94", confirmed_index=0)
            ],
        ),
        factory.snapshot(
            V.TEST_LEVELS,
            TF.M5,
            index,
            aois=[
                factory.aoi(
                    V.LEVEL_LOW, "bullish", "93.5", "96", confirmed_index=0
                )
            ],
        ),
    ]


def test_overlap(plan_for: Callable[..., compiler.CompiledStrategy]) -> None:
    """AOI_OVERLAP finds intersecting zones of the two kind sets."""
    overlap = {
        "op": "AOI_OVERLAP",
        "kinds_a": [V.ZONE_UP],
        "kinds_b": [V.LEVEL_LOW],
        "side": "bullish",
    }
    assert _result(plan_for(overlap), _series(2, _overlap_snaps)) is T.TRUE
    strict = {**overlap, "min_overlap_pct": "50"}
    assert _result(plan_for(strict), _series(2, _overlap_snaps)) is T.FALSE


# ------------------------------------------------------- EVENT / SEQUENCE


def _signal_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds empty 5m ``test_signals`` and ``test_state`` snapshots.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [
        factory.snapshot(V.TEST_SIGNALS, TF.M5, index),
        factory.snapshot(V.TEST_STATE, TF.M5, index),
    ]


def _state_only_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds an empty 5m ``test_state`` snapshot only.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [factory.snapshot(V.TEST_STATE, TF.M5, index)]


def _events_at(spec: dict[int, list[str]], source: str | None = None) -> Events:
    """Returns an event builder.

    Args:
        spec: Event names by bar index.
        source: Publishing plugin (the name's default source otherwise).

    Returns:
        A builder of each bar's events.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.StructureEvent]:
        """Builds events for one bar.

        Args:
            factory: Frame factory of the series.
            index: Bar index.

        Returns:
            The bar's events.
        """
        return [
            factory.event(name, index, source=source)
            for name in spec.get(index, [])
        ]

    return build


def test_event_within_bars(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """EVENT finds events inside the lookback only."""
    plan = plan_for({"op": "EVENT", "name": V.SIGNAL_DOWN, "within_bars": 2})
    events = _events_at({3: [V.SIGNAL_DOWN]})
    assert _result(plan, _series(6, _signal_snaps, events)) is T.TRUE
    assert _result(plan, _series(7, _signal_snaps, events)) is T.FALSE


def test_event_with_short_history_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Absence inside an incomplete window is UNKNOWN."""
    plan = plan_for({"op": "EVENT", "name": V.SIGNAL_DOWN, "within_bars": 5})
    assert _result(plan, _series(3, _signal_snaps)) is T.UNKNOWN


def _h1_signal_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds a 1h ``test_signals`` snapshot.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [
        factory.snapshot(
            V.TEST_SIGNALS,
            TF.H1,
            index,
            as_of_ms=TF.H1.floor(factory.close(index)),
        )
    ]


def test_event_timeframe_filter(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Events of another timeframe do not match."""
    plan = plan_for(
        {"op": "EVENT", "name": V.SIGNAL_UP, "tf": "1h", "within_bars": 0}
    )
    frames = _series(2, _h1_signal_snaps, _events_at({1: [V.SIGNAL_UP]}))
    assert _result(plan, frames) is T.FALSE


def test_event_source_filters_publishers(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """With a source only that plugin's events count; without, any do."""
    any_source = plan_for(
        {"op": "EVENT", "name": V.SIGNAL_SHARED, "within_bars": 0}
    )
    from_state = plan_for(
        {
            "op": "EVENT",
            "name": V.SIGNAL_SHARED,
            "source": V.TEST_STATE,
            "within_bars": 0,
        }
    )
    by_state = _series(
        2, _signal_snaps, _events_at({1: [V.SIGNAL_SHARED]}, V.TEST_STATE)
    )
    by_signals = _series(2, _signal_snaps, _events_at({1: [V.SIGNAL_SHARED]}))
    assert by_signals[1].events[0].source == V.TEST_SIGNALS
    assert _result(any_source, by_state) is T.TRUE
    assert _result(any_source, by_signals) is T.TRUE
    assert _result(from_state, by_state) is T.TRUE
    assert _result(from_state, by_signals) is T.FALSE


def test_event_absence_needs_the_resolved_source(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Absence is proven by ``source`` or by the first publisher."""
    any_source = plan_for(
        {"op": "EVENT", "name": V.SIGNAL_SHARED, "within_bars": 0}
    )
    from_state = plan_for(
        {
            "op": "EVENT",
            "name": V.SIGNAL_SHARED,
            "source": V.TEST_STATE,
            "within_bars": 0,
        }
    )
    state_only = _series(2, _state_only_snaps)
    assert _result(any_source, state_only) is T.UNKNOWN
    assert _result(from_state, state_only) is T.FALSE
    assert _result(any_source, _series(2, _signal_snaps)) is T.FALSE


def test_reserved_event_absence_needs_a_usable_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Reserved events may come from any plugin; a usable bar proves none."""
    plan = plan_for(
        {"op": "EVENT", "name": structures.AOI_EXPIRED, "within_bars": 0}
    )
    assert _result(plan, _series(2)) is T.FALSE
    expired = _series(
        2, events=_events_at({1: [structures.AOI_EXPIRED]}, "echo_zones")
    )
    assert _result(plan, expired) is T.TRUE


_SEQ = {
    "op": "SEQUENCE",
    "within_bars": 4,
    "steps": [{"name": V.SIGNAL_DOWN}, {"name": V.SIGNAL_UP}],
}


@pytest.mark.parametrize(
    ("events", "count", "expected"),
    [
        ({3: [V.SIGNAL_DOWN], 5: [V.SIGNAL_UP]}, 6, T.TRUE),
        ({5: [V.SIGNAL_DOWN, V.SIGNAL_UP]}, 6, T.FALSE),
        ({3: [V.SIGNAL_UP], 5: [V.SIGNAL_DOWN]}, 6, T.FALSE),
        ({1: [V.SIGNAL_DOWN], 5: [V.SIGNAL_UP]}, 6, T.FALSE),
        ({3: [V.SIGNAL_DOWN], 4: [V.SIGNAL_UP]}, 6, T.FALSE),
    ],
    ids=["ordered", "same-bar", "reversed", "outside-window", "fires-once"],
)
def test_sequence(
    plan_for: Callable[..., compiler.CompiledStrategy],
    events: dict[int, list[str]],
    count: int,
    expected: tristate.TriState,
) -> None:
    """Steps occur in order, on distinct bars, ending on this bar."""
    plan = plan_for(_SEQ)
    assert (
        _result(plan, _series(count, _signal_snaps, _events_at(events)))
        is expected
    )


def test_sequence_step_sources(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A step with a source ignores the other publishers' events."""
    plan = plan_for(
        {
            "op": "SEQUENCE",
            "within_bars": 4,
            "steps": [
                {"name": V.SIGNAL_SHARED, "source": V.TEST_STATE},
                {"name": V.SIGNAL_UP},
            ],
        }
    )
    up = _events_at({5: [V.SIGNAL_UP]})

    def from_source(source: str) -> Events:
        """Builds the two-step events with a given first-step source.

        Args:
            source: Plugin publishing the first step.

        Returns:
            The event builder.
        """
        shared = _events_at({3: [V.SIGNAL_SHARED]}, source)
        return lambda f, i: [*shared(f, i), *up(f, i)]

    assert (
        _result(plan, _series(6, _signal_snaps, from_source(V.TEST_STATE)))
        is T.TRUE
    )
    assert (
        _result(plan, _series(6, _signal_snaps, from_source(V.TEST_SIGNALS)))
        is T.FALSE
    )


def test_sequence_unknown_when_window_has_bad_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A stale bar inside the window makes a missing step UNKNOWN."""
    plan = plan_for(_SEQ)
    frames = _series(
        6,
        _signal_snaps,
        _events_at({5: [V.SIGNAL_UP]}),
        feeds={3: quality.FeedState.STALE},
    )
    assert _result(plan, frames) is T.UNKNOWN


# ---------------------------------------------------------- operands


def _delta_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds a volume delta snapshot with a delta of -25.5 %.

    Args:
        factory: Frame factory of the series.
        index: Bar index.

    Returns:
        The bar's snapshots.
    """
    return [
        factory.snapshot(
            "volume_delta",
            TF.M5,
            index,
            values={"delta_pct": "-25.5"},
        )
    ]


def test_compare_candle_and_indicator(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """COMPARE reads candle fields with bars_ago and published values."""
    plan = plan_for(
        {
            "op": "COMPARE",
            "left": {"kind": "candle", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "candle", "field": "close", "bars_ago": 1},
        }
    )
    rising = _series(3, specs={2: frame_testing.bar("100", "102", "99", "101")})
    assert _result(plan, rising) is T.TRUE
    flow = plan_for(
        {
            "op": "COMPARE",
            "left": {
                "kind": "indicator",
                "plugin_id": "volume_delta",
                "field": "delta_pct",
            },
            "cmp": "lte",
            "right": {"kind": "const", "value": "-20"},
        }
    )
    assert _result(flow, _series(2, _delta_snaps)) is T.TRUE
    assert _result(flow, _series(2)) is T.UNKNOWN


def test_market_operand_requires_context(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Missing or stale market context is UNKNOWN."""
    plan = plan_for(
        {
            "op": "COMPARE",
            "left": {"kind": "market", "field": "oi_change_pct"},
            "cmp": "lt",
            "right": {"kind": "const", "value": "-5"},
        }
    )
    frames = _series(2)
    close = frames[-1].close_ms
    assert _result(plan, frames) is T.UNKNOWN
    fresh = evaluator.MarketContext(
        points=(
            evaluator.MarketPoint(
                ts_ms=close - 1000, oi_change_pct=decimal.Decimal("-6")
            ),
        )
    )
    stale = evaluator.MarketContext(
        points=(
            evaluator.MarketPoint(
                ts_ms=close - 10_000_000, oi_change_pct=decimal.Decimal("-6")
            ),
        )
    )
    assert _result(plan, frames, market=fresh) is T.TRUE
    assert _result(plan, frames, market=stale) is T.UNKNOWN


def test_in_range_bounds(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """IN_RANGE honors inclusive and exclusive bounds."""
    node = {
        "op": "IN_RANGE",
        "value": {"kind": "candle", "field": "close"},
        "low": {"kind": "const", "value": "95"},
        "high": {"kind": "const", "value": "100"},
    }
    frames = _series(2)
    assert _result(plan_for(node), frames) is T.TRUE
    assert _result(plan_for({**node, "inclusive": False}), frames) is T.FALSE


@pytest.mark.parametrize(
    ("direction", "closes", "expected"),
    [
        ("ABOVE", ("99", "101"), T.TRUE),
        ("ABOVE", ("101", "102"), T.FALSE),
        ("BELOW", ("101", "99"), T.TRUE),
    ],
)
def test_crosses(
    plan_for: Callable[..., compiler.CompiledStrategy],
    direction: str,
    closes: tuple[str, str],
    expected: tristate.TriState,
) -> None:
    """CROSSES compares the previous and current bar."""
    plan = plan_for(
        {
            "op": "CROSSES",
            "left": {"kind": "candle", "field": "close"},
            "right": {"kind": "const", "value": "100"},
            "direction": direction,
        }
    )
    previous, current = closes
    frames = _series(
        2,
        specs={
            0: frame_testing.bar(previous, "103", "97", previous),
            1: frame_testing.bar(current, "103", "97", current),
        },
    )
    assert _result(plan, frames) is expected


def test_persist_and_within(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """PERSIST_FOR needs every bar; WITHIN_BARS needs one."""
    above = {
        "op": "COMPARE",
        "left": {"kind": "candle", "field": "close"},
        "cmp": "gt",
        "right": {"kind": "const", "value": "100"},
    }
    up = frame_testing.bar("101", "102", "100", "101")
    frames = _series(5, specs={2: up, 3: up, 4: up})
    assert (
        _result(
            plan_for({"op": "PERSIST_FOR", "bars": 3, "child": above}), frames
        )
        is T.TRUE
    )
    assert (
        _result(
            plan_for({"op": "PERSIST_FOR", "bars": 4, "child": above}), frames
        )
        is T.FALSE
    )
    once = _series(5, specs={1: up})
    assert (
        _result(
            plan_for({"op": "WITHIN_BARS", "bars": 3, "child": above}), once
        )
        is T.TRUE
    )
    assert (
        _result(
            plan_for({"op": "WITHIN_BARS", "bars": 2, "child": above}), once
        )
        is T.FALSE
    )


# -------------------------------------------------------------- TIME_WINDOW


def _utc_ms(day: tuple[int, int, int], hour: int, minute: int) -> int:
    """Converts a UTC wall time to epoch milliseconds.

    Args:
        day: Year, month and day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(*day, hour, minute, tzinfo=datetime.UTC)
    return int(moment.timestamp() * 1000)


def _windows(*spans: tuple[str, str]) -> list[ast.LocalTimeWindow]:
    """Builds local-time windows.

    Args:
        *spans: ``(start, end)`` pairs.

    Returns:
        The windows.
    """
    return [ast.LocalTimeWindow(start=start, end=end) for start, end in spans]


def test_time_window_follows_the_zone_dst() -> None:
    """Windows are local times, so daylight saving moves their UTC hours."""
    morning = _windows(("09:30", "11:30"))
    new_york = "America/New_York"
    assert evaluator.in_time_window(
        _utc_ms((2026, 1, 15), 14, 30), new_york, morning
    )
    assert evaluator.in_time_window(
        _utc_ms((2026, 7, 15), 13, 30), new_york, morning
    )
    assert not evaluator.in_time_window(
        _utc_ms((2026, 1, 15), 13, 30), new_york, morning
    )
    assert not evaluator.in_time_window(
        _utc_ms((2026, 1, 15), 16, 30), new_york, morning
    )


def test_time_window_wraps_past_midnight() -> None:
    """An end at or before the start wraps past midnight."""
    night = _windows(("22:00", "02:00"))
    assert evaluator.in_time_window(_utc_ms((2026, 1, 15), 23, 0), "UTC", night)
    assert evaluator.in_time_window(_utc_ms((2026, 1, 16), 1, 59), "UTC", night)
    assert not evaluator.in_time_window(
        _utc_ms((2026, 1, 16), 2, 0), "UTC", night
    )
    assert not evaluator.in_time_window(
        _utc_ms((2026, 1, 15), 21, 59), "UTC", night
    )


def test_time_window_weekdays_use_the_local_date() -> None:
    """Weekdays filter the ISO weekday of the local bar start."""
    day = _windows(("00:00", "23:59"))
    friday_evening_ny = _utc_ms((2026, 1, 17), 3, 0)
    assert evaluator.in_time_window(
        friday_evening_ny, "America/New_York", day, [5]
    )
    assert not evaluator.in_time_window(friday_evening_ny, "UTC", day, [5])
    assert evaluator.in_time_window(friday_evening_ny, "UTC", day, [6, 7])


def test_time_window_node(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """TIME_WINDOW tests the start of the evaluated bar."""
    node = {
        "op": "TIME_WINDOW",
        "time_zone": "UTC",
        "windows": [{"start": "00:00", "end": "00:10"}],
    }
    plan = plan_for(node)
    assert _result(plan, _series(2)) is T.TRUE
    assert _result(plan, _series(3)) is T.FALSE
    saturday_only = plan_for({**node, "weekdays": [6]})
    assert _result(saturday_only, _series(2)) is T.FALSE
    stale = _series(2, feeds={1: quality.FeedState.STALE})
    assert _result(plan, stale) is T.UNKNOWN


# ---------------------------------------------------------- data policy


def test_unusable_current_bar_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """STALE, unconfirmed or disallowed POLL bars are never evaluated."""
    plan = plan_for(
        {
            "op": "COMPARE",
            "left": {"kind": "candle", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "1"},
        }
    )
    stale = _series(2, feeds={1: quality.FeedState.STALE})
    assert _result(plan, stale) is T.UNKNOWN
    factory = frame_testing.FrameFactory()
    open_bar = [
        factory.frame(0, _FLAT),
        factory.frame(1, _FLAT, is_final=False),
    ]
    assert _result(plan, open_bar) is T.UNKNOWN
    no_poll = plan_for(
        {
            "op": "COMPARE",
            "left": {"kind": "candle", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "1"},
        },
        data_policy={"allow_rest_poll": False},
    )
    polled = _series(2, feeds={1: quality.FeedState.POLL})
    assert _result(no_poll, polled) is T.UNKNOWN
    assert _result(plan, polled) is T.TRUE


def test_data_age_budget(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Bars older than max_data_age_ms at evaluation are unusable."""
    plan = plan_for(
        {
            "op": "COMPARE",
            "left": {"kind": "candle", "field": "close"},
            "cmp": "gt",
            "right": {"kind": "const", "value": "1"},
        }
    )
    frames = _series(2)
    close = frames[-1].close_ms
    assert _result(plan, frames, evaluated_at_ms=close + 1000) is T.TRUE
    evaluation = evaluator.evaluate(
        plan, frames, evaluated_at_ms=close + 10_000_000
    )
    assert evaluation.result is T.UNKNOWN
    assert evaluation.current_issue is not None


def test_gap_makes_lookback_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A missing bar inside a lookback is UNKNOWN, not FALSE."""
    plan = plan_for({"op": "EVENT", "name": V.SIGNAL_DOWN, "within_bars": 3})
    frames = _series(6, _signal_snaps)
    gapped = frames[:3] + frames[4:]
    evaluation = evaluator.evaluate(plan, gapped)
    assert evaluation.result is T.UNKNOWN
    assert "GAP_IN_WINDOW" in evaluation.data_flags


def test_extra_history_does_not_change_results(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """History beyond the plan window changes neither result nor trace."""
    plan = plan_for(_SEQ)
    frames = _series(
        150,
        _signal_snaps,
        _events_at({146: [V.SIGNAL_DOWN], 149: [V.SIGNAL_UP]}),
    )
    full = evaluator.evaluate(plan, frames)
    trimmed = evaluator.evaluate(plan, frames[-plan.window_bars :])
    assert full.result is trimmed.result is T.TRUE
    assert full.reason_tree == trimmed.reason_tree


def test_invalid_frame_sequences_raise(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Unordered, empty or mixed-instrument frames raise ValueError."""
    plan = plan_for(_SEQ)
    frames = _series(3, _signal_snaps)
    with pytest.raises(ValueError):
        evaluator.evaluate(plan, [frames[1], frames[0]])
    with pytest.raises(ValueError):
        evaluator.evaluate(plan, [])
    other = frame_testing.FrameFactory(instrument_id="binance:perp:ETHUSDT")
    with pytest.raises(ValueError):
        evaluator.evaluate(plan, [frames[0], other.frame(1, _FLAT)])


def test_reason_tree_covers_false_nodes(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Every node, true or false, appears in the trace."""
    node = {
        "op": "ALL",
        "id": "root",
        "children": [
            {
                "op": "EVENT",
                "id": "e1",
                "name": V.SIGNAL_DOWN,
                "within_bars": 1,
            },
            {
                "op": "NOT",
                "id": "n1",
                "child": {
                    "op": "EVENT",
                    "id": "e2",
                    "name": V.SIGNAL_UP,
                    "within_bars": 1,
                },
            },
        ],
    }
    tree = evaluator.evaluate(
        plan_for(node), _series(3, _signal_snaps)
    ).reason_tree.to_contract()
    assert tree["node_id"] == "root"
    children = tree["children"]
    assert isinstance(children, list)
    assert [c["node_id"] for c in children] == ["e1", "n1"]
    assert children[0]["result"] == "FALSE"
    assert children[1]["result"] == "TRUE"
    assert children[1]["children"][0]["node_path"] == "/when/children/1/child"
