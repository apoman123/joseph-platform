# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for the indicators API router."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import fastapi
import pytest
from fastapi import responses
from fastapi import testclient

from joseph.api.routers import indicators as indicators_router
from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.indicators import catalog
from joseph.indicators import service
from joseph.indicators import testing

TF = timeframes.Timeframe
BTC = str(testing.DEFAULT_INSTRUMENT)


class _Source:
    """In-memory candle source recording requested windows.

    Attributes:
        requests: ``(timeframe, start_ms, end_ms)`` of every request, in order.
    """

    def __init__(self, data: dict[TF, list[market.Candle]]) -> None:
        """Stores candles.

        Args:
            data: Candles per timeframe.
        """
        self._data = data
        self.requests: list[tuple[TF, int, int]] = []

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: TF,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns candles for the default instrument and records the request.

        Args:
            instrument_id: Requested instrument.
            timeframe: Timeframe to serve.
            start_ms: Window start, inclusive, epoch ms.
            end_ms: Window end, exclusive, epoch ms.

        Returns:
            The stored candles starting in the window; empty for any other
            instrument.
        """
        self.requests.append((timeframe, start_ms, end_ms))
        if instrument_id != testing.DEFAULT_INSTRUMENT:
            return []
        return [
            c
            for c in self._data.get(timeframe, [])
            if start_ms <= c.start_ms < end_ms
        ]


@pytest.fixture(scope="module")
def data() -> dict[TF, list[market.Candle]]:
    """Builds 1m/5m/1h/1d data, shared by the module's tests.

    Returns:
        Candles per timeframe, aggregated from one 1m walk.
    """
    m1 = testing.random_walk(2400, seed=31)
    return {
        TF.M1: m1,
        TF.M5: testing.aggregate(m1, TF.M5),
        TF.H1: testing.aggregate(m1, TF.H1),
        TF.D1: testing.aggregate(m1, TF.D1),
    }


@pytest.fixture
def client(data: dict[TF, list[market.Candle]]) -> testclient.TestClient:
    """Builds an app with the router and a minimal error mapping.

    Args:
        data: Candles per timeframe to serve.

    Returns:
        A client for the app; its clock is at the end of the 1m data.
    """
    registry = catalog.build_default_registry()
    query = service.IndicatorQueryService(
        _Source(data), registry, clock.ManualClock(data[TF.M1][-1].end_ms)
    )
    app = fastapi.FastAPI()
    app.include_router(indicators_router.build_router(query, registry))

    @app.exception_handler(errors.JosephError)
    async def _handle(
        _: fastapi.Request, err: errors.JosephError
    ) -> responses.JSONResponse:
        """Maps domain errors to a JSON body (stand-in for the API layer).

        Args:
            err: Domain error.

        Returns:
            JSON error response.
        """
        return responses.JSONResponse(
            status_code=404 if err.code == "NOT_FOUND" else 400,
            content={"code": err.code, "message": err.message},
        )

    return testclient.TestClient(app)


def test_lists_plugins(client: testclient.TestClient) -> None:
    """GET /v1/indicators/plugins returns the nine built-ins in order."""
    body = client.get("/v1/indicators/plugins").json()
    assert [p["plugin_id"] for p in body["plugins"]] == [
        "sma",
        "ema",
        "anchored_vwap",
        "regression_channel",
        "bollinger_bands",
        "rsi",
        "atr",
        "volume_delta",
        "pivot_points",
    ]
    assert body["plugins"][1]["display"]["default_visible"] is True
    assert body["plugins"][8]["output_kinds"][0] == "PIVOT_P"


def test_snapshot_set_filter(client: testclient.TestClient) -> None:
    """The set parameter limits plugins."""
    body = client.get(
        "/v1/indicators/snapshot",
        params={
            "instrument_id": BTC,
            "tf": "5m",
            "set": "ema,rsi",
        },
    ).json()
    assert {s["plugin_id"] for s in body["snapshots"]} == {"ema", "rsi"}


def test_aois_are_point_in_time(
    client: testclient.TestClient, data: dict[TF, list[market.Candle]]
) -> None:
    """at_ms never exposes structures confirmed after that time."""
    at_ms = data[TF.M5][300].end_ms
    body = client.get(
        "/v1/aois", params={"instrument_id": BTC, "tf": "5m", "at_ms": at_ms}
    ).json()
    assert body["as_of_ms"] == at_ms
    assert body["feed_state"] == "LIVE"
    assert {a["source"] for a in body["aois"]} == {"pivot_points"}
    assert all(a["confirmed_at_ms"] <= at_ms for a in body["aois"])
    assert all(e["confirmed_at_ms"] <= at_ms for e in body["events"])
    latest = client.get(
        "/v1/aois", params={"instrument_id": BTC, "tf": "5m"}
    ).json()
    assert latest["as_of_ms"] == data[TF.M5][-1].end_ms


def test_daily_aois_skip_incompatible_default_pivots(
    client: testclient.TestClient,
) -> None:
    """A default daily AOI query works while explicit daily pivots fail."""
    query = {"instrument_id": BTC, "tf": "1d"}
    default = client.get("/v1/aois", params=query)
    assert default.status_code == 200
    assert default.json()["timeframe"] == "1d"

    explicit = client.get("/v1/aois", params={**query, "set": "pivot_points"})
    assert explicit.status_code == 400
    assert "chart must be shorter" in explicit.json()["message"]


def test_invalid_parameters_are_rejected(client: testclient.TestClient) -> None:
    """Bad ids and timeframes fail validation; unknown plugins are 404."""
    bad_tf = client.get("/v1/aois", params={"instrument_id": BTC, "tf": "7m"})
    assert bad_tf.status_code == 400
    assert bad_tf.json()["code"] == "VALIDATION_FAILED"
    bad_id = client.get(
        "/v1/aois", params={"instrument_id": "BTCUSDT", "tf": "5m"}
    )
    assert bad_id.json()["code"] == "VALIDATION_FAILED"
    unknown = client.get(
        "/v1/aois", params={"instrument_id": BTC, "tf": "5m", "set": "nope"}
    )
    assert unknown.status_code == 404


def test_missing_data_is_reported(client: testclient.TestClient) -> None:
    """An instrument without candles yields MISSING_DATA, not zeros."""
    body = client.get(
        "/v1/aois", params={"instrument_id": "hyperliquid:perp:BTC", "tf": "5m"}
    ).json()
    assert body["code"] == "MISSING_DATA"


def test_service_requests_bounded_windows(
    data: dict[TF, list[market.Candle]],
) -> None:
    """Intrabar history is fetched only for the recent window."""
    source = _Source(data)
    query = service.IndicatorQueryService(
        source,
        catalog.build_default_registry(),
        clock.ManualClock(data[TF.M1][-1].end_ms),
        service.QueryConfig(lookback_bars=200, intrabar_lookback_bars=30),
    )
    asyncio.run(
        query.snapshots(testing.DEFAULT_INSTRUMENT, TF.M5, ["volume_delta"])
    )
    windows = {tf: end - start for tf, start, end in source.requests}
    assert windows[TF.M1] < windows[TF.M5]


async def test_monthly_series_caps_intrabar_history() -> None:
    """Caps lower-timeframe downloads while retaining missing coverage.

    Returns:
        None. Validates requested history and older null delta values.
    """
    start = TF.MN1.floor(testing.DEFAULT_START_MS)
    months = [
        testing.make_candle(TF.MN1, TF.MN1.shift(start, i), 100, 101, 99, 100)
        for i in range(24)
    ]
    now = months[-1].end_ms
    source = _Source({TF.MN1: months})
    query = service.IndicatorQueryService(
        source,
        catalog.build_default_registry(),
        clock.ManualClock(now),
        service.QueryConfig(lookback_bars=24, max_intrabar_bars=100),
    )
    result = await query.series(
        testing.DEFAULT_INSTRUMENT, TF.MN1, "volume_delta"
    )
    hourly = next(request for request in source.requests if request[0] is TF.H1)
    assert sessions.CONTINUOUS.bars_between(*hourly) == 100
    points = result["points"]
    assert isinstance(points, list)
    assert len(points) == 24
    assert all(point["values"]["delta_pct"] is None for point in points)
    assert all(point["values"]["intrabar_count"] == 0 for point in points)
