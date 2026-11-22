"""Shared building blocks for REST/WebSocket venue adapters."""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import AsyncIterator
from collections.abc import Mapping
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import http
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health


def dec(value: object) -> decimal.Decimal:
    """Parses a venue numeric value exactly.

    Args:
        value: String, int, float or Decimal.

    Returns:
        Decimal value.
    """
    return decimals.to_decimal(value)


def opt_dec(value: object) -> decimal.Decimal | None:
    """Parses an optional venue numeric value.

    Args:
        value: Value, ``None`` or ``""``.

    Returns:
        Decimal or ``None``.
    """
    return decimals.to_optional_decimal(value)


def quantize(value: decimal.Decimal, places: int) -> decimal.Decimal:
    """Rounds a float-derived value to the venue's published precision.

    Args:
        value: Value parsed from a JSON float.
        places: Decimal places published by the venue.

    Returns:
        Rounded value.
    """
    return value.quantize(decimal.Decimal(1).scaleb(-places))


def pow10(exponent: int) -> decimal.Decimal:
    """Returns ``10 ** exponent`` exactly.

    Args:
        exponent: Power of ten (may be negative).

    Returns:
        Decimal power of ten.
    """
    return decimal.Decimal(1).scaleb(exponent)


def make_candle(
    *,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    open_: decimal.Decimal,
    high: decimal.Decimal,
    low: decimal.Decimal,
    close: decimal.Decimal,
    base_volume: decimal.Decimal,
    now_ms: int,
    source: str,
    feed_state: quality.FeedState = quality.FeedState.LIVE,
    quote_turnover: decimal.Decimal | None = None,
    trade_count: int | None = None,
    event_ts_ms: int | None = None,
    is_final: bool | None = None,
) -> market.Candle:
    """Builds a canonical candle from venue values.

    Finality defaults to "the bar has ended by ``now_ms``", so the
    in-progress bar is never final. High/low are widened to contain open and
    close when a venue publishes a slightly inconsistent bar.

    Args:
        instrument_id: Exact venue instrument.
        timeframe: Resolution.
        start_ms: UTC bar start.
        open_: Open.
        high: High.
        low: Low.
        close: Close.
        base_volume: Base volume.
        now_ms: Receipt time.
        source: Provenance label.
        feed_state: Provenance state.
        quote_turnover: Optional quote turnover.
        trade_count: Optional trade count.
        event_ts_ms: Optional venue event time.
        is_final: Explicit finality (e.g. Binance ``x``); derived if None.

    Returns:
        Candle.

    Raises:
        errors.UnsupportedError: If the venue bar is not UTC aligned.
    """
    if not timeframe.is_aligned(start_ms):
        raise errors.UnsupportedError(
            f"{source} {timeframe.value} bars are not UTC aligned"
        )
    end_ms = timeframe.end_of(start_ms)
    final = (
        end_ms <= now_ms
        if is_final is None
        else is_final and (end_ms <= now_ms + 1_000)
    )
    return market.Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=open_,
        high=max(high, open_, close),
        low=min(low, open_, close),
        close=close,
        base_volume=max(base_volume, decimals.ZERO),
        is_final=final,
        source=source,
        feed_state=feed_state,
        received_at_ms=now_ms,
        quote_turnover=quote_turnover,
        trade_count=trade_count,
        event_ts_ms=event_ts_ms,
    )


def clip_range(
    rows: list[market.Candle], start_ms: int, end_ms: int, limit: int | None
) -> list[market.Candle]:
    """Keeps candles starting in [start, end), newest ``limit`` bars.

    Args:
        rows: Candles in any order.
        start_ms: Inclusive start.
        end_ms: Exclusive end.
        limit: Optional maximum bars.

    Returns:
        Ascending unique candles.
    """
    by_start = {c.start_ms: c for c in rows if start_ms <= c.start_ms < end_ms}
    ordered = [by_start[s] for s in sorted(by_start)]
    if limit is not None:
        ordered = ordered[-limit:]
    return ordered


