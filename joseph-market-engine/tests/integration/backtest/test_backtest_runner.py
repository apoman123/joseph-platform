# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Backtests run the production evaluator over provided frames."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

import pytest

from joseph.backtest import runner
from joseph.backtest import service
from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.strategy.adapters import memory
from joseph.strategy.app import version_service
from joseph.strategy.domain import compiler
from joseph.strategy.domain import errors as strategy_errors
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios
from joseph.strategy.testing import vocabulary

D = decimal.Decimal
ALICE = identity.Principal("tenant-a", "alice")
BTC = "binance:perp:BTCUSDT"


class _Provider:
    """Frame provider returning prepared frames.

    Attributes:
        calls: ``(instrument_id, signal timeframe, requirements)`` per call.
    """

    def __init__(self, frames: Sequence[structures.BarFrame]) -> None:
        """Initializes the provider.

        Args:
            frames: Frames returned.
        """
        self._frames = list(frames)
        self.calls: list[tuple[str, str, compiler.EngineRequirements]] = []

    async def frames(
        self,
        instrument_id: instruments.InstrumentId,
        signal_tf: timeframes.Timeframe,
        requirements: compiler.EngineRequirements,
        from_ms: int,
        to_ms: int,
    ) -> Sequence[structures.BarFrame]:
        """Returns the prepared frames and records the call.

        Args:
            instrument_id: Instrument.
            signal_tf: Signal timeframe.
            requirements: Engine requirements.
            from_ms: Window start (ignored).
            to_ms: Window end (ignored).

        Returns:
            The prepared frames, whatever the window.
        """
        self.calls.append((str(instrument_id), signal_tf.value, requirements))
        del from_ms, to_ms
        return self._frames


def _winning_world() -> list[structures.BarFrame]:
    """Builds the zone-touch scenario with bar 35 reaching the 110 target.

    Returns:
        The scenario frames.
    """
    frames = scenarios.zone_long_frames()
    factory = frame_testing.FrameFactory()
    frames[35] = dataclasses.replace(
        frames[35],
        candle=factory.candle(
            35, frame_testing.bar("104", "111", "103", "110")
        ),
    )
    return frames


@pytest.fixture
def services(
    manual_clock: clock.ManualClock,
) -> Callable[[Sequence[structures.BarFrame]], service.BacktestService]:
    """Builds a backtest service with the zone-touch rule stored for tenant-a.

    Args:
        manual_clock: Clock of the version service.

    Returns:
        ``build(frames) -> service``; each service runs over ``frames``.
    """
    versions = version_service.StrategyVersionService(
        memory.InMemoryStrategyRepository(),
        manual_clock,
        vocabulary=vocabulary.VOCABULARY,
    )
    versions.create(ALICE, scenarios.zone_long_definition())

    def build(frames: Sequence[structures.BarFrame]) -> service.BacktestService:
        """Creates a service whose provider returns ``frames``.

        Args:
            frames: Frames every backtest sees.

        Returns:
            The service.
        """
        return service.BacktestService(
            versions,
            runner.BacktestRunner(
                _Provider(frames), vocabulary=vocabulary.VOCABULARY
            ),
        )

    return build


def _request(**changes: Any) -> runner.BacktestRequest:
    """Builds a request covering all 40 scenario bars of BTCUSDT.

    Args:
        **changes: ``BacktestRequest`` field overrides.

    Returns:
        The request, with 4 bps fees and 2 bps slippage.
    """
    base = runner.BacktestRequest(
        version=1,
        instrument_id=BTC,
        from_ms=frame_testing.BASE_MS,
        to_ms=frame_testing.BASE_MS + 40 * 300_000,
        fee_bps=D(4),
        slippage_bps=D(2),
        max_hold_bars=96,
    )
    return dataclasses.replace(base, **changes)


