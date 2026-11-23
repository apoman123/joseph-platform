"""Fixtures serving captured venue responses through respx."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
CAPTURE_MS = 1_795_277_530_000


def load(venue: str, name: str) -> Any:
    """Loads one captured response.

    Args:
        venue: Venue directory.
        name: Fixture name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((FIXTURES / venue / f"{name}.json").read_text())


def _json(payload: Any) -> httpx.Response:
    """Builds a JSON response.

    Args:
        payload: Body.

    Returns:
        Response.
    """
    return httpx.Response(200, json=payload)


def _binance(request: httpx.Request) -> httpx.Response:
    """Serves Binance fixtures.

    Args:
        request: Request.

    Returns:
        Response.
    """
    path = request.url.path
    single = "symbol" in request.url.params
    if path.endswith("/exchangeInfo"):
        return _json(load("binance", "exchange_info"))
    if path.endswith("/fundingInfo"):
        return _json(load("binance", "funding_info"))
    if path.endswith("/klines"):
        if request.url.params.get("interval") == "1M":
            return _json(load("binance", "klines_1M"))
        return _json(load("binance", "klines_5m"))
    if path.endswith("/aggTrades"):
        return _json(load("binance", "agg_trades"))
    if path.endswith("/premiumIndex"):
        row = load("binance", "premium_index")
        return _json(row if single else [row])
    if path.endswith("/ticker/24hr"):
        return _json(
            load("binance", "ticker_24hr")
            if single
            else load("binance", "ticker_24hr_all")
        )
    if path.endswith("/ticker/bookTicker"):
        row = load("binance", "book_ticker")
        return _json(row if single else [row])
    if path.endswith("/openInterest"):
        return _json(load("binance", "open_interest"))
    if path.endswith("/depth"):
        return _json(load("binance", "depth"))
    return httpx.Response(404)


def _hyperliquid(request: httpx.Request) -> httpx.Response:
    """Serves Hyperliquid fixtures.

    Args:
        request: Request.

    Returns:
        Response.
    """
    body = json.loads(request.content)
    kind = body.get("type")
    if kind == "metaAndAssetCtxs":
        return _json(load("hyperliquid", "meta_and_asset_ctxs"))
    if kind == "candleSnapshot":
        if body["req"].get("interval") == "1d":
            return _json(load("hyperliquid", "candles_1d"))
        return _json(load("hyperliquid", "candles_5m"))
    if kind == "l2Book":
        return _json(load("hyperliquid", "l2_book"))
    return httpx.Response(422)


def _lighter(request: httpx.Request) -> httpx.Response:
    """Serves Lighter fixtures.

    Args:
        request: Request.

    Returns:
        Response.
    """
    path = request.url.path
    if path.endswith("/orderBooks"):
        return _json(load("lighter", "order_books"))
    if path.endswith("/orderBookDetails"):
        return _json(load("lighter", "order_book_details"))
    if path.endswith("/candles"):
        if request.url.params.get("resolution") == "1d":
            return _json(load("lighter", "candles_1d"))
        return _json(load("lighter", "candles_5m"))
    if path.endswith("/trades"):
        return _json(load("lighter", "trades_range"))
    if path.endswith("/fundings"):
        return _json(load("lighter", "fundings"))
    if path.endswith("/orderBookOrders"):
        return _json(load("lighter", "order_book_orders"))
    return httpx.Response(404)


def _bitget(request: httpx.Request) -> httpx.Response:
    """Serves Bitget fixtures.

    Args:
        request: Request.

    Returns:
        Response.
    """
    path = request.url.path
    monthly = request.url.params.get("granularity") == "1Mutc"
    if path.endswith("/history-candles") and monthly:
        return _json(load("bitget", "history_candles_1Mutc"))
    if path.endswith("/candles") and monthly:
        return _json(load("bitget", "candles_1Mutc"))
    for suffix, name in (
        ("/contracts", "contracts"),
        ("/fills-history", "fills_history"),
        ("/candles", "candles_5m"),
        ("/ticker", "ticker"),
        ("/tickers", "tickers"),
        ("/open-interest", "open_interest"),
        ("/current-fund-rate", "current_fund_rate"),
        ("/orderbook", "orderbook"),
    ):
        if path.endswith(suffix):
            return _json(load("bitget", name))
    return httpx.Response(404)


def _mexc(request: httpx.Request) -> httpx.Response:
    """Serves MEXC fixtures.

    Args:
        request: Request.

    Returns:
        Response.
    """
    path = request.url.path
    if path.endswith("/detail"):
        return _json(load("mexc", "detail"))
    if "/kline/" in path:
        if request.url.params.get("interval") == "Month1":
            return _json(load("mexc", "kline_month1"))
        return _json(load("mexc", "kline_min5"))
    if path.endswith("/ticker"):
        return _json(
            load("mexc", "ticker")
            if "symbol" in request.url.params
            else load("mexc", "ticker_all")
        )
    if "/funding_rate/" in path:
        return _json(load("mexc", "funding_rate"))
    if "/depth/" in path:
        return _json(load("mexc", "depth"))
    return httpx.Response(404)


def install_routes(router: respx.Router) -> None:
    """Registers every venue route on a respx router.

    Args:
        router: respx router.
    """
    router.route(host="fapi.binance.com").mock(side_effect=_binance)
    router.route(host="api.hyperliquid.xyz").mock(side_effect=_hyperliquid)
    router.route(host="mainnet.zklighter.elliot.ai").mock(side_effect=_lighter)
    router.route(host="api.bitget.com").mock(side_effect=_bitget)
    router.route(host="contract.mexc.com").mock(side_effect=_mexc)


@pytest.fixture
def venue_api(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[respx.Router]:
    """Serves captured fixtures for every venue host.

    Args:
        exchange_routes: Installer of the exchange and rate venue routes.

    Yields:
        Active respx router.
    """
    with respx.mock(assert_all_called=False) as router:
        install_routes(router)
        exchange_routes(router)
        yield router


@pytest.fixture
def capture_clock() -> clock_lib.ManualClock:
    """Returns a clock set to the fixture capture time.

    Returns:
        Manual clock.
    """
    return clock_lib.ManualClock(CAPTURE_MS)
