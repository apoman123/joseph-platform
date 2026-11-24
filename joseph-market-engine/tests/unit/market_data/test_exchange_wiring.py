# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests: exchange venue wiring (bar derivation, TWSE request pace)."""

from __future__ import annotations

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import http
from joseph.market_data.adapters import twse
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


class _SimulatedTime:
    """A clock that only moves when the limiter sleeps.

    Attributes:
        now: Current time in seconds.
    """

    def __init__(self) -> None:
        """Starts at 0 s."""
        self.now = 0.0

    def monotonic(self) -> float:
        """Returns the current time.

        Returns:
            Seconds since the start.
        """
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Advances the clock instead of waiting.

        Args:
            seconds: Seconds to advance.
        """
        self.now += seconds


async def _grant_times(limiter: http.RateLimiter, count: int) -> list[float]:
    """Replays a limiter's rate from a full bucket in simulated time.

    Args:
        limiter: Limiter whose rate and burst are replayed.
        count: Requests to send back to back.

    Returns:
        The simulated time at which each request was let through.
    """
    clock = _SimulatedTime()
    replay = http.RateLimiter(
        limiter.rate_per_s,
        limiter.burst,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    granted = []
    for _ in range(count):
        await replay.acquire()
        granted.append(clock.now)
    return granted


def _busiest_window(granted: list[float], window_s: float) -> int:
    """Counts the most requests let through in any window.

    Args:
        granted: Times requests were let through, ascending.
        window_s: Window length in seconds.

    Returns:
        The largest count in a window ``[t, t + window_s)``.
    """
    return max(
        sum(1 for u in granted if t <= u < t + window_s) for t in granted
    )


async def test_twse_hosts_never_exceed_three_requests_per_five_seconds() -> (
    None
):
    """OpenAPI, www and MIS stay under TWSE's block threshold when full."""
    adapters = factory.build_adapters(
        [instruments.Venue.TWSE, instruments.Venue.TPEX],
        clock_lib.ManualClock(0),
        client=httpx.AsyncClient(),
    )
    listed = adapters[instruments.Venue.TWSE]
    assert isinstance(listed, twse.TwseAdapter)
    otc = adapters[instruments.Venue.TPEX]
    assert isinstance(otc, twse.TaiwanExchangeAdapter)
    clients = {
        "openapi": listed._http,
        "www": listed._www,
        "mis": listed._mis_http,
    }
    for name, client in clients.items():
        granted = await _grant_times(client._limiter, 12)
        assert _busiest_window(granted, 5.0) <= 3, name
    # TPEx polls MIS through the same bucket as TWSE.
    assert otc._mis_http._limiter is listed._mis_http._limiter
