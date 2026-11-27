# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared building blocks for REST/WebSocket venue adapters.

Besides parsing helpers, ``BaseVenueAdapter`` implements the venue-neutral
half of public trades and seconds candles. A venue opts in by overriding
``_trade_spec`` (native trade stream) and/or ``_trade_history`` (REST trade
history for a time range, newest trades kept when a page cap truncates).
Seconds bars are then built from trades (``fetch_candles`` from the REST
history, ``stream_candles`` from the stream) and never fabricated: only the
fully covered part of a range is returned.

Bar boundaries follow the instrument's session calendar
(``sessions.for_instrument``): exchange-listed bars start at the session
open and the last bar of a session is cut at the close. ``first_start`` and
``newest_window_start`` take the calendar explicitly (UTC-aligned bars by
default, as on every crypto venue).
"""

from __future__ import annotations

import asyncio
import contextlib
import decimal
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from typing import TypeAlias

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import http
from joseph.market_data.adapters import ws
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

TRADE_PUBLISH_LAG_MS = 1_000
"""Milliseconds REST trade history may trail real time (never covered)."""
_SEED_MS = 10_000
"""Milliseconds of history fetched before a seconds range for its seed."""
MAX_TRADE_BARS = 10_000
"""Newest seconds bars built per REST call (bounds flat runs and memory)."""

TradePage: TypeAlias = Callable[[int, int], Awaitable[trades_lib.TradeCoverage]]
"""One trade-history request: ``(start_ms, end_ms) -> coverage``."""


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
        Decimal, or ``None`` when the value is ``None`` or ``""``.
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
    taker_buy_base_volume: decimal.Decimal | None = None,
    taker_buy_method: market.BuyVolumeMethod | None = None,
) -> market.Candle:
    """Builds a canonical candle from venue values.

    Finality defaults to "the bar has ended by ``now_ms``", so the
    in-progress bar is never final. High/low are widened to contain open and
    close when a venue publishes a slightly inconsistent bar; a negative
    base volume becomes zero and the taker buy volume is clamped into
    ``[0, base_volume]`` likewise.

    Args:
        instrument_id: Exact venue instrument.
        timeframe: Resolution.
        start_ms: Bar start, UTC epoch milliseconds.
        open_: Open.
        high: High.
        low: Low.
        close: Close.
        base_volume: Base volume.
        now_ms: Receipt time, epoch milliseconds.
        source: Provenance label.
        feed_state: Provenance state.
        quote_turnover: Optional quote turnover.
        trade_count: Optional trade count.
        event_ts_ms: Optional venue event time, epoch milliseconds.
        is_final: Explicit venue finality (e.g. Binance ``x``), honoured
            only once the bar has ended (1 s clock-skew allowance); derived
            from ``now_ms`` if None.
        taker_buy_base_volume: Optional venue-published taker buy volume.
        taker_buy_method: How that buy volume was obtained (``classified``
            for stock exchanges, which publish no aggressor side).

    Returns:
        Candle.

    Raises:
        errors.UnsupportedError: If the venue bar is not aligned to the
            instrument's session grid (UTC for crypto venues).
    """
    calendar = sessions.for_instrument(instrument_id)
    if not calendar.is_aligned(timeframe, start_ms):
        grid = (
            "UTC aligned"
            if calendar.continuous
            else f"aligned to the {calendar.calendar_id.value} sessions"
        )
        raise errors.UnsupportedError(
            f"{source} {timeframe.value} bars are not {grid}"
        )
    end_ms = calendar.end_of(timeframe, start_ms)
    # A venue may flag its bar final a moment before our clock reaches the
    # bar end; never accept a "final" bar that is still more than 1 s open.
    final = (
        end_ms <= now_ms
        if is_final is None
        else is_final and (end_ms <= now_ms + 1_000)
    )
    volume = max(base_volume, decimals.ZERO)
    taker = (
        None
        if taker_buy_base_volume is None
        else min(max(taker_buy_base_volume, decimals.ZERO), volume)
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
        base_volume=volume,
        is_final=final,
        source=source,
        feed_state=feed_state,
        received_at_ms=now_ms,
        quote_turnover=quote_turnover,
        trade_count=trade_count,
        event_ts_ms=event_ts_ms,
        taker_buy_base_volume=taker,
        taker_buy_method=None if taker is None else taker_buy_method,
    )


def first_start(
    timeframe: timeframes.Timeframe,
    start_ms: int,
    *,
    calendar: sessions.BarCalendar = sessions.CONTINUOUS,
) -> int:
    """Returns the first bar start at or after ``start_ms``.

    Args:
        timeframe: Resolution.
        start_ms: Any time.
        calendar: Session calendar of the instrument.

    Returns:
        Aligned bar start.
    """
    return calendar.ceil(timeframe, start_ms)


def newest_window_start(
    timeframe: timeframes.Timeframe,
    start_ms: int,
    end_ms: int,
    limit: int,
    *,
    calendar: sessions.BarCalendar = sessions.CONTINUOUS,
) -> int:
    """Returns the paging start that still yields the newest ``limit`` bars.

    Args:
        timeframe: Resolution.
        start_ms: Requested inclusive start.
        end_ms: Requested exclusive end.
        limit: Maximum bars wanted.
        calendar: Session calendar of the instrument.

    Returns:
        Aligned start, no earlier than the bar containing ``start_ms``
        (continuous calendars) or the first bar at or after it (session
        calendars).
    """
    if calendar.continuous:
        newest = timeframe.floor(end_ms - 1)
        return max(
            timeframe.floor(start_ms), timeframe.shift(newest, -(limit - 1))
        )
    first = calendar.ceil(timeframe, start_ms)
    if first >= end_ms:
        return first
    newest = calendar.floor(timeframe, end_ms - 1)
    return max(first, calendar.shift(timeframe, newest, -(limit - 1)))


async def suffix_by_chunks(
    page: TradePage,
    start_ms: int,
    end_ms: int,
    max_pages: int,
    *,
    first_window_ms: int = 5_000,
    max_window_ms: int = 3_600_000,
) -> trades_lib.TradeCoverage:
    """Collects the newest complete trades of a range with forward pages.

    For venues whose trade history pages forwards in time, the range is
    walked backwards in windows (four times longer after a window that one
    page covered, up to ``max_window_ms``), each window paged forwards to
    completion. A window that runs out of the page budget, or whose page
    makes no progress, is discarded and ends the walk, so the result is a
    complete suffix of the range.

    Args:
        page: ``(start, end) -> coverage`` of one request (a prefix).
        start_ms: Inclusive start, epoch milliseconds.
        end_ms: Exclusive end, epoch milliseconds.
        max_pages: Request budget.
        first_window_ms: First window length in milliseconds.
        max_window_ms: Largest window the venue accepts, in milliseconds.

    Returns:
        Coverage ``[from, end_ms)`` with ``from >= start_ms``.
    """
    collected: list[market.Trade] = []
    cover_from = end_ms
    pages = 0
    window = first_window_ms
    while cover_from > start_ms and pages < max_pages:
        chunk_start = max(start_ms, cover_from - window)
        chunk: list[market.Trade] = []
        cursor = chunk_start
        used = 0
        complete = False
        while pages < max_pages:
            coverage = await page(cursor, cover_from)
            pages += 1
            used += 1
            chunk.extend(coverage.trades)
            if coverage.to_ms >= cover_from:
                complete = True
                break
            if coverage.to_ms <= cursor:
                break
            cursor = coverage.to_ms
        if not complete:
            break
        collected[:0] = chunk
        cover_from = chunk_start
        if used == 1:
            window = min(max_window_ms, window * 4)
    return trades_lib.TradeCoverage(tuple(collected), cover_from, end_ms)


def clip_range(
    rows: list[market.Candle], start_ms: int, end_ms: int, limit: int | None
) -> list[market.Candle]:
    """Keeps candles starting in [start, end), newest ``limit`` bars.

    Args:
        rows: Candles in any order.
        start_ms: Inclusive start.
        end_ms: Exclusive end.
        limit: Optional maximum bars; the newest are kept (none for 0).

    Returns:
        Ascending candles, one per start (the last duplicate wins).
    """
    by_start = {c.start_ms: c for c in rows if start_ms <= c.start_ms < end_ms}
    ordered = [by_start[s] for s in sorted(by_start)]
    if limit is None:
        return ordered
    return ordered[-limit:] if limit > 0 else []


def candle_coverage(
    rows: list[market.Candle],
    start_ms: int,
    end_ms: int,
    limit: int | None,
    covered: Iterable[candle_rules.Span],
    *,
    complete: bool,
) -> candle_rules.CandleCoverage:
    """Clips fetched candles and the spans they cover to a request.

    Coverage is kept only where the returned candles are: inside
    ``[start, end)`` and, when ``limit`` dropped older bars, from the
    oldest bar returned (none when ``limit`` 0 dropped every bar).

    Args:
        rows: Fetched candles in any order.
        start_ms: Inclusive request start.
        end_ms: Exclusive request end.
        limit: Optional maximum bars (newest kept).
        covered: Spans the fetch delivered completely.
        complete: Whether no page or size cap truncated the fetch.

    Returns:
        Candles as ``clip_range`` returns them, with their coverage.
    """
    kept = clip_range(rows, start_ms, end_ms, limit)
    low = start_ms
    if limit is not None and len(clip_range(rows, start_ms, end_ms, None)) > (
        len(kept)
    ):
        # Bars were dropped: nothing older than the oldest kept bar is
        # covered, and nothing at all when the limit kept none.
        low = kept[0].start_ms if kept else end_ms
    spans = candle_rules.clip_spans(
        candle_rules.merge_spans(covered), low, end_ms
    )
    return candle_rules.CandleCoverage(tuple(kept), tuple(spans), complete)


async def candles_of(
    updates: AsyncIterator[candle_rules.CandleCoverage],
) -> AsyncGenerator[market.Candle, None]:
    """Projects a coverage stream onto its candles.

    Args:
        updates: Stream of candle updates with coverage.

    Yields:
        The candles of each update.
    """
    try:
        async for update in updates:
            for candle in update.candles:
                yield candle
    finally:
        # ``AsyncIterator`` has no ``aclose``; close the source when it is an
        # async generator so its connection is released with this one.
        close = getattr(updates, "aclose", None)
        if close is not None:
            await close()


async def _without_coverage(
    candles: AsyncIterator[market.Candle],
) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
    """Wraps a candle stream as updates that prove no coverage.

    Args:
        candles: Candle stream.

    Yields:
        One update per candle.
    """
    try:
        async for candle in candles:
            yield candle_rules.CandleCoverage((candle,))
    finally:
        close = getattr(candles, "aclose", None)
        if close is not None:
            await close()


class BaseVenueAdapter:
    """Common state and helpers for venue adapters.

    Subclasses implement the ``MarketDataPort`` methods; unsupported ones
    inherit implementations that raise ``UnsupportedError``.

    Attributes:
        VENUE: Venue served; every subclass sets its own.
        INSTRUMENT_TTL_MS: Milliseconds ``list_instruments`` reuses the
            loaded instrument list before fetching it again.
        TRADE_MAX_PAGES: REST requests per trade-history call
            (``fetch_trades``, stream backfills, trade-built bars) when the
            caller gives no budget.
        TRADE_BUY_VOLUME_METHOD: Origin of the aggressor side of the venue's
            public trades, stamped on trade-built bars: ``None`` for a
            venue taker flag, ``classified`` where trades are classified
            against quotes (stock exchanges).
        SPARSE_BARS: True where an interval without trades has no bar
            (stock exchanges). Such adapters implement
            ``ports.SparseBarsPort``: their fetches and streams state the
            spans they delivered completely, inside which a missing bar is
            not a gap. Crypto venues publish dense bars (False).
    """

    VENUE: instruments.Venue = instruments.Venue.MOCK
    INSTRUMENT_TTL_MS = 300_000
    TRADE_MAX_PAGES = 5
    TRADE_BUY_VOLUME_METHOD: market.BuyVolumeMethod | None = None
    SPARSE_BARS = False

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
        self._connect: ws.ConnectFn = ws.default_connect
        self._instruments: dict[str, instruments.Instrument] = {}
        self._meta: dict[str, Mapping[str, Any]] = {}
        self._loaded_at_ms: int | None = None
        self._lock = asyncio.Lock()

    @property
    def venue(self) -> instruments.Venue:
        """The venue served by this adapter."""
        return self.VENUE

    def price_adjustment(
        self, timeframe: timeframes.Timeframe
    ) -> market.PriceAdjustment:
        """Reports raw historical prices unless a source overrides the policy.

        Args:
            timeframe: Requested chart or analysis resolution.

        Returns:
            RAW; source-specific corporate actions are never guessed.
        """
        del timeframe
        return market.PriceAdjustment.RAW

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns capabilities (overridden by each venue).

        Returns:
            What the venue serves; each venue's override defines it.

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

        Returns:
            The instruments, and native metadata keyed by venue market id
            (what ``_meta_for`` returns later); each venue overrides this.

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
        if found is None or found.instrument_id != instrument:
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

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Rejects order-book reads for price-only adapters.

        Args:
            instrument: Canonical source instrument.
            depth: Requested levels per side; unused without a book source.

        Returns:
            Never returns an invented order book.

        Raises:
            errors.UnsupportedError: Unless the adapter overrides this method.
        """
        del instrument, depth
        raise errors.UnsupportedError(f"{self.venue.value} has no order book")

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Rejects open-interest reads for adapters without derivatives data.

        Args:
            instrument: Canonical source instrument.

        Returns:
            Never returns an invented derivative position count.

        Raises:
            errors.UnsupportedError: Unless the adapter overrides this method.
        """
        del instrument
        raise errors.UnsupportedError(
            f"{self.venue.value} has no open interest"
        )

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Rejects funding reads for adapters without perpetual funding data.

        Args:
            instrument: Canonical source instrument.

        Returns:
            Never returns an invented perpetual funding rate.

        Raises:
            errors.UnsupportedError: Unless the adapter overrides this method.
        """
        del instrument
        raise errors.UnsupportedError(f"{self.venue.value} has no funding rate")

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches every instrument's snapshot (unsupported unless overridden).

        Returns:
            One snapshot per instrument, in an override.

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
        """Streams trade-built seconds candles; kline streams need an override.

        Args:
            instrument: Canonical id.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of seconds candles built from the trade stream.

        Raises:
            errors.UnsupportedError: For kline timeframes (no native candle
                stream) or seconds without a native trade stream.
        """
        caps = self.capabilities()
        if caps.is_trade_built(timeframe) and caps.market_data.native_ws_trades:
            return self._stream_trade_candles(instrument, timeframe, tracker)
        raise errors.UnsupportedError(
            f"{self.VENUE.value} has no native candle stream in Joseph"
        )

    def stream_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[candle_rules.CandleCoverage]:
        """Streams candles as updates without coverage (unless overridden).

        Args:
            instrument: Canonical id.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of ``stream_candles`` updates proving nothing.
        """
        return _without_coverage(
            self.stream_candles(instrument, timeframe, tracker)
        )

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Streams public trades over the native WebSocket.

        Args:
            instrument: Canonical id.
            tracker: Optional health tracker.

        Returns:
            Async iterator of de-duplicated trades.

        Raises:
            errors.UnsupportedError: When the venue stream is not wired.
        """
        if not self.capabilities().market_data.native_ws_trades:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} has no native trade stream in Joseph"
            )
        return self._stream_trades(instrument, tracker)

    async def _stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[market.Trade, None]:
        """Runs the reconnecting trade stream.

        Args:
            instrument: Canonical id.
            tracker: Optional health tracker.

        Yields:
            Trades.
        """
        await self._require(instrument)
        trades = ws.stream_trades_with_recovery(
            self._trade_spec(instrument),
            clock=self._clock,
            connect=self._connect,
            backfill=self._trade_backfill(instrument),
            tracker=tracker,
        )
        async with contextlib.aclosing(trades):
            async for trade in trades:
                yield trade

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Fetches public trades whose venue time lies in [start, end).

        At most ``TRADE_MAX_PAGES`` requests are made; when they (or
        ``limit``) truncate the range, the most recent trades are kept.

        Args:
            instrument: Canonical id.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum number of most recent trades.

        Returns:
            Ascending trades.

        Raises:
            errors.UnsupportedError: Without a time-range trade history.
            errors.NotFoundError: If the venue does not list the instrument.
        """
        if not self.capabilities().market_data.trades_history:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} publishes no time-range trade history"
            )
        if end_ms <= start_ms:
            return []
        await self._require(instrument)
        coverage = await self._trade_history(
            instrument, start_ms, end_ms, self.TRADE_MAX_PAGES
        )
        return trades_lib.keep_latest(coverage.trades, limit)

    async def fetch_trade_coverage(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int | None = None,
    ) -> trades_lib.TradeCoverage:
        """Fetches the newest complete trades of a range with their coverage.

        Unlike ``fetch_trades`` the result states which interval it covers
        completely, so trade recorders can archive coverage honestly.

        Args:
            instrument: Canonical id.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            max_pages: Request budget; ``None`` or 0 means
                ``TRADE_MAX_PAGES``.

        Returns:
            Complete suffix ``[from, end_ms)`` of the range.

        Raises:
            errors.UnsupportedError: Without a time-range trade history.
            errors.NotFoundError: If the venue does not list the instrument.
        """
        if not self.capabilities().market_data.trades_history:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} publishes no time-range trade history"
            )
        if end_ms <= start_ms:
            return trades_lib.TradeCoverage((), end_ms, end_ms)
        await self._require(instrument)
        return await self._trade_history(
            instrument, start_ms, end_ms, max_pages or self.TRADE_MAX_PAGES
        )

    def stream_trade_events(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
        *,
        prime_from_ms: int | None = None,
    ) -> AsyncIterator[ws.TradeStreamEvent]:
        """Streams coverage-tagged public trade events (for recorders).

        Args:
            instrument: Canonical id.
            tracker: Optional health tracker.
            prime_from_ms: With a REST trade history, the first connection
                backfills from this venue time so coverage continues an
                earlier recording; otherwise coverage starts at the first
                trade received.

        Returns:
            Async iterator of ``CoverageStart``, ``TradesReceived`` and
            ``FrameReceived`` events.

        Raises:
            errors.UnsupportedError: When the venue stream is not wired.
        """
        if not self.capabilities().market_data.native_ws_trades:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} has no native trade stream in Joseph"
            )
        return self._stream_trade_events(instrument, tracker, prime_from_ms)

    async def _stream_trade_events(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
        prime_from_ms: int | None,
    ) -> AsyncGenerator[ws.TradeStreamEvent, None]:
        """Runs the reconnecting, coverage-tracking trade stream.

        Args:
            instrument: Canonical id.
            tracker: Optional health tracker.
            prime_from_ms: Optional first-connection backfill start.

        Yields:
            Trade stream events.
        """
        await self._require(instrument)
        backfill = self._trade_backfill(instrument)

        def prime(now_ms: int) -> int:
            """Returns the first connection's backfill start.

            Args:
                now_ms: Current time.

            Returns:
                Venue time to backfill from.
            """
            del now_ms
            if prime_from_ms is None:
                raise AssertionError("prime is passed only with a start")
            return prime_from_ms

        events = ws.trade_stream_events(
            self._trade_spec(instrument),
            clock=self._clock,
            connect=self._connect,
            backfill=backfill,
            prime=(
                prime
                if prime_from_ms is not None and backfill is not None
                else None
            ),
            tracker=tracker,
        )
        async with contextlib.aclosing(events):
            async for event in events:
                yield event

    def _trade_spec(
        self, instrument: instruments.InstrumentId
    ) -> ws.WsTradeSpec:
        """Describes the native trade stream (overridden by venues).

        Args:
            instrument: Canonical id.

        Returns:
            In an override, the venue's trade stream for the instrument.

        Raises:
            errors.UnsupportedError: Always in the base class.
        """
        del instrument  # Unused.
        raise errors.UnsupportedError(
            f"{self.VENUE.value} has no native trade stream in Joseph"
        )

    async def _trade_history(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int,
    ) -> trades_lib.TradeCoverage:
        """Collects the newest complete trades of a range (venue hook).

        Args:
            instrument: Canonical id.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            max_pages: Request budget.

        Returns:
            In an override, a complete suffix ``[from, end_ms)`` of the
            range: the newest trades are kept when ``max_pages`` runs out.

        Raises:
            errors.UnsupportedError: Always in the base class.
        """
        del instrument, start_ms, end_ms, max_pages  # Unused.
        raise errors.UnsupportedError(
            f"{self.VENUE.value} publishes no time-range trade history"
        )

    def _trade_backfill(
        self, instrument: instruments.InstrumentId
    ) -> ws.TradeBackfill | None:
        """Returns the REST trade backfill of an instrument, if any.

        Args:
            instrument: Canonical id.

        Returns:
            Backfill callable, or ``None`` without trade history.
        """
        if not self.capabilities().market_data.trades_history:
            return None

        async def backfill(start: int, end: int) -> trades_lib.TradeCoverage:
            """Fetches missed trades over REST.

            Args:
                start: Inclusive start.
                end: Exclusive end.

            Returns:
                Coverage.
            """
            return await self._trade_history(
                instrument, start, end, self.TRADE_MAX_PAGES
            )

        return backfill

    def _trade_source(self) -> str:
        """Returns the provenance label of stream-built seconds bars."""
        return f"{self.VENUE.value}.trades"

    def _require_trade_built(self, timeframe: timeframes.Timeframe) -> None:
        """Rejects seconds resolutions the venue cannot build.

        Args:
            timeframe: Requested resolution.

        Raises:
            errors.UnsupportedError: If not built from this venue's trades.
        """
        if not self.capabilities().is_trade_built(timeframe):
            raise errors.UnsupportedError(
                f"{self.VENUE.value} does not provide {timeframe.value} "
                "candles (seconds bars need a native trade stream)"
            )

    async def _fetch_trade_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None,
    ) -> list[market.Candle]:
        """Builds historical seconds candles from the REST trade history.

        Only the fully covered part of the range is returned: at most the
        newest ``MAX_TRADE_BARS`` bars are built, the page cap keeps the
        newest trades, and the bar straddling the live edge stays
        non-final.

        Args:
            instrument: Canonical id.
            timeframe: Seconds resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles.

        Raises:
            errors.UnsupportedError: If the venue does not build this
                resolution from trades, or has no time-range trade history.
        """
        self._require_trade_built(timeframe)
        if not self.capabilities().market_data.trades_history:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} publishes no time-range trade history; "
                f"{timeframe.value} bars exist only from the live trade stream"
            )
        await self._require(instrument)
        now = self._clock.now_ms()
        calendar = sessions.for_instrument(instrument)
        first = first_start(timeframe, start_ms, calendar=calendar)
        stop = min(
            end_ms, calendar.end_of(timeframe, calendar.floor(timeframe, now))
        )
        if stop > first:
            wanted = (
                MAX_TRADE_BARS if limit is None else min(limit, MAX_TRADE_BARS)
            )
            first = max(
                first,
                newest_window_start(
                    timeframe, first, stop, wanted, calendar=calendar
                ),
            )
        cover_to = min(stop, now - TRADE_PUBLISH_LAG_MS)
        try:
            seed_from = calendar.floor(timeframe, first - _SEED_MS)
        except ValueError:
            # Session calendars reject times before their first session;
            # there is no earlier close to seed from then.
            seed_from = first
        if cover_to <= first:
            return []
        coverage = await self._trade_history(
            instrument, seed_from, cover_to, self.TRADE_MAX_PAGES
        )
        bars = trade_bars.bars_from_coverage(
            coverage,
            instrument,
            timeframe,
            source=f"{self._trade_source()}.rest",
            now_ms=now,
            buy_volume_method=self.TRADE_BUY_VOLUME_METHOD,
        )
        return clip_range(bars, first, stop, limit)

    async def _stream_trade_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[market.Candle, None]:
        """Streams seconds candles built from the native trade stream.

        Args:
            instrument: Canonical id.
            timeframe: Seconds resolution.
            tracker: Optional health tracker.

        Yields:
            Live and final seconds candles.
        """
        self._require_trade_built(timeframe)
        await self._require(instrument)
        builder = trade_bars.TradeBarBuilder(
            instrument,
            timeframe,
            source=self._trade_source(),
            buy_volume_method=self.TRADE_BUY_VOLUME_METHOD,
        )
        candles = ws.stream_trade_candles(
            self._trade_spec(instrument),
            builder,
            clock=self._clock,
            connect=self._connect,
            backfill=self._trade_backfill(instrument),
            tracker=tracker,
        )
        async with contextlib.aclosing(candles):
            async for candle in candles:
                yield candle

    async def close(self) -> None:
        """Closes network resources."""
        await self._http.close()
