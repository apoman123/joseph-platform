# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared pytest fixtures for the market engine test suite."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import pathlib
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import httpx
import jsonschema
import pytest
import referencing
import respx

from joseph.market_data.adapters import http


class _SimulatedRateLimiter(http.RateLimiter):
    """A venue token bucket on simulated time.

    Waiting for a token advances the bucket's own clock instead of the wall
    clock, so requests keep the production order and spacing (in simulated
    seconds) without a test waiting for them.
    """

    def __init__(
        self,
        rate_per_s: float,
        burst: int,
        *,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Initializes the bucket full on its own simulated clock.

        Args:
            rate_per_s: Sustained rate, as in production.
            burst: Bucket capacity, as in production.
            monotonic: Clock to use instead of the simulated one.
            sleep: Sleep to use instead of advancing the simulated clock.
        """
        self._simulated_s = 0.0
        super().__init__(
            rate_per_s,
            burst,
            monotonic=monotonic or self._simulated_now,
            sleep=sleep or self._simulated_sleep,
        )

    def _simulated_now(self) -> float:
        """Returns the simulated time.

        Returns:
            Simulated seconds since the bucket was created.
        """
        return self._simulated_s

    async def _simulated_sleep(self, seconds: float) -> None:
        """Advances the simulated clock and lets other tasks run.

        Args:
            seconds: Simulated seconds to advance.
        """
        self._simulated_s += seconds
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _simulated_request_pacing(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs venue request pacing in simulated time, except in live tests.

    Fixture-backed venues answer at once, so real pacing would only make
    the suite wait (TWSE allows one request per 2 s). Tests marked
    ``network`` or ``sinopac_data`` talk to live venues and keep the real
    pacing.

    Args:
        request: The requesting test.
        monkeypatch: Replaces ``http.RateLimiter`` for the test.
    """
    if request.node.get_closest_marker(
        "network"
    ) or request.node.get_closest_marker("sinopac_data"):
        return
    monkeypatch.setattr(http, "RateLimiter", _SimulatedRateLimiter)


_DEFAULT_CONTRACTS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "schemas"
)
_BASE_URI = "https://schemas.joseph.local/v1/"


def _contracts_dir() -> pathlib.Path:
    """Returns the directory holding the canonical JSON Schemas.

    Returns:
        ``$JOSEPH_CONTRACTS_DIR`` or the sibling public terminal package.
    """
    return pathlib.Path(
        os.environ.get("JOSEPH_CONTRACTS_DIR", str(_DEFAULT_CONTRACTS))
    )


def _build_registry() -> referencing.Registry:
    """Loads every schema file into a ``referencing`` registry.

    Returns:
        Registry keyed by each schema's ``$id``.
    """
    resources = []
    for path in sorted(_contracts_dir().glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append(
            (contents["$id"], referencing.Resource.from_contents(contents))
        )
    return referencing.Registry().with_resources(resources)


@pytest.fixture(scope="session")
def contract_validate() -> Callable[[str, str, object], None]:
    """Provides ``validate(schema_file, definition, instance)``.

    The callable raises ``jsonschema.ValidationError`` when ``instance`` does
    not conform to ``{schema_file}#/$defs/{definition}``.

    Returns:
        The validation callable.
    """
    registry = _build_registry()

    def validate(schema_file: str, definition: str, instance: object) -> None:
        """Validates ``instance`` against one contract definition.

        Args:
            schema_file: File name such as ``market.schema.json``.
            definition: ``$defs`` entry such as ``Candle``.
            instance: JSON-compatible value to validate.

        Raises:
            jsonschema.ValidationError: ``instance`` does not conform.
        """
        schema = {"$ref": f"{_BASE_URI}{schema_file}#/$defs/{definition}"}
        validator = jsonschema.Draft202012Validator(
            schema,
            registry=registry,
            format_checker=jsonschema.FormatChecker(),
        )
        validator.validate(instance)

    return validate


# Exchange and rate venues ------------------------------------------------

_VENUE_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "venues"
)
_NO_DATA = {
    "stat": "\u5f88\u62b1\u6b49\uff0c"
    "\u6c92\u6709\u7b26\u5408\u689d\u4ef6\u7684\u8cc7\u6599!"
}
"""What TWSE's www endpoints return for a month or year without data."""


