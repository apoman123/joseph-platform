# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Keyless CoinGecko coin markets for the crypto heatmap.

``/api/v3/coins/markets`` lists coins by market capitalization with price,
fully diluted valuation, 24-hour volume, high and low, and price changes
over 1h, 24h, 7d, 30d and 1y. Categories select stablecoins and DeFi. The
keyless plan allows only a few requests per minute, so a rate-limit answer
blocks further requests for a back-off period.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
from collections.abc import Mapping
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.market_data.adapters import http

API_URL = "https://api.coingecko.com"
MARKETS_PATH = "/api/v3/coins/markets"
PER_PAGE = 250
"""Coins per page (the API maximum)."""
BACKOFF_MS = 90_000
"""Pause after HTTP 429 before the next request."""
STABLECOINS = "stablecoins"
DEFI = "decentralized-finance-defi"


@dataclasses.dataclass(frozen=True)
class Coin:
    """One coin market row (USD).

    Attributes:
        id: CoinGecko id, e.g. ``bitcoin``.
        symbol: Upper-case ticker, e.g. ``BTC``.
        name: Coin name.
        price: Current price, or None.
        market_cap: Market capitalization, or None when zero or missing.
        fd_market_cap: Fully diluted valuation, or None.
        volume: 24-hour volume in USD, or None.
        high_24h: 24-hour high, or None.
        low_24h: 24-hour low, or None.
        changes: Percentage change by window (``1h``, ``24h``, ``7d``,
            ``30d``, ``1y``); missing windows are absent.
        as_of_ms: CoinGecko update time, UTC epoch milliseconds, or None.
    """

    id: str
    symbol: str
    name: str
    price: decimal.Decimal | None
    market_cap: decimal.Decimal | None
    fd_market_cap: decimal.Decimal | None
    volume: decimal.Decimal | None
    high_24h: decimal.Decimal | None
    low_24h: decimal.Decimal | None
    changes: dict[str, decimal.Decimal]
    as_of_ms: int | None


def _number(value: object) -> decimal.Decimal | None:
    """Parses a JSON number.

    Args:
        value: Raw value.

    Returns:
        Decimal, or None for null and malformed values.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return decimals.to_decimal(str(value))
    except (ValueError, decimal.InvalidOperation):
        return None


def _positive(value: object) -> decimal.Decimal | None:
    """Parses a positive amount.

    Args:
        value: Raw value.

    Returns:
        Decimal above zero, or None.
    """
    number = _number(value)
    return number if number is not None and number > 0 else None


def _iso_ms(value: object) -> int | None:
    """Parses an ISO 8601 UTC timestamp.

    Args:
        value: Text such as ``2026-11-28T09:13:50.000Z``.

    Returns:
        UTC epoch milliseconds, or None.
    """
    try:
        stamp = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return int(stamp.timestamp() * 1000)


def parse_markets(payload: Any) -> list[Coin]:
    """Parses a ``coins/markets`` page.

    Args:
        payload: JSON list of coin objects.

    Returns:
        Coins in the page order.

    Raises:
        errors.VenueUnavailableError: If the body is not a list.
    """
    if not isinstance(payload, list):
        raise errors.VenueUnavailableError("coingecko returned no market list")
    out = []
    for row in payload:
        if not isinstance(row, Mapping) or not row.get("id"):
            continue
        changes = {}
        for window in ("1h", "24h", "7d", "30d", "1y"):
            value = _number(
                row.get(f"price_change_percentage_{window}_in_currency")
            )
            if value is None and window == "24h":
                value = _number(row.get("price_change_percentage_24h"))
            if value is not None:
                changes[window] = value
        out.append(
            Coin(
                id=str(row["id"]),
                symbol=str(row.get("symbol") or "").upper(),
                name=str(row.get("name") or row["id"]),
                price=_positive(row.get("current_price")),
                market_cap=_positive(row.get("market_cap")),
                fd_market_cap=_positive(row.get("fully_diluted_valuation")),
                volume=_positive(row.get("total_volume")),
                high_24h=_positive(row.get("high_24h")),
                low_24h=_positive(row.get("low_24h")),
                changes=changes,
                as_of_ms=_iso_ms(row.get("last_updated")),
            )
        )
    return out


class CoinGeckoSource:
    """Reads coin markets with a rate-limit back-off (no caching here).

    Attributes:
        client: Client for ``API_URL``.
    """

    def __init__(self, client: http.VenueHttp, clock: clock_lib.Clock) -> None:
        """Binds the client.

        Args:
            client: Rate-limited client for ``API_URL``.
            clock: Time source of the back-off.
        """
        self.client = client
        self._clock = clock
        self._blocked_until = 0

    async def markets(
        self, *, category: str | None = None, pages: int = 1
    ) -> list[Coin]:
        """Downloads coins by market capitalization.

        Args:
            category: Optional CoinGecko category id (``STABLECOINS``,
                ``DEFI``).
            pages: Pages of ``PER_PAGE`` coins to read at most.

        Returns:
            Coins, largest first; fewer pages are read when a page is short.

        Raises:
            errors.RateLimitedError: While backing off after HTTP 429.
        """
        coins: list[Coin] = []
        for page in range(1, pages + 1):
            if self._clock.now_ms() < self._blocked_until:
                raise errors.RateLimitedError(
                    "coingecko rate limit: backing off before the next request"
                )
            params: dict[str, Any] = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": PER_PAGE,
                "page": page,
                "price_change_percentage": "1h,24h,7d,30d,1y",
            }
            if category:
                params["category"] = category
            try:
                payload = await self.client.get(MARKETS_PATH, params)
            except errors.RateLimitedError:
                self._blocked_until = self._clock.now_ms() + BACKOFF_MS
                raise
            rows = parse_markets(payload)
            coins.extend(rows)
            if len(rows) < PER_PAGE:
                break
        return coins

    async def close(self) -> None:
        """Closes an owned client."""
        await self.client.close()
