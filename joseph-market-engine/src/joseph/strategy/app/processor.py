"""Per-instance processing shared by the live runtime and backtests.

``InstanceProcessor`` owns the frame window and the setup state of one
``(strategy version, instrument, direction)`` instance. Live trading,
paper/shadow forwarding and backtests all call ``step`` with the same
frames, so they produce the same signals for the same confirmed bars.
"""

from __future__ import annotations

import collections
import dataclasses

from joseph.domain import decimals
from joseph.domain import structures
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.domain import compiler
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.domain import versions

_KIND_TEXT = {
    "WATCHING_AOI": "Watching AOI",
    "PRE_SIGNAL": "Pre-signal",
    "ENTRY_CONFIRMED": "Entry confirmed",
    "ENTRY_BLOCKED": "Entry blocked",
    "AOI_INVALIDATED": "AOI invalidated",
    "SETUP_EXPIRED": "Setup expired",
}


@dataclasses.dataclass(frozen=True, slots=True)
class StepResult:
    """Outcome of processing one frame.

    Attributes:
        signals: Signals built from the emissions (not yet recorded).
        outcome: State machine outcome (``None`` before evaluation).
        evaluation: Evaluation, when the frame was new.
    """

    signals: tuple[signal_domain.Signal, ...]
    outcome: setup_fsm.StepOutcome
    evaluation: evaluator.Evaluation | None


def indicator_versions(frame: structures.BarFrame) -> dict[str, str]:
    """Collects the plugin versions visible in a frame.

    Args:
        frame: Frame.

    Returns:
        Mapping of plugin id to version (``tf=version`` pairs joined when a
        plugin runs different versions per timeframe).
    """
    found: dict[str, set[str]] = {}
    for (plugin, tf), snap in sorted(
        frame.snapshots.items(), key=lambda item: (item[0][0], item[0][1].value)
    ):
        found.setdefault(plugin, set()).add(
            f"{tf.value}={snap.indicator_version}"
        )
    result: dict[str, str] = {}
    for plugin, entries in found.items():
        plain = {entry.split("=", 1)[1] for entry in entries}
        result[plugin] = (
            plain.pop() if len(plain) == 1 else ",".join(sorted(entries))
        )
    return result


def build_signal(
    *,
    version: versions.StrategyVersion,
    key: setup_fsm.InstanceKey,
    emission: setup_fsm.Emission,
    evaluation: evaluator.Evaluation,
    mode: str,
    evaluated_at_ms: int,
) -> signal_domain.Signal:
    """Builds the contract signal for one emission.

    Args:
        version: Strategy version.
        key: Instance key.
        emission: State machine emission.
        evaluation: Evaluation of the bar.
        mode: Activation mode.
        evaluated_at_ms: Evaluation time.

    Returns:
        The signal.
    """
    dedupe = signal_domain.dedupe_key(
        tenant_id=key.tenant_id,
        strategy_version_id=key.strategy_version_id,
        account_scope=key.account_scope,
        instrument_id=key.instrument_id,
        direction=key.direction,
        setup_id=emission.setup_id,
        kind=emission.kind,
    )
    assessment = evaluation.risk
    frame = evaluation.frames[-1]
    levels = (
        f"entry {decimals.format_optional(assessment.entry)}, "
        f"stop {decimals.format_optional(assessment.stop)}, "
        f"target {decimals.format_optional(assessment.target)}, "
        f"net R:R {decimals.format_optional(assessment.rr)}"
    )
    message = (
        f"{_KIND_TEXT.get(emission.kind, emission.kind)}: {key.direction} "
        f"{key.instrument_id} {frame.timeframe.value} "
        f"({key.strategy_version_id}); {levels}; {emission.reason}"
    )
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(dedupe),
        dedupe_key=dedupe,
        tenant_id=key.tenant_id,
        strategy_id=version.strategy_id,
        strategy_version_id=key.strategy_version_id,
        setup_id=emission.setup_id,
        instrument_id=key.instrument_id,
        direction=key.direction,
        timeframe=frame.timeframe.value,
        kind=emission.kind,
        setup_state=emission.setup_state.value,
        severity=signal_domain.SEVERITY_BY_KIND.get(emission.kind, "INFO"),
        bar_start_ms=evaluation.bar_start_ms,
        bar_close_ms=evaluation.bar_close_ms,
        evaluated_at_ms=evaluated_at_ms,
        inputs_snapshot_hash=signal_domain.inputs_snapshot_hash(
            evaluation.frames
        ),
        rule_version=version.definition_hash,
        evaluator_version=evaluator.EVALUATOR_VERSION,
        indicator_versions=indicator_versions(frame),
        reason_tree=evaluation.reason_tree.to_contract(),
        aoi_ids=evaluation.aoi_ids,
        range_ids=evaluation.range_ids,
        entry_price=assessment.entry,
        stop_price=assessment.stop,
        target_price=assessment.target,
        rr=assessment.rr,
        risk_status=emission.risk_status,
        risk_reasons=emission.risk_reasons or assessment.reasons,
        mode=mode,
        data_source=frame.feed_state.value,
        message=message,
    )


