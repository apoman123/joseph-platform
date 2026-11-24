# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Setup state machine: transition table, checkpoints and lifecycle."""

from __future__ import annotations

import copy
import dataclasses
import decimal
import itertools
from collections.abc import Sequence

import pytest

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.app import processor
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import errors
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios

S = setup_fsm.SetupState
TF = timeframes.Timeframe
_ALL_KINDS = [
    "WATCHING_AOI",
    "PRE_SIGNAL",
    "ENTRY_CONFIRMED",
    "ENTRY_BLOCKED",
    "AOI_INVALIDATED",
    "SETUP_EXPIRED",
]

_LEGAL = {
    (S.WAITING, S.AOI_TOUCHED),
    (S.WAITING, S.CONFIRMING),
    (S.WAITING, S.READY),
    (S.WAITING, S.CANCELLED),
    (S.AOI_TOUCHED, S.CONFIRMING),
    (S.AOI_TOUCHED, S.READY),
    (S.AOI_TOUCHED, S.EXPIRED),
    (S.AOI_TOUCHED, S.INVALIDATED),
    (S.AOI_TOUCHED, S.CANCELLED),
    (S.CONFIRMING, S.AOI_TOUCHED),
    (S.CONFIRMING, S.READY),
    (S.CONFIRMING, S.EXPIRED),
    (S.CONFIRMING, S.INVALIDATED),
    (S.CONFIRMING, S.CANCELLED),
    (S.READY, S.FIRED),
    (S.READY, S.BLOCKED),
    (S.BLOCKED, S.AOI_TOUCHED),
    (S.BLOCKED, S.CONFIRMING),
    (S.BLOCKED, S.READY),
    (S.BLOCKED, S.EXPIRED),
    (S.BLOCKED, S.INVALIDATED),
    (S.BLOCKED, S.CANCELLED),
}


@pytest.mark.parametrize(
    ("source", "target"), list(itertools.product(list(S), list(S)))
)
def test_transition_table_is_exhaustive(
    source: setup_fsm.SetupState, target: setup_fsm.SetupState
) -> None:
    """Exactly the documented transitions are legal."""
    if (source, target) in _LEGAL:
        setup_fsm.check_transition(source, target)
    else:
        with pytest.raises(errors.IllegalTransitionError):
            setup_fsm.check_transition(source, target)


def test_terminal_states() -> None:
    """FIRED, EXPIRED, INVALIDATED and CANCELLED are terminal."""
    assert {
        S.FIRED,
        S.EXPIRED,
        S.INVALIDATED,
        S.CANCELLED,
    } == setup_fsm.TERMINAL


def test_checkpoint_round_trip() -> None:
    """Instance state survives serialization."""
    key = setup_fsm.InstanceKey(
        "t1", "s:v1", "*", "binance:perp:BTCUSDT", "LONG"
    )
    state = setup_fsm.InstanceState(
        key=key,
        setup=setup_fsm.Setup(
            "setup-1", S.CONFIRMING, 100, "fvg-1", frozenset({"PRE_SIGNAL"})
        ),
        last_close_ms=200,
        last_revision=1,
        cooldown_until_ms=900,
        fired_aoi_ids=frozenset({"ob-1"}),
        last_fired_close_ms=50,
    )
    assert setup_fsm.InstanceState.from_dict(state.to_dict()) == state
    with pytest.raises(ValueError):
        setup_fsm.InstanceState.from_dict({"key": None})


def test_precheck_classifies_bars() -> None:
    """Older, duplicate and revised bars are never re-evaluated."""
    key = setup_fsm.InstanceKey(
        "t1", "s:v1", "*", "binance:perp:BTCUSDT", "LONG"
    )
    state = setup_fsm.InstanceState(
        key=key, last_close_ms=1000, last_revision=0
    )
    assert setup_fsm.precheck(state, 2000, 0) is None
    older = setup_fsm.precheck(state, 500, 0)
    duplicate = setup_fsm.precheck(state, 1000, 0)
    revised = setup_fsm.precheck(state, 1000, 2)
    assert older is not None and older.skipped == "out_of_order"
    assert duplicate is not None and duplicate.skipped == "duplicate"
    assert revised is not None and revised.revision is not None
    assert revised.revision.new_revision == 2
    assert revised.state.last_revision == 2


