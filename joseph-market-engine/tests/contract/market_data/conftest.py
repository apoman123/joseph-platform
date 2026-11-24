# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

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

_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_CAPTURE_MS = 1_795_277_530_000
"""A time just after the crypto venue captures."""


def _load(venue: str, name: str) -> Any:
    """Loads one captured response.

    Args:
        venue: Venue directory under ``fixtures/venues``.
        name: Fixture name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / venue / f"{name}.json").read_text())


def _json(payload: Any) -> httpx.Response:
    """Builds a JSON response.

    Args:
        payload: JSON-serializable body.

    Returns:
        A 200 response carrying ``payload``.
    """
    return httpx.Response(200, json=payload)


def _binance(request: httpx.Request) -> httpx.Response:
    """Serves Binance fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response for a known path, else a 404.
    """
    path = request.url.path
    single = "symbol" in request.url.params
    if path.endswith("/exchangeInfo"):
        return _json(_load("binance", "exchange_info"))
    if path.endswith("/fundingInfo"):
        return _json(_load("binance", "funding_info"))
    if path.endswith("/klines"):
        if request.url.params.get("interval") == "1M":
            return _json(_load("binance", "klines_1M"))
        return _json(_load("binance", "klines_5m"))
    if path.endswith("/aggTrades"):
        return _json(_load("binance", "agg_trades"))
    if path.endswith("/premiumIndex"):
        row = _load("binance", "premium_index")
        return _json(row if single else [row])
    if path.endswith("/ticker/24hr"):
        return _json(
            _load("binance", "ticker_24hr")
            if single
            else _load("binance", "ticker_24hr_all")
        )
    if path.endswith("/ticker/bookTicker"):
        row = _load("binance", "book_ticker")
        return _json(row if single else [row])
    if path.endswith("/openInterest"):
        return _json(_load("binance", "open_interest"))
    if path.endswith("/depth"):
        return _json(_load("binance", "depth"))
    return httpx.Response(404)


def _hyperliquid(request: httpx.Request) -> httpx.Response:
    """Serves Hyperliquid fixtures.

    Args:
        request: Mocked ``/info`` request; its JSON ``type`` picks the reply.

    Returns:
        The captured response for a known type, else a 422.
    """
    body = json.loads(request.content)
    kind = body.get("type")
    if kind == "metaAndAssetCtxs":
        return _json(_load("hyperliquid", "meta_and_asset_ctxs"))
    if kind == "candleSnapshot":
        if body["req"].get("interval") == "1d":
            return _json(_load("hyperliquid", "candles_1d"))
        return _json(_load("hyperliquid", "candles_5m"))
    if kind == "l2Book":
        return _json(_load("hyperliquid", "l2_book"))
    return httpx.Response(422)


def _lighter(request: httpx.Request) -> httpx.Response:
    """Serves Lighter fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response for a known path, else a 404.
    """
    path = request.url.path
    if path.endswith("/orderBooks"):
        return _json(_load("lighter", "order_books"))
    if path.endswith("/orderBookDetails"):
        return _json(_load("lighter", "order_book_details"))
    if path.endswith("/candles"):
        if request.url.params.get("resolution") == "1d":
            return _json(_load("lighter", "candles_1d"))
        return _json(_load("lighter", "candles_5m"))
    if path.endswith("/trades"):
        return _json(_load("lighter", "trades_range"))
    if path.endswith("/fundings"):
        return _json(_load("lighter", "fundings"))
    if path.endswith("/orderBookOrders"):
        return _json(_load("lighter", "order_book_orders"))
    return httpx.Response(404)


def _bitget(request: httpx.Request) -> httpx.Response:
    """Serves Bitget fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response for a known path, else a 404.
    """
    path = request.url.path
    monthly = request.url.params.get("granularity") == "1Mutc"
    if path.endswith("/history-candles") and monthly:
        return _json(_load("bitget", "history_candles_1Mutc"))
    if path.endswith("/candles") and monthly:
        return _json(_load("bitget", "candles_1Mutc"))
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
            return _json(_load("bitget", name))
    return httpx.Response(404)


def _mexc(request: httpx.Request) -> httpx.Response:
    """Serves MEXC fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response for a known path, else a 404.
    """
    path = request.url.path
    if path.endswith("/detail"):
        return _json(_load("mexc", "detail"))
    if "/kline/" in path:
        if request.url.params.get("interval") == "Month1":
            return _json(_load("mexc", "kline_month1"))
        return _json(_load("mexc", "kline_min5"))
    if path.endswith("/ticker"):
        return _json(
            _load("mexc", "ticker")
            if "symbol" in request.url.params
            else _load("mexc", "ticker_all")
        )
    if "/funding_rate/" in path:
        return _json(_load("mexc", "funding_rate"))
    if "/depth/" in path:
        return _json(_load("mexc", "depth"))
    return httpx.Response(404)


def _install_routes(router: respx.Router) -> None:
    """Registers every crypto venue route on a respx router.

    Args:
        router: respx router to add the routes to.
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
        _install_routes(router)
        exchange_routes(router)
        yield router


@pytest.fixture
def capture_clock() -> clock_lib.ManualClock:
    """Returns a clock set to the fixture capture time.

    Returns:
        A manual clock at ``_CAPTURE_MS``.
    """
    return clock_lib.ManualClock(_CAPTURE_MS)
