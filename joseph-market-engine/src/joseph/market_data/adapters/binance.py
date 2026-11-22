"""Binance USDⓈ-M futures market-data adapter.

REST: ``https://fapi.binance.com/fapi/v1`` (exchangeInfo, fundingInfo,
klines, premiumIndex, ticker/24hr, ticker/bookTicker, openInterest, depth).
WebSocket klines: ``wss://fstream.binance.com/market/ws/<sym>@kline_<tf>``
(verified 2026-11: the legacy ``/ws/<sym>@kline`` path no longer streams
klines). Binance flags closed klines (``k.x``), so finals come from the
stream itself.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
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
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.adapters import ws
from joseph.market_data.domain import books
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

REST_URL = "https://fapi.binance.com"
WS_URL = "wss://fstream.binance.com/market/ws"
_TF = tuple(timeframes.Timeframe)
_DEPTH_LIMITS = (5, 10, 20, 50, 100, 500, 1000)
_MAX_KLINES = 1500
_PERPETUAL_TYPES = frozenset({"PERPETUAL", "TRADIFI_PERPETUAL"})
_STATUS = {
    "TRADING": instruments.InstrumentStatus.TRADING,
    "PENDING_TRADING": instruments.InstrumentStatus.PRE_LAUNCH,
    "SETTLING": instruments.InstrumentStatus.DELISTED,
    "CLOSE": instruments.InstrumentStatus.DELISTED,
    "DELIVERING": instruments.InstrumentStatus.HALTED,
}
_EQUITY = frozenset({"EQUITY", "HK_EQUITY", "KR_EQUITY", "CN_EQUITY"})
_RWA = frozenset({"COMMODITY", "FX", "INDEX"})


def asset_class(symbol: Mapping[str, Any]) -> instruments.AssetClass:
    """Maps Binance ``underlyingType``/``underlyingSubType`` to asset class.

    Args:
        symbol: exchangeInfo symbol entry.

    Returns:
        Asset class.
    """
    underlying = str(symbol.get("underlyingType", ""))
    if underlying in _EQUITY:
        return instruments.AssetClass.EQUITY
    if underlying in _RWA or "RWA" in symbol.get("underlyingSubType", []):
        return instruments.AssetClass.RWA
    if underlying == "COIN":
        return instruments.AssetClass.CRYPTO
    return instruments.AssetClass.UNKNOWN


def parse_instrument(
    symbol: Mapping[str, Any], funding_hours: Mapping[str, float], now_ms: int
) -> instruments.Instrument | None:
    """Converts an exchangeInfo symbol to an instrument.

    Args:
        symbol: exchangeInfo symbol entry.
        funding_hours: Per-symbol funding interval overrides.
        now_ms: Fetch time.

    Returns:
        Instrument, or ``None`` for non-perpetual contracts.
    """
    if symbol.get("contractType") not in _PERPETUAL_TYPES:
        return None
    filters = {f["filterType"]: f for f in symbol.get("filters", [])}
    price = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("MIN_NOTIONAL", {})
    native = str(symbol["symbol"])
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(
                instruments.Venue.BINANCE, instruments.MarketType.PERP, native
            ),
            canonical_base=str(symbol["baseAsset"]),
            quote=str(symbol["quoteAsset"]),
            settlement=str(symbol.get("marginAsset", symbol["quoteAsset"])),
        ),
        display_symbol=native,
        tick_size=base.dec(price.get("tickSize", "0")),
        lot_size=base.dec(lot.get("stepSize", "0")),
        min_qty=base.dec(lot.get("minQty", "0")),
        max_qty=base.opt_dec(lot.get("maxQty")),
        min_notional=base.opt_dec(notional.get("notional")),
        contract_multiplier=decimals.ONE,
        linear=True,
        status=_STATUS.get(
            str(symbol.get("status")), instruments.InstrumentStatus.UNKNOWN
        ),
        asset_class=asset_class(symbol),
        source="binance.rest",
        as_of_ms=now_ms,
        funding_interval_hours=funding_hours.get(native, 8.0),
    )


def parse_kline_row(
    row: list[Any],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    now_ms: int,
) -> market.Candle:
    """Converts a REST kline row to a candle.

    Args:
        row: ``[openTime, o, h, l, c, v, closeTime, quoteVol, trades, ...]``.
        instrument_id: Instrument.
        timeframe: Resolution.
        now_ms: Receipt time.

    Returns:
        Candle.
    """
    return base.make_candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=int(row[0]),
        open_=base.dec(row[1]),
        high=base.dec(row[2]),
        low=base.dec(row[3]),
        close=base.dec(row[4]),
        base_volume=base.dec(row[5]),
        quote_turnover=base.dec(row[7]),
        trade_count=int(row[8]),
        now_ms=now_ms,
        source="binance.rest",
    )


def parse_ws_kline(
    raw: str | bytes,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    now_ms: int,
) -> list[market.Candle]:
    """Parses a ``kline`` WebSocket frame.

    Args:
        raw: Frame payload.
        instrument_id: Instrument.
        timeframe: Resolution.
        now_ms: Receipt time.

    Returns:
        Zero or one candle.
    """
    message = json.loads(raw)
    kline = message.get("k") if isinstance(message, dict) else None
    if not kline or kline.get("i") != timeframe.value:
        return []
    return [
        base.make_candle(
            instrument_id=instrument_id,
            timeframe=timeframe,
            start_ms=int(kline["t"]),
            open_=base.dec(kline["o"]),
            high=base.dec(kline["h"]),
            low=base.dec(kline["l"]),
            close=base.dec(kline["c"]),
            base_volume=base.dec(kline["v"]),
            quote_turnover=base.opt_dec(kline.get("q")),
            trade_count=int(kline.get("n", 0)),
            event_ts_ms=int(message.get("E", now_ms)),
            now_ms=now_ms,
            source="binance.ws",
            is_final=bool(kline.get("x")),
        )
    ]


class BinanceAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for Binance USDⓈ-M perpetuals."""

    VENUE = instruments.Venue.BINANCE

    def __init__(
        self,
        http_client: http.VenueHttp,
        clock: clock_lib.Clock,
        connect: ws.ConnectFn = ws.default_connect,
        verified_at_ms: int | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            http_client: HTTP client for ``REST_URL``.
            clock: Time source.
            connect: WebSocket connector.
            verified_at_ms: When capabilities were last verified live.
        """
        super().__init__(http_client, clock)
        self._connect = connect
        self._verified_at_ms = verified_at_ms

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="Binance USDⓈ-M",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
            ),
            timeframes=_TF,
            notes=(
                "Kline WebSocket uses the /market/ws route.",
                "Trade and depth streams are not wired yet.",
                "Open interest is per symbol (no bulk endpoint).",
            ),
            docs_url=(
                "https://developers.binance.com/docs/derivatives/"
                "usds-margined-futures/"
            ),
            verified_at_ms=self._verified_at_ms,
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Fetches exchangeInfo and fundingInfo.

        Returns:
            Instruments and empty native metadata.
        """
        info, funding = await asyncio.gather(
            self._http.get("/fapi/v1/exchangeInfo"),
            self._http.get("/fapi/v1/fundingInfo"),
        )
        hours = {
            str(row["symbol"]): float(row["fundingIntervalHours"])
            for row in funding
            if row.get("fundingIntervalHours")
        }
        now = self._clock.now_ms()
        rows = [
            parsed
            for parsed in (
                parse_instrument(s, hours, now) for s in info["symbols"]
            )
            if parsed is not None
        ]
        return rows, {}

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches klines with pagination (1500 per request).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars.

        Returns:
            Ascending candles.
        """
        self._require_timeframe(timeframe)
        await self._require(instrument)
        rows: list[market.Candle] = []
        cursor = timeframe.floor(start_ms)
        wanted = limit or 10_000
        if limit is not None:
            cursor = max(
                cursor,
                timeframe.floor(end_ms - 1)
                - (limit - 1) * timeframe.duration_ms,
            )
        while cursor < end_ms and len(rows) < wanted:
            batch = await self._http.get(
                "/fapi/v1/klines",
                params={
                    "symbol": instrument.venue_market_id,
                    "interval": timeframe.value,
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": _MAX_KLINES,
                },
            )
            now = self._clock.now_ms()
            parsed = [
                parse_kline_row(r, instrument, timeframe, now) for r in batch
            ]
            rows.extend(parsed)
            if len(batch) < _MAX_KLINES:
                break
            cursor = parsed[-1].end_ms
        return base.clip_range(rows, start_ms, end_ms, limit)

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches mark/index/funding, 24h stats, BBO and open interest.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.
        """
        listed = await self._require(instrument)
        params = {"symbol": instrument.venue_market_id}
        premium, ticker, book, oi = await asyncio.gather(
            self._http.get("/fapi/v1/premiumIndex", params=params),
            self._http.get("/fapi/v1/ticker/24hr", params=params),
            self._http.get("/fapi/v1/ticker/bookTicker", params=params),
            self._http.get("/fapi/v1/openInterest", params=params),
        )
        now = self._clock.now_ms()
        snapshot = self._snapshot(
            instrument,
            listed=listed,
            premium=premium,
            ticker=ticker,
            book=book,
            now_ms=now,
        )
        mark = base.dec(premium["markPrice"])
        base_oi = base.dec(oi["openInterest"])
        return dataclasses.replace(
            snapshot,
            open_interest=market.OpenInterest(
                instrument_id=instrument,
                base_quantity=base_oi,
                notional_usd=base_oi * mark,
                mark_price=mark,
                source="binance.rest",
                as_of_ms=int(oi.get("time", now)),
            ),
        )

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        *,
        listed: instruments.Instrument,
        premium: Mapping[str, Any],
        ticker: Mapping[str, Any] | None,
        book: Mapping[str, Any] | None,
        now_ms: int,
    ) -> market.MarketSnapshot:
        """Assembles a snapshot from REST payloads.

        Args:
            instrument: Instrument.
            listed: Instrument metadata.
            premium: premiumIndex payload.
            ticker: ticker/24hr payload.
            book: bookTicker payload.
            now_ms: Receipt time.

        Returns:
            Snapshot without open interest.
        """
        funding = market.Funding(
            instrument_id=instrument,
            rate=base.dec(premium["lastFundingRate"]),
            interval_hours=listed.funding_interval_hours or 8.0,
            kind=market.FundingKind.PREDICTED,
            effective_time_ms=int(premium["nextFundingTime"]),
            next_funding_ms=int(premium["nextFundingTime"]),
            source="binance.rest",
            as_of_ms=int(premium.get("time", now_ms)),
        )
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source="binance.rest",
            as_of_ms=now_ms,
            last=base.opt_dec(ticker.get("lastPrice")) if ticker else None,
            mark=base.dec(premium["markPrice"]),
            index=base.opt_dec(premium.get("indexPrice")),
            best_bid=base.opt_dec(book.get("bidPrice")) if book else None,
            best_ask=base.opt_dec(book.get("askPrice")) if book else None,
            turnover_24h_quote=(
                base.opt_dec(ticker.get("quoteVolume")) if ticker else None
            ),
            volume_24h_base=(
                base.opt_dec(ticker.get("volume")) if ticker else None
            ),
            change_24h_pct=(
                base.opt_dec(ticker.get("priceChangePercent"))
                if ticker
                else None
            ),
            funding=funding,
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches bulk 24h tickers, premium index and BBO (no OI).

        Returns:
            Snapshots for listed perpetuals.
        """
        await self.list_instruments()
        tickers, premiums, books_ = await asyncio.gather(
            self._http.get("/fapi/v1/ticker/24hr"),
            self._http.get("/fapi/v1/premiumIndex"),
            self._http.get("/fapi/v1/ticker/bookTicker"),
        )
        by_ticker = {str(t["symbol"]): t for t in tickers}
        by_book = {str(b["symbol"]): b for b in books_}
        now = self._clock.now_ms()
        snapshots = []
        for premium in premiums:
            native = str(premium["symbol"])
            listed = self._instruments.get(native)
            if listed is None or not premium.get("markPrice"):
                continue
            snapshots.append(
                self._snapshot(
                    listed.instrument_id,
                    listed=listed,
                    premium=premium,
                    ticker=by_ticker.get(native),
                    book=by_book.get(native),
                    now_ms=now,
                )
            )
        return snapshots

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches a depth snapshot.

        Args:
            instrument: Instrument.
            depth: Levels per side (rounded up to a Binance limit).

        Returns:
            Order book.
        """
        await self._require(instrument)
        limit = next((d for d in _DEPTH_LIMITS if d >= depth), 1000)
        payload = await self._http.get(
            "/fapi/v1/depth",
            params={
                "symbol": instrument.venue_market_id,
                "limit": limit,
            },
        )
        now = self._clock.now_ms()
        return books.snapshot_book(
            instrument,
            [(base.dec(p), base.dec(q)) for p, q in payload["bids"]],
            [(base.dec(p), base.dec(q)) for p, q in payload["asks"]],
            ts_ms=int(payload.get("T", now)),
            received_ms=now,
            source="binance.rest",
            sequence=int(payload["lastUpdateId"]),
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Fetches open interest valued at the mark price.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.
        """
        await self._require(instrument)
        params = {"symbol": instrument.venue_market_id}
        oi, premium = await asyncio.gather(
            self._http.get("/fapi/v1/openInterest", params=params),
            self._http.get("/fapi/v1/premiumIndex", params=params),
        )
        mark = base.dec(premium["markPrice"])
        base_oi = base.dec(oi["openInterest"])
        return market.OpenInterest(
            instrument_id=instrument,
            base_quantity=base_oi,
            notional_usd=base_oi * mark,
            mark_price=mark,
            source="binance.rest",
            as_of_ms=int(oi.get("time", self._clock.now_ms())),
        )

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Fetches the predicted funding for the current period.

        Args:
            instrument: Instrument.

        Returns:
            Funding.
        """
        listed = await self._require(instrument)
        premium = await self._http.get(
            "/fapi/v1/premiumIndex",
            params={"symbol": instrument.venue_market_id},
        )
        snapshot = self._snapshot(
            instrument,
            listed=listed,
            premium=premium,
            ticker=None,
            book=None,
            now_ms=self._clock.now_ms(),
        )
        if snapshot.funding is None:  # pragma: no cover - always set.
            raise errors.MissingDataError("funding unavailable")
        return snapshot.funding

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams native klines with REST recovery.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates; closed bars carry ``is_final=True``.
        """
        self._require_timeframe(timeframe)
        await self._require(instrument)
        symbol = instrument.venue_market_id.lower()

        def parse(raw: str | bytes, now_ms: int) -> list[market.Candle]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time.

            Returns:
                Candles.
            """
            return parse_ws_kline(raw, instrument, timeframe, now_ms)

        async def backfill(start: int, end: int) -> list[market.Candle]:
            """Fetches missed bars over REST.

            Args:
                start: Inclusive start.
                end: Exclusive end.

            Returns:
                Candles.
            """
            return await self.fetch_candles(instrument, timeframe, start, end)

        spec = ws.WsCandleSpec(
            url=f"{WS_URL}/{symbol}@kline_{timeframe.value}",
            subscribe=(),
            parse=parse,
            timeframe=timeframe,
            venue_marks_final=True,
        )
        async for candle in ws.stream_with_recovery(
            spec,
            backfill=backfill,
            clock=self._clock,
            connect=self._connect,
            tracker=tracker,
        ):
            yield candle
