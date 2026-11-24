# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests: the composition root wires trade recording."""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.app import derived_bars
from joseph.market_data.app import feed_router
from joseph.market_data.domain import candles as candle_rules
from joseph.platform import config

_T0 = 1_795_349_040_000


def _settings(tmp_path: pathlib.Path, **overrides: object) -> config.Settings:
    """Builds settings for real venues without network side effects.

    Args:
        tmp_path: Scratch directory.
        **overrides: Field overrides.

    Returns:
        Settings.
    """
    base = config.Settings(
        database_url="sqlite://",
        enabled_venues=(
            instruments.Venue.BINANCE,
            instruments.Venue.BITGET,
            instruments.Venue.HYPERLIQUID,
            instruments.Venue.MOCK,
        ),
        pipeline_enabled=False,
        binance_archive_dir=str(tmp_path / "vision"),
        bitget_archive_dir=str(tmp_path / "bitget"),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


async def test_container_records_trades_and_wraps_real_venues(
    tmp_path: pathlib.Path,
) -> None:
    """Real venues are wrapped; candles of a live session lease recording."""
    container = bootstrap.build_container(
        _settings(
            tmp_path,
            trade_archive_url=f"sqlite:///{tmp_path / 'trades.db'}",
            trade_record_max_instruments=0,
        ),
        clock=clock_lib.ManualClock(_T0),
    )
    recorder = container.recorder
    assert recorder is not None
    assert isinstance(
        container.adapters[instruments.Venue.HYPERLIQUID],
        derived_bars.DerivedBarsAdapter,
    )
    assert not isinstance(
        container.adapters[instruments.Venue.MOCK],
        derived_bars.DerivedBarsAdapter,
    )
    assert recorder.delta_venues == frozenset(
        {instruments.Venue.HYPERLIQUID, instruments.Venue.BITGET}
    )
    for venue, site in (
        (instruments.Venue.BINANCE, "data.binance.vision"),
        (instruments.Venue.BITGET, "bitget.com/data-download"),
    ):
        notes = container.adapters[venue].capabilities().notes
        assert any(site in note for note in notes), venue
    caps = container.adapters[instruments.Venue.HYPERLIQUID].capabilities()
    assert timeframes.Timeframe.MN1 in caps.aggregated_timeframes
    await container.astart()
    instrument = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
    candle = market.Candle(
        instrument_id=instrument,
        timeframe=timeframes.Timeframe.M1,
        start_ms=_T0,
        end_ms=_T0 + 60_000,
        open=decimals.ONE,
        high=decimals.ONE,
        low=decimals.ONE,
        close=decimals.ONE,
        base_volume=decimals.ONE,
        is_final=True,
        source="hyperliquid.ws",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=_T0,
    )
    session = feed_router.FeedSession(
        instrument=instrument,
        timeframe=timeframes.Timeframe.M1,
        mode=quality.FeedMode.AUTO,
        tracker=container.router.rest_tracker(instrument.venue),
    )
    on_candle = container.router._on_candle
    assert on_candle is not None
    await on_candle(session, candle, candle_rules.MergeAction.INSERTED)
    assert not recorder.recording(instrument)  # capacity 0 in this test
    await container.aclose()


def test_recording_can_be_disabled(tmp_path: pathlib.Path) -> None:
    """With recording off the venues still aggregate but record nothing."""
    container = bootstrap.build_container(
        _settings(tmp_path, trade_recording=False, binance_archive=False),
        clock=clock_lib.ManualClock(_T0),
    )
    assert container.recorder is None
    caps = container.adapters[instruments.Venue.HYPERLIQUID].capabilities()
    assert timeframes.Timeframe.MN1 in caps.aggregated_timeframes
    assert not caps.market_data.trade_delta
    demo = bootstrap.build_container(
        _settings(tmp_path, demo_only=True), clock=clock_lib.ManualClock(_T0)
    )
    assert demo.recorder is None


@pytest.mark.parametrize("enabled", [True, False])
async def test_lighter_archive_follows_its_setting(
    tmp_path: pathlib.Path, enabled: bool
) -> None:
    """``JOSEPH_LIGHTER_ARCHIVE`` adds or omits the Lighter day archive."""
    container = bootstrap.build_container(
        _settings(
            tmp_path,
            enabled_venues=(instruments.Venue.LIGHTER,),
            lighter_archive=enabled,
        ),
        clock=clock_lib.ManualClock(_T0),
    )
    assert container.recorder is not None
    notes = container.adapters[instruments.Venue.LIGHTER].capabilities().notes
    assert any("aws-public-blockchain" in note for note in notes) is enabled
    await container.aclose()
