"""Builds venue adapters from settings (used by the composition root)."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import bitget
from joseph.market_data.adapters import http
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import mexc
from joseph.market_data.adapters import mock
from joseph.market_data.adapters import treasury
from joseph.market_data.adapters import twse
from joseph.market_data.adapters import ws
from joseph.market_data.app import ports

# Live verification of every endpoint and stream used by the adapters
# (scripts/probe_venues.py, 2026-11-21; trade streams, trade history,
# seconds bars, 1M klines and taker volume via the opt-in tests/network
# suite, 26 passed), 2026-11-22 12:43 UTC.
VERIFIED_AT_MS = 1_795_351_391_000

# TWSE (OpenAPI, STOCK_DAY, MIS, holiday schedules), the Nasdaq Trader
# symbol directory and Treasury yield-curve CSVs verified live by the opt-in
# tests/network suite and fixture captures, 2026-11-23 04:50 UTC. Alpaca
# market data is implemented from its documentation (no key here) and
# carries no verification time.
EXCHANGE_VERIFIED_AT_MS = 1_795_409_443_000

_RATES: dict[instruments.Venue, tuple[float, int]] = {
    instruments.Venue.BINANCE: (10.0, 20),
    instruments.Venue.HYPERLIQUID: (5.0, 10),
    instruments.Venue.LIGHTER: (3.0, 6),
    instruments.Venue.BITGET: (10.0, 20),
    instruments.Venue.MEXC: (8.0, 16),
    instruments.Venue.TREASURY: (1.0, 2),
}
_TWSE_OPENAPI_RATE = (1.0, 3)
_TWSE_PACED_RATE = (0.5, 2)
"""www.twse.com.tw and MIS block clients above roughly 3 requests / 5 s."""
_ALPACA_RATE = (3.0, 10)
"""Alpaca's free plan allows 200 requests per minute (shared by venues)."""
_SYMDIR_RATE = (1.0, 2)


def build_adapters(
    venues: Iterable[instruments.Venue],
    clock: clock_lib.Clock,
    *,
    timeout_s: float = 10.0,
    client: httpx.AsyncClient | None = None,
    connect: ws.ConnectFn = ws.default_connect,
    alpaca_config: alpaca.AlpacaConfig | None = None,
) -> dict[instruments.Venue, ports.MarketDataPort]:
    """Constructs one adapter per requested venue.

    Args:
        venues: Venues to enable.
        clock: Time source.
        timeout_s: HTTP timeout.
        client: Optional shared HTTP client (tests inject respx-backed ones).
        connect: WebSocket connector.
        alpaca_config: Alpaca credentials and feed for ``nasdaq``/``nyse``
            (unconfigured when omitted: the venues list instruments but
            data requests ask for keys).

    Returns:
        Adapter per venue, in the requested order.
    """

    def venue_http(
        venue: instruments.Venue,
        url: str,
        rate: tuple[float, int] | None = None,
        name: str | None = None,
    ) -> http.VenueHttp:
        """Creates the rate-limited HTTP client of a venue.

        Args:
            venue: Venue.
            url: Base URL.
            rate: ``(rate_per_s, burst)`` (defaults to the venue's).
            name: Source name for error messages.

        Returns:
            HTTP client.
        """
        rate_per_s, burst = rate or _RATES[venue]
        return http.VenueHttp(
            venue,
            url,
            client=client,
            timeout_s=timeout_s,
            rate_per_s=rate_per_s,
            burst=burst,
            name=name,
        )

    alpaca_source: alpaca.AlpacaSource | None = None

    def shared_alpaca() -> alpaca.AlpacaSource:
        """Returns the Alpaca source shared by ``nasdaq`` and ``nyse``.

        Returns:
            Source.
        """
        nonlocal alpaca_source
        if alpaca_source is None:
            alpaca_source = alpaca.AlpacaSource(
                alpaca_config or alpaca.AlpacaConfig(),
                venue_http(
                    instruments.Venue.NASDAQ,
                    alpaca.DATA_URL,
                    _ALPACA_RATE,
                    "alpaca",
                ),
                venue_http(
                    instruments.Venue.NASDAQ,
                    alpaca.SYMDIR_URL,
                    _SYMDIR_RATE,
                    "nasdaqtrader",
                ),
                clock,
                connect,
            )
        return alpaca_source

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
        elif venue is instruments.Venue.TWSE:
            adapters[venue] = twse.TwseAdapter(
                venue_http(venue, twse.OPENAPI_URL, _TWSE_OPENAPI_RATE),
                venue_http(venue, twse.WWW_URL, _TWSE_PACED_RATE),
                venue_http(venue, twse.MIS_URL, _TWSE_PACED_RATE, "twse MIS"),
                clock,
                EXCHANGE_VERIFIED_AT_MS,
            )
        elif venue in (instruments.Venue.NASDAQ, instruments.Venue.NYSE):
            adapters[venue] = alpaca.AlpacaAdapter(
                venue, shared_alpaca(), clock
            )
        elif venue is instruments.Venue.TREASURY:
            adapters[venue] = treasury.TreasuryAdapter(
                venue_http(venue, treasury.REST_URL),
                clock,
                EXCHANGE_VERIFIED_AT_MS,
            )
    return adapters
