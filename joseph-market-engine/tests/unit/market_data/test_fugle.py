# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fugle free-tier parsing, source identity and calendar alignment tests."""

from __future__ import annotations

import datetime
import decimal
import json
import zoneinfo

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import fugle
from joseph.market_data.adapters import http

_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_ID = instruments.InstrumentId.parse("twse:stock:2330")


def _ms(day: int, hour: int, minute: int) -> int:
    """Return a November 2026 Taipei local time as UTC milliseconds.

    Args:
        day: Day of month.
        hour: Taipei hour.
        minute: Taipei minute.

    Returns:
        UTC epoch milliseconds.
    """
    return int(
        datetime.datetime(2026, 11, day, hour, minute, tzinfo=_TPE).timestamp()
        * 1000
    )


async def test_intraday_auction_folds_into_last_minute() -> None:
    """Keep Fugle's separate 13:30 auction on the XTAI session grid."""
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return documented Fugle candle fields for an HTTP request.

        Args:
            request: HTTP request under test.

        Returns:
            Intraday candle response.
        """
        requests.append(str(request.url))
        assert request.headers["X-API-KEY"] == "free-key"
        return httpx.Response(
            200,
            json={
                "exchange": "TWSE",
                "data": [
                    {
                        "date": "2026-11-25T13:29:00.000+08:00",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 2,
                    },
                    {
                        "date": "2026-11-25T13:30:00.000+08:00",
                        "open": 102,
                        "high": 102,
                        "low": 102,
                        "close": 102,
                        "volume": 3,
                    },
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(_ID.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 13, 35)),
    )
    fetched = await source.fetch_candle_coverage(
        _ID, timeframes.Timeframe.M1, _ms(25, 13, 29), _ms(25, 13, 31)
    )
    assert len(fetched.candles) == 1
    candle = fetched.candles[0]
    assert candle.start_ms == _ms(25, 13, 29)
    assert candle.end_ms == _ms(25, 13, 30)
    assert candle.open == decimal.Decimal(100)
    assert candle.close == decimal.Decimal(102)
    assert candle.base_volume == decimal.Decimal(5000)
    assert candle.source == fugle.SOURCE
    assert fetched.complete
    assert len(requests) == 1
    assert "/intraday/candles/2330" in requests[0]
    await source.close()


async def test_quote_rejects_wrong_exchange() -> None:
    """Do not attach a same-code TPEx quote to a TWSE instrument."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=json.dumps({"exchange": "TPEX", "closePrice": 100}),
            )
        )
    )
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(_ID.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 10, 0)),
    )
    with pytest.raises(errors.VenueUnavailableError, match="exchange differs"):
        await source.fetch_snapshot(_ID)
    await source.close()


async def test_long_history_fetches_newest_ranges_first() -> None:
    """A bounded Fugle fetch keeps the newest chart history available."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Record the queried date window and return no trades.

        Args:
            request: Historical candle request.

        Returns:
            Fugle's documented no-data 404.
        """
        requests.append(request)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(_ID.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 13, 35)),
    )
    start = int(datetime.datetime(2023, 5, 23, tzinfo=_TPE).timestamp() * 1000)
    fetched = await source.fetch_candle_coverage(
        _ID, timeframes.Timeframe.M1, start, _ms(24, 13, 31)
    )
    assert not fetched.complete
    assert requests[0].url.params["to"] == "2026-11-24"
    assert len(requests) == 12
    await source.close()