# --------------------------------------------------------------- lifecycle


def _definition(
    strategy_id: str = fixtures.LONG_A, **notification: object
) -> ast.StrategyDefinition:
    """Returns a fixture definition with notification overrides.

    Args:
        strategy_id: Fixture strategy id.
        **notification: Notification overrides (``setup`` key accepted).

    Returns:
        Definition.
    """
    raw = copy.deepcopy(
        fixtures.strategy_definitions()[strategy_id].to_contract()
    )
    setup = notification.pop("setup", None)
    if setup is not None:
        raw["setup"] = setup
    note = raw["notification"]
    assert isinstance(note, dict)
    note.update(notification)
    return ast.parse_definition(raw)


def _processor(
    definition: ast.StrategyDefinition,
) -> processor.InstanceProcessor:
    """Builds a processor for the BTC instrument.

    Args:
        definition: Definition.

    Returns:
        Processor.
    """
    plan = compiler.compile_strategy(definition).plan
    assert plan is not None
    version = fixtures.make_version(definition)
    key = setup_fsm.InstanceKey(
        "tenant-01",
        version.strategy_version_id,
        "*",
        "binance:perp:BTCUSDT",
        definition.direction,
    )
    return processor.InstanceProcessor(plan, version, key, mode="PAPER")


def _run(
    proc: processor.InstanceProcessor,
    frames: Sequence[structures.BarFrame],
    **kwargs: object,
) -> list[tuple[int, str, str]]:
    """Feeds frames and collects ``(bar index, kind, state)``.

    Args:
        proc: Processor.
        frames: Frames.
        **kwargs: Extra ``step`` arguments.

    Returns:
        Emitted signal summaries.
    """
    base = frame_testing.BASE_MS
    tf_ms = proc.plan.definition.signal_timeframe.duration_ms
    out = []
    for frame in frames:
        result = proc.step(frame, evaluated_at_ms=frame.close_ms, **kwargs)  # type: ignore[arg-type]
        for signal in result.signals:
            index = (signal.bar_start_ms - base) // tf_ms
            out.append((index, signal.kind, signal.setup_state))
    return out


def test_long_a_fires_once_on_the_entry_bar() -> None:
    """The positive Long A world fires exactly one entry, on bar 30."""
    emitted = _run(_processor(_definition()), scenarios.long_a_frames())
    assert emitted == [(30, "ENTRY_CONFIRMED", "FIRED")]


def test_full_lifecycle_kinds_are_emitted_when_requested() -> None:
    """With every kind enabled the setup lifecycle is visible."""
    opts = scenarios.WorldOptions(sweep=False)
    emitted = _run(
        _processor(_definition(emit=_ALL_KINDS)), scenarios.long_a_frames(opts)
    )
    assert emitted == [
        (30, "PRE_SIGNAL", "CONFIRMING"),
        (31, "WATCHING_AOI", "AOI_TOUCHED"),
        (34, "SETUP_EXPIRED", "EXPIRED"),
    ]


def test_expire_after_bars() -> None:
    """An open setup expires after expire_after_bars."""
    opts = scenarios.WorldOptions(sweep=False)
    emitted = _run(
        _processor(
            _definition(emit=_ALL_KINDS, setup={"expire_after_bars": 1})
        ),
        scenarios.long_a_frames(opts),
    )
    assert emitted[0] == (30, "PRE_SIGNAL", "CONFIRMING")
    assert (31, "SETUP_EXPIRED", "EXPIRED") in emitted


def test_missing_target_blocks_the_entry_as_unknown() -> None:
    """No opposing liquidity: ENTRY_BLOCKED with an UNKNOWN risk status."""
    proc = _processor(_definition())
    signals = []
    for frame in scenarios.long_a_frames(scenarios.WorldOptions(target=False)):
        signals.extend(proc.step(frame, evaluated_at_ms=frame.close_ms).signals)
    assert [s.kind for s in signals] == ["ENTRY_BLOCKED"]
    assert signals[0].risk_status == "UNKNOWN"
    assert any("opposing liquidity" in r for r in signals[0].risk_reasons)


