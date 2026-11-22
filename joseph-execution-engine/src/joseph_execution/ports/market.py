"""Market-data gateway port (read-only views from the market engine)."""

from __future__ import annotations

from typing import Protocol

from joseph_execution.domain import market


class MarketDataGateway(Protocol):
    """Provides instrument metadata, quotes and books.

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
            Metadata or ``None``.
        """
        ...

    async def quote(self, instrument_id: str) -> market.MarketQuote | None:
        """Fetches the latest quote.

        Args:
            instrument_id: Canonical id.

        Returns:
            Quote or ``None``.
        """
        ...

    async def order_book(
        self, instrument_id: str, depth: int = 50
    ) -> market.BookView | None:
        """Fetches the order book.

        Args:
            instrument_id: Canonical id.
            depth: Levels per side.

        Returns:
            Book or ``None``.
        """
        ...
