# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Live strategy runtime: frames in, recorded and published signals out.

The integrator feeds every closed signal-timeframe ``BarFrame`` produced by
the indicator engine to ``on_frame``. For each ACTIVE version whose
universe contains the instrument, the runtime runs the shared
``InstanceProcessor`` (same evaluator and state machine as backtests),
records new signals idempotently, publishes them and checkpoints the setup
state. Duplicate or out-of-order frames never produce a second signal.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence

from joseph.domain import clock as clock_port
from joseph.domain import structures
from joseph.signals.app import service as signal_service
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.app import ports
from joseph.strategy.app import processor
from joseph.strategy.domain import ast
from joseph.strategy.domain import catalog
from joseph.strategy.domain import compiler
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.domain import versions

_LOG = logging.getLogger(__name__)

_TriState = tristate.TriState


@dataclasses.dataclass(frozen=True, slots=True)
class ActivePlan:
    """A compiled ACTIVE version.

    Attributes:
        version: Stored version.
        plan: Compiled plan.
    """

    version: versions.StrategyVersion
    plan: compiler.CompiledStrategy


def universe_allows(
    version: versions.StrategyVersion, instrument_id: str
) -> bool:
    """Checks an instrument against a version's universe and activation.

    Args:
        version: Strategy version.
        instrument_id: Canonical instrument id.

    Returns:
        True when the instrument may be evaluated.
    """
    universe: ast.Universe = version.definition.universe
    venue = instrument_id.split(":", 1)[0]
    if venue not in {v.value for v in universe.venues}:
        return False
    if universe.whitelist and instrument_id not in universe.whitelist:
        return False
    if instrument_id in universe.blacklist:
        return False
    activation = version.activation
    return not (
        activation is not None
        and activation.instruments
        and instrument_id not in activation.instruments
    )


