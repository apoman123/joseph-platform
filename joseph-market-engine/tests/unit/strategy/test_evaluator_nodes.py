# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Node-by-node semantics of the tri-state evaluator."""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Callable
from collections.abc import Sequence

import pytest

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import tristate
from joseph.strategy.testing import frames as frame_testing

T = tristate.TriState
TF = timeframes.Timeframe
Make = Callable[..., ast.StrategyDefinition]
Plan = Callable[[ast.StrategyDefinition], compiler.CompiledStrategy]
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
        make_definition: Definition builder.
        compile_plan: Compiler.

    Returns:
        ``plan_for(node, **overrides)``.
    """

    def build(
        node: dict[str, object], **overrides: object
    ) -> compiler.CompiledStrategy:
        """Builds the plan.

        Args:
            node: Root node.
            **overrides: Definition overrides.

        Returns:
            Plan.
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
    """Builds a frame series.

    Args:
        count: Number of bars.
        snapshots: Snapshots per bar.
        events: Events per bar.
        specs: OHLC overrides per bar.
        feeds: Feed state overrides per bar.

    Returns:
        Frames.
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
        plan: Plan.
        frames: Frames.
        **kwargs: Extra ``evaluate`` arguments.

    Returns:
        Truth value.
    """
    return evaluator.evaluate(plan, frames, **kwargs).result  # type: ignore[arg-type]


# --------------------------------------------------------------------- BIAS


def _bias_snaps(
    trends: dict[TF, str | None], *, last_event: str = "BOS", warm: bool = True
) -> Callable[
    [frame_testing.FrameFactory, int], list[structures.IndicatorSnapshot]
]:
    """Returns a snapshot builder publishing daily_bias trends.

    Args:
        trends: Trend per timeframe (``None`` omits the snapshot).
        last_event: Published last event.
        warm: Warm-up flag.

    Returns:
        Builder.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds snapshots for one bar.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        return [
            factory.snapshot(
                "daily_bias",
                tf,
                index,
                warm=warm,
                as_of_ms=tf.floor(factory.close(index)),
                values={
                    "trend": trend,
                    "last_event": last_event,
                    "last_event_direction": trend,
                },
            )
            for tf, trend in trends.items()
            if trend is not None
        ]

    return build


def test_bias_all_requires_every_timeframe(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """ALL bias is FALSE when any timeframe disagrees."""
    plan = plan_for(
        {"op": "BIAS", "timeframes": ["4h", "1h"], "value": "BULLISH"}
    )
    agree = _series(3, _bias_snaps({TF.H4: "BULLISH", TF.H1: "BULLISH"}))
    mixed = _series(3, _bias_snaps({TF.H4: "BULLISH", TF.H1: "BEARISH"}))
    assert _result(plan, agree) is T.TRUE
    assert _result(plan, mixed) is T.FALSE


def test_bias_missing_snapshot_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A missing or warming-up bias snapshot yields UNKNOWN, not FALSE."""
    plan = plan_for(
        {"op": "BIAS", "timeframes": ["4h", "1h"], "value": "BULLISH"}
    )
    assert (
        _result(plan, _series(3, _bias_snaps({TF.H4: "BULLISH", TF.H1: None})))
        is T.UNKNOWN
    )
    assert (
        _result(
            plan,
            _series(
                3, _bias_snaps({TF.H4: "BULLISH", TF.H1: "BULLISH"}, warm=False)
            ),
        )
        is T.UNKNOWN
    )


