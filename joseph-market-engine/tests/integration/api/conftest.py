# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""API test fixtures: the DEMO app, a fixture-backed venue app, tokens."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import testclient

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.platform import config

_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_CAPTURE_MS = 1_795_277_530_000
"""Capture time of the venue fixtures; clocks start here so data is fresh."""


def _load(venue: str, name: str) -> Any:
    """Loads one captured response.

    Args:
        venue: Venue directory under ``fixtures/venues``.
        name: Fixture name without extension.

    Returns:
        The parsed JSON.
    """
    return json.loads((_FIXTURES / venue / f"{name}.json").read_text())


def _json(payload: Any) -> httpx.Response:
    """Builds a 200 JSON response.

    Args:
        payload: Decoded JSON body.

    Returns:
        The response.
    """
    return httpx.Response(200, json=payload)


def _binance(request: httpx.Request) -> httpx.Response:
    """Serves Binance fixtures by request path.

    Args:
        request: Request to the Binance futures host.

    Returns:
        The matching fixture (all-symbol variants when no ``symbol`` is
        given), or 404 for an unknown path.
    """
    path = request.url.path
    single = "symbol" in request.url.params
    if path.endswith("/exchangeInfo"):
        return _json(_load("binance", "exchange_info"))
    if path.endswith("/fundingInfo"):
        return _json(_load("binance", "funding_info"))
    if path.endswith("/klines"):
        return _json(_load("binance", "klines_5m"))
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
    """Serves Hyperliquid fixtures by the ``type`` of the posted body.

    Args:
        request: Request to the Hyperliquid info endpoint.

    Returns:
        The matching fixture, or 422 for an unknown request type.
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
    """Serves Lighter fixtures by request path.

    Args:
        request: Request to the Lighter host.

    Returns:
        The matching fixture, or 404 for an unknown path.
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
    if path.endswith("/fundings"):
        return _json(_load("lighter", "fundings"))
    if path.endswith("/orderBookOrders"):
        return _json(_load("lighter", "order_book_orders"))
    return httpx.Response(404)


def _bitget(request: httpx.Request) -> httpx.Response:
    """Serves Bitget fixtures by request path suffix.

    Args:
        request: Request to the Bitget host.

    Returns:
        The fixture of the first matching suffix, or 404.
    """
    path = request.url.path
    for suffix, name in (
        ("/contracts", "contracts"),
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
    """Serves MEXC fixtures by request path.

    Args:
        request: Request to the MEXC contract host.

    Returns:
        The matching fixture (all-symbol tickers when no ``symbol`` is
        given), or 404 for an unknown path.
    """
    path = request.url.path
    if path.endswith("/detail"):
        return _json(_load("mexc", "detail"))
    if "/kline/" in path:
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
    """Registers the crypto venue fixture handlers on a respx router.

    Args:
        router: Router to add the host routes to.
    """
    router.route(host="fapi.binance.com").mock(side_effect=_binance)
    router.route(host="api.hyperliquid.xyz").mock(side_effect=_hyperliquid)
    router.route(host="mainnet.zklighter.elliot.ai").mock(side_effect=_lighter)
    router.route(host="api.bitget.com").mock(side_effect=_bitget)
    router.route(host="contract.mexc.com").mock(side_effect=_mexc)


_TOKEN = "it-token-tenant-01"
_TOKEN_B = "it-token-tenant-02"


def _settings(**overrides: Any) -> config.Settings:
    """Builds DEMO-only settings on in-memory SQLite with two tenants.

    Args:
        **overrides: ``Settings`` field overrides.

    Returns:
        The settings.
    """
    base = config.Settings(
        dev_tokens=config.parse_dev_tokens(
            f"{_TOKEN}:tenant-01:user-01,{_TOKEN_B}:tenant-02:user-02"
        ),
        database_url="sqlite://",
        demo_only=True,
        poll_interval_s=1.0,
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def binance_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Returns the fixture handler for Binance requests.

    Returns:
        The handler, for tests that wrap it to inject failures.
    """
    return _binance


@pytest.fixture
def tokens() -> tuple[str, str]:
    """Returns bearer tokens of tenant-01 and tenant-02.

    Returns:
        ``(tenant-01 token, tenant-02 token)``.
    """
    return _TOKEN, _TOKEN_B


@pytest.fixture
def demo_container() -> bootstrap.Container:
    """Builds a DEMO-only container.

    Returns:
        The container, not yet started.
    """
    return bootstrap.build_container(_settings())


@pytest.fixture
def demo_client(
    demo_container: bootstrap.Container,
) -> Iterator[testclient.TestClient]:
    """Serves the DEMO app.

    Args:
        demo_container: Container to serve.

    Yields:
        A client for the running app; it stops when the test ends.
    """
    app = bootstrap.create_application(demo_container)
    with testclient.TestClient(app) as client:
        yield client


@pytest.fixture
def venue_client(
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[tuple[testclient.TestClient, respx.Router]]:
    """Serves an app with every venue backed by captured fixtures.

    Args:
        exchange_routes: Installer of the exchange and rate venue routes.

    Yields:
        Test client and the respx router (to inject failures).
    """
    with respx.mock(assert_all_called=False) as router:
        _install_routes(router)
        exchange_routes(router)
        container = bootstrap.build_container(
            _settings(demo_only=False),
            clock=clock_lib.ManualClock(_CAPTURE_MS),
            http_client=httpx.AsyncClient(),
        )
        app = bootstrap.create_application(container)
        with testclient.TestClient(app) as client:
            yield client, router