class StrategyRuntime:
    """Evaluates ACTIVE strategies on closed frames.

    Attributes:
        account_scope: Account scope stamped on instance keys.
    """

    def __init__(
        self,
        *,
        repository: ports.StrategyRepository,
        signals: signal_service.SignalService,
        checkpoints: ports.CheckpointRepository,
        clock: clock_port.Clock,
        publisher: ports.SignalPublisher | None = None,
        qualification: ports.QualificationGate | None = None,
        market: ports.MarketContextProvider | None = None,
        vocabulary: catalog.Vocabulary,
        account_scope: str = "*",
    ) -> None:
        """Initializes the runtime.

        Args:
            repository: Version store.
            signals: Signal service (idempotent recording).
            checkpoints: Setup checkpoint store.
            clock: Time source.
            publisher: Receives newly recorded signals.
            qualification: Universe qualification source; without one,
                QUALIFIED_ONLY strategies block entries as UNKNOWN.
            market: Market context provider for MARKET operands.
            vocabulary: Kinds, events and values of the installed indicator
                plugins.
            account_scope: Account scope of instance keys.
        """
        self._repository = repository
        self._signals = signals
        self._checkpoints = checkpoints
        self._clock = clock
        self._publisher = publisher
        self._qualification = qualification
        self._market = market
        self._vocabulary = vocabulary
        self.account_scope = account_scope
        self._plans: dict[tuple[str, str], ActivePlan] = {}
        self._processors: dict[
            tuple[str, str, str], processor.InstanceProcessor
        ] = {}

    def refresh(self) -> list[ActivePlan]:
        """Reloads ACTIVE versions and cancels setups of removed ones.

        Returns:
            Active compiled plans (also used to configure the indicator
            engine via ``plan.requirements``).
        """
        fresh: dict[tuple[str, str], ActivePlan] = {}
        for version in self._repository.list_active():
            result = compiler.compile_strategy(
                version.definition, vocabulary=self._vocabulary
            )
            if result.plan is None:
                _LOG.warning(
                    "skipping invalid active version %s",
                    version.strategy_version_id,
                )
                continue
            fresh[(version.tenant_id, version.strategy_version_id)] = (
                ActivePlan(version=version, plan=result.plan)
            )
        now = self._clock.now_ms()
        for key in list(self._processors):
            tenant_id, version_id, _ = key
            if (tenant_id, version_id) not in fresh:
                instance = self._processors.pop(key)
                outcome = instance.cancel(now, "strategy paused or replaced")
                self._checkpoints.save(outcome.state, now)
        self._plans = fresh
        return list(fresh.values())

    def requirements(self) -> list[compiler.EngineRequirements]:
        """Lists the indicator requirements of active plans.

        Returns:
            Requirements per active plan.
        """
        return [active.plan.requirements for active in self._plans.values()]

    def _processor(
        self, active: ActivePlan, instrument_id: str
    ) -> processor.InstanceProcessor:
        """Returns (creating and restoring) an instance processor.

        Args:
            active: Active plan.
            instrument_id: Canonical instrument id.

        Returns:
            Processor.
        """
        version = active.version
        cache_key = (
            version.tenant_id,
            version.strategy_version_id,
            instrument_id,
        )
        found = self._processors.get(cache_key)
        if found is not None:
            return found
        key = setup_fsm.InstanceKey(
            tenant_id=version.tenant_id,
            strategy_version_id=version.strategy_version_id,
            account_scope=self.account_scope,
            instrument_id=instrument_id,
            direction=version.definition.direction,
        )
        mode = (
            version.activation.mode
            if version.activation is not None
            else version.definition.execution_mode
        )
        created = processor.InstanceProcessor(
            active.plan,
            version,
            key,
            mode=mode,
            state=self._checkpoints.load(key),
        )
        self._processors[cache_key] = created
        return created

    def _qualification_of(
        self, version: versions.StrategyVersion, instrument_id: str, at_ms: int
    ) -> tristate.TriState:
        """Returns the universe qualification for an instrument.

        Args:
            version: Strategy version.
            instrument_id: Canonical instrument id.
            at_ms: Bar close.

        Returns:
            Qualification truth value.
        """
        universe = version.definition.universe
        if universe.qualification == "ANY":
            return _TriState.TRUE
        if self._qualification is None:
            return _TriState.UNKNOWN
        return self._qualification.qualification(
            instrument_id, at_ms, universe.min_qualified_minutes
        )

    async def on_frame(
        self, instrument_id: str, frame: structures.BarFrame
    ) -> list[signal_domain.Signal]:
        """Processes one closed signal-timeframe frame.

        Args:
            instrument_id: Canonical instrument id of the frame.
            frame: Closed frame from the indicator engine.

        Returns:
            Signals recorded for the first time (already published).

        Raises:
            ValueError: If ``instrument_id`` is not the frame's instrument.
        """
        if str(frame.instrument_id) != instrument_id:
            raise ValueError("instrument_id does not match the frame")
        if not frame.candle.is_final:
            return []
        recorded: list[signal_domain.Signal] = []
        for active in list(self._plans.values()):
            definition = active.version.definition
            if definition.signal_timeframe != frame.timeframe:
                continue
            if not universe_allows(active.version, instrument_id):
                continue
            instance = self._processor(active, instrument_id)
            market = (
                None
                if self._market is None
                else self._market.context(instrument_id, frame.close_ms)
            )
            result = instance.step(
                frame,
                evaluated_at_ms=self._clock.now_ms(),
                market=market,
                qualification=self._qualification_of(
                    active.version, instrument_id, frame.close_ms
                ),
            )
            if result.outcome.revision is not None:
                _LOG.info(
                    "revised bar %s for %s: revision %d -> %d (fired signals "
                    "are kept)",
                    result.outcome.revision.bar_close_ms,
                    instrument_id,
                    result.outcome.revision.old_revision,
                    result.outcome.revision.new_revision,
                )
            self._checkpoints.save(instance.state, self._clock.now_ms())
            for signal in result.signals:
                if self._signals.record(signal):
                    recorded.append(signal)
                    if self._publisher is not None:
                        await self._publisher.publish(signal)
        return recorded

    def warm_up(
        self, instrument_id: str, frames: Sequence[structures.BarFrame]
    ) -> int:
        """Primes evaluation windows with historical frames, firing nothing.

        Unlike ``replay`` this never steps the setup state machine, so a new
        activation cannot emit alerts or orders for bars that closed before
        it went live.

        Args:
            instrument_id: Canonical instrument id.
            frames: Historical frames, oldest first.

        Returns:
            Number of (plan, frame) pairs offered to the windows; a window
            keeps only final frames.
        """
        added = 0
        for active in list(self._plans.values()):
            definition = active.version.definition
            if not universe_allows(active.version, instrument_id):
                continue
            instance = self._processor(active, instrument_id)
            for frame in frames:
                if (
                    str(frame.instrument_id) == instrument_id
                    and frame.timeframe == definition.signal_timeframe
                ):
                    instance.warm(frame)
                    added += 1
        return added

    def plans(self) -> list[ActivePlan]:
        """Returns the currently loaded active plans.

        Returns:
            Active plans (call ``refresh`` to reload).
        """
        return list(self._plans.values())

    async def replay(
        self, instrument_id: str, frames: Sequence[structures.BarFrame]
    ) -> list[signal_domain.Signal]:
        """Feeds historical frames in order (warm-up or recovery).

        Already-processed bars are ignored by the state machine but still
        populate evaluation windows.

        Args:
            instrument_id: Canonical instrument id.
            frames: Frames, oldest first.

        Returns:
            Signals recorded for the first time.

        Raises:
            ValueError: If a frame belongs to another instrument.
        """
        recorded: list[signal_domain.Signal] = []
        for frame in frames:
            recorded.extend(await self.on_frame(instrument_id, frame))
        return recorded
