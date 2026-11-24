# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests of the live strategy runtime."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
import sqlalchemy as sa

from joseph.domain import clock
from joseph.domain import identity
from joseph.domain import structures
from joseph.signals.adapters import memory as signal_memory
from joseph.signals.adapters import sql as signal_sql
from joseph.signals.app import ports as signal_ports
from joseph.signals.app import service as signal_service
from joseph.signals.domain import signal as signal_domain
from joseph.strategy.adapters import memory
from joseph.strategy.adapters import sql
from joseph.strategy.app import ports
from joseph.strategy.app import runtime
from joseph.strategy.app import version_service
from joseph.strategy.domain import tristate
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary

ALICE = identity.Principal("tenant-a", "alice")
BTC = "binance:perp:BTCUSDT"


class _Recorder:
    """Publisher collecting published signals.

    Attributes:
        published: Signals received.
    """

    def __init__(self) -> None:
        """Initializes an empty recorder."""
        self.published: list[signal_domain.Signal] = []

    async def publish(self, signal: signal_domain.Signal) -> None:
        """Records a signal.

        Args:
            signal: Signal to record.
        """
        self.published.append(signal)


class _Qualified:
    """Qualification gate returning a fixed answer.

    Attributes:
        answer: Truth value returned.
    """

    def __init__(self, answer: tristate.TriState) -> None:
        """Initializes the gate.

        Args:
            answer: Truth value returned.
        """
        self.answer = answer

    def qualification(
        self, instrument_id: str, at_ms: int, min_minutes: int
    ) -> tristate.TriState:
        """Returns the configured answer.

        Args:
            instrument_id: Instrument (unused).
            at_ms: Time (unused).
            min_minutes: Minimum minutes (unused).

        Returns:
            The answer.
        """
        del instrument_id, at_ms, min_minutes
        return self.answer


class _World:
    """Wires services and the runtime around shared stores.

    Attributes:
        clock: Manual clock shared by every component.
        repo: Strategy repository.
        checkpoints: Checkpoint repository the runtime resumes from.
        signals: Signal service.
        recorder: Publisher.
        gate: Qualification gate.
        versions: Version service.
        runtime: Runtime built at construction.
    """

    def __init__(
        self,
        manual_clock: clock.ManualClock,
        *,
        engine: sa.Engine | None = None,
        qualification: tristate.TriState = tristate.TriState.TRUE,
    ) -> None:
        """Builds the world.

        Args:
            manual_clock: Clock shared by every component.
            engine: SQLite engine for SQL adapters (memory when None).
            qualification: Answer of the qualification gate.
        """
        self.clock = manual_clock
        if engine is None:
            self.repo: ports.StrategyRepository = (
                memory.InMemoryStrategyRepository()
            )
            self.checkpoints: ports.CheckpointRepository = (
                memory.InMemoryCheckpointRepository()
            )
            signal_repo: signal_ports.SignalRepository = (
                signal_memory.InMemorySignalRepository()
            )
        else:
            sql.create_schema(engine)
            signal_sql.create_schema(engine)
            self.repo = sql.SqlStrategyRepository(engine)
            self.checkpoints = sql.SqlCheckpointRepository(engine)
            signal_repo = signal_sql.SqlSignalRepository(engine)
        self.signals = signal_service.SignalService(signal_repo)
        self.recorder = _Recorder()
        self.gate = _Qualified(qualification)
        self.versions = version_service.StrategyVersionService(
            self.repo, manual_clock, vocabulary=vocabulary.VOCABULARY
        )
        self.runtime = self.new_runtime()

    def new_runtime(self) -> runtime.StrategyRuntime:
        """Builds a runtime over the shared stores (simulates a restart).

        Returns:
            A runtime that has not loaded any strategy yet.
        """
        return runtime.StrategyRuntime(
            repository=self.repo,
            signals=self.signals,
            checkpoints=self.checkpoints,
            clock=self.clock,
            publisher=self.recorder,
            qualification=self.gate,
            vocabulary=vocabulary.VOCABULARY,
        )

    def activate_zone_long(self, instruments: tuple[str, ...] = ()) -> None:
        """Creates and activates the zone-touch rule in PAPER mode.

        Args:
            instruments: Instruments to activate on; empty for the universe.
        """
        definition = scenarios.zone_long_definition()
        self.versions.create(ALICE, definition)
        self.versions.activate(
            ALICE, definition.strategy_id, 1, "PAPER", instruments
        )
        self.runtime.refresh()


async def _feed(
    world: _World,
    runtime_: runtime.StrategyRuntime,
    frames: Sequence[structures.BarFrame],
) -> list[signal_domain.Signal]:
    """Feeds BTCUSDT frames with the clock 100 ms after each close.

    Args:
        world: World whose clock is moved.
        runtime_: Runtime to feed.
        frames: Frames in delivery order.

    Returns:
        The signals the runtime recorded, in order.
    """
    out = []
    for frame in frames:
        world.clock.current_ms = frame.close_ms + 100
        out.extend(await runtime_.on_frame(BTC, frame))
    return out


