# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Optional CoinGecko trending source (heat-score bonus only).

Disabled by default. Its output can only reorder results; it never changes
a qualification status.
"""

from __future__ import annotations

import httpx

from joseph.domain import errors

TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


class CoinGeckoTrending:
    """Fetches trending base-asset symbols."""

    def __init__(
        self, client: httpx.AsyncClient | None = None, timeout_s: float = 10.0
    ) -> None:
        """Initializes the source.

        Args:
            client: Optional injected client.
            timeout_s: Request timeout.
        """
        self._owns = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def symbols(self) -> frozenset[str]:
        """Returns upper-case trending symbols.

        Returns:
            Symbols such as ``{"BTC", "SOL"}``.

        Raises:
            errors.VenueUnavailableError: If the request fails.
        """
        try:
            response = await self._client.get(TRENDING_URL)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as err:
            raise errors.VenueUnavailableError(
                f"coingecko trending unavailable: {type(err).__name__}"
            ) from err
        return frozenset(
            str(coin["item"]["symbol"]).upper()
            for coin in payload.get("coins", [])
            if coin.get("item", {}).get("symbol")
        )

    async def close(self) -> None:
        """Closes the client if owned."""
        if self._owns:
            await self._client.aclose()
