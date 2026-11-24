# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Live signal pipeline.

Every ``interval_s`` the pipeline reloads ACTIVE strategy versions, selects
the instruments in each universe, keeps one incremental indicator engine per
``(instrument, signal timeframe)`` computing the union of all requirements,
feeds it newly closed candles and hands every closed frame to the strategy
runtime. New engines are warmed up from history with ``warm_up`` so no
signal is ever fired for a bar that closed before the pipeline saw it live.

Polling keeps one code path for every venue (native-WS and REST-polled
alike); signal latency is bounded by ``interval_s`` after a bar closes.
History windows count bars of the instrument's session calendar, so an
exchange series warms up over sessions, not over nights and weekends.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.indicators.sdk import engine as engine_lib
from joseph.market_data.app import service as market_service
from joseph.pipeline import frames as frames_lib
from joseph.pipeline import market_context
from joseph.pipeline import qualification
from joseph.strategy.app import runtime as runtime_lib
from joseph.strategy.domain import compiler

_LOG = logging.getLogger(__name__)

_FLUSH_GRACE_BARS = 2
"""Signal bars after which a frame is forced despite a stalled input."""

_INITIAL_LOOKBACK_BARS = 3
"""Bars fetched for a timeframe that has not been fed yet."""


@dataclasses.dataclass
class _EngineState:
    """Incremental engine of one ``(instrument, signal timeframe)``.

    Attributes:
        engine: Indicator engine.
        requirements: Requirements the engine was built for.
        last_end_ms: End (epoch ms) of the latest candle fed per timeframe.
    """

    engine: engine_lib.IndicatorEngine
    requirements: compiler.EngineRequirements
    last_end_ms: dict[timeframes.Timeframe, int]


@dataclasses.dataclass
class PipelineStats:
    """Counters of one pipeline pass.

    Attributes:
        plans: Active plans loaded.
        series: Engines advanced.
        frames: Frames delivered to the runtime.
        signals: Signals recorded.
        errors: Series that failed this pass.
    """

    plans: int = 0
    series: int = 0
    frames: int = 0
    signals: int = 0
    errors: int = 0


