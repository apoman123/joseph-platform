# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for pipeline helpers (requirements, gate, context) and stop."""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.pipeline import frames
from joseph.pipeline import live
from joseph.pipeline import market_context
from joseph.pipeline import qualification
from joseph.screening.domain import policy
from joseph.strategy.domain import compiler
from joseph.strategy.domain import tristate

TF = timeframes.Timeframe
_BTC = instruments.InstrumentId.parse("binance:perp:BTCUSDT")


def test_merge_requirements_unions_plugins_per_signal_timeframe() -> None:
    """Plans per signal timeframe merge their plugins and longest history."""
    a = compiler.EngineRequirements(
        TF.M5, {"ema": frozenset({TF.M5})}, history_bars=50
    )
    b = compiler.EngineRequirements(
        TF.M5,
        {"ema": frozenset({TF.H1}), "rsi": frozenset({TF.H4})},
        history_bars=120,
    )
    c = compiler.EngineRequirements(TF.H1, {}, history_bars=10)
    merged = frames.merge_requirements([a, b, c])
    assert set(merged) == {TF.M5, TF.H1}
    assert merged[TF.M5].plugins["ema"] == frozenset({TF.M5, TF.H1})
    assert merged[TF.M5].plugins["rsi"] == frozenset({TF.H4})
    assert merged[TF.M5].history_bars == 120


def test_fetch_windows_extend_higher_timeframes() -> None:
    """Higher timeframes are fetched further back for pivot warm-up."""
    start = TF.M5.floor(1_795_000_000_000)
    windows = frames.fetch_windows({TF.M1, TF.M5, TF.H4}, TF.M5, start)
    assert windows[TF.M1] == start and windows[TF.M5] == start
    assert windows[TF.H4] < start - 60 * TF.H4.duration_ms


@dataclasses.dataclass
class _Record:
    """The qualification record fields the gate reads.

    Attributes:
        instrument_id: Canonical instrument id.
        status: Qualification status.
    """

    instrument_id: str
    status: policy.Status


@dataclasses.dataclass
class _Report:
    """The qualification report fields the gate reads.

    Attributes:
        records: One record per instrument, best ranked first.
    """

    records: list[_Record]


class _FakeService:
    """Qualification service stub returning fixed records.

    Attributes:
        statuses: Status per instrument id, in ranking order.
        fail: Whether ``report`` raises, as a venue outage would.
    """

    def __init__(self, statuses: dict[str, policy.Status]) -> None:
        """Initializes the stub.

        Args:
            statuses: Status per instrument id, in ranking order.
        """
        self.statuses = statuses
        self.fail = False

    async def report(self, venue: instruments.Venue) -> _Report:
        """Returns the fixed records as a report.

        Args:
            venue: Venue (unused).

        Returns:
            A report with one record per entry of ``statuses``.

        Raises:
            RuntimeError: When ``fail`` is set.
        """
        del venue
        if self.fail:
            raise RuntimeError("venue down")
        return _Report([_Record(k, v) for k, v in self.statuses.items()])


class _Clock:
    """Settable clock.

    Attributes:
        now: Current time in epoch ms; tests assign it directly.
    """

    def __init__(self) -> None:
        """Initializes at t=0."""
        self.now = 0

    def now_ms(self) -> int:
        """Returns the settable time in epoch ms."""
        return self.now


async def test_gate_is_tristate_and_honors_minimum_minutes() -> None:
    """The gate is tri-state, waits minimum minutes, survives refresh errors."""
    service = _FakeService(
        {
            "binance:perp:A": policy.Status.QUALIFIED,
            "binance:perp:B": policy.Status.REJECTED,
            "binance:perp:C": policy.Status.INSUFFICIENT_DATA,
        }
    )
    clock = _Clock()
    gate = qualification.CachedQualificationGate(service, clock)  # type: ignore[arg-type]  # a stub stands in for QualificationService
    await gate.refresh([instruments.Venue.BINANCE])
    t = tristate.TriState
    assert gate.qualification("binance:perp:A", 0, 0) is t.TRUE
    assert gate.qualification("binance:perp:A", 60_000, 5) is t.FALSE
    assert gate.qualification("binance:perp:A", 300_000, 5) is t.TRUE
    assert gate.qualification("binance:perp:B", 0, 0) is t.FALSE
    assert gate.qualification("binance:perp:C", 0, 0) is t.UNKNOWN
    assert gate.qualification("binance:perp:Z", 0, 0) is t.UNKNOWN
    assert gate.ranked(instruments.Venue.BINANCE)[0] == "binance:perp:A"
    service.fail = True
    await gate.refresh([instruments.Venue.BINANCE])
    assert gate.is_qualified("binance:perp:A")


def _snapshot(ts: int, oi: str) -> market.MarketSnapshot:
    """Builds a snapshot with open interest.

    Args:
        ts: Observation time, epoch ms.
        oi: Open interest notional in USD, a decimal string.

    Returns:
        A live BTCUSDT perpetual snapshot.
    """
    return market.MarketSnapshot(
        instrument_id=_BTC,
        feed_state=quality.FeedState.LIVE,
        source="test",
        as_of_ms=ts,
        best_bid=decimal.Decimal("99"),
        best_ask=decimal.Decimal("101"),
        open_interest=market.OpenInterest(
            _BTC, decimal.Decimal(1), decimal.Decimal(oi), "test", ts
        ),
    )


def test_market_context_computes_oi_change_and_respects_time() -> None:
    """OI change is relative to the oldest kept observation; no look-ahead."""
    recorder = market_context.SnapshotMarketContext(max_age_ms=10**9)
    recorder.record(_snapshot(1_000, "100"))
    recorder.record(_snapshot(2_000, "90"))
    context = recorder.context(str(_BTC), 2_000)
    assert context is not None
    assert context.value_at("oi_change_pct", 2_000) == decimal.Decimal(-10)
    early = recorder.context(str(_BTC), 1_500)
    assert early is not None
    assert early.value_at("oi_change_pct", 1_500) is None
    assert recorder.context("binance:perp:NONE", 1) is None


def _blocked_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[live.LiveSignalPipeline, asyncio.Event]:
    """Builds a pipeline whose pass blocks until cancelled.

    Args:
        monkeypatch: Replaces ``run_once`` on the pipeline.

    Returns:
        The pipeline (not started) and an event set once a pass began.
    """
    unused: Any = None
    pipeline = live.LiveSignalPipeline(
        unused,
        unused,
        unused,
        gate=unused,
        context=unused,
        clock=clock_lib.ManualClock(0),
    )
    started = asyncio.Event()

    async def run_once() -> None:
        """Signals the pass start, then waits forever."""
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "run_once", run_once)
    return pipeline, started


async def test_stop_propagates_the_cancellation_of_its_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a task blocked in ``stop`` cancels that task."""
    pipeline, started = _blocked_pipeline(monkeypatch)
    pipeline.start()
    await started.wait()
    stopper = asyncio.create_task(pipeline.stop())
    await asyncio.sleep(0)
    stopper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopper
    assert stopper.cancelled()


async def test_stop_absorbs_a_cancellation_of_the_loop_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loop task cancelled elsewhere still lets ``stop`` return."""
    pipeline, started = _blocked_pipeline(monkeypatch)
    pipeline.start()
    await started.wait()
    task = pipeline._task
    assert task is not None
    task.cancel()
    await pipeline.stop()
    assert task.cancelled() and pipeline._task is None
