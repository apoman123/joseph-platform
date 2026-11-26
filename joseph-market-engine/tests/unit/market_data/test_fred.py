# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""FRED rate-series identity, missing observations and daily-only tests."""

from __future__ import annotations

import datetime
import decimal

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import fred
from joseph.market_data.adapters import http
from joseph.market_data.adapters import treasury
from joseph.platform import config

_ID = instruments.InstrumentId.parse("treasury:rate:FRED_DGS10")


async def test_fred_daily_observations_skip_missing_values() -> None:
    """Preserve FRED provenance and never invent a bar for a missing day."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return a FRED observation response for a requested series.

        Args:
            request: HTTP request with FRED query parameters.

        Returns:
            Two values and one missing observation.
        """
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-11-23", "value": "4.10"},
                    {"date": "2026-11-24", "value": "."},
                    {"date": "2026-11-25", "value": "4.20"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    now = int(
        datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC).timestamp() * 1000
    )
    source = fred.FredSource(
        "fred-test-key",
        http.VenueHttp(_ID.venue, fred.REST_URL, client=client),
        clock_lib.ManualClock(now),
    )
    start = int(
        datetime.datetime(2026, 11, 23, tzinfo=datetime.UTC).timestamp() * 1000
    )
    rows = await source.fetch_candles(_ID, timeframes.Timeframe.D1, start, now)
    assert [row.close for row in rows] == [
        decimal.Decimal("4.10"),
        decimal.Decimal("4.20"),
    ]
    assert {row.source for row in rows} == {fred.SOURCE}
    assert all(row.base_volume == 0 for row in rows)
    assert requests[0].url.params["series_id"] == "DGS10"
    assert requests[0].url.params["file_type"] == "json"
    assert requests[0].url.params["api_key"] == "fred-test-key"
    with pytest.raises(errors.UnsupportedError, match="daily only"):
        await source.fetch_candles(_ID, timeframes.Timeframe.H1, start, now)
    await source.close()


async def test_fred_series_are_distinct_from_treasury_par_yields() -> None:
    """Keep FRED observations under separate, non-tradable series IDs."""
    source = fred.FredSource(
        "fred-test-key",
        http.VenueHttp(_ID.venue, fred.REST_URL),
        clock_lib.ManualClock(0),
    )
    listed = source.list_instruments()
    assert any(item.key.instrument_id == _ID for item in listed)
    assert all(item.source == fred.SOURCE for item in listed)
    assert (
        fred.series_id(instruments.InstrumentId.parse("treasury:rate:UST10Y"))
        is None
    )
    await source.close()


async def test_factory_lists_fred_without_replacing_treasury_series() -> None:
    """Expose both free daily sources under distinct instrument IDs."""
    adapter = factory.build_adapters(
        [instruments.Venue.TREASURY],
        clock_lib.ManualClock(0),
        crypto_source="native",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
        fred_api_key="fred-test-key",
    )[instruments.Venue.TREASURY]
    assert isinstance(adapter, treasury.TreasuryAdapter)
    listed = await adapter.list_instruments()
    ids = {str(item.key.instrument_id) for item in listed}
    assert "treasury:rate:UST10Y" in ids
    assert "treasury:rate:FRED_DGS10" in ids
    assert adapter.capabilities().timeframes == (
        timeframes.Timeframe.D1,
        timeframes.Timeframe.W1,
        timeframes.Timeframe.MN1,
    )
    await adapter.close()


def test_fred_key_is_optional_and_redacted() -> None:
    """Expose FRED instruments only when a key is configured securely."""
    without = config.Settings.from_env({})
    assert without.fred_api_key is None
    with_key = config.Settings.from_env({"JOSEPH_FRED_API_KEY": "fred-secret"})
    assert with_key.fred_api_key == "fred-secret"
    assert "fred-secret" not in repr(with_key)