class InstanceProcessor:
    """Window, evaluation and state machine for one strategy instance.

    Attributes:
        plan: Compiled strategy.
        version: Strategy version.
        key: Instance key.
        mode: Activation mode stamped on signals.
        policy: Lifecycle policy.
        state: Current instance state.
    """

    def __init__(
        self,
        plan: compiler.CompiledStrategy,
        version: versions.StrategyVersion,
        key: setup_fsm.InstanceKey,
        *,
        mode: str,
        state: setup_fsm.InstanceState | None = None,
    ) -> None:
        """Initializes the processor.

        Args:
            plan: Compiled strategy.
            version: Strategy version.
            key: Instance key.
            mode: Activation mode stamped on signals.
            state: Restored checkpoint, if any.
        """
        self.plan = plan
        self.version = version
        self.key = key
        self.mode = mode
        self.policy = setup_fsm.LifecyclePolicy.from_plan(plan)
        self.state = state or setup_fsm.InstanceState(key=key)
        self._window: collections.deque[structures.BarFrame] = (
            collections.deque(maxlen=plan.window_bars)
        )

    @property
    def window(self) -> tuple[structures.BarFrame, ...]:
        """Returns the frames currently held, oldest first."""
        return tuple(self._window)

    def warm(self, frame: structures.BarFrame) -> None:
        """Adds a historical frame to the window without evaluating it.

        Used after activation or restart so lookback conditions have history
        while no signal is ever fired for a bar that closed in the past.

        Args:
            frame: Closed historical frame.
        """
        if frame.candle.is_final:
            self._remember(frame)

    def _remember(self, frame: structures.BarFrame) -> None:
        """Adds a frame to the window idempotently.

        Newer bars append; a higher revision of the latest bar replaces it;
        duplicates and older bars are ignored.

        Args:
            frame: Incoming frame.
        """
        if not self._window or frame.close_ms > self._window[-1].close_ms:
            self._window.append(frame)
            return
        last = self._window[-1]
        if (
            frame.close_ms == last.close_ms
            and frame.candle.revision > last.candle.revision
        ):
            self._window[-1] = frame

    def step(
        self,
        frame: structures.BarFrame,
        *,
        evaluated_at_ms: int,
        market: evaluator.MarketContext | None = None,
        qualification: tristate.TriState = tristate.TriState.TRUE,
    ) -> StepResult:
        """Processes one closed signal-timeframe frame.

        Args:
            frame: Frame of this instance's instrument.
            evaluated_at_ms: Evaluation time (frame close in backtests).
            market: Market context for MARKET operands.
            qualification: Universe qualification of the instrument.

        Returns:
            Signals built for this bar plus the state machine outcome.

        Raises:
            ValueError: If the frame belongs to another series.
        """
        if (
            str(frame.instrument_id) != self.key.instrument_id
            or frame.timeframe != self.plan.definition.signal_timeframe
        ):
            raise ValueError("frame does not belong to this instance")
        self._remember(frame)
        early = setup_fsm.precheck(
            self.state, frame.close_ms, frame.candle.revision
        )
        if early is not None:
            self.state = early.state
            return StepResult(signals=(), outcome=early, evaluation=None)
        window = [f for f in self._window if f.close_ms <= frame.close_ms]
        evaluation = evaluator.evaluate(
            self.plan, window, market, evaluated_at_ms
        )
        outcome = setup_fsm.step(
            self.state,
            evaluation,
            self.policy,
            qualification=qualification,
            candle_revision=frame.candle.revision,
        )
        self.state = outcome.state
        signals = tuple(
            build_signal(
                version=self.version,
                key=self.key,
                emission=emission,
                evaluation=evaluation,
                mode=self.mode,
                evaluated_at_ms=evaluated_at_ms,
            )
            for emission in outcome.emissions
        )
        return StepResult(
            signals=signals, outcome=outcome, evaluation=evaluation
        )

    def cancel(self, at_ms: int, reason: str) -> setup_fsm.StepOutcome:
        """Cancels the open setup.

        Args:
            at_ms: Cancellation time.
            reason: Explanation.

        Returns:
            The cancellation outcome.
        """
        outcome = setup_fsm.cancel(self.state, at_ms, reason)
        self.state = outcome.state
        return outcome