async def test_report_contains_trade_and_metrics(
    services: Callable[
        [Sequence[structures.BarFrame]], service.BacktestService
    ],
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """The entry becomes one target-exit trade with consistent metrics."""
    svc = services(_winning_world())
    report = await svc.run(ALICE, scenarios.ZONE_LONG_ID, _request())
    payload = report.to_contract()
    contract_validate("strategy.schema.json", "BacktestReport", payload)
    assert report.bars_evaluated == 40
    assert [s.kind for s in report.signals] == ["ENTRY_CONFIRMED"]
    trade = report.trades[0]
    assert trade.exit_reason == "TARGET"
    assert trade.entry_price == (D(104) * (1 + D("0.0002"))).quantize(
        D("0.00000001")
    )
    assert trade.r_multiple > 0
    assert report.metrics.trades == 1 and report.metrics.wins == 1
    assert report.feed_state is quality.FeedState.LIVE
    assert report.missing_bars == 0
    assert any("stop is assumed first" in a for a in report.assumptions)
    assert report.evaluator_version == "2.0.0"


async def test_backtest_matches_live_processing(
    services: Callable[
        [Sequence[structures.BarFrame]], service.BacktestService
    ],
) -> None:
    """Repeated backtests match and duplicated frames change no signal."""
    frames = _winning_world()
    report = await services(frames).run(
        ALICE, scenarios.ZONE_LONG_ID, _request()
    )
    again = await services(frames).run(
        ALICE, scenarios.ZONE_LONG_ID, _request()
    )
    assert report == again
    assert report.backtest_id == again.backtest_id
    noisy = await services(scenarios.with_duplicates(frames)).run(
        ALICE, scenarios.ZONE_LONG_ID, _request()
    )
    assert [s.signal_id for s in noisy.signals] == [
        s.signal_id for s in report.signals
    ]


async def test_gaps_and_provenance_are_reported(
    services: Callable[
        [Sequence[structures.BarFrame]], service.BacktestService
    ],
) -> None:
    """Missing bars and replayed data are visible in the report."""
    frames = scenarios.zone_long_frames()
    gapped = frames[:10] + frames[12:]
    replayed = [
        dataclasses.replace(f, feed_state=quality.FeedState.REPLAY)
        for f in gapped
    ]
    report = await services(replayed).run(
        ALICE, scenarios.ZONE_LONG_ID, _request()
    )
    assert report.missing_bars == 2
    assert report.feed_state is quality.FeedState.REPLAY


async def test_request_validation(
    services: Callable[
        [Sequence[structures.BarFrame]], service.BacktestService
    ],
) -> None:
    """Ranges, universe and ownership are checked."""
    svc = services(scenarios.zone_long_frames())
    with pytest.raises(errors.ValidationFailedError):
        await svc.run(ALICE, scenarios.ZONE_LONG_ID, _request(to_ms=0))
    with pytest.raises(errors.ValidationFailedError):
        await svc.run(
            ALICE,
            scenarios.ZONE_LONG_ID,
            _request(instrument_id="mexc:perp:BTC_USDT"),
        )
    with pytest.raises(errors.ValidationFailedError):
        await svc.run(
            ALICE,
            scenarios.ZONE_LONG_ID,
            _request(to_ms=frame_testing.BASE_MS + 500 * 86_400_000),
        )
    with pytest.raises(errors.NotFoundError):
        await svc.run(
            identity.Principal("tenant-b", "bob"),
            scenarios.ZONE_LONG_ID,
            _request(),
        )


async def test_invalid_version_is_refused() -> None:
    """A version invalid against the catalog cannot be backtested."""
    definition = scenarios.zone_long_definition()
    version = fixtures.make_version(definition)
    reduced = runner.BacktestRunner(
        _Provider([]),
        vocabulary=vocabulary.vocabulary(disabled=[vocabulary.TEST_ZONES]),
    )
    with pytest.raises(strategy_errors.StrategyValidationError):
        await reduced.run(version, _request())


async def test_provider_receives_engine_requirements() -> None:
    """The runner tells the provider which plugins and timeframes to run."""
    provider = _Provider(scenarios.zone_long_frames())
    definition = scenarios.zone_long_definition()
    await runner.BacktestRunner(provider, vocabulary=vocabulary.VOCABULARY).run(
        fixtures.make_version(definition), _request()
    )
    instrument, tf, requirements = provider.calls[0]
    assert (instrument, tf) == (BTC, "5m")
    assert requirements.plugins[vocabulary.TEST_ZONES] == frozenset(
        {timeframes.Timeframe.H1, timeframes.Timeframe.M5}
    )
    assert requirements.plugins[vocabulary.TEST_STATE] == frozenset(
        {timeframes.Timeframe.H4}
    )
