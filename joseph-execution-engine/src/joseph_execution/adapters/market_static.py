# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Static in-memory market gateway for tests and standalone DEMO runs.

Quotes are labeled DEMO so the Risk Gateway never treats them as live.
"""

from __future__ import annotations

import dataclasses
import decimal
import random
from collections.abc import Sequence

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import market
from joseph_execution.ports import services

_D = decimal.Decimal

MOCK_INSTRUMENTS: tuple[market.InstrumentMeta, ...] = (
    market.InstrumentMeta(
        instrument_id="mock:perp:BTCUSD",
        venue="mock",
        tick_size=_D("0.1"),
        lot_size=_D("0.001"),
        min_qty=_D("0.001"),
        status=enums.InstrumentStatus.TRADING,
        min_notional=_D(5),
        max_leverage=_D(50),
    ),
    market.InstrumentMeta(
        instrument_id="mock:perp:ETHUSD",
        venue="mock",
        tick_size=_D("0.01"),
        lot_size=_D("0.01"),
        min_qty=_D("0.01"),
        status=enums.InstrumentStatus.TRADING,
        min_notional=_D(5),
        max_leverage=_D(50),
    ),
    market.InstrumentMeta(
        instrument_id="mock:perp:SOLUSD",
        venue="mock",
        tick_size=_D("0.001"),
        lot_size=_D("0.1"),
        min_qty=_D("0.1"),
        status=enums.InstrumentStatus.TRADING,
        min_notional=_D(5),
        max_leverage=_D(20),
    ),
)

_MARKS = {
    "mock:perp:BTCUSD": _D(95000),
    "mock:perp:ETHUSD": _D(3400),
    "mock:perp:SOLUSD": _D(180),
}


class StaticMarketDataGateway:
    """Deterministic gateway with settable quotes.

    Attributes:
        feed_state: Provenance reported on quotes (DEMO by default).
        unavailable: When True every call returns ``None``.
    """

    def __init__(
        self,
        clock: services.Clock,
        instruments: tuple[market.InstrumentMeta, ...] = MOCK_INSTRUMENTS,
        seed: int = 7,
    ) -> None:
        """Initializes the gateway.

        Args:
            clock: Time source.
            instruments: Instrument metadata.
            seed: Random seed for the optional walk.
        """
        self._clock = clock
        self._instruments = {i.instrument_id: i for i in instruments}
        self._quotes: dict[str, market.MarketQuote] = {}
        self._pinned: set[str] = set()
        self._stale_books: set[str] = set()
        self._calendars: dict[str, tuple[market.TradingSession, ...]] = {}
        self._closes: dict[str, decimal.Decimal] = {}
        self._random = random.Random(seed)  # noqa: S311 - simulation only.
        self.feed_state = enums.FeedState.DEMO
        self.unavailable = False
        for instrument_id, mark in _MARKS.items():
            if instrument_id in self._instruments:
                self.set_mark(instrument_id, mark)

    def set_mark(
        self,
        instrument_id: str,
        mark: decimal.Decimal,
        spread_ticks: int = 1,
        as_of_ms: int | None = None,
    ) -> market.MarketQuote:
        """Sets a quote around a mark.

        Args:
            instrument_id: Instrument.
            mark: Mark price.
            spread_ticks: Half-spread in ticks.
            as_of_ms: Fixed quote time; when omitted the quote stays fresh
                (its time follows the clock).

        Returns:
            The quote.
        """
        if as_of_ms is None:
            self._pinned.discard(instrument_id)
        else:
            self._pinned.add(instrument_id)
        tick = self._instruments[instrument_id].tick_for(mark)
        mark = decimals.round_to_step(mark, tick)
        quote = market.MarketQuote(
            instrument_id=instrument_id,
            feed_state=self.feed_state,
            as_of_ms=self._clock.now_ms() if as_of_ms is None else as_of_ms,
            mark=mark,
            last=mark,
            best_bid=mark - tick * spread_ticks,
            best_ask=mark + tick * spread_ticks,
        )
        self._quotes[instrument_id] = quote
        return quote

    def set_quote(self, quote: market.MarketQuote) -> None:
        """Sets an explicit quote.

        Args:
            quote: Quote.
        """
        self._quotes[quote.instrument_id] = quote
        self._pinned.add(quote.instrument_id)

    def add_instrument(
        self,
        meta: market.InstrumentMeta,
        mark: decimal.Decimal | None = None,
    ) -> None:
        """Registers an instrument (tests and demos).

        Args:
            meta: Metadata.
            mark: Optional initial mark.
        """
        self._instruments[meta.instrument_id] = meta
        if mark is not None:
            self.set_mark(meta.instrument_id, mark)

    def set_sessions(
        self, calendar: str, sessions: Sequence[market.TradingSession]
    ) -> None:
        """Sets the sessions of a calendar (fake of the market engine).

        Args:
            calendar: Calendar id.
            sessions: Sessions.
        """
        self._calendars[calendar] = tuple(
            sorted(sessions, key=lambda s: s.open_ms)
        )

    def set_previous_close(
        self, instrument_id: str, close: decimal.Decimal | None
    ) -> None:
        """Sets the previous session close of an instrument.

        Args:
            instrument_id: Instrument.
            close: Close, or ``None`` to make it unknown.
        """
        if close is None:
            self._closes.pop(instrument_id, None)
        else:
            self._closes[instrument_id] = close

    def set_book_stale(self, instrument_id: str, stale: bool = True) -> None:
        """Marks an instrument's book stale.

        Args:
            instrument_id: Instrument.
            stale: Stale flag.
        """
        if stale:
            self._stale_books.add(instrument_id)
        else:
            self._stale_books.discard(instrument_id)

    def walk(self, max_bps: int = 5) -> None:
        """Moves every mark by a random step (DEMO animation).

        Args:
            max_bps: Maximum move in basis points.
        """
        for instrument_id, quote in list(self._quotes.items()):
            if quote.mark is None:
                continue
            step = _D(self._random.randint(-max_bps, max_bps)) / decimals.BPS
            self.set_mark(instrument_id, quote.mark * (decimals.ONE + step))

    async def instrument(
        self, instrument_id: str
    ) -> market.InstrumentMeta | None:
        """Returns instrument metadata.

        Args:
            instrument_id: Instrument.

        Returns:
            Metadata or ``None``.
        """
        if self.unavailable:
            return None
        return self._instruments.get(instrument_id)

    async def quote(self, instrument_id: str) -> market.MarketQuote | None:
        """Returns the latest quote.

        Unpinned quotes are reported as observed now.

        Args:
            instrument_id: Instrument.

        Returns:
            Quote or ``None``.
        """
        if self.unavailable:
            return None
        quote = self._quotes.get(instrument_id)
        if quote is None:
            return None
        as_of = (
            quote.as_of_ms
            if instrument_id in self._pinned
            else self._clock.now_ms()
        )
        return dataclasses.replace(
            quote, feed_state=self.feed_state, as_of_ms=as_of
        )

    async def order_book(
        self, instrument_id: str, depth: int = 50
    ) -> market.BookView | None:
        """Synthesizes a book around the quote.

        Args:
            instrument_id: Instrument.
            depth: Levels per side.

        Returns:
            Book or ``None``.
        """
        quote = await self.quote(instrument_id)
        meta = self._instruments.get(instrument_id)
        if (
            quote is None
            or meta is None
            or quote.best_bid is None
            or (quote.best_ask is None)
        ):
            return None
        size = meta.lot_size * 1000
        levels = min(depth, 20)
        bid_tick = meta.tick_for(quote.best_bid)
        ask_tick = meta.tick_for(quote.best_ask)
        bids = tuple(
            (quote.best_bid - bid_tick * i, size) for i in range(levels)
        )
        asks = tuple(
            (quote.best_ask + ask_tick * i, size) for i in range(levels)
        )
        return market.BookView(
            instrument_id=instrument_id,
            bids=bids,
            asks=asks,
            as_of_ms=quote.as_of_ms,
            stale=instrument_id in self._stale_books,
        )

    async def sessions(
        self, calendar: str, from_ms: int, to_ms: int
    ) -> Sequence[market.TradingSession] | None:
        """Returns configured sessions overlapping a window.

        Args:
            calendar: Calendar id.
            from_ms: Window start.
            to_ms: Window end.

        Returns:
            Sessions, ``()`` for ``24x7``, ``None`` when not configured.
        """
        if self.unavailable:
            return None
        if calendar == market.CONTINUOUS_CALENDAR:
            return ()
        sessions = self._calendars.get(calendar)
        if sessions is None:
            return None
        return tuple(
            s for s in sessions if s.close_ms > from_ms and s.open_ms < to_ms
        )

    async def previous_close(
        self, instrument_id: str
    ) -> decimal.Decimal | None:
        """Returns the configured previous close.

        Args:
            instrument_id: Instrument.

        Returns:
            Close or ``None``.
        """
        if self.unavailable:
            return None
        return self._closes.get(instrument_id)
