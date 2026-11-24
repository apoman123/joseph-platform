# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Property tests: replay determinism, idempotency and no look-ahead."""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Sequence

import hypothesis
from hypothesis import strategies as st

from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.app import processor
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios

TF = timeframes.Timeframe
_SETTINGS = hypothesis.settings(max_examples=30, deadline=None)

_OPTIONS = st.builds(
    scenarios.WorldOptions,
    bars=st.just(40),
    entry_index=st.integers(min_value=12, max_value=37),
    bias=st.sampled_from([None, "BEARISH"]),
    in_zone=st.booleans(),
    touch=st.booleans(),
    sweep=st.booleans(),
    failure=st.booleans(),
    target=st.booleans(),
    entry_feed=st.sampled_from(
        [
            quality.FeedState.LIVE,
            quality.FeedState.POLL,
            quality.FeedState.STALE,
        ]
    ),
)


def _processor() -> processor.InstanceProcessor:
    """Builds a Long A processor.

    Returns:
        A PAPER processor for the tenant-01 BTCUSDT long instance.
    """
    definition = fixtures.strategy_definitions()[fixtures.LONG_A]
    plan = compiler.compile_strategy(definition).plan
    assert plan is not None
    version = fixtures.make_version(definition)
    return processor.InstanceProcessor(
        plan,
        version,
        setup_fsm.InstanceKey(
            "tenant-01",
            version.strategy_version_id,
            "*",
            "binance:perp:BTCUSDT",
            "LONG",
        ),
        mode="PAPER",
    )


def _signals(
    frames: Sequence[structures.BarFrame],
) -> list[signal_domain.Signal]:
    """Runs a fresh processor over frames, evaluating at each close.

    Args:
        frames: Frames in delivery order.

    Returns:
        The emitted signals, in order.
    """
    proc = _processor()
    signals: list[signal_domain.Signal] = []
    for frame in frames:
        signals.extend(proc.step(frame, evaluated_at_ms=frame.close_ms).signals)
    return signals


@_SETTINGS
@hypothesis.given(_OPTIONS)
def test_replay_is_deterministic(options: scenarios.WorldOptions) -> None:
    """The same frames always yield the same signals."""
    frames = scenarios.long_a_frames(options)
    assert _signals(frames) == _signals(frames)


@_SETTINGS
@hypothesis.given(_OPTIONS, st.integers(min_value=2, max_value=6))
def test_duplicates_and_late_frames_change_nothing(
    options: scenarios.WorldOptions,
    every: int,
) -> None:
    """Duplicated and out-of-order deliveries never add signals."""
    frames = scenarios.long_a_frames(options)
    clean = _signals(frames)
    noisy = _signals(scenarios.with_duplicates(frames, every))
    assert [s.signal_id for s in noisy] == [s.signal_id for s in clean]
    assert len({s.dedupe_key for s in clean}) == len(clean)


@_SETTINGS
@hypothesis.given(_OPTIONS)
def test_batch_equals_incremental(options: scenarios.WorldOptions) -> None:
    """Evaluating each prefix equals the processor's incremental result."""
    frames = scenarios.long_a_frames(options)
    proc = _processor()
    for k, frame in enumerate(frames):
        step = proc.step(frame, evaluated_at_ms=frame.close_ms)
        assert step.evaluation is not None
        batch = evaluator.evaluate(
            proc.plan, frames[: k + 1], evaluated_at_ms=frame.close_ms
        )
        assert batch.result is step.evaluation.result
        assert batch.reason_tree == step.evaluation.reason_tree


@_SETTINGS
@hypothesis.given(
    _OPTIONS,
    st.integers(min_value=5, max_value=35),
    st.randoms(use_true_random=False),
)
def test_future_bars_never_change_past_signals(
    options: scenarios.WorldOptions,
    cut: int,
    rng: random.Random,
) -> None:
    """Rewriting bars after ``cut`` leaves earlier signals untouched."""
    frames = scenarios.long_a_frames(options)
    factory = frame_testing.FrameFactory()
    altered = list(frames)
    for index in range(cut + 1, len(frames)):
        price = 90 + rng.randint(0, 30)
        spec = frame_testing.bar(
            str(price), str(price + 2), str(price - 2), str(price + 1)
        )
        altered[index] = dataclasses.replace(
            frames[index],
            candle=factory.candle(index, spec),
            events=tuple(
                factory.event(name, index)
                for name in rng.sample(
                    ["SSL_SWEEP", "SELLER_FAILURE", "BSL_SWEEP"],
                    rng.randint(0, 2),
                )
            ),
        )
    cutoff = frames[cut].close_ms

    def early(signals: list[signal_domain.Signal]) -> list[str]:
        """Selects ids of signals whose bar closes by the cut.

        Args:
            signals: Signals to filter.

        Returns:
            Their ids, in order.
        """
        return [s.signal_id for s in signals if s.bar_close_ms <= cutoff]

    assert early(_signals(frames)) == early(_signals(altered))


# ------------------------------------------------------- SEQUENCE oracle


def _sequence_oracle(
    events: Sequence[set[str]], pos: int, window: int, steps: Sequence[str]
) -> bool:
    """Brute-force reference for SEQUENCE semantics.

    Args:
        events: Event names per bar.
        pos: Evaluated bar.
        window: Window length in bars.
        steps: Ordered step names.

    Returns:
        Whether an assignment of strictly increasing distinct bars exists
        with the last step on ``pos``.
    """
    if steps[-1] not in events[pos]:
        return False
    start = pos - window + 1
    candidates = range(max(start, 0), pos)

    def place(step: int, after: int) -> bool:
        """Places ``steps[step]`` on a bar after ``after``.

        Args:
            step: Step index.
            after: Previous bar index.

        Returns:
            True when the remaining steps fit.
        """
        if step == len(steps) - 1:
            return True
        return any(
            steps[step] in events[b] and place(step + 1, b)
            for b in candidates
            if b > after
        )

    return place(0, start - 1)


@_SETTINGS
@hypothesis.given(
    st.lists(
        st.sets(
            st.sampled_from(["SSL_SWEEP", "CHOCH_BULL", "SELLER_FAILURE"]),
            max_size=3,
        ),
        min_size=12,
        max_size=12,
    ),
    st.integers(min_value=3, max_value=8),
)
def test_sequence_matches_brute_force_oracle(
    events: list[set[str]],
    window: int,
) -> None:
    """SEQUENCE equals an exhaustive search on complete windows."""
    steps = ["SSL_SWEEP", "CHOCH_BULL", "SELLER_FAILURE"]
    raw = fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()
    raw["when"] = {
        "op": "SEQUENCE",
        "within_bars": window,
        "steps": [{"name": s} for s in steps],
    }
    raw["risk"] = {
        "min_rr": "0",
        "stop_model": "SWING",
        "target_model": "FIXED_R",
        "fixed_r": "1",
    }
    plan = compiler.compile_strategy(ast.parse_definition(raw)).plan
    assert plan is not None
    factory = frame_testing.FrameFactory()
    frames = []
    for index, names in enumerate(events):
        snaps = [
            factory.snapshot(p, TF.M5, index)
            for p in ("liquidity_sweeps", "daily_bias", "order_flow_pressure")
        ]
        frames.append(
            factory.frame(
                index,
                frame_testing.bar("100", "101", "99", "100"),
                snaps,
                [factory.event(name, index) for name in sorted(names)],
            )
        )
    pos = len(frames) - 1
    result = evaluator.evaluate(plan, frames).result
    expected = _sequence_oracle(events, pos, window, steps)
    assert result.value == ("TRUE" if expected else "FALSE")
