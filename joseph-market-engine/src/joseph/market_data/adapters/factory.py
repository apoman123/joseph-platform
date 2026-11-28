# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Builds venue adapters from settings (used by the composition root)."""

from __future__ import annotations

import functools
from collections.abc import Iterable

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import bitget
from joseph.market_data.adapters import ccxt
from joseph.market_data.adapters import deriv
from joseph.market_data.adapters import fred
from joseph.market_data.adapters import fugle
from joseph.market_data.adapters import http
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import mexc
from joseph.market_data.adapters import mock
from joseph.market_data.adapters import public_sources
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import treasury
from joseph.market_data.adapters import twse
from joseph.market_data.adapters import ws
from joseph.market_data.app import bar_archive as bar_archive_lib
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

# TPEx (OpenAPI, tradingStock, tradingDate schedules, MIS otc channels)
# verified live by fixture captures and the opt-in tests/network suite,
# 2026-11-23 08:18 UTC.
TPEX_VERIFIED_AT_MS = 1_795_421_880_000

_RATES: dict[instruments.Venue, tuple[float, int]] = {
    # (requests per second, burst) of each venue's token bucket.
    instruments.Venue.BINANCE: (10.0, 20),
    instruments.Venue.HYPERLIQUID: (5.0, 10),
    instruments.Venue.LIGHTER: (3.0, 6),
    instruments.Venue.BITGET: (10.0, 20),
    instruments.Venue.MEXC: (8.0, 16),
    instruments.Venue.TREASURY: (1.0, 2),
}
_TWSE_PACED_RATE = (0.5, 1)
"""www.twse.com.tw and MIS: one request per 2 s, no burst.

TWSE blocks clients above roughly 3 requests per 5 s. TWSE publishes no
figure; the threshold is the one the twstock project reports
(https://github.com/mlouielu/twstock, README), and the fixture capture
script (``scripts/capture_venue_fixtures.py``) paces at 2.5 s for it.
Without a burst, requests are at least 2 s apart, so any 5 s window holds
at most 3 of them, from a full bucket too.
"""
_TWSE_OPENAPI_RATE = _TWSE_PACED_RATE
"""openapi.twse.com.tw: no published limit is known, so its own bucket gets
the same conservative pace as www.twse.com.tw and MIS."""
_TPEX_RATE = (0.5, 3)
"""www.tpex.org.tw (OpenAPI and reports share one budget; none published)."""
_ALPACA_RATE = (3.0, 10)
"""Alpaca's free plan allows 200 requests per minute (shared by venues)."""
_SYMDIR_RATE = (1.0, 2)
"""www.nasdaqtrader.com symbol directory files (US instrument lists)."""
_FUGLE_RATE = (1.0, 1)
"""Fugle free tier allows 60 stock REST requests per minute, shared here."""
_FRED_RATE = (1.0, 1)
"""Conservative pace for free FRED observations."""


