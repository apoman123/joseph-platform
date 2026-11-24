# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests: exchange and rate venues bypass crypto bar derivation."""

from __future__ import annotations

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.app import derived_bars
from joseph.market_data.app import ports
from joseph.market_data.app import trade_archive
from joseph.market_data.app import trade_recorder

_VENUES = [
    instruments.Venue.BINANCE,
    instruments.Venue.TWSE,
    instruments.Venue.TPEX,
    instruments.Venue.NASDAQ,
    instruments.Venue.NYSE,
    instruments.Venue.CBOE,
    instruments.Venue.TREASURY,
]


def _raw() -> dict[instruments.Venue, ports.MarketDataPort]:
    """Builds raw adapters of a crypto venue and every exchange venue.

    Returns:
        The unwrapped adapters per venue, Alpaca with placeholder keys.
    """
    return dict(
        factory.build_adapters(
            _VENUES,
            clock_lib.ManualClock(0),
            client=httpx.AsyncClient(),
            alpaca_config=alpaca.AlpacaConfig(key_id="k", secret_key="s"),
        )
    )


def test_wrap_leaves_session_venues_to_their_adapters() -> None:
    """Only crypto venues get engine-derived bars."""
    raw = _raw()
    wrapped = derived_bars.wrap(raw, clock_lib.ManualClock(0))
    assert isinstance(
        wrapped[instruments.Venue.BINANCE], derived_bars.DerivedBarsAdapter
    )
    for venue in _VENUES[1:]:
        assert wrapped[venue] is raw[venue]


def test_recorder_never_records_venues_without_aggressor_side() -> None:
    """Classified venues get neither recording nor recorded taker delta."""
    recorder = trade_recorder.TradeRecorder(
        _raw(),
        trade_archive.MemoryTradeArchive(),
        clock_lib.ManualClock(0),
    )
    assert recorder.records_venue(instruments.Venue.BINANCE)
    for venue in _VENUES[1:]:
        assert not recorder.records_venue(venue)
        assert venue not in recorder.delta_venues
