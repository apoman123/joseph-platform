"""Ports of the market-data bounded context.

``MarketDataPort`` is implemented once per venue (plus mock and replay).
Adapters own authentication, reconnects, heartbeats, rate limits, venue
error mapping and timestamp normalization. A capability that is not
implemented raises ``errors.UnsupportedError``; nothing is ever fabricated.
Seconds candles are built from public trades (``stream_trades`` and
``fetch_trades``) because no perpetual venue publishes them as klines.
``CandleSource`` is the narrow read port consumed by the indicator engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Sequence
from typing import Protocol

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health


class MarketDataPort(Protocol):
    """Venue market-data adapter."""

    @property
    def venue(self) -> instruments.Venue:
        """Returns the venue this adapter serves."""
        ...

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns the implemented and verified capabilities."""
        ...

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Lists tradable and recently delisted instruments.

        Returns:
            Instruments with precision metadata.
        """
        ...

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches historical candles whose start lies in [start, end).

        Args:
            instrument: Exact venue instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum number of most recent bars.

        Returns:
            Ascending candles; the in-progress bar has ``is_final=False``.
        """
        ...

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches the latest market snapshot.

        Args:
            instrument: Exact venue instrument.

        Returns:
            Snapshot with missing fields left ``None``.
        """
        ...

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches snapshots for every instrument in bulk (for screening).

        Returns:
            Snapshots; per-symbol-only data (e.g. Binance OI) may be ``None``.
        """
        ...

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches an order-book snapshot.

        Args:
            instrument: Exact venue instrument.
            depth: Levels per side requested.

        Returns:
            Order book.
        """
        ...

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Fetches open interest with USD notional.

        Args:
            instrument: Exact venue instrument.

        Returns:
            Open interest.
        """
        ...

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Fetches the current funding rate.

        Args:
            instrument: Exact venue instrument.

        Returns:
            Funding observation.
        """
        ...

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams candle updates over the venue's native WebSocket.

        Args:
            instrument: Exact venue instrument.
            timeframe: Resolution.
            tracker: Optional health tracker updated by the stream.

        Returns:
            Async iterator of updates (live and finalized bars).
        """
        ...

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Streams public trades over the venue's native WebSocket.

        Reconnects with backoff; after a reconnect missed trades are
        backfilled over REST where the venue supports it, otherwise a gap
        is recorded on the tracker. Duplicates are dropped by trade id.

        Args:
            instrument: Exact venue instrument.
            tracker: Optional health tracker updated by the stream.

        Returns:
            Async iterator of trades with the venue's aggressor side.

        Raises:
            errors.UnsupportedError: If the venue stream is not wired.
        """
        ...

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Fetches public trades whose venue time lies in [start, end).

        Requests are capped per call; when the cap or ``limit`` truncates
        the range, the most recent trades are kept.

        Args:
            instrument: Exact venue instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum number of most recent trades.

        Returns:
            Ascending trades.

        Raises:
            errors.UnsupportedError: If the venue has no time-range trade
                history.
        """
        ...

    async def close(self) -> None:
        """Releases network resources."""
        ...


class CandleSource(Protocol):
    """Read port for closed candles (consumed by the indicator engine)."""

    async def get_candles(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> Sequence[market.Candle]:
        """Returns final candles with ``start_ms`` in [start, end).

        Args:
            instrument_id: Exact venue instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending final candles.
        """
        ...