def test_unqualified_instrument_is_blocked() -> None:
    """QUALIFIED_ONLY universes block entries without qualification."""
    for qualification, status in (
        (tristate.TriState.FALSE, "FAIL"),
        (tristate.TriState.UNKNOWN, "UNKNOWN"),
    ):
        proc = _processor(_definition())
        signals = []
        for frame in scenarios.long_a_frames():
            signals.extend(
                proc.step(
                    frame,
                    evaluated_at_ms=frame.close_ms,
                    qualification=qualification,
                ).signals
            )
        assert [s.kind for s in signals] == ["ENTRY_BLOCKED"]
        assert signals[0].risk_status == status


def test_rr_below_floor_blocks() -> None:
    """A net R:R below the strategy floor blocks with FAIL."""
    raw = copy.deepcopy(
        fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()
    )
    risk = raw["risk"]
    assert isinstance(risk, dict)
    risk["min_rr"] = "8"
    proc = _processor(ast.parse_definition(raw))
    signals = []
    for frame in scenarios.long_a_frames():
        signals.extend(proc.step(frame, evaluated_at_ms=frame.close_ms).signals)
    assert [(s.kind, s.risk_status) for s in signals] == [
        ("ENTRY_BLOCKED", "FAIL")
    ]


def test_stale_entry_bar_is_held() -> None:
    """A stale entry bar holds state and emits nothing."""
    proc = _processor(_definition())
    frames = scenarios.long_a_frames(
        scenarios.WorldOptions(entry_feed=quality.FeedState.STALE)
    )
    outcomes = [proc.step(f, evaluated_at_ms=f.close_ms) for f in frames]
    assert all(not o.signals for o in outcomes)
    assert outcomes[30].outcome.skipped is not None
    assert outcomes[30].outcome.skipped.startswith("held")


def _reenter(
    frames: list[structures.BarFrame], index: int
) -> list[structures.BarFrame]:
    """Repeats the Long A trigger (sweep then failure) ending at ``index``.

    Args:
        frames: Long A frames.
        index: New entry bar.

    Returns:
        Modified frames.
    """
    factory = frame_testing.FrameFactory()
    entry = frames[30]
    result = list(frames)
    sweep = result[index - 2]
    result[index - 2] = dataclasses.replace(
        sweep, events=(factory.event("SSL_SWEEP", index - 2),)
    )
    candle = dataclasses.replace(
        entry.candle,
        start_ms=factory.start(index),
        end_ms=factory.close(index),
        received_at_ms=factory.close(index) + 50,
    )
    result[index] = dataclasses.replace(
        result[index],
        candle=candle,
        events=(factory.event("SELLER_FAILURE", index),),
    )
    return result


def test_cooldown_blocks_a_second_entry() -> None:
    """A second trigger inside the cooldown is blocked."""
    frames = _reenter(scenarios.long_a_frames(), 33)
    emitted = _run(_processor(_definition(once_per_aoi=False)), frames)
    assert emitted[0] == (30, "ENTRY_CONFIRMED", "FIRED")
    assert emitted[1][:2] == (33, "ENTRY_BLOCKED")


def test_once_per_aoi_suppresses_repeat_entries() -> None:
    """The same AOI never fires twice when once_per_aoi is set."""
    frames = _reenter(scenarios.long_a_frames(), 33)
    emitted = _run(_processor(_definition(cooldown_bars=0)), frames)
    assert emitted == [(30, "ENTRY_CONFIRMED", "FIRED")]


def test_repeat_entries_allowed_without_dedupe_policies() -> None:
    """Without cooldown or once_per_aoi a new setup may fire again."""
    frames = _reenter(scenarios.long_a_frames(), 33)
    emitted = _run(
        _processor(_definition(cooldown_bars=0, once_per_aoi=False)), frames
    )
    assert [e[:2] for e in emitted] == [
        (30, "ENTRY_CONFIRMED"),
        (33, "ENTRY_CONFIRMED"),
    ]