class LiveSignalPipeline:
    """Polling loop that feeds closed candles to the strategy runtime.

    One background task (``start``/``stop``) runs ``run_once`` every
    ``interval_s``. Engines live only in memory: a restart warms them up
    again from history, and the runtime's checkpoints keep setup state.

    Attributes:
        last_stats: Counters of the latest completed pass.
    """

    def __init__(
        self,
        runtime: runtime_lib.StrategyRuntime,
        provider: frames_lib.EngineFrameProvider,
        market_data: market_service.MarketDataService,
        *,
        gate: qualification.CachedQualificationGate,
        context: market_context.SnapshotMarketContext,
        clock: clock_lib.Clock,
        interval_s: float = 10.0,
        max_instruments_per_venue: int = 10,
    ) -> None:
        """Initializes the pipeline.

        Args:
            runtime: Strategy runtime.
            provider: Engine/candle provider shared with backtests.
            market_data: Market-data service.
            gate: Qualification gate (also used by the runtime).
            context: Market context recorder (also used by the runtime).
            clock: Time source.
            interval_s: Seconds to wait after a pass before the next one.
            max_instruments_per_venue: Top-ranked instruments taken per venue
                when a universe has neither activation instruments nor a
                whitelist.
        """
        self._runtime = runtime
        self._provider = provider
        self._market = market_data
        self._gate = gate
        self._context = context
        self._clock = clock
        self._interval_s = interval_s
        self._max_per_venue = max_instruments_per_venue
        self._engines: dict[tuple[str, timeframes.Timeframe], _EngineState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.last_stats = PipelineStats()

    def _instruments_for(self, active: runtime_lib.ActivePlan) -> list[str]:
        """Selects instruments to evaluate for one active plan.

        Explicit activation instruments win, then the universe whitelist,
        then the top-ranked instruments of each venue's qualification
        report (QUALIFIED only when the universe requires it).

        Args:
            active: Active plan.

        Returns:
            Canonical instrument ids.
        """
        version = active.version
        if version.activation is not None and version.activation.instruments:
            return list(version.activation.instruments)
        universe = version.definition.universe
        if universe.whitelist:
            return list(universe.whitelist)
        chosen: list[str] = []
        for venue in universe.venues:
            ranked = self._gate.ranked(instruments.Venue(venue.value))
            if universe.qualification == "QUALIFIED_ONLY":
                ranked = [i for i in ranked if self._gate.is_qualified(i)]
            chosen.extend(
                i
                for i in ranked[: self._max_per_venue]
                if i not in universe.blacklist
            )
        return chosen

    async def _start_series(
        self,
        instrument_id: str,
        requirements: compiler.EngineRequirements,
    ) -> _EngineState:
        """Creates an engine and warms the runtime with history.

        The warm-up frames go to ``StrategyRuntime.warm_up``, which fills
        evaluation windows without stepping the setup state machine, so
        they fire no signal.

        Args:
            instrument_id: Canonical instrument id.
            requirements: Merged requirements.

        Returns:
            Engine state positioned at the latest closed bars.
        """
        parsed = instruments.InstrumentId.parse(instrument_id)
        tf = requirements.signal_timeframe
        engine = await self._provider.engine_for(parsed, requirements)
        calendar = engine.calendar
        now = self._clock.now_ms()
        start = calendar.shift(
            tf, calendar.floor(tf, now), -max(requirements.history_bars, 1)
        )
        candles = await self._provider.candles(engine, parsed, tf, start, now)
        warm = engine.run(candles)
        self._runtime.warm_up(instrument_id, warm)
        last_end = {
            series_tf: rows[-1].end_ms
            for series_tf, rows in candles.items()
            if rows
        }
        return _EngineState(engine, requirements, last_end)

    async def _advance(
        self, instrument_id: str, state: _EngineState, stats: PipelineStats
    ) -> None:
        """Feeds newly closed candles and delivers frames.

        Args:
            instrument_id: Canonical instrument id.
            state: Engine state; ``last_end_ms`` is advanced in place.
            stats: Pass counters, updated in place.
        """
        now = self._clock.now_ms()
        calendar = state.engine.calendar
        fresh: list[market.Candle] = []
        for tf in state.engine.required_timeframes:
            since = state.last_end_ms.get(
                tf,
                calendar.shift(
                    tf, calendar.floor(tf, now), -_INITIAL_LOOKBACK_BARS
                ),
            )
            rows = await self._market.get_candles(instrument_id, tf, since, now)
            fresh.extend(
                c
                for c in rows
                if c.is_final and c.end_ms > state.last_end_ms.get(tf, 0)
            )
        # Event-time order, as in ``IndicatorEngine.run``: by close, and the
        # shorter timeframe first when two candles close together.
        fresh.sort(key=lambda c: (c.end_ms, c.timeframe.duration_ms))
        delivered = []
        for candle in fresh:
            delivered.extend(state.engine.on_candle(candle))
            state.last_end_ms[candle.timeframe] = max(
                candle.end_ms, state.last_end_ms.get(candle.timeframe, 0)
            )
        signal_tf = state.requirements.signal_timeframe
        # A wall-clock grace (nominal bar lengths), not a bar boundary: work
        # stalled on a missing input this long after its close is forced.
        delivered.extend(
            state.engine.flush(now - _FLUSH_GRACE_BARS * signal_tf.duration_ms)
        )
        # The market context is best effort: without a snapshot the
        # evaluator sees older (or no) observations, and frames still go out.
        with contextlib.suppress(Exception):
            self._context.record(await self._market.get_snapshot(instrument_id))
        for frame in delivered:
            stats.frames += 1
            recorded = await self._runtime.on_frame(instrument_id, frame)
            stats.signals += len(recorded)

    async def run_once(self) -> PipelineStats:
        """Executes one pass over every active universe.

        Reloads the active plans, refreshes the qualification cache of their
        venues, drops engines no plan targets any more, (re)builds engines
        whose merged requirements changed and advances every target series.
        A failing series is logged and counted; the others still run.

        Returns:
            Counters of the pass.
        """
        stats = PipelineStats()
        plans = self._runtime.refresh()
        stats.plans = len(plans)
        if not plans:
            self._engines.clear()
            self.last_stats = stats
            return stats
        venues: set[instruments.Venue] = set()
        for active in plans:
            for venue in active.version.definition.universe.venues:
                venues.add(instruments.Venue(venue.value))
        await self._gate.refresh(venues)
        merged = frames_lib.merge_requirements(
            active.plan.requirements for active in plans
        )
        targets: dict[
            tuple[str, timeframes.Timeframe], compiler.EngineRequirements
        ] = {}
        for active in plans:
            tf = active.version.definition.signal_timeframe
            for instrument_id in self._instruments_for(active):
                targets[(instrument_id, tf)] = merged[tf]
        for key in list(self._engines):
            if key not in targets:
                del self._engines[key]
        for key, requirements in targets.items():
            instrument_id = key[0]
            try:
                state = self._engines.get(key)
                if state is None or state.requirements != requirements:
                    state = await self._start_series(
                        instrument_id, requirements
                    )
                    self._engines[key] = state
                await self._advance(instrument_id, state, stats)
                stats.series += 1
            except Exception:  # noqa: BLE001 - isolation point: one failing series must not stop the pass; logged with the traceback
                stats.errors += 1
                _LOG.exception("pipeline pass failed for %s", instrument_id)
        self.last_stats = stats
        return stats

    async def _loop(self) -> None:
        """Runs passes until ``stop`` sets the stopping event.

        The wait between passes ends early when the event is set, so a stop
        takes effect after the current pass, without cancelling it.
        """
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - isolation point: a failed pass must not end the loop; logged with the traceback
                _LOG.exception("signal pipeline pass failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._interval_s
                )

    def start(self) -> None:
        """Starts the background loop (idempotent).

        Must be called from inside the running event loop. A loop that has
        already finished is replaced by a new one.
        """
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stops the background loop and waits for it.

        The current pass runs to completion. If the task was cancelled
        elsewhere (for example at event-loop shutdown), the cancellation is
        absorbed here.
        """
        self._stopping.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