def test_bias_k_of_n(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """K_OF_N counts agreeing timeframes."""
    plan = plan_for(
        {
            "op": "BIAS",
            "timeframes": ["1d", "4h", "1h"],
            "value": "BEARISH",
            "match": "K_OF_N",
            "k": 2,
        }
    )
    two = _bias_snaps({TF.D1: "BEARISH", TF.H4: "BEARISH", TF.H1: "BULLISH"})
    one = _bias_snaps({TF.D1: "BEARISH", TF.H4: "BULLISH", TF.H1: "BULLISH"})
    undecided = _bias_snaps({TF.D1: "BEARISH", TF.H4: None, TF.H1: "BULLISH"})
    assert _result(plan, _series(2, two)) is T.TRUE
    assert _result(plan, _series(2, one)) is T.FALSE
    assert _result(plan, _series(2, undecided)) is T.UNKNOWN


def test_bias_require_bos(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """require_bos rejects a trend whose last event was a CHoCH."""
    plan = plan_for(
        {
            "op": "BIAS",
            "timeframes": ["4h"],
            "value": "BULLISH",
            "require_bos": True,
        }
    )
    assert _result(plan, _series(2, _bias_snaps({TF.H4: "BULLISH"}))) is T.TRUE
    assert (
        _result(
            plan,
            _series(2, _bias_snaps({TF.H4: "BULLISH"}, last_event="CHOCH")),
        )
        is T.FALSE
    )


def test_snapshot_from_the_future_is_rejected(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A snapshot stamped after the frame close is never read."""
    plan = plan_for({"op": "BIAS", "timeframes": ["4h"], "value": "BULLISH"})

    def future(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds a look-ahead snapshot.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        return [
            factory.snapshot(
                "daily_bias",
                TF.H4,
                index,
                as_of_ms=factory.close(index) + TF.H4.duration_ms,
                values={"trend": "BULLISH"},
            )
        ]

    evaluation = evaluator.evaluate(plan, _series(2, future))
    assert evaluation.result is T.UNKNOWN
    assert "LOOKAHEAD_REJECTED" in evaluation.data_flags


# ------------------------------------------------------------ PRICE_IN_ZONE


def _range_snaps(
    range_id: str = "r1",
    *,
    with_aois: bool = True,
    per_bar_range: dict[int, str] | None = None,
) -> Callable[
    [frame_testing.FrameFactory, int], list[structures.IndicatorSnapshot]
]:
    """Returns a builder for 1h dealing-range snapshots.

    Args:
        range_id: Range id.
        with_aois: Publish zone AOIs (else values only).
        per_bar_range: Range id overrides per bar.

    Returns:
        Builder.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds one snapshot.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        rid = (per_bar_range or {}).get(index, range_id)
        aois = []
        if with_aois:
            aois = [
                factory.aoi(
                    "DISCOUNT",
                    "bullish",
                    "80",
                    "100",
                    confirmed_index=0,
                    tf=TF.H1,
                    range_id=rid,
                    aoi_id=f"d-{rid}",
                ),
                factory.aoi(
                    "OTE",
                    "bullish",
                    "88",
                    "95",
                    confirmed_index=0,
                    tf=TF.H1,
                    range_id=rid,
                    aoi_id=f"o-{rid}",
                ),
                factory.aoi(
                    "EQUILIBRIUM",
                    "neutral",
                    "99",
                    "101",
                    confirmed_index=0,
                    tf=TF.H1,
                    range_id=rid,
                    aoi_id=f"e-{rid}",
                ),
            ]
        return [
            factory.snapshot(
                "premium_discount_ote",
                TF.H1,
                index,
                as_of_ms=TF.H1.floor(factory.close(index)),
                aois=aois,
                values={
                    "range_id": rid,
                    "range_high": decimal.Decimal("120"),
                    "range_low": decimal.Decimal("80"),
                    "eq": decimal.Decimal("100"),
                    "ote_lower": decimal.Decimal("88"),
                    "ote_upper": decimal.Decimal("95"),
                },
            )
        ]

    return build


@pytest.mark.parametrize(
    ("zone", "source", "spec", "expected"),
    [
        (
            "DISCOUNT",
            "close",
            frame_testing.bar("95", "96", "90", "94"),
            T.TRUE,
        ),
        (
            "DISCOUNT",
            "close",
            frame_testing.bar("101", "104", "100", "102"),
            T.FALSE,
        ),
        (
            "PREMIUM",
            "close",
            frame_testing.bar("101", "104", "100", "102"),
            T.TRUE,
        ),
        ("OTE", "low", frame_testing.bar("97", "98", "94", "97"), T.TRUE),
        ("OTE", "close", frame_testing.bar("97", "98", "94", "97"), T.FALSE),
        ("OTE", "hl2", frame_testing.bar("93", "94", "90", "93"), T.TRUE),
        (
            "EQUILIBRIUM",
            "close",
            frame_testing.bar("100", "101", "99", "100"),
            T.TRUE,
        ),
    ],
)
def test_price_in_zone(
    plan_for: Callable[..., compiler.CompiledStrategy],
    zone: str,
    source: str,
    spec: frame_testing.BarSpec,
    expected: tristate.TriState,
) -> None:
    """Zones and price sources follow the published bounds."""
    plan = plan_for(
        {
            "op": "PRICE_IN_ZONE",
            "zone": zone,
            "tf": "1h",
            "price_source": source,
        }
    )
    frames = _series(3, _range_snaps(), specs={2: spec})
    assert _result(plan, frames) is expected


def test_zone_falls_back_to_values(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Without zone AOIs the published range values are used."""
    plan = plan_for({"op": "PRICE_IN_ZONE", "zone": "DISCOUNT", "tf": "1h"})
    frames = _series(
        2,
        _range_snaps(with_aois=False),
        specs={1: frame_testing.bar("90", "91", "89", "90")},
    )
    assert _result(plan, frames) is T.TRUE


def test_missing_range_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """No dealing range snapshot means UNKNOWN."""
    plan = plan_for({"op": "PRICE_IN_ZONE", "zone": "DISCOUNT", "tf": "1h"})
    assert _result(plan, _series(2)) is T.UNKNOWN


def test_range_group_binds_the_same_range(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Grouped zones evaluated on another bar must share the range id."""
    node = {
        "op": "ALL",
        "children": [
            {
                "op": "PRICE_IN_ZONE",
                "zone": "DISCOUNT",
                "tf": "1h",
                "range_group": "A",
            },
            {
                "op": "WITHIN_BARS",
                "bars": 2,
                "child": {
                    "op": "PRICE_IN_ZONE",
                    "zone": "OTE",
                    "tf": "1h",
                    "range_group": "A",
                    "price_source": "low",
                },
            },
        ],
    }
    plan = plan_for(node)
    specs = {
        1: frame_testing.bar("93", "94", "90", "93"),
        3: frame_testing.bar("99", "99.5", "97", "99"),
    }
    same = _series(4, _range_snaps(), specs=specs)
    assert _result(plan, same) is T.TRUE
    switched = _series(4, _range_snaps(per_bar_range={3: "r2"}), specs=specs)
    evaluation = evaluator.evaluate(plan, switched)
    assert evaluation.result is T.FALSE
    assert evaluation.range_ids == ("r2",)


# --------------------------------------------------------------- AOI_TOUCH


def _fvg_snaps(
    *,
    lower: str = "92",
    upper: str = "94",
    confirmed_index: int = 1,
    quality_score: int | None = 70,
    state: structures.AoiState = structures.AoiState.ACTIVE,
    invalidated_at_index: int | None = None,
    include_ob: bool = False,
) -> Callable[
    [frame_testing.FrameFactory, int], list[structures.IndicatorSnapshot]
]:
    """Returns a builder for fvg_ifvg (and optional OB) snapshots.

    Args:
        lower: FVG lower bound.
        upper: FVG upper bound.
        confirmed_index: Bar whose close confirmed the FVG.
        quality_score: FVG score.
        state: FVG state.
        invalidated_at_index: Bar whose close invalidated it.
        include_ob: Also publish an empty order-block snapshot.

    Returns:
        Builder.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds snapshots.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        aois = []
        if index >= confirmed_index:
            invalidated = (
                None
                if invalidated_at_index is None
                else factory.close(invalidated_at_index)
            )
            aois.append(
                factory.aoi(
                    "FVG",
                    "bullish",
                    lower,
                    upper,
                    confirmed_index=confirmed_index,
                    aoi_id="fvg-a",
                    quality_score=quality_score,
                    state=(
                        state
                        if invalidated_at_index is None
                        or index >= invalidated_at_index
                        else structures.AoiState.ACTIVE
                    ),
                    invalidated_at_ms=(
                        invalidated
                        if invalidated_at_index is not None
                        and index >= invalidated_at_index
                        else None
                    ),
                )
            )
        snaps = [factory.snapshot("fvg_ifvg", TF.M5, index, aois=aois)]
        if include_ob:
            snaps.append(factory.snapshot("order_breaker_blocks", TF.M5, index))
        return snaps

    return build


_TOUCH_BAR = frame_testing.bar("96", "97", "93.5", "95")


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
    plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 0,
            "touch": mode,
        }
    )
    frames = _series(4, _fvg_snaps(), specs={3: spec})
    assert _result(plan, frames) is expected


def test_touch_requires_confirmation_before_the_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """An AOI confirmed at the touching bar's own close does not count."""
    plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 0,
        }
    )
    frames = _series(4, _fvg_snaps(confirmed_index=3), specs={3: _TOUCH_BAR})
    assert _result(plan, frames) is T.FALSE


def test_touch_lookback_window(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """within_bars counts closed bars back from the current one."""
    plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 2,
        }
    )
    near = _series(6, _fvg_snaps(), specs={3: _TOUCH_BAR})
    far = _series(7, _fvg_snaps(), specs={3: _TOUCH_BAR})
    assert _result(plan, near) is T.TRUE
    assert _result(plan, far) is T.FALSE


def test_touch_require_active_and_quality(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Invalidated or low-quality AOIs do not count when required."""
    invalid = _series(
        4,
        _fvg_snaps(
            state=structures.AoiState.INVALIDATED, invalidated_at_index=3
        ),
        specs={3: _TOUCH_BAR},
    )
    active_plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 0,
        }
    )
    lenient = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 0,
            "require_active": False,
        }
    )
    assert _result(active_plan, invalid) is T.FALSE
    assert _result(lenient, invalid) is T.TRUE
    quality_plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bullish",
            "within_bars": 0,
            "min_quality": 80,
        }
    )
    assert (
        _result(quality_plan, _series(4, _fvg_snaps(), specs={3: _TOUCH_BAR}))
        is T.FALSE
    )


def test_touch_side_must_match(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A bullish AOI never satisfies a bearish touch."""
    plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG"],
            "side": "bearish",
            "within_bars": 0,
        }
    )
    assert (
        _result(plan, _series(4, _fvg_snaps(), specs={3: _TOUCH_BAR}))
        is T.FALSE
    )


