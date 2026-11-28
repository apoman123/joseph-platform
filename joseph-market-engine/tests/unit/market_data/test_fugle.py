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
from joseph.domain import market
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


@pytest.mark.parametrize("adjusted", [True, False])
async def test_0050_split_uses_confirmed_provider_adjustment(
    adjusted: bool,
) -> None:
    """Keep split-day continuity using provider OHLC, preserving raw volumes.

    Args:
        adjusted: Whether the server requests corporate-action adjusted history.

    Returns:
        None after checking split-day prices and the explicit raw-price opt-out.
    """
    instrument = instruments.InstrumentId.parse("twse:etf:0050")

    def respond(request: httpx.Request) -> httpx.Response:
        """Return provider-shaped raw or adjusted split-day observations.

        Args:
            request: Authenticated daily history request with adjustment policy.

        Returns:
            Confirmed daily OHLC and unchanged exchange-reported volumes.
        """
        assert request.url.params["adjusted"] == str(adjusted).lower()
        assert request.url.params["timeframe"] == "D"
        prices = ["45.90", "46.30"] if adjusted else ["188.65", "47.50"]
        dates = ["2025-06-10", "2025-06-18"]
        rows = []
        for day, price in zip(dates, prices, strict=True):
            rows.append(
                {
                    "date": day,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 10,
                }
            )
        return httpx.Response(
            200, json={"exchange": "TWSE", "adjusted": adjusted, "data": rows}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(instrument.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 13, 35)),
        adjusted_daily=adjusted,
    )
    start = int(datetime.datetime(2025, 6, 10, tzinfo=_TPE).timestamp() * 1000)
    end = int(datetime.datetime(2025, 6, 19, tzinfo=_TPE).timestamp() * 1000)
    fetched = await source.fetch_candle_coverage(
        instrument, timeframes.Timeframe.D1, start, end
    )
    assert len(fetched.candles) == 2
    before, after = fetched.candles
    change = abs(after.open / before.close - 1)
    assert (change < decimal.Decimal("0.03")) is adjusted
    assert before.base_volume == after.base_volume == decimal.Decimal(10)
    expected = (
        market.PriceAdjustment.ALL if adjusted else market.PriceAdjustment.RAW
    )
    assert source.price_adjustment(timeframes.Timeframe.D1) is expected
    assert source.price_adjustment(timeframes.Timeframe.W1) is expected
    assert source.price_adjustment(timeframes.Timeframe.M1) is expected
    assert before.source == (
        "fugle.rest.adjusted" if adjusted else fugle.SOURCE
    )
    await source.close()


async def test_unconfirmed_adjustment_fails_instead_of_mixing_prices() -> None:
    """Reject an ignored adjustment request rather than label raw data adjusted.

    Returns:
        None after checking the actionable provider error and closing clients.
    """
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"exchange": "TWSE", "adjusted": False, "data": []}
            )
        )
    )
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(_ID.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 13, 35)),
    )
    with pytest.raises(errors.VenueUnavailableError, match="did not confirm"):
        await source.fetch_candle_coverage(
            _ID, timeframes.Timeframe.D1, _ms(24, 9, 0), _ms(24, 13, 30)
        )
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


@pytest.mark.parametrize(
    "tf", [tf for tf in timeframes.Timeframe if not tf.is_seconds]
)
@pytest.mark.parametrize(
    "symbol,factor",
    [
        ("twse:etf:0050", "0.25"),
        ("twse:stock:2330", "0.98"),
        ("tpex:stock:6488", "4"),
    ],
)
async def test_all_timeframes_share_evidenced_prices_before_aggregation(
    tf: timeframes.Timeframe,
    symbol: str,
    factor: str,
) -> None:
    """Normalize split, dividend and reverse-split examples on every interval.

    Args:
        tf: Every minute-or-longer interval supported by the Taiwan adapter.
        symbol: ETF, listed stock or OTC stock without symbol-specific rules.
        factor: Provider's pre-action adjusted/raw daily close ratio.

    Returns:
        None after verifying consistent OHLC, unchanged volume and provenance.
    """
    instrument = instruments.InstrumentId.parse(symbol)
    ratio = decimal.Decimal(factor)
    queries: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Serve matched raw minutes, raw days and confirmed adjusted days.

        Args:
            request: Authenticated candle request on the requested exchange.

        Returns:
            Provider-shaped observations spanning the known action date.
        """
        params = dict(request.url.params)
        queries.append(params)
        daily = params["timeframe"] == "D"
        adjusted = params.get("adjusted") == "true"
        rows = []
        for index, day in enumerate(["2025-06-10", "2025-06-18"]):
            price = decimal.Decimal(100 if index == 0 else 102)
            if index == 1 or adjusted:
                price *= ratio
            rows.append(
                {
                    "date": day if daily else f"{day}T09:00:00+08:00",
                    "open": str(price),
                    "high": str(price),
                    "low": str(price),
                    "close": str(price),
                    "volume": 2000 if daily else 2,
                }
            )
        return httpx.Response(
            200,
            json={
                "symbol": instrument.venue_market_id,
                "exchange": instrument.venue.value.upper(),
                "adjusted": adjusted,
                "data": rows,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(instrument.venue, fugle.REST_URL, client=client),
        clock_lib.ManualClock(_ms(25, 13, 35)),
    )
    start = int(datetime.datetime(2025, 6, 1, tzinfo=_TPE).timestamp() * 1000)
    end = int(datetime.datetime(2025, 7, 1, tzinfo=_TPE).timestamp() * 1000)
    try:
        fetched = await source.fetch_candle_coverage(instrument, tf, start, end)
        assert fetched.candles
        assert min(bar.low for bar in fetched.candles) == 100 * ratio
        assert max(bar.high for bar in fetched.candles) == 102 * ratio
        assert sum(bar.base_volume for bar in fetched.candles) == 4000
        assert all(bar.source.endswith(".adjusted") for bar in fetched.candles)
        assert source.price_adjustment(tf) is market.PriceAdjustment.ALL
        if tf.duration_ms < timeframes.Timeframe.D1.duration_ms:
            daily = [q for q in queries if q["timeframe"] == "D"]
            assert {q["adjusted"] for q in daily} == {"true", "false"}
    finally:
        await source.close()


def _minute_without_daily_evidence(request: httpx.Request) -> httpx.Response:
    """Serve one historical minute but no daily adjustment observations.

    Args:
        request: Minute history or daily evidence request.

    Returns:
        Raw minute or the documented missing-data status.
    """
    if request.url.params["timeframe"] == "D":
        return httpx.Response(404)
    return httpx.Response(
        200,
        json={
            "exchange": "TWSE",
            "data": [
                {
                    "date": "2026-11-24T09:00:00+08:00",
                    "open": 100,
                    "high": 100,
                    "low": 100,
                    "close": 100,
                    "volume": 1,
                }
            ],
        },
    )


async def test_intraday_missing_daily_evidence_fails_without_guessing() -> None:
    """Require daily evidence before bridging a historical split.

    Returns:
        None after checking the explicit provider failure.
    """
    source = fugle.FugleSource(
        "free-key",
        http.VenueHttp(
            _ID.venue,
            fugle.REST_URL,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(_minute_without_daily_evidence)
            ),
        ),
        clock_lib.ManualClock(_ms(25, 13, 35)),
    )
    try:
        with pytest.raises(
            errors.VenueUnavailableError, match="evidence is unavailable"
        ):
            await source.fetch_candle_coverage(
                _ID, timeframes.Timeframe.H1, _ms(24, 9, 0), _ms(24, 13, 30)
            )
    finally:
        await source.close()