class BaseVenueAdapter:
    """Common state and helpers for venue adapters.

    Subclasses implement the ``MarketDataPort`` methods; unsupported ones
    inherit implementations that raise ``UnsupportedError``.
    """

    VENUE: instruments.Venue = instruments.Venue.MOCK
    INSTRUMENT_TTL_MS = 300_000

    def __init__(
        self, http_client: http.VenueHttp, clock: clock_lib.Clock
    ) -> None:
        """Initializes the adapter.

        Args:
            http_client: Venue HTTP client.
            clock: Time source.
        """
        self._http = http_client
        self._clock = clock
        self._instruments: dict[str, instruments.Instrument] = {}
        self._meta: dict[str, Mapping[str, Any]] = {}
        self._loaded_at_ms: int | None = None
        self._lock = asyncio.Lock()

    @property
    def venue(self) -> instruments.Venue:
        """Returns the venue served by this adapter."""
        return self.VENUE

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns capabilities (overridden by each venue).

        Raises:
            NotImplementedError: Always in the base class.
        """
        raise NotImplementedError

    def instrument_id(self, venue_market_id: str) -> instruments.InstrumentId:
        """Builds the canonical id for a native market id.

        Args:
            venue_market_id: Venue-native id.

        Returns:
            Canonical instrument id.
        """
        return instruments.InstrumentId(
            self.VENUE, instruments.MarketType.PERP, venue_market_id
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Fetches instruments and per-instrument native metadata.

        Raises:
            NotImplementedError: Always in the base class.
        """
        raise NotImplementedError

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Lists instruments, cached for ``INSTRUMENT_TTL_MS``.

        Returns:
            Instruments.
        """
        async with self._lock:
            now = self._clock.now_ms()
            if (
                self._loaded_at_ms is None
                or now - self._loaded_at_ms > self.INSTRUMENT_TTL_MS
            ):
                rows, meta = await self._load_instruments()
                self._instruments = {
                    row.instrument_id.venue_market_id: row for row in rows
                }
                self._meta = meta
                self._loaded_at_ms = now
            return list(self._instruments.values())

    async def _require(
        self, instrument: instruments.InstrumentId
    ) -> instruments.Instrument:
        """Resolves an instrument of this venue.

        Args:
            instrument: Canonical id.

        Returns:
            The instrument.

        Raises:
            errors.NotFoundError: If the venue does not list it.
        """
        if instrument.venue is not self.VENUE:
            raise errors.NotFoundError(
                f"{instrument} does not belong to {self.VENUE.value}"
            )
        if not self._instruments:
            await self.list_instruments()
        found = self._instruments.get(instrument.venue_market_id)
        if found is None:
            raise errors.NotFoundError(f"unknown instrument {instrument}")
        return found

    def _meta_for(
        self, instrument: instruments.InstrumentId
    ) -> Mapping[str, Any]:
        """Returns native metadata captured at instrument load.

        Args:
            instrument: Canonical id.

        Returns:
            Metadata mapping (empty if unknown).
        """
        return self._meta.get(instrument.venue_market_id, {})

    def _require_timeframe(self, timeframe: timeframes.Timeframe) -> None:
        """Rejects resolutions the venue does not serve UTC aligned.

        Args:
            timeframe: Requested resolution.

        Raises:
            errors.UnsupportedError: If unsupported.
        """
        if not self.capabilities().supports_timeframe(timeframe):
            raise errors.UnsupportedError(
                f"{self.VENUE.value} does not provide UTC-aligned "
                f"{timeframe.value} candles"
            )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Bulk snapshots are unsupported unless overridden.

        Raises:
            errors.UnsupportedError: Always in the base class.
        """
        raise errors.UnsupportedError(
            f"{self.VENUE.value} bulk snapshots are not implemented"
        )

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Native candle streams are unsupported unless overridden.

        Args:
            instrument: Canonical id.
            timeframe: Resolution.
            tracker: Unused health tracker.

        Raises:
            errors.UnsupportedError: Always in the base class.
        """
        raise errors.UnsupportedError(
            f"{self.VENUE.value} has no native candle stream in Joseph"
        )

    async def close(self) -> None:
        """Closes network resources."""
        await self._http.close()