def test_anchor_invalidation_ends_the_setup() -> None:
    """Invalidating the anchoring AOI emits AOI_INVALIDATED."""
    frames = scenarios.long_a_frames(scenarios.WorldOptions(sweep=False))
    factory = frame_testing.FrameFactory()
    for index in range(31, len(frames)):
        frame = frames[index]
        snaps = dict(frame.snapshots)
        fvg = factory.aoi(
            "FVG",
            "bullish",
            "92",
            "94",
            confirmed_index=10,
            aoi_id="fvg-5m-long",
            state=structures.AoiState.INVALIDATED,
            invalidated_at_ms=factory.close(31),
        )
        snaps[("fvg_ifvg", TF.M5)] = factory.snapshot(
            "fvg_ifvg", TF.M5, index, aois=[fvg]
        )
        frames[index] = dataclasses.replace(frame, snapshots=snaps)
    emitted = _run(_processor(_definition(emit=_ALL_KINDS)), frames)
    assert emitted == [
        (30, "PRE_SIGNAL", "CONFIRMING"),
        (31, "AOI_INVALIDATED", "INVALIDATED"),
    ]


def test_duplicate_and_out_of_order_frames_emit_nothing_new() -> None:
    """Replayed and late frames never produce a second signal."""
    clean = scenarios.long_a_frames()
    proc = _processor(_definition())
    once = [
        s
        for f in clean
        for s in proc.step(f, evaluated_at_ms=f.close_ms).signals
    ]
    noisy_proc = _processor(_definition())
    noisy = [
        s
        for f in scenarios.with_duplicates(clean)
        for s in noisy_proc.step(f, evaluated_at_ms=f.close_ms).signals
    ]
    assert [s.signal_id for s in once] == [s.signal_id for s in noisy]
    assert len(once) == 1


def test_revision_of_a_fired_bar_is_audited_not_rewritten() -> None:
    """A corrected bar yields a revision notice and no new signal."""
    frames = scenarios.long_a_frames()
    proc = _processor(_definition())
    for frame in frames[:31]:
        proc.step(frame, evaluated_at_ms=frame.close_ms)
    factory = frame_testing.FrameFactory()
    corrected = dataclasses.replace(
        frames[30],
        candle=factory.candle(
            30, frame_testing.bar("95", "95.5", "93.5", "94.6"), revision=1
        ),
    )
    result = proc.step(corrected, evaluated_at_ms=corrected.close_ms)
    assert result.signals == ()
    assert result.outcome.revision is not None
    assert result.outcome.revision.new_revision == 1


def test_checkpoint_restore_continues_without_duplicates() -> None:
    """A restarted processor replays history without re-firing."""
    frames = scenarios.long_a_frames()
    single = _run(_processor(_definition()), frames)
    first = _processor(_definition())
    before = _run(first, frames[:31])
    checkpoint = setup_fsm.InstanceState.from_dict(first.state.to_dict())
    restarted = _processor(_definition())
    restarted.state = checkpoint
    after = _run(restarted, frames)
    assert before + after == single


def test_cancel_closes_open_setup() -> None:
    """Pausing a strategy cancels an open setup."""
    proc = _processor(_definition())
    for frame in scenarios.long_a_frames(scenarios.WorldOptions(sweep=False))[
        :31
    ]:
        proc.step(frame, evaluated_at_ms=frame.close_ms)
    assert proc.state.setup is not None
    outcome = proc.cancel(0, "paused")
    assert outcome.transitions[0].target is S.CANCELLED
    assert proc.state.setup is None


def test_signal_payload_traces_the_decision() -> None:
    """The entry signal carries versions, hashes, reasons and levels."""
    proc = _processor(_definition())
    signals = [
        s
        for f in scenarios.long_a_frames()
        for s in proc.step(f, evaluated_at_ms=f.close_ms).signals
    ]
    signal = signals[0]
    assert signal.aoi_ids == ("fvg-5m-long",)
    assert signal.range_ids == ("rng-1h-1",)
    assert signal.entry_price == decimal.Decimal("94.5")
    assert signal.stop_price == decimal.Decimal("92")
    assert signal.target_price == decimal.Decimal("110")
    assert signal.rule_version == _definition().definition_hash()
    assert signal.inputs_snapshot_hash.startswith("sha256:")
    assert signal.indicator_versions["fvg_ifvg"] == "1.0.0"
    assert signal.reason_tree["result"] == "TRUE"
    assert "binance:perp:BTCUSDT" in signal.message
    assert signal.dedupe_key.endswith("|ENTRY_CONFIRMED")