def _fixture(venue: str, name: str) -> Any:
    """Loads a JSON fixture.

    Args:
        venue: Venue directory under ``fixtures/venues``.
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads(
        (_VENUE_FIXTURES / venue / f"{name}.json").read_text(encoding="utf-8")
    )


def _ok(payload: Any) -> httpx.Response:
    """Builds a JSON response.

    Args:
        payload: JSON-serializable body.

    Returns:
        A 200 response carrying ``payload``.
    """
    return httpx.Response(200, json=payload)


def _twse_openapi(request: httpx.Request) -> httpx.Response:
    """Serves the TWSE OpenAPI fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response for a known path, else a 404.
    """
    names = {
        "/v1/exchangeReport/STOCK_DAY_ALL": "stock_day_all",
        "/v1/opendata/t187ap03_L": "t187ap03_L",
        "/v1/opendata/t187ap47_L": "t187ap47_L",
        "/v1/exchangeReport/TWT88U": "twt88u",
        "/v1/exchangeReport/TWTAWU": "twtawu",
        "/v1/holidaySchedule/holidaySchedule": "holiday_openapi",
    }
    name = names.get(request.url.path)
    return httpx.Response(404) if name is None else _ok(_fixture("twse", name))


def _twse_www(request: httpx.Request) -> httpx.Response:
    """Serves STOCK_DAY months and holiday schedules.

    Args:
        request: Mocked request.

    Returns:
        The captured response, TWSE's no-data body for a month or year not
        captured, or a 404 for any other path.
    """
    params = request.url.params
    if request.url.path.endswith("/holidaySchedule/holidaySchedule"):
        path = (
            _VENUE_FIXTURES / "twse" / f"holiday_rwd_{params['date'][:4]}.json"
        )
        return _ok(
            json.loads(path.read_text("utf-8")) if path.exists() else _NO_DATA
        )
    if request.url.path.endswith("/afterTrading/STOCK_DAY"):
        path = _VENUE_FIXTURES / "twse" / f"stock_day_{params['stockNo']}.json"
        if path.exists():
            payload = json.loads(path.read_text("utf-8"))
            if payload.get("date", "")[:6] == params["date"][:6]:
                return _ok(payload)
        return _ok(_NO_DATA)
    return httpx.Response(404)


def _mis_response(
    channels: list[str], series: str = "mis_series"
) -> dict[str, Any]:
    """Builds a MIS body for channels from the newest captured responses.

    ``tse_`` channels are served from the TWSE capture, ``otc_`` channels
    from the TPEx capture, like MIS (a code under the other market's prefix
    gets the empty record).

    Args:
        channels: Requested channels such as ``tse_2330.tw``.
        series: Fixture with captured responses (in both venue folders).

    Returns:
        ``getStockInfo.jsp`` body (unknown codes get MIS's empty record).
    """
    records: dict[str, dict[str, Any]] = {}
    body: dict[str, Any] = {}
    for market, venue in (("tse", "twse"), ("otc", "tpex")):
        responses = _fixture(venue, series)["responses"]
        newest = max(
            responses, key=lambda r: r["response"]["queryTime"]["sysTime"]
        )
        body = body or newest["response"]
        for record in newest["response"]["msgArray"]:
            if record.get("c"):
                records[f"{market}_{record['c']}.tw"] = record
    return {
        **body,
        "msgArray": [
            records.get(channel, {"tv": "-", "s": "-", "c": "", "z": "-"})
            for channel in channels
        ],
    }


def _twse_mis(request: httpx.Request) -> httpx.Response:
    """Serves MIS snapshots of both markets from the captured series.

    Args:
        request: Mocked request; ``ex_ch`` lists the channels.

    Returns:
        A ``getStockInfo.jsp`` body with one record per requested channel.
    """
    channels = request.url.params.get("ex_ch", "").split("|")
    return _ok(_mis_response([c for c in channels if c]))


_TPEX_OPENAPI = {
    "/openapi/v1/tpex_mainboard_quotes": "mainboard_quotes",
    "/openapi/v1/mopsfin_t187ap03_O": "t187ap03_O",
    "/openapi/v1/tpex_ipo_no_limit": "ipo_no_limit",
    "/openapi/v1/tpex_cmode": "cmode",
}


def _tpex_empty_month(code: str, date: str) -> dict[str, Any]:
    """Returns what TPEx's tradingStock answers for a month without data.

    Args:
        code: Security code.
        date: Requested ``YYYY/MM/01``.

    Returns:
        Body with ``stat`` ``ok`` and no rows.
    """
    day = date.replace("/", "")
    return {
        "code": code,
        "date": day,
        "stat": "ok",
        "tables": [{"data": [], "date": day, "totalCount": 0}],
    }


def _tpex(request: httpx.Request) -> httpx.Response:
    """Serves the TPEx OpenAPI, tradingStock and tradingDate fixtures.

    Args:
        request: Mocked request.

    Returns:
        The captured response; TPEx's empty month for a month not
        captured, its no-data body for a year not published, or a 404 for
        any other path.
    """
    path, params = request.url.path, request.url.params
    name = _TPEX_OPENAPI.get(path)
    if name is not None:
        return _ok(_fixture("tpex", name))
    if path == "/www/zh-tw/bulletin/tradingDate":
        file = _VENUE_FIXTURES / "tpex" / f"trading_date_{params['date']}.json"
        return _ok(
            json.loads(file.read_text("utf-8")) if file.exists() else _NO_DATA
        )
    if path == "/www/zh-tw/afterTrading/tradingStock":
        code, date = params["code"], params["date"]
        month = date.replace("/", "")[:6]
        for stem in (f"trading_stock_{code}_{month}", f"trading_stock_{code}"):
            file = _VENUE_FIXTURES / "tpex" / f"{stem}.json"
            if file.exists():
                payload = json.loads(file.read_text("utf-8"))
                if payload.get("date", "")[:6] == month:
                    return _ok(payload)
        return _ok(_tpex_empty_month(code, date))
    return httpx.Response(404)


def _nasdaqtrader(request: httpx.Request) -> httpx.Response:
    """Serves the symbol-directory slices.

    Args:
        request: Mocked request; the last path segment names the file.

    Returns:
        The captured text file, or a 404 when there is none.
    """
    name = request.url.path.rsplit("/", 1)[-1]
    path = _VENUE_FIXTURES / "nasdaqtrader" / name
    if not path.exists():
        return httpx.Response(404)
    return httpx.Response(200, text=path.read_text(encoding="utf-8"))


def _alpaca(request: httpx.Request) -> httpx.Response:
    """Serves Alpaca fixtures (401 without key headers, like Alpaca).

    Args:
        request: Mocked request.

    Returns:
        The captured response for AAPL, an empty page for other symbols, a
        401 without keys, or a 404 for any other path.
    """
    if not request.headers.get("APCA-API-KEY-ID"):
        return httpx.Response(401, json={"message": "unauthorized."})
    path, params = request.url.path, request.url.params
    if path == "/v2/stocks/snapshots":
        wanted = set(params.get("symbols", "").split(","))
        rows = _fixture("alpaca", "snapshots")
        return _ok({k: v for k, v in rows.items() if k in wanted})
    if path == "/v2/stocks/bars":
        page = (
            "bars_multi_page2"
            if params.get("page_token")
            else "bars_multi_page1"
        )
        return _ok(_fixture("alpaca", page))
    if not path.startswith("/v2/stocks/AAPL/"):
        empty = {"next_page_token": None, "symbol": path.split("/")[3]}
        kind = path.rsplit("/", 1)[-1]
        return _ok({**empty, kind: []})
    if path.endswith("/bars"):
        if params.get("timeframe") == "1Day":
            return _ok(_fixture("alpaca", "bars_1day"))
        page = (
            "bars_1min_page2" if params.get("page_token") else "bars_1min_page1"
        )
        return _ok(_fixture("alpaca", page))
    if path.endswith("/trades"):
        return _ok(_fixture("alpaca", "trades"))
    if path.endswith("/quotes"):
        return _ok(_fixture("alpaca", "quotes"))
    return httpx.Response(404)


def _treasury(request: httpx.Request) -> httpx.Response:
    """Serves Treasury CSV years (header only for years not captured).

    Args:
        request: Mocked request; ``type`` picks the nominal or real curve.

    Returns:
        The captured CSV of the year, or a header-only CSV.
    """
    year = request.url.path.split("/")[-2]
    curve = (
        "real" if "real" in request.url.params.get("type", "") else "nominal"
    )
    path = _VENUE_FIXTURES / "treasury" / f"{curve}_{year}.csv"
    if path.exists():
        return httpx.Response(200, text=path.read_text(encoding="utf-8"))
    return httpx.Response(200, text='Date,"3 Mo","10 Yr"\n')


def _install_exchange_routes(router: respx.Router) -> None:
    """Registers the TWSE, TPEx, Nasdaq Trader, Alpaca and Treasury routes.

    Args:
        router: respx router to add the routes to.
    """
    router.route(host="openapi.twse.com.tw").mock(side_effect=_twse_openapi)
    router.route(host="www.twse.com.tw").mock(side_effect=_twse_www)
    router.route(host="mis.twse.com.tw").mock(side_effect=_twse_mis)
    router.route(host="www.tpex.org.tw").mock(side_effect=_tpex)
    router.route(host="www.nasdaqtrader.com").mock(side_effect=_nasdaqtrader)
    router.route(host="data.alpaca.markets").mock(side_effect=_alpaca)
    router.route(host="home.treasury.gov").mock(side_effect=_treasury)


@pytest.fixture(scope="session")
def exchange_routes() -> Callable[[respx.Router], None]:
    """Provides the installer of exchange and rate venue fixture routes.

    Returns:
        ``install(router)``, which adds the routes to a respx router.
    """
    return _install_exchange_routes


# Crypto venues -------------------------------------------------------------
# Shared by the API, screening and market-data contract suites. With
# ``history`` a handler also serves monthly bars and trade history, which
# only the contract suite reads (``crypto_history_routes``); the other
# suites see those paths as unknown (``crypto_routes``).


def _binance(
    request: httpx.Request, *, history: bool = False
) -> httpx.Response:
    """Serves Binance fixtures by request path.

    Args:
        request: Request to the Binance futures host.
        history: Whether ``1M`` klines and ``aggTrades`` are served.

    Returns:
        The matching fixture (all-symbol variants when no ``symbol`` is
        given), or 404 for an unknown path.
    """
    path = request.url.path
    single = "symbol" in request.url.params
    if path.endswith("/exchangeInfo"):
        return _ok(_fixture("binance", "exchange_info"))
    if path.endswith("/fundingInfo"):
        return _ok(_fixture("binance", "funding_info"))
    if path.endswith("/klines"):
        if history and request.url.params.get("interval") == "1M":
            return _ok(_fixture("binance", "klines_1M"))
        return _ok(_fixture("binance", "klines_5m"))
    if history and path.endswith("/aggTrades"):
        return _ok(_fixture("binance", "agg_trades"))
    if path.endswith("/premiumIndex"):
        row = _fixture("binance", "premium_index")
        return _ok(row if single else [row])
    if path.endswith("/ticker/24hr"):
        return _ok(
            _fixture("binance", "ticker_24hr")
            if single
            else _fixture("binance", "ticker_24hr_all")
        )
    if path.endswith("/ticker/bookTicker"):
        row = _fixture("binance", "book_ticker")
        return _ok(row if single else [row])
    if path.endswith("/openInterest"):
        return _ok(_fixture("binance", "open_interest"))
    if path.endswith("/depth"):
        return _ok(_fixture("binance", "depth"))
    return httpx.Response(404)


def _hyperliquid(request: httpx.Request) -> httpx.Response:
    """Serves Hyperliquid fixtures by the ``type`` of the posted body.

    Args:
        request: Request to the Hyperliquid info endpoint.

    Returns:
        The matching fixture (daily candles for a ``1d`` interval, else 5m
        candles), or 422 for an unknown request type.
    """
    body = json.loads(request.content)
    kind = body.get("type")
    if kind == "metaAndAssetCtxs":
        return _ok(_fixture("hyperliquid", "meta_and_asset_ctxs"))
    if kind == "candleSnapshot":
        if body["req"].get("interval") == "1d":
            return _ok(_fixture("hyperliquid", "candles_1d"))
        return _ok(_fixture("hyperliquid", "candles_5m"))
    if kind == "l2Book":
        return _ok(_fixture("hyperliquid", "l2_book"))
    return httpx.Response(422)


def _lighter(
    request: httpx.Request, *, history: bool = False
) -> httpx.Response:
    """Serves Lighter fixtures by request path.

    Args:
        request: Request to the Lighter host.
        history: Whether ``/trades`` (trade history) is served.

    Returns:
        The matching fixture (daily candles for a ``1d`` resolution, else
        5m candles), or 404 for an unknown path.
    """
    path = request.url.path
    if path.endswith("/orderBooks"):
        return _ok(_fixture("lighter", "order_books"))
    if path.endswith("/orderBookDetails"):
        return _ok(_fixture("lighter", "order_book_details"))
    if path.endswith("/candles"):
        if request.url.params.get("resolution") == "1d":
            return _ok(_fixture("lighter", "candles_1d"))
        return _ok(_fixture("lighter", "candles_5m"))
    if history and path.endswith("/trades"):
        return _ok(_fixture("lighter", "trades_range"))
    if path.endswith("/fundings"):
        return _ok(_fixture("lighter", "fundings"))
    if path.endswith("/orderBookOrders"):
        return _ok(_fixture("lighter", "order_book_orders"))
    return httpx.Response(404)


def _bitget(request: httpx.Request, *, history: bool = False) -> httpx.Response:
    """Serves Bitget fixtures by request path suffix.

    Args:
        request: Request to the Bitget host.
        history: Whether ``1Mutc`` candles and ``/fills-history`` are
            served.

    Returns:
        The fixture of the first matching suffix, or 404.
    """
    path = request.url.path
    if history and request.url.params.get("granularity") == "1Mutc":
        if path.endswith("/history-candles"):
            return _ok(_fixture("bitget", "history_candles_1Mutc"))
        if path.endswith("/candles"):
            return _ok(_fixture("bitget", "candles_1Mutc"))
    if history and path.endswith("/fills-history"):
        return _ok(_fixture("bitget", "fills_history"))
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
            return _ok(_fixture("bitget", name))
    return httpx.Response(404)


def _mexc(request: httpx.Request, *, history: bool = False) -> httpx.Response:
    """Serves MEXC fixtures by request path.

    Args:
        request: Request to the MEXC contract host.
        history: Whether ``Month1`` klines are served.

    Returns:
        The matching fixture (all-symbol tickers when no ``symbol`` is
        given), or 404 for an unknown path.
    """
    path = request.url.path
    if path.endswith("/detail"):
        return _ok(_fixture("mexc", "detail"))
    if "/kline/" in path:
        if history and request.url.params.get("interval") == "Month1":
            return _ok(_fixture("mexc", "kline_month1"))
        return _ok(_fixture("mexc", "kline_min5"))
    if path.endswith("/ticker"):
        return _ok(
            _fixture("mexc", "ticker")
            if "symbol" in request.url.params
            else _fixture("mexc", "ticker_all")
        )
    if "/funding_rate/" in path:
        return _ok(_fixture("mexc", "funding_rate"))
    if "/depth/" in path:
        return _ok(_fixture("mexc", "depth"))
    return httpx.Response(404)


def _install_crypto_routes(
    router: respx.Router, *, history: bool = False
) -> None:
    """Registers the Binance, Hyperliquid, Lighter, Bitget and MEXC routes.

    Args:
        router: respx router to add the routes to.
        history: Whether monthly bars and trade history are served too.
    """
    router.route(host="fapi.binance.com").mock(
        side_effect=functools.partial(_binance, history=history)
    )
    router.route(host="api.hyperliquid.xyz").mock(side_effect=_hyperliquid)
    router.route(host="mainnet.zklighter.elliot.ai").mock(
        side_effect=functools.partial(_lighter, history=history)
    )
    router.route(host="api.bitget.com").mock(
        side_effect=functools.partial(_bitget, history=history)
    )
    router.route(host="contract.mexc.com").mock(
        side_effect=functools.partial(_mexc, history=history)
    )


@pytest.fixture(scope="session")
def crypto_routes() -> Callable[[respx.Router], None]:
    """Provides the installer of crypto venue fixture routes.

    Returns:
        ``install(router)``, which adds the routes to a respx router.
    """
    return _install_crypto_routes


@pytest.fixture(scope="session")
def crypto_history_routes() -> Callable[[respx.Router], None]:
    """Provides the installer of crypto routes with history endpoints.

    Returns:
        ``install(router)``, which adds the routes of ``crypto_routes``
        plus monthly bars and trade history to a respx router.
    """
    return functools.partial(_install_crypto_routes, history=True)


@pytest.fixture
def binance_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Returns the fixture handler for Binance requests.

    Returns:
        The handler, for tests that wrap it to inject failures.
    """
    return _binance


# Keyless heatmap sources ---------------------------------------------------
# Recorded public responses, trimmed to a few rows (fixtures/heatmap).

_HEATMAP_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "heatmap"
)


