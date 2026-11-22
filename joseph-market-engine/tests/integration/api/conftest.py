"""Fixtures: DEMO app, fixture-backed venue app and test tokens."""

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
        return _json(load("binance", "klines_5m"))
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
        return _json(load("lighter", "candles_5m"))
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


_TOKEN = "it-token-tenant-01"
_TOKEN_B = "it-token-tenant-02"


def _settings(**overrides: object) -> config.Settings:
    """Builds test settings with two tenants.

    Args:
        **overrides: Field overrides.

    Returns:
        Settings.
    """
    base = config.Settings(
        dev_tokens=config.parse_dev_tokens(
            f"{_TOKEN}:tenant-01:user-01,{_TOKEN_B}:tenant-02:user-02"
        ),
        database_url="sqlite://",
        demo_only=True,
        poll_interval_s=1.0,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def binance_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Returns the fixture handler for Binance requests.

    Returns:
        Handler.
    """
    return _binance


@pytest.fixture
def tokens() -> tuple[str, str]:
    """Returns bearer tokens of tenant-01 and tenant-02.

    Returns:
        Token pair.
    """
    return _TOKEN, _TOKEN_B


@pytest.fixture
def demo_container() -> bootstrap.Container:
    """Builds a DEMO-only container.

    Returns:
        Container.
    """
    return bootstrap.build_container(_settings())


@pytest.fixture
def demo_client(
    demo_container: bootstrap.Container,
) -> Iterator[testclient.TestClient]:
    """Serves the DEMO app.

    Args:
        demo_container: Container.

    Yields:
        Test client.
    """
    app = bootstrap.create_application(demo_container)
    with testclient.TestClient(app) as client:
        yield client


@pytest.fixture
def venue_client() -> Iterator[tuple[testclient.TestClient, respx.Router]]:
    """Serves an app with every venue backed by captured fixtures.

    Yields:
        Test client and the respx router (to inject failures).
    """
    with respx.mock(assert_all_called=False) as router:
        install_routes(router)
        container = bootstrap.build_container(
            _settings(demo_only=False),
            clock=clock_lib.ManualClock(CAPTURE_MS),
            http_client=httpx.AsyncClient(),
        )
        app = bootstrap.create_application(container)
        with testclient.TestClient(app) as client:
            yield client, router
