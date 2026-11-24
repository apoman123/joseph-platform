# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for session calendars over HTTP and the DEMO stock."""

from __future__ import annotations

import datetime
import json
import zoneinfo
from collections.abc import Callable
from typing import TypeAlias

import httpx
from fastapi import testclient

from joseph.domain import sessions
from joseph.domain import timeframes

Validate: TypeAlias = Callable[[str, str, object], None]
NY = zoneinfo.ZoneInfo("America/New_York")
ACME = "mock:stock:ACME"


def _ny(year: int, month: int, day: int, hour: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _error(response: httpx.Response, status: int, code: str) -> None:
    """Asserts a contract error response.

    Args:
        response: Response to check.
        status: Expected HTTP status.
        code: Expected error code.
    """
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_sessions_endpoint_lists_exchange_sessions(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """XNYS skips Thanksgiving and closes early after; calendars validate."""
    body = demo_client.get(
        "/v1/calendars/XNYS/sessions",
        params={"from_ms": _ny(2026, 11, 23), "to_ms": _ny(2026, 11, 28)},
    ).json()
    contract_validate("market.schema.json", "SessionList", body)
    assert body["calendar"] == "XNYS"
    assert [s["day"] for s in body["sessions"]] == [
        "2026-11-23",
        "2026-11-24",
        "2026-11-25",
        "2026-11-27",
    ]
    assert body["sessions"][-1]["close_ms"] == _ny(2026, 11, 27, 13)
    taipei = demo_client.get(
        "/v1/calendars/XTAI/sessions",
        params={"from_ms": _ny(2026, 11, 23), "to_ms": _ny(2026, 11, 28)},
    ).json()
    contract_validate("market.schema.json", "SessionList", taipei)
    always = demo_client.get(
        "/v1/calendars/24x7/sessions", params={"from_ms": 0, "to_ms": 1_000}
    ).json()
    contract_validate("market.schema.json", "SessionList", always)
    assert always["sessions"] == []


def test_sessions_endpoint_rejects_bad_windows(
    demo_client: testclient.TestClient,
) -> None:
    """Unknown calendars, long or empty windows are contract errors."""
    _error(
        demo_client.get(
            "/v1/calendars/LSE/sessions", params={"from_ms": 0, "to_ms": 1}
        ),
        404,
        "NOT_FOUND",
    )
    _error(
        demo_client.get(
            "/v1/calendars/XNYS/sessions",
            params={"from_ms": 0, "to_ms": 401 * 86_400_000},
        ),
        422,
        "VALIDATION_FAILED",
    )
    _error(
        demo_client.get(
            "/v1/calendars/XNYS/sessions", params={"from_ms": 5, "to_ms": 5}
        ),
        422,
        "VALIDATION_FAILED",
    )
    _error(
        demo_client.get("/v1/calendars/XNYS/sessions", params={"from_ms": 5}),
        422,
        "VALIDATION_FAILED",
    )


def test_demo_stock_endpoints_are_contract_valid(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """Instrument, candles, snapshot, book and venue of ACME validate."""
    checks = [
        ("/v1/venues", {}, "market.schema.json", "VenueStatusList"),
        (
            "/v1/instruments",
            {"q": "acme"},
            "market.schema.json",
            "InstrumentList",
        ),
        (
            "/v1/candles",
            {"instrument_id": ACME, "tf": "1h", "limit": 30},
            "market.schema.json",
            "CandleBatch",
        ),
        (
            "/v1/candles",
            {"instrument_id": ACME, "tf": "1d", "limit": 10},
            "market.schema.json",
            "CandleBatch",
        ),
        (
            "/v1/market/snapshot",
            {"instrument_id": ACME},
            "market.schema.json",
            "MarketSnapshot",
        ),
        (
            "/v1/market/orderbook",
            {"instrument_id": ACME, "depth": 5},
            "market.schema.json",
            "OrderBook",
        ),
    ]
    bodies = {}
    for path, params, schema, definition in checks:
        response = demo_client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        contract_validate(schema, definition, response.json())
        bodies[(path, params.get("tf"))] = response.json()
    listed = bodies[("/v1/instruments", None)]["instruments"]
    assert [i["calendar"] for i in listed] == ["XNYS"]
    hours = bodies[("/v1/candles", "1h")]
    assert hours["complete"] is True
    assert {c["taker_buy_method"] for c in hours["candles"]} == {"classified"}


def test_anchored_vwap_series_over_http_reaches_far_anchors(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """An ACME 5m anchored VWAP 2,500 session bars back is fully drawn."""
    batch = demo_client.get(
        "/v1/candles", params={"instrument_id": ACME, "tf": "5m", "limit": 1}
    ).json()
    newest = batch["candles"][-1]["start_ms"]
    anchor = sessions.XNYS.shift(timeframes.Timeframe.M5, newest, -2_500)
    response = demo_client.get(
        "/v1/indicators/series",
        params={
            "instrument_id": ACME,
            "tf": "5m",
            "plugin": "anchored_vwap",
            "from_ms": anchor,
            "settings": json.dumps(
                {"anchored_vwap": {"anchor": "time", "anchor_ms": anchor}}
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    contract_validate("indicators.schema.json", "IndicatorSeries", body)
    points = body["points"]
    assert len(points) >= 2_500
    assert all(p["values"]["anchor_in_window"] for p in points)
    assert points[0]["values"]["anchor_ms"] == points[0]["bar_start_ms"]