def test_touch_with_missing_source_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Kinds whose plugin snapshot is missing make the touch UNKNOWN."""
    plan = plan_for(
        {
            "op": "AOI_TOUCH",
            "kinds": ["FVG", "OB"],
            "side": "bullish",
            "within_bars": 0,
        }
    )
    frames = _series(
        4, _fvg_snaps(), specs={3: frame_testing.bar("100", "101", "99", "100")}
    )
    assert _result(plan, frames) is T.UNKNOWN
    with_ob = _series(4, _fvg_snaps(include_ob=True), specs={3: _TOUCH_BAR})
    assert _result(plan, with_ob) is T.TRUE


def test_overlap(plan_for: Callable[..., compiler.CompiledStrategy]) -> None:
    """AOI_OVERLAP finds intersecting zones of the two kind sets."""

    def snaps(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds FVG and OB snapshots that overlap.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        return [
            factory.snapshot(
                "fvg_ifvg",
                TF.M5,
                index,
                aois=[
                    factory.aoi("FVG", "bullish", "92", "94", confirmed_index=0)
                ],
            ),
            factory.snapshot(
                "order_breaker_blocks",
                TF.M5,
                index,
                aois=[
                    factory.aoi(
                        "OB", "bullish", "93.5", "96", confirmed_index=0
                    )
                ],
            ),
        ]

    overlap = {
        "op": "AOI_OVERLAP",
        "kinds_a": ["FVG"],
        "kinds_b": ["OB"],
        "side": "bullish",
    }
    assert _result(plan_for(overlap), _series(2, snaps)) is T.TRUE
    strict = {**overlap, "min_overlap_pct": "50"}
    assert _result(plan_for(strict), _series(2, snaps)) is T.FALSE


# ------------------------------------------------------- EVENT / SEQUENCE


def _flow_snaps(
    factory: frame_testing.FrameFactory, index: int
) -> list[structures.IndicatorSnapshot]:
    """Builds liquidity and order-flow snapshots.

    Args:
        factory: Factory.
        index: Bar index.

    Returns:
        Snapshots.
    """
    return [
        factory.snapshot("liquidity_sweeps", TF.M5, index),
        factory.snapshot("order_flow_pressure", TF.M5, index),
    ]


def _events_at(
    spec: dict[int, list[str]],
) -> Callable[
    [frame_testing.FrameFactory, int], list[structures.StructureEvent]
]:
    """Returns an event builder.

    Args:
        spec: Event names per bar.

    Returns:
        Builder.
    """

    def build(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.StructureEvent]:
        """Builds events for one bar.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Events.
        """
        return [factory.event(name, index) for name in spec.get(index, [])]

    return build


def test_event_within_bars(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """EVENT finds events inside the lookback only."""
    plan = plan_for({"op": "EVENT", "name": "SSL_SWEEP", "within_bars": 2})
    assert (
        _result(plan, _series(6, _flow_snaps, _events_at({3: ["SSL_SWEEP"]})))
        is T.TRUE
    )
    assert (
        _result(plan, _series(7, _flow_snaps, _events_at({3: ["SSL_SWEEP"]})))
        is T.FALSE
    )


def test_event_with_short_history_is_unknown(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Absence inside an incomplete window is UNKNOWN."""
    plan = plan_for({"op": "EVENT", "name": "SSL_SWEEP", "within_bars": 5})
    assert _result(plan, _series(3, _flow_snaps)) is T.UNKNOWN


