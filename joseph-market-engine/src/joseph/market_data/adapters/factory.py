"""Builds venue adapters from settings (used by the composition root)."""

from __future__ import annotations

from collections.abc import Callable
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
from joseph.market_data.adapters import sinopac
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
_TPEX_RATE = (0.5, 3)
"""www.tpex.org.tw (OpenAPI and reports share one budget; none published)."""
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
    mexc_ws_format: mexc.WsFormat = mexc.WsFormat.PROTOBUF,
    sinopac_config: sinopac.SinopacConfig | None = None,
    sinopac_factory: Callable[[], sinopac.SjDataFactory] = (
        sinopac.ShioajiDataFactory
    ),
    bar_archive: bar_archive_lib.BarArchive | None = None,
) -> dict[instruments.Venue, ports.MarketDataPort]:
    """Constructs one adapter per requested venue.

    Args:
        venues: Venues to enable.
        clock: Time source.
        timeout_s: HTTP timeout.
        client: Optional shared HTTP client (tests inject respx-backed ones).
        connect: WebSocket connector.
        alpaca_config: Alpaca credentials and feed for ``nasdaq``,
            ``nyse`` and ``cboe`` (unconfigured when omitted: the venues
            list instruments but data requests ask for keys).
        mexc_ws_format: Push format of the MEXC futures WebSocket.
        sinopac_config: Shioaji settings for ``twse``/``tpex`` market data
            (``JOSEPH_TW_DATA_SOURCE=sinopac``); ``None`` keeps the
            official sources only.
        sinopac_factory: Creates the Shioaji SDK factory (tests inject a
            fake).
        bar_archive: Durable store of the TWSE/TPEx recorded minutes.

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

    mis_limiter: http.RateLimiter | None = None

    def shared_mis_limiter() -> http.RateLimiter:
        """Returns the MIS token bucket shared by TWSE and TPEx.

        Returns:
            Limiter (MIS is one host whatever the market).
        """
        nonlocal mis_limiter
        if mis_limiter is None:
            mis_limiter = http.RateLimiter(*_TWSE_PACED_RATE)
        return mis_limiter

    alpaca_source: alpaca.AlpacaSource | None = None

    def shared_alpaca() -> alpaca.AlpacaSource:
        """Returns the Alpaca source shared by the US exchange venues.

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

    sinopac_source: sinopac.SinopacSource | None = None

    def shared_sinopac() -> sinopac.SinopacSource | None:
        """Returns the Shioaji source shared by ``twse`` and ``tpex``.

        Returns:
            Source, or ``None`` when not configured.
        """
        nonlocal sinopac_source
        if sinopac_config is None:
            return None
        if sinopac_source is None:
            sinopac_source = sinopac.SinopacSource(
                sinopac_config, clock, factory=sinopac_factory
            )
            sinopac_source.check_sdk()
        return sinopac_source

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
                sinopac=shared_sinopac(),
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
                sinopac=shared_sinopac(),
                bar_archive=bar_archive,
            )
        elif venue in alpaca.VENUES:
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