@pytest.mark.parametrize("backend", ["memory", "sql"])
async def test_runtime_records_and_publishes_once(
    backend: str,
    engine: sa.Engine,
    manual_clock: clock.ManualClock,
) -> None:
    """The entry is recorded, published and queryable exactly once."""
    world = _World(manual_clock, engine=engine if backend == "sql" else None)
    world.activate_zone_long()
    frames = scenarios.zone_long_frames()
    recorded = await _feed(world, world.runtime, frames)
    assert [s.kind for s in recorded] == ["ENTRY_CONFIRMED"]
    assert recorded[0].mode == "PAPER"
    assert recorded[0].tenant_id == "tenant-a"
    assert world.recorder.published == recorded
    again = await _feed(world, world.runtime, scenarios.with_duplicates(frames))
    assert again == []
    stored = world.signals.query(ALICE, signal_ports.SignalFilter())
    assert [s.signal_id for s in stored] == [recorded[0].signal_id]
    other = identity.Principal("tenant-b", "bob")
    assert world.signals.query(other, signal_ports.SignalFilter()) == []


@pytest.mark.parametrize("backend", ["memory", "sql"])
async def test_restart_resumes_from_checkpoint(
    backend: str,
    engine: sa.Engine,
    manual_clock: clock.ManualClock,
) -> None:
    """A restarted runtime replays history without duplicate signals."""
    world = _World(manual_clock, engine=engine if backend == "sql" else None)
    world.activate_zone_long()
    frames = scenarios.zone_long_frames()
    first = await _feed(world, world.runtime, frames[:31])
    restarted = world.new_runtime()
    restarted.refresh()
    second = await _feed(world, restarted, frames)
    assert [s.kind for s in first] == ["ENTRY_CONFIRMED"]
    assert second == []


async def test_universe_and_activation_scope(
    manual_clock: clock.ManualClock,
) -> None:
    """Instruments outside the activation list are not evaluated."""
    world = _World(manual_clock)
    world.activate_zone_long(instruments=("binance:perp:ETHUSDT",))
    assert await _feed(world, world.runtime, scenarios.zone_long_frames()) == []
    eth = scenarios.zone_long_frames(instrument_id="binance:perp:ETHUSDT")
    recorded = []
    for frame in eth:
        manual_clock.current_ms = frame.close_ms + 100
        recorded.extend(
            await world.runtime.on_frame("binance:perp:ETHUSDT", frame)
        )
    assert [s.instrument_id for s in recorded] == ["binance:perp:ETHUSDT"]
    bitget = scenarios.zone_long_frames(instrument_id="bitget:perp:BTCUSDT")
    for frame in bitget:
        assert await world.runtime.on_frame("bitget:perp:BTCUSDT", frame) == []


async def test_unknown_qualification_blocks_entries(
    manual_clock: clock.ManualClock,
) -> None:
    """QUALIFIED_ONLY strategies fail closed without qualification data."""
    world = _World(manual_clock, qualification=tristate.TriState.UNKNOWN)
    world.activate_zone_long()
    recorded = await _feed(world, world.runtime, scenarios.zone_long_frames())
    assert [s.kind for s in recorded] == ["ENTRY_BLOCKED"]
    assert "qualification unknown" in " ".join(recorded[0].risk_reasons)


async def test_stale_data_age_holds(manual_clock: clock.ManualClock) -> None:
    """Frames delivered too late (older than max_data_age_ms) never fire."""
    world = _World(manual_clock)
    world.activate_zone_long()
    out = []
    for frame in scenarios.zone_long_frames():
        manual_clock.current_ms = frame.close_ms + 10 * 60_000
        out.extend(await world.runtime.on_frame(BTC, frame))
    assert out == []


async def test_pause_cancels_and_stops_evaluation(
    manual_clock: clock.ManualClock,
) -> None:
    """Paused strategies are dropped from the runtime."""
    world = _World(manual_clock)
    world.activate_zone_long()
    frames = scenarios.zone_long_frames(
        scenarios.WorldOptions(first_signal=False)
    )
    await _feed(world, world.runtime, frames[:31])
    world.versions.pause(ALICE, scenarios.ZONE_LONG_ID)
    assert world.runtime.refresh() == []
    assert world.runtime.requirements() == []
    assert await _feed(world, world.runtime, frames[31:]) == []


async def test_mismatched_instrument_is_rejected(
    manual_clock: clock.ManualClock,
) -> None:
    """The instrument argument must match the frame."""
    world = _World(manual_clock)
    with pytest.raises(ValueError):
        await world.runtime.on_frame(
            "binance:perp:ETHUSDT", scenarios.zone_long_frames()[0]
        )