def test_event_timeframe_filter(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Events of another timeframe do not match."""
    plan = plan_for(
        {"op": "EVENT", "name": "BOS_BULL", "tf": "1h", "within_bars": 0}
    )

    def snaps(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds a 1h bias snapshot.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        return [
            factory.snapshot(
                "daily_bias",
                TF.H1,
                index,
                as_of_ms=TF.H1.floor(factory.close(index)),
            )
        ]

    def events(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.StructureEvent]:
        """Builds a 5m BOS event (wrong timeframe).

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Events.
        """
        return [factory.event("BOS_BULL", index)] if index == 1 else []

    assert _result(plan, _series(2, snaps, events)) is T.FALSE


_SEQ = {
    "op": "SEQUENCE",
    "within_bars": 4,
    "steps": [{"name": "SSL_SWEEP"}, {"name": "SELLER_FAILURE"}],
}


@pytest.mark.parametrize(
    ("events", "count", "expected"),
    [
        ({3: ["SSL_SWEEP"], 5: ["SELLER_FAILURE"]}, 6, T.TRUE),
        ({5: ["SSL_SWEEP", "SELLER_FAILURE"]}, 6, T.FALSE),
        ({3: ["SELLER_FAILURE"], 5: ["SSL_SWEEP"]}, 6, T.FALSE),
        ({1: ["SSL_SWEEP"], 5: ["SELLER_FAILURE"]}, 6, T.FALSE),
        ({3: ["SSL_SWEEP"], 4: ["SELLER_FAILURE"]}, 6, T.FALSE),
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
        _result(plan, _series(count, _flow_snaps, _events_at(events)))
        is expected
    )


def test_sequence_unknown_when_window_has_bad_bar(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """A stale bar inside the window makes a missing step UNKNOWN."""
    plan = plan_for(_SEQ)
    frames = _series(
        6,
        _flow_snaps,
        _events_at({5: ["SELLER_FAILURE"]}),
        feeds={3: quality.FeedState.STALE},
    )
    assert _result(plan, frames) is T.UNKNOWN


# ---------------------------------------------------------- operands


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
                "plugin_id": "order_flow_pressure",
                "field": "delta_pct",
            },
            "cmp": "lte",
            "right": {"kind": "const", "value": "-20"},
        }
    )

    def snaps(
        factory: frame_testing.FrameFactory, index: int
    ) -> list[structures.IndicatorSnapshot]:
        """Builds an order-flow snapshot.

        Args:
            factory: Factory.
            index: Bar index.

        Returns:
            Snapshots.
        """
        return [
            factory.snapshot(
                "order_flow_pressure",
                TF.M5,
                index,
                values={"delta_pct": "-25.5"},
            )
        ]

    assert _result(flow, _series(2, snaps)) is T.TRUE
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


# ---------------------------------------------------------------- SESSION


def test_session_windows_follow_new_york_dst() -> None:
    """The same New York wall time maps to different UTC hours."""
    winter = int(
        datetime.datetime(2026, 1, 15, 13, 30, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    summer = int(
        datetime.datetime(2026, 7, 15, 12, 30, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    assert evaluator.session_of(winter) == "NEW_YORK"
    assert evaluator.session_of(summer) == "NEW_YORK"
    asia = int(
        datetime.datetime(2026, 1, 16, 2, 0, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    assert evaluator.session_of(asia) == "ASIA"
    london = int(
        datetime.datetime(2026, 1, 16, 8, 0, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    assert evaluator.session_of(london) == "LONDON"
    off = int(
        datetime.datetime(2026, 1, 16, 18, 0, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    assert evaluator.session_of(off) == "NONE"


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
    plan = plan_for({"op": "EVENT", "name": "SSL_SWEEP", "within_bars": 3})
    frames = _series(6, _flow_snaps)
    gapped = frames[:3] + frames[4:]
    evaluation = evaluator.evaluate(plan, gapped)
    assert evaluation.result is T.UNKNOWN
    assert "GAP_IN_WINDOW" in evaluation.data_flags


def test_extra_history_does_not_change_results(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Only the plan window is read."""
    plan = plan_for(_SEQ)
    frames = _series(
        150,
        _flow_snaps,
        _events_at({146: ["SSL_SWEEP"], 149: ["SELLER_FAILURE"]}),
    )
    full = evaluator.evaluate(plan, frames)
    trimmed = evaluator.evaluate(plan, frames[-plan.window_bars :])
    assert full.result is trimmed.result is T.TRUE
    assert full.reason_tree == trimmed.reason_tree


def test_invalid_frame_sequences_raise(
    plan_for: Callable[..., compiler.CompiledStrategy],
) -> None:
    """Callers must pass ordered frames of one series."""
    plan = plan_for(_SEQ)
    frames = _series(3, _flow_snaps)
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
            {"op": "EVENT", "id": "e1", "name": "SSL_SWEEP", "within_bars": 1},
            {
                "op": "NOT",
                "id": "n1",
                "child": {
                    "op": "EVENT",
                    "id": "e2",
                    "name": "BSL_SWEEP",
                    "within_bars": 1,
                },
            },
        ],
    }
    tree = evaluator.evaluate(
        plan_for(node), _series(3, _flow_snaps)
    ).reason_tree.to_contract()
    assert tree["node_id"] == "root"
    children = tree["children"]
    assert isinstance(children, list)
    assert [c["node_id"] for c in children] == ["e1", "n1"]
    assert children[0]["result"] == "FALSE"
    assert children[1]["result"] == "TRUE"
    assert children[1]["children"][0]["node_path"] == "/when/children/1/child"
