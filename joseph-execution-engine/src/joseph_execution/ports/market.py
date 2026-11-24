# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Market-data gateway port (read-only views from the market engine)."""

from __future__ import annotations

import decimal
from collections.abc import Sequence
from typing import Protocol

from joseph_execution.domain import market


class MarketDataGateway(Protocol):
    """A read-only source of instrument metadata, quotes, books and sessions.

    Implementations return ``None`` (never fabricated values) when data is
    unavailable; the Risk Gateway then reports UNKNOWN and fails closed.
    """

    async def instrument(
        self, instrument_id: str
    ) -> market.InstrumentMeta | None:
        """Fetches instrument metadata.

        Args:
            instrument_id: Canonical id.

        Returns:
            Metadata, or ``None`` when unknown or unavailable.
        """
        ...

    async def quote(self, instrument_id: str) -> market.MarketQuote | None:
        """Fetches the latest quote.

        Args:
            instrument_id: Canonical id.

        Returns:
            Quote, or ``None`` when unavailable.
        """
        ...

    async def order_book(
        self, instrument_id: str, depth: int = 50
    ) -> market.BookView | None:
        """Fetches the order book.

        Args:
            instrument_id: Canonical id.
            depth: Maximum levels per side.

        Returns:
            Book, or ``None`` when unavailable.
        """
        ...

    async def sessions(
        self, calendar: str, from_ms: int, to_ms: int
    ) -> Sequence[market.TradingSession] | None:
        """Lists exchange sessions overlapping ``[from_ms, to_ms)``.

        Backed by the market engine's
        ``GET /v1/calendars/{id}/sessions?from_ms&to_ms``.

        Args:
            calendar: Calendar id (contract ``CalendarId``).
            from_ms: Window start.
            to_ms: Window end (exclusive).

        Returns:
            Sessions (empty for ``24x7``, which never closes), or ``None``
            when the calendar cannot be read.
        """
        ...

    async def previous_close(
        self, instrument_id: str
    ) -> decimal.Decimal | None:
        """Returns the close of the last completed session (daily bar).

        Used as the reference price of daily price limits.

        Args:
            instrument_id: Canonical id.

        Returns:
            Close, or ``None`` when unknown.
        """
        ...
