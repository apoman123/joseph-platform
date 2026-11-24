# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared pytest fixtures for the market engine test suite."""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import httpx
import jsonschema
import pytest
import referencing
import respx

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
        venue: Venue directory.
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
        payload: Body.

    Returns:
        Response.
    """
    return httpx.Response(200, json=payload)


def _twse_openapi(request: httpx.Request) -> httpx.Response:
    """Serves the TWSE OpenAPI fixtures.

    Args:
        request: Request.

    Returns:
        Response.
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
        request: Request.

    Returns:
        Response (TWSE's no-data body for anything not captured).
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


def mis_response(
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
        request: Request.

    Returns:
        Response.
    """
    channels = request.url.params.get("ex_ch", "").split("|")
    return _ok(mis_response([c for c in channels if c]))


_TPEX_OPENAPI = {
    "/openapi/v1/tpex_mainboard_quotes": "mainboard_quotes",
    "/openapi/v1/mopsfin_t187ap03_O": "t187ap03_O",
    "/openapi/v1/tpex_ipo_no_limit": "ipo_no_limit",
    "/openapi/v1/tpex_cmode": "cmode",
}


def tpex_empty_month(code: str, date: str) -> dict[str, Any]:
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
        request: Request.

    Returns:
        Response (TPEx's empty month for months not captured, its no-data
        body for years not published).
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
        return _ok(tpex_empty_month(code, date))
    return httpx.Response(404)


def _nasdaqtrader(request: httpx.Request) -> httpx.Response:
    """Serves the symbol-directory slices.

    Args:
        request: Request.

    Returns:
        Response.
    """
    name = request.url.path.rsplit("/", 1)[-1]
    path = _VENUE_FIXTURES / "nasdaqtrader" / name
    if not path.exists():
        return httpx.Response(404)
    return httpx.Response(200, text=path.read_text(encoding="utf-8"))


def _alpaca(request: httpx.Request) -> httpx.Response:
    """Serves Alpaca fixtures (401 without key headers, like Alpaca).

    Args:
        request: Request.

    Returns:
        Response.
    """
    if not request.headers.get("APCA-API-KEY-ID"):
        return httpx.Response(401, json={"message": "unauthorized."})
    path, params = request.url.path, request.url.params
    if path == "/v2/stocks/snapshots":
        wanted = set(params.get("symbols", "").split(","))
        rows = _fixture("alpaca", "snapshots")
        return _ok({k: v for k, v in rows.items() if k in wanted})
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
        request: Request.

    Returns:
        Response.
    """
    year = request.url.path.split("/")[-2]
    curve = (
        "real" if "real" in request.url.params.get("type", "") else "nominal"
    )
    path = _VENUE_FIXTURES / "treasury" / f"{curve}_{year}.csv"
    if path.exists():
        return httpx.Response(200, text=path.read_text(encoding="utf-8"))
    return httpx.Response(200, text='Date,"3 Mo","10 Yr"\n')


def install_exchange_routes(router: respx.Router) -> None:
    """Registers the TWSE, TPEx, Nasdaq Trader, Alpaca and Treasury routes.

    Args:
        router: respx router.
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
        ``install(router)``.
    """
    return install_exchange_routes
