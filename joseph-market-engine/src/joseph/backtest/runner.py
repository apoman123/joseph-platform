# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Backtest runner using the production compiler, evaluator and FSM.

Bars are counted on the instrument's session calendar: an exchange backtest
evaluates session bars only, and nights, weekends and holidays are never
missing bars.
"""

from __future__ import annotations

import dataclasses
import decimal
import itertools
from collections.abc import Collection
from collections.abc import Sequence

from joseph.backtest import ports
from joseph.backtest import simulator
from joseph.domain import errors
from joseph.domain import ids
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import structures
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.app import ports as strategy_ports
from joseph.strategy.app import processor
from joseph.strategy.domain import compiler
from joseph.strategy.domain import errors as strategy_errors
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import setup_fsm
from joseph.strategy.domain import tristate
from joseph.strategy.domain import versions

MAX_RANGE_MS = 400 * 86_400_000
"""Longest backtest range accepted (400 days, in ms)."""

# Least trustworthy first: ``worst_feed`` reports the first state present.
_FEED_PRIORITY = (
    quality.FeedState.STALE,
    quality.FeedState.UNAVAILABLE,
    quality.FeedState.CONNECTING,
    quality.FeedState.DEMO,
    quality.FeedState.REPLAY,
    quality.FeedState.POLL,
    quality.FeedState.LIVE,
)


@dataclasses.dataclass(frozen=True, slots=True)
class BacktestRequest:
    """Backtest parameters (contract ``BacktestRequest``).

    Attributes:
        version: Strategy version number.
        instrument_id: Canonical instrument id.
        from_ms: Inclusive start of the evaluated range (bar start), epoch
            ms.
        to_ms: Exclusive end, epoch ms; at most ``MAX_RANGE_MS`` after
            ``from_ms``.
        fee_bps: Fee per side in basis points (``None`` uses the
            strategy's fee estimate, or 0).
        slippage_bps: Adverse slippage in basis points on every fill except
            target exits.
        max_hold_bars: Signal bars held, entry bar included, before a
            timeout exit.
    """

    version: int
    instrument_id: str
    from_ms: int
    to_ms: int
    fee_bps: decimal.Decimal | None = None
    slippage_bps: decimal.Decimal = decimal.Decimal(2)
    max_hold_bars: int = 96


@dataclasses.dataclass(frozen=True, slots=True)
class BacktestReport:
    """Backtest result (contract ``BacktestReport``).

    Attributes:
        backtest_id: Deterministic id of the run parameters.
        strategy_version_id: Evaluated version.
        instrument_id: Instrument.
        from_ms: Inclusive start, epoch ms.
        to_ms: Exclusive end, epoch ms.
        bars_evaluated: Signal bars evaluated inside the range.
        signals: Signals emitted by bars inside the range.
        trades: Simulated trades.
        metrics: Aggregates.
        missing_bars: Session-calendar bars missing between the in-range
            frames.
        feed_state: Worst provenance of the in-range frames
            (``UNAVAILABLE`` when there are none).
        assumptions: Simulation assumptions.
        evaluator_version: Evaluator semantics version.
    """

    backtest_id: str
    strategy_version_id: str
    instrument_id: str
    from_ms: int
    to_ms: int
    bars_evaluated: int
    signals: tuple[signal_domain.Signal, ...]
    trades: tuple[simulator.Trade, ...]
    metrics: simulator.Metrics
    missing_bars: int
    feed_state: quality.FeedState
    assumptions: tuple[str, ...]
    evaluator_version: str

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "backtest_id": self.backtest_id,
            "strategy_version_id": self.strategy_version_id,
            "instrument_id": self.instrument_id,
            "from_ms": self.from_ms,
            "to_ms": self.to_ms,
            "bars_evaluated": self.bars_evaluated,
            "signals": [s.to_contract() for s in self.signals],
            "trades": [t.to_contract() for t in self.trades],
            "metrics": self.metrics.to_contract(),
            "missing_bars": self.missing_bars,
            "feed_state": self.feed_state.value,
            "assumptions": list(self.assumptions),
            "evaluator_version": self.evaluator_version,
        }


def clean_frames(
    frames: Sequence[structures.BarFrame],
    instrument_id: str,
    plan: compiler.CompiledStrategy,
) -> list[structures.BarFrame]:
    """Keeps one final frame per signal bar of one series, oldest first.

    Frames of other instruments or timeframes and non-final frames are
    dropped; of several frames for one bar, the highest revision wins.

    Args:
        frames: Provider output.
        instrument_id: Expected instrument.
        plan: Compiled strategy.

    Returns:
        Closed frames of the series, oldest first, one per bar.
    """
    best: dict[int, structures.BarFrame] = {}
    for frame in frames:
        if (
            str(frame.instrument_id) != instrument_id
            or frame.timeframe != plan.definition.signal_timeframe
            or not frame.candle.is_final
        ):
            continue
        current = best.get(frame.close_ms)
        if current is None or frame.candle.revision > current.candle.revision:
            best[frame.close_ms] = frame
    return [best[k] for k in sorted(best)]


def count_missing(frames: Sequence[structures.BarFrame]) -> int:
    """Counts missing bars between consecutive frames.

    Args:
        frames: Frames of one series, oldest first.

    Returns:
        Number of missing bars (on the instrument's session calendar).
    """
    missing = 0
    for previous, frame in itertools.pairwise(frames):
        calendar = sessions.for_instrument(frame.instrument_id)
        missing += calendar.bars_between(
            frame.timeframe, previous.candle.end_ms, frame.candle.start_ms
        )
    return missing


def worst_feed(frames: Sequence[structures.BarFrame]) -> quality.FeedState:
    """Returns the least trustworthy provenance among frames.

    Args:
        frames: Frames.

    Returns:
        Feed state (LIVE when every frame is live; UNAVAILABLE if empty).
    """
    if not frames:
        return quality.FeedState.UNAVAILABLE
    states = {frame.feed_state for frame in frames}
    for state in _FEED_PRIORITY:
        if state in states:
            return state
    return quality.FeedState.LIVE


class BacktestRunner:
    """Runs a stored version over historical frames.

    Attributes:
        provider: Historical frame provider.
    """

    def __init__(
        self,
        provider: ports.FrameProvider,
        *,
        available_plugins: Collection[str] | None = None,
        market: strategy_ports.MarketContextProvider | None = None,
    ) -> None:
        """Initializes the runner.

        Args:
            provider: Historical frame provider.
            available_plugins: Registered indicator plugins.
            market: Optional market context provider.
        """
        self.provider = provider
        self._available = available_plugins
        self._market = market

    async def run(
        self, version: versions.StrategyVersion, request: BacktestRequest
    ) -> BacktestReport:
        """Runs the backtest.

        Args:
            version: Strategy version.
            request: Parameters.

        Returns:
            The report.

        Raises:
            errors.ValidationFailedError: If the range is empty, longer than
                ``MAX_RANGE_MS`` or starts before the instrument's calendar,
                or the instrument is malformed, untradable or outside the
                strategy universe.
            strategy_errors.StrategyValidationError: If the version does not
                compile against the current plugin catalog.
        """
        if request.to_ms <= request.from_ms:
            raise errors.ValidationFailedError("to_ms must be after from_ms")
        if request.to_ms - request.from_ms > MAX_RANGE_MS:
            raise errors.ValidationFailedError("range exceeds 400 days")
        try:
            instrument = instruments.InstrumentId.parse(request.instrument_id)
        except ValueError as err:
            raise errors.ValidationFailedError(str(err)) from err
        reason = compiler.untradable_reason(request.instrument_id)
        if reason is not None:
            raise errors.ValidationFailedError(reason)
        definition = version.definition
        venues = {v.value for v in definition.universe.venues}
        if instrument.venue.value not in venues:
            raise errors.ValidationFailedError(
                f"{request.instrument_id} is outside the strategy universe"
            )
        result = compiler.compile_strategy(
            definition, available_plugins=self._available
        )
        if result.plan is None:
            raise strategy_errors.StrategyValidationError(
                "version is not valid against the current catalog",
                [issue.to_contract() for issue in result.validation.errors],
            )
        plan = result.plan
        calendar = sessions.for_instrument(instrument)
        try:
            calendar.floor(definition.signal_timeframe, request.from_ms)
        except ValueError as err:
            raise errors.ValidationFailedError(
                f"from_ms precedes the {calendar.calendar_id.value} calendar: "
                f"{err}"
            ) from err
        raw = await self.provider.frames(
            instrument,
            definition.signal_timeframe,
            plan.requirements,
            request.from_ms,
            request.to_ms,
        )
        frames = clean_frames(raw, request.instrument_id, plan)
        key = setup_fsm.InstanceKey(
            tenant_id=version.tenant_id,
            strategy_version_id=version.strategy_version_id,
            account_scope="backtest",
            instrument_id=request.instrument_id,
            direction=definition.direction,
        )
        proc = processor.InstanceProcessor(
            plan, version, key, mode=definition.execution_mode
        )
        signals: list[signal_domain.Signal] = []
        evaluated = 0
        # Warm-up frames also step the processor, so its windows and setup
        # state are primed; their signals are discarded and only bars inside
        # the range are counted. Point-in-time qualification is not
        # replayed (see ``simulator.ASSUMPTIONS``).
        for frame in frames:
            market = (
                None
                if self._market is None
                else self._market.context(request.instrument_id, frame.close_ms)
            )
            step = proc.step(
                frame,
                evaluated_at_ms=frame.close_ms,
                market=market,
                qualification=tristate.TriState.TRUE,
            )
            if request.from_ms <= frame.candle.start_ms < request.to_ms:
                evaluated += 1
                signals.extend(step.signals)
        in_range = [
            f
            for f in frames
            if request.from_ms <= f.candle.start_ms < request.to_ms
        ]
        fee = request.fee_bps
        if fee is None:
            fee = definition.risk.fee_bps_estimate or decimal.Decimal(0)
        params = simulator.SimulationParams(
            fee_bps=fee,
            slippage_bps=request.slippage_bps,
            max_hold_bars=request.max_hold_bars,
        )
        trades = simulator.simulate(signals, frames, params)
        return BacktestReport(
            backtest_id=ids.stable_id(
                "bt",
                version.strategy_version_id,
                request.instrument_id,
                request.from_ms,
                request.to_ms,
                fee,
                request.slippage_bps,
                request.max_hold_bars,
            ),
            strategy_version_id=version.strategy_version_id,
            instrument_id=request.instrument_id,
            from_ms=request.from_ms,
            to_ms=request.to_ms,
            bars_evaluated=evaluated,
            signals=tuple(signals),
            trades=tuple(trades),
            metrics=simulator.compute_metrics(trades),
            missing_bars=count_missing(in_range),
            feed_state=worst_feed(in_range),
            assumptions=simulator.ASSUMPTIONS,
            evaluator_version=evaluator.EVALUATOR_VERSION,
        )
