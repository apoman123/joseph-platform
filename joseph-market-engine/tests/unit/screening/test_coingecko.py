# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the optional CoinGecko trending adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from joseph.domain import errors
from joseph.screening.adapters import coingecko


async def test_trending_symbols_are_parsed() -> None:
    """Trending coins become upper-case symbols."""
    with respx.mock() as router:
        router.get(coingecko.TRENDING_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "coins": [
                        {"item": {"symbol": "sol"}},
                        {"item": {"symbol": "Btc"}},
                        {"item": {}},
                    ]
                },
            )
        )
        source = coingecko.CoinGeckoTrending(client=httpx.AsyncClient())
        assert await source.symbols() == frozenset({"SOL", "BTC"})
        await source.close()


async def test_trending_failure_is_venue_unavailable() -> None:
    """Outages raise a domain error the service tolerates."""
    with respx.mock() as router:
        router.get(coingecko.TRENDING_URL).mock(
            return_value=httpx.Response(503)
        )
        source = coingecko.CoinGeckoTrending()
        with pytest.raises(errors.VenueUnavailableError):
            await source.symbols()
        await source.close()
