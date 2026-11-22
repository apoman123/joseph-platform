"""Builds venue adapters from settings (used by the composition root)."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import bitget
from joseph.market_data.adapters import http
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import mexc
from joseph.market_data.adapters import mock
from joseph.market_data.adapters import ws
from joseph.market_data.app import ports

# Live verification of every endpoint and stream used by the adapters
# (scripts/probe_venues.py, 2026-11-21; trade streams, trade history,
# seconds bars, 1M klines and taker volume via the opt-in tests/network
# suite, 26 passed), 2026-11-22 12:43 UTC.
VERIFIED_AT_MS = 1_795_351_391_000

_RATES: dict[instruments.Venue, tuple[float, int]] = {
    instruments.Venue.BINANCE: (10.0, 20),
    instruments.Venue.HYPERLIQUID: (5.0, 10),
    instruments.Venue.LIGHTER: (3.0, 6),
    instruments.Venue.BITGET: (10.0, 20),
    instruments.Venue.MEXC: (8.0, 16),
}


def build_adapters(
    venues: Iterable[instruments.Venue],
    clock: clock_lib.Clock,
    *,
    timeout_s: float = 10.0,
    client: httpx.AsyncClient | None = None,
    connect: ws.ConnectFn = ws.default_connect,
) -> dict[instruments.Venue, ports.MarketDataPort]:
    """Constructs one adapter per requested venue.

    Args:
        venues: Venues to enable.
        clock: Time source.
        timeout_s: HTTP timeout.
        client: Optional shared HTTP client (tests inject respx-backed ones).
        connect: WebSocket connector.

    Returns:
        Adapter per venue, in the requested order.
    """

    def venue_http(venue: instruments.Venue, url: str) -> http.VenueHttp:
        """Creates the rate-limited HTTP client of a venue.

        Args:
            venue: Venue.
            url: Base URL.

        Returns:
            HTTP client.
        """
        rate, burst = _RATES[venue]
        return http.VenueHttp(
            venue,
            url,
            client=client,
            timeout_s=timeout_s,
            rate_per_s=rate,
            burst=burst,
        )

    adapters: dict[instruments.Venue, ports.MarketDataPort] = {}
    for venue in venues:
        if venue is instruments.Venue.MOCK:
            adapters[venue] = mock.MockAdapter(clock)
        elif venue is instruments.Venue.BINANCE:
            adapters[venue] = binance.BinanceAdapter(
                venue_http(venue, binance.REST_URL),
                clock,
                connect,
                VERIFIED_AT_MS,
            )
        elif venue is instruments.Venue.HYPERLIQUID:
            adapters[venue] = hyperliquid.HyperliquidAdapter(
                venue_http(venue, hyperliquid.REST_URL),
                clock,
                connect,
                VERIFIED_AT_MS,
            )
        elif venue is instruments.Venue.LIGHTER:
            adapters[venue] = lighter.LighterAdapter(
                venue_http(venue, lighter.REST_URL),
                clock,
                VERIFIED_AT_MS,
                connect,
            )
        elif venue is instruments.Venue.BITGET:
            adapters[venue] = bitget.BitgetAdapter(
                venue_http(venue, bitget.REST_URL),
                clock,
                connect,
                VERIFIED_AT_MS,
            )
        elif venue is instruments.Venue.MEXC:
            adapters[venue] = mexc.MexcAdapter(
                venue_http(venue, mexc.REST_URL),
                clock,
                connect,
                VERIFIED_AT_MS,
            )
    return adapters
