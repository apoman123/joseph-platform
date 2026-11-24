# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests: backtests of the DEMO stock count session bars."""

from __future__ import annotations

import copy
import datetime
import zoneinfo

import pytest

from joseph.backtest import runner
from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.market_data.adapters import mock
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service as market_service
from joseph.pipeline import frames as frames_lib
from joseph.strategy.domain import ast
from joseph.strategy.domain import versions
from joseph.strategy.testing import fixtures

NY = zoneinfo.ZoneInfo("America/New_York")
ACME = "mock:stock:ACME"


def _ny(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _runner(
    now: int,
) -> tuple[runner.BacktestRunner, frames_lib.EngineFrameProvider]:
    """Builds a runner over the DEMO venue with the production engine.

    Args:
        now: Clock time, epoch ms.

    Returns:
        The runner and its frame provider.
    """
    clock = clock_lib.ManualClock(now)
    adapters = {instruments.Venue.MOCK: mock.MockAdapter(clock)}
    store = candle_store.CandleStore()
    market_data = market_service.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )
    provider = frames_lib.EngineFrameProvider(
        market_data, catalog.build_default_registry()
    )
    return runner.BacktestRunner(provider), provider


def _stock_version() -> versions.StrategyVersion:
    """Builds the Long A version with a universe of only the DEMO stock.

    Returns:
        The strategy version.
    """
    raw = copy.deepcopy(
        fixtures.strategy_definitions()[fixtures.LONG_A].to_contract()
    )
    raw["universe"] = {
        "qualification": "ANY",
        "venues": ["mock"],
        "whitelist": [ACME],
        "blacklist": [],
        "min_qualified_minutes": 0,
    }
    return fixtures.make_version(ast.parse_definition(raw))


@pytest.mark.slow
async def test_thanksgiving_week_backtest_counts_session_bars() -> None:
    """Mon-Wed full sessions plus Friday's half day are 276 5m bars."""
    backtests, _ = _runner(_ny(2026, 12, 1, 12))
    report = await backtests.run(
        _stock_version(),
        runner.BacktestRequest(
            version=1,
            instrument_id=ACME,
            from_ms=_ny(2026, 11, 23),
            to_ms=_ny(2026, 11, 28),
        ),
    )
    assert report.bars_evaluated == 3 * 78 + 42
    assert report.missing_bars == 0


async def test_backtest_start_before_the_calendar_is_refused() -> None:
    """A range starting before XNYS's first session is a client error."""
    backtests, _ = _runner(_ny(2026, 12, 1, 12))
    with pytest.raises(errors.ValidationFailedError, match="XNYS"):
        await backtests.run(
            _stock_version(),
            runner.BacktestRequest(
                version=1, instrument_id=ACME, from_ms=0, to_ms=86_400_000
            ),
        )


async def test_rate_series_cannot_be_backtested() -> None:
    """A rate series is not a tradable instrument."""
    backtests, _ = _runner(_ny(2026, 12, 1, 12))
    with pytest.raises(errors.ValidationFailedError, match="rate series"):
        await backtests.run(
            _stock_version(),
            runner.BacktestRequest(
                version=1,
                instrument_id="treasury:rate:UST10Y",
                from_ms=_ny(2026, 11, 23),
                to_ms=_ny(2026, 11, 28),
            ),
        )


async def test_frame_windows_count_session_bars() -> None:
    """Aux windows reach 64 session bars back, not 64 wall-clock bars."""
    calendar_windows = frames_lib.fetch_windows(
        [timeframes.Timeframe.M5, timeframes.Timeframe.H1],
        timeframes.Timeframe.M5,
        _ny(2026, 11, 30, 9, 30),
        calendar=sessions.for_instrument(instruments.InstrumentId.parse(ACME)),
    )
    # 25 bars in Thanksgiving week, 35 the week before, 4 on Fri 13 Nov.
    assert calendar_windows[timeframes.Timeframe.H1] == _ny(
        2026, 11, 13, 12, 30
    )
    assert calendar_windows[timeframes.Timeframe.M5] == _ny(2026, 11, 30, 9, 30)