def build_adapters(  # noqa: PLR0912 - explicit venue dispatch remains legible
    venues: Iterable[instruments.Venue],
    clock: clock_lib.Clock,
    *,
    timeout_s: float = 10.0,
    client: httpx.AsyncClient | None = None,
    connect: ws.ConnectFn = ws.default_connect,
    alpaca_config: alpaca.AlpacaConfig | None = None,
    mexc_ws_format: mexc.WsFormat = mexc.WsFormat.PROTOBUF,
    fugle_api_key: str | None = None,
    fugle_adjusted_daily: bool = True,
    fred_api_key: str | None = None,
    bar_archive: bar_archive_lib.BarArchive | None = None,
    crypto_source: str = "native",
) -> dict[instruments.Venue, ports.MarketDataPort]:
    """Constructs one adapter per requested venue.

    Args:
        venues: Venues to enable.
        clock: Time source.
        timeout_s: HTTP timeout in seconds (unused with ``client``).
        client: Optional shared HTTP client (tests inject respx-backed ones).
        connect: WebSocket connector.
        alpaca_config: Alpaca credentials and feed for ``nasdaq``,
            ``nyse`` and ``cboe`` (unconfigured when omitted: the venues
            list instruments but data requests ask for keys).
        mexc_ws_format: Push format of the MEXC futures WebSocket.
        fugle_api_key: Fugle free-tier market-data key; ``None`` uses only
            the official TWSE/TPEx sources.
        fugle_adjusted_daily: Normalize all Fugle historical OHLC for actions.
        fred_api_key: Optional FRED key for distinct daily rate series.
        bar_archive: Durable store of the TWSE/TPEx recorded minutes.
        crypto_source: ``native`` for exchange APIs and public WebSockets
            (default); ``ccxt`` retains the optional compatibility adapter.

    Returns:
        Adapter per venue, in the requested order.
    """

    def venue_http(
        venue: instruments.Venue,
        url: str,
        rate: tuple[float, int] | None = None,
        name: str | None = None,
        limiter: http.RateLimiter | None = None,
    ) -> http.VenueHttp:
        """Creates the rate-limited HTTP client of a venue.

        Args:
            venue: Venue.
            url: Base URL.
            rate: ``(rate_per_s, burst)`` (defaults to the venue's).
            name: Source name for error messages.
            limiter: Token bucket shared with other clients of the host
                (``rate`` is then unused).

        Returns:
            HTTP client.
        """
        if limiter is not None:
            rate_per_s, burst = limiter.rate_per_s, limiter.burst
        else:
            rate_per_s, burst = rate or _RATES[venue]
        return http.VenueHttp(
            venue,
            url,
            client=client,
            timeout_s=timeout_s,
            rate_per_s=rate_per_s,
            burst=burst,
            name=name,
            limiter=limiter,
        )

    # The shared objects below are built on first use, once per call of
    # ``build_adapters``, so venues left out create no clients or SDK.
    @functools.cache
    def shared_mis_limiter() -> http.RateLimiter:
        """Returns the MIS token bucket shared by TWSE and TPEx.

        Returns:
            Limiter (MIS is one host whatever the market).
        """
        return http.RateLimiter(*_TWSE_PACED_RATE)

    @functools.cache
    def shared_alpaca() -> alpaca.AlpacaSource:
        """Returns the Alpaca source shared by the US exchange venues.

        Returns:
            Source.
        """
        return alpaca.AlpacaSource(
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

    @functools.cache
    def shared_fugle_limiter() -> http.RateLimiter:
        """Returns the Fugle token bucket shared by Taiwan venues.

        Returns:
            Limiter paced at the free-tier REST allowance.
        """
        return http.RateLimiter(*_FUGLE_RATE)

    def fugle_source(venue: instruments.Venue) -> fugle.FugleSource | None:
        """Creates Fugle data access for one Taiwan venue when configured.

        Args:
            venue: TWSE or TPEx venue.

        Returns:
            Fugle source, or ``None`` when no key is configured.
        """
        if fugle_api_key is None:
            return None
        return fugle.FugleSource(
            fugle_api_key,
            venue_http(
                venue,
                fugle.REST_URL,
                name="fugle",
                limiter=shared_fugle_limiter(),
            ),
            clock,
            adjusted_daily=fugle_adjusted_daily,
        )

    adapters: dict[instruments.Venue, ports.MarketDataPort] = {}
    for venue in venues:
        if venue is instruments.Venue.MOCK:
            adapters[venue] = mock.MockAdapter(clock)
        elif venue is instruments.Venue.DERIV:
            adapters[venue] = deriv.DerivAdapter(clock, connect)
        elif venue in public_sources.URLS:
            adapters[venue] = public_sources.PublicSourceAdapter(
                venue,
                venue_http(venue, public_sources.URLS[venue], (3.0, 5)),
                clock,
                connect,
            )
        elif venue in ccxt.EXCHANGES and crypto_source == "ccxt":
            adapters[venue] = ccxt.CcxtAdapter(venue, clock)
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
                mexc_ws_format,
            )
        elif venue is instruments.Venue.TWSE:
            adapters[venue] = twse.TwseAdapter(
                venue_http(venue, twse.OPENAPI_URL, _TWSE_OPENAPI_RATE),
                venue_http(venue, twse.WWW_URL, _TWSE_PACED_RATE),
                venue_http(
                    venue,
                    twse.MIS_URL,
                    name="twse MIS",
                    limiter=shared_mis_limiter(),
                ),
                clock,
                EXCHANGE_VERIFIED_AT_MS,
                fugle=fugle_source(venue),
                bar_archive=bar_archive,
            )
        elif venue is instruments.Venue.TPEX:
            tpex_limiter = http.RateLimiter(*_TPEX_RATE)
            adapters[venue] = tpex.TpexAdapter(
                venue_http(venue, tpex.OPENAPI_URL, limiter=tpex_limiter),
                venue_http(venue, tpex.WWW_URL, limiter=tpex_limiter),
                venue_http(
                    venue,
                    twse.MIS_URL,
                    name="tpex MIS",
                    limiter=shared_mis_limiter(),
                ),
                clock,
                TPEX_VERIFIED_AT_MS,
                fugle=fugle_source(venue),
                bar_archive=bar_archive,
            )
        elif venue in alpaca.VENUES:
            adapters[venue] = alpaca.AlpacaAdapter(
                venue, shared_alpaca(), clock
            )
        elif venue is instruments.Venue.TREASURY:
            fred_source = None
            if fred_api_key:
                fred_source = fred.FredSource(
                    fred_api_key,
                    venue_http(venue, fred.REST_URL, _FRED_RATE, "fred"),
                    clock,
                )
            adapters[venue] = treasury.TreasuryAdapter(
                venue_http(venue, treasury.REST_URL),
                clock,
                EXCHANGE_VERIFIED_AT_MS,
                fred=fred_source,
            )
    return adapters