def heatmap_fixture(path: str) -> Any:
    """Loads a recorded heatmap source response.

    Args:
        path: File under ``fixtures/heatmap`` without ``.json``.

    Returns:
        Parsed JSON.
    """
    return json.loads(
        (_HEATMAP_FIXTURES / f"{path}.json").read_text(encoding="utf-8")
    )


def _heatmap_public(request: httpx.Request) -> httpx.Response:
    """Serves the recorded Nasdaq.com, Wikipedia, TWSE, TPEx and CoinGecko data.

    Args:
        request: Mocked request.

    Returns:
        The recorded body; Nasdaq.com answers 403 without a browser agent
        like the live API, unknown paths 404.
    """
    host, path, params = request.url.host, request.url.path, request.url.params
    if host == "api.nasdaq.com":
        if "Mozilla" not in request.headers.get("User-Agent", ""):
            return httpx.Response(403)
        if path == "/api/screener/stocks":
            exchange = params.get("exchange", "nasdaq")
            return _ok(heatmap_fixture(f"nasdaq/screener_{exchange}"))
        if path == "/api/quote/list-type/nasdaq100":
            return _ok(heatmap_fixture("nasdaq/nasdaq100"))
    if host == "en.wikipedia.org" and path == "/w/api.php":
        return _ok(heatmap_fixture("wikipedia/sp500"))
    files = {
        "/v1/opendata/t187ap03_L": "twse/company_info",
        "/v1/exchangeReport/STOCK_DAY_ALL": "twse/stock_day_all",
        "/openapi/v1/mopsfin_t187ap03_O": "tpex/company_info",
        "/openapi/v1/tpex_mainboard_daily_close_quotes": (
            "tpex/daily_close_quotes"
        ),
    }
    if path in files:
        return _ok(heatmap_fixture(files[path]))
    if host == "api.coingecko.com" and path == "/api/v3/coins/markets":
        category = params.get("category")
        if category == "stablecoins":
            return _ok(heatmap_fixture("coingecko/markets_stablecoins"))
        if category == "decentralized-finance-defi":
            return _ok(heatmap_fixture("coingecko/markets_defi"))
        if params.get("page") == "1":
            return _ok(heatmap_fixture("coingecko/markets_page1"))
        return _ok([])
    return httpx.Response(404)


def _install_heatmap_routes(router: respx.Router) -> None:
    """Registers the keyless heatmap source routes.

    TWSE and TPEx routes match their open-data paths only, so the exchange
    venue routes of the same hosts keep working when installed after them.

    Args:
        router: respx router to add the routes to.
    """
    for host in ("api.nasdaq.com", "en.wikipedia.org", "api.coingecko.com"):
        router.route(host=host).mock(side_effect=_heatmap_public)
    for host, path in (
        ("openapi.twse.com.tw", "/v1/opendata/t187ap03_L"),
        ("openapi.twse.com.tw", "/v1/exchangeReport/STOCK_DAY_ALL"),
        ("www.tpex.org.tw", "/openapi/v1/mopsfin_t187ap03_O"),
        ("www.tpex.org.tw", "/openapi/v1/tpex_mainboard_daily_close_quotes"),
    ):
        router.route(host=host, path=path).mock(side_effect=_heatmap_public)


@pytest.fixture(scope="session")
def heatmap_routes() -> Callable[[respx.Router], None]:
    """Provides the installer of the keyless heatmap source routes.

    Returns:
        ``install(router)``, which adds the routes to a respx router.
    """
    return _install_heatmap_routes
