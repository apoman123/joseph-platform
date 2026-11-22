"""Bitget v2 mix (``USDT-FUTURES``) market-data adapter.

REST base: ``https://api.bitget.com/api/v2/mix/market`` — ``contracts``,
``candles``, ``ticker(s)``, ``open-interest``, ``current-fund-rate`` and
``orderbook``. WebSocket: ``wss://ws.bitget.com/v2/ws/public`` with
``candle<granularity>`` channels (all used channels verified live).

Daily, weekly, 6h and 12h granularities default to UTC+8 boundaries on
Bitget; the ``...utc`` variants are used so bars align to UTC. Candle frames
do not flag closed bars, so closed bars are confirmed over REST. The server
expects a text ``ping`` at least every 30 seconds.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Mapping
from collections.abc import Sequence
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

REST_URL = "https://api.bitget.com"
WS_URL = "wss://ws.bitget.com/v2/ws/public"
PRODUCT = "USDT-FUTURES"
_PREFIX = "/api/v2/mix/market"
_GRANULARITY: dict[timeframes.Timeframe, str] = {
    timeframes.Timeframe.M1: "1m",
    timeframes.Timeframe.M3: "3m",
    timeframes.Timeframe.M5: "5m",
    timeframes.Timeframe.M15: "15m",
    timeframes.Timeframe.M30: "30m",
    timeframes.Timeframe.H1: "1H",
    timeframes.Timeframe.H2: "2H",
    timeframes.Timeframe.H4: "4H",
    timeframes.Timeframe.H6: "6Hutc",
    timeframes.Timeframe.H12: "12Hutc",
    timeframes.Timeframe.D1: "1Dutc",
    timeframes.Timeframe.W1: "1Wutc",
}
_MAX_CANDLES = 1000
_BOOK_LIMITS = (1, 5, 15, 50, 100)
_STATUS = {
    "normal": instruments.InstrumentStatus.TRADING,
    "listed": instruments.InstrumentStatus.PRE_LAUNCH,
    "maintain": instruments.InstrumentStatus.HALTED,
    "limit_open": instruments.InstrumentStatus.HALTED,
    "restrictedAPI": instruments.InstrumentStatus.HALTED,
    "off": instruments.InstrumentStatus.DELISTED,
}


def parse_contract(
    row: Mapping[str, Any], now_ms: int
) -> instruments.Instrument | None:
    """Converts a ``contracts`` row.

    Args:
        row: Contract metadata.
        now_ms: Fetch time.

    Returns:
        Instrument, or ``None`` for non-perpetuals.
    """
    if row.get("symbolType") != "perpetual":
        return None
    native = str(row["symbol"])
    tick = base.dec(row["priceEndStep"]) * base.pow10(-int(row["pricePlace"]))
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(
                instruments.Venue.BITGET, instruments.MarketType.PERP, native
            ),
            canonical_base=str(row["baseCoin"]),
            quote=str(row["quoteCoin"]),
            settlement=str((row.get("supportMarginCoins") or ["USDT"])[0]),
        ),
        display_symbol=native,
        tick_size=tick,
        lot_size=base.dec(row["sizeMultiplier"]),
        min_qty=base.dec(row["minTradeNum"]),
        max_qty=base.opt_dec(row.get("maxOrderQty")),
        min_notional=base.opt_dec(row.get("minTradeUSDT")),
        contract_multiplier=decimals.ONE,
        linear=True,
        status=_STATUS.get(
            str(row.get("symbolStatus")), instruments.InstrumentStatus.UNKNOWN
        ),
        asset_class=(
            instruments.AssetClass.RWA
            if row.get("isRwa") == "YES"
            else instruments.AssetClass.CRYPTO
        ),
        source="bitget.rest",
        as_of_ms=now_ms,
        funding_interval_hours=float(row.get("fundInterval") or 8),
        max_leverage=base.opt_dec(row.get("maxLever")),
    )


def parse_candle_row(
    row: Sequence[Any],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    now_ms: int,
    source: str,
) -> market.Candle:
    """Converts ``[ts, o, h, l, c, baseVol, quoteVol, ...]``.

    Args:
        row: Candle row.
        instrument_id: Instrument.
        timeframe: Resolution.
        now_ms: Receipt time.
        source: Provenance label (``.ws`` rows are never final).

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
        quote_turnover=base.opt_dec(row[6]) if len(row) > 6 else None,
        now_ms=now_ms,
        source=source,
        is_final=False if source.endswith(".ws") else None,
    )


class BitgetAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for Bitget USDT-margined perpetuals."""

    VENUE = instruments.Venue.BITGET

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
            display_name="Bitget Futures",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
            ),
            timeframes=tuple(_GRANULARITY),
            notes=(
                "USDT-FUTURES product line only (v2 mix API).",
                "UTC granularities (1Dutc, 1Wutc, 6Hutc, 12Hutc) are used.",
                "RWA contracts are flagged by isRwa; equities are not "
                "distinguished from other RWA.",
            ),
            docs_url="https://www.bitget.com/api-doc/contract/intro",
            verified_at_ms=self._verified_at_ms,
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Loads USDT-FUTURES contracts.

        Returns:
            Instruments and empty metadata.
        """
        payload = await self._http.get(
            f"{_PREFIX}/contracts", params={"productType": PRODUCT}
        )
        now = self._clock.now_ms()
        rows = [
            parsed
            for parsed in (
                parse_contract(row, now) for row in payload.get("data", [])
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
        """Fetches candles with forward pagination (1000 per request).

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
        duration = timeframe.duration_ms
        cursor = timeframe.floor(start_ms)
        if limit is not None:
            cursor = max(
                cursor, timeframe.floor(end_ms - 1) - (limit - 1) * duration
            )
        rows: list[market.Candle] = []
        while cursor < end_ms:
            window_end = min(end_ms, cursor + _MAX_CANDLES * duration)
            payload = await self._http.get(
                f"{_PREFIX}/candles",
                params={
                    "symbol": instrument.venue_market_id,
                    "productType": PRODUCT,
                    "granularity": _GRANULARITY[timeframe],
                    "startTime": cursor,
                    "endTime": window_end - 1,
                    "limit": _MAX_CANDLES,
                },
            )
            now = self._clock.now_ms()
            rows.extend(
                parse_candle_row(r, instrument, timeframe, now, "bitget.rest")
                for r in payload.get("data", [])
            )
            cursor = window_end
        return base.clip_range(rows, start_ms, end_ms, limit)

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        listed: instruments.Instrument,
        ticker: Mapping[str, Any],
        now_ms: int,
    ) -> market.MarketSnapshot:
        """Assembles a snapshot from a ticker row.

        Args:
            instrument: Instrument.
            listed: Instrument metadata.
            ticker: Ticker row.
            now_ms: Receipt time.

        Returns:
            Snapshot.
        """
        mark = base.opt_dec(ticker.get("markPrice"))
        oi_base = base.opt_dec(ticker.get("holdingAmount"))
        change = base.opt_dec(ticker.get("change24h"))
        ts = int(ticker.get("ts") or now_ms)
        funding_rate = base.opt_dec(ticker.get("fundingRate"))
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source="bitget.rest",
            as_of_ms=ts,
            last=base.opt_dec(ticker.get("lastPr")),
            mark=mark,
            index=base.opt_dec(ticker.get("indexPrice")),
            best_bid=base.opt_dec(ticker.get("bidPr")),
            best_ask=base.opt_dec(ticker.get("askPr")),
            turnover_24h_quote=base.opt_dec(ticker.get("quoteVolume")),
            volume_24h_base=base.opt_dec(ticker.get("baseVolume")),
            change_24h_pct=(
                None if change is None else change * decimals.HUNDRED
            ),
            funding=None
            if funding_rate is None
            else market.Funding(
                instrument_id=instrument,
                rate=funding_rate,
                interval_hours=listed.funding_interval_hours or 8.0,
                kind=market.FundingKind.PREDICTED,
                effective_time_ms=ts,
                source="bitget.rest",
                as_of_ms=ts,
            ),
            open_interest=(
                None
                if oi_base is None or mark is None
                else market.OpenInterest(
                    instrument_id=instrument,
                    base_quantity=oi_base,
                    notional_usd=oi_base * mark,
                    mark_price=mark,
                    source="bitget.rest",
                    as_of_ms=ts,
                )
            ),
        )

    async def _ticker(
        self, instrument: instruments.InstrumentId
    ) -> Mapping[str, Any]:
        """Fetches one ticker row.

        Args:
            instrument: Instrument.

        Returns:
            Ticker row.

        Raises:
            errors.MissingDataError: If the venue returns no row.
        """
        payload = await self._http.get(
            f"{_PREFIX}/ticker",
            params={
                "symbol": instrument.venue_market_id,
                "productType": PRODUCT,
            },
        )
        rows = payload.get("data") or []
        if not rows:
            raise errors.MissingDataError(f"no ticker for {instrument}")
        return rows[0]  # type: ignore[no-any-return]

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches the ticker snapshot (includes mark, funding and OI).

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.
        """
        listed = await self._require(instrument)
        return self._snapshot(
            instrument,
            listed,
            await self._ticker(instrument),
            self._clock.now_ms(),
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches all USDT-FUTURES tickers.

        Returns:
            Snapshots.
        """
        await self.list_instruments()
        payload = await self._http.get(
            f"{_PREFIX}/tickers", params={"productType": PRODUCT}
        )
        now = self._clock.now_ms()
        rows = []
        for ticker in payload.get("data", []):
            listed = self._instruments.get(str(ticker.get("symbol")))
            if listed is not None:
                rows.append(
                    self._snapshot(listed.instrument_id, listed, ticker, now)
                )
        return rows

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches a depth snapshot.

        Args:
            instrument: Instrument.
            depth: Levels per side (rounded up to a Bitget limit).

        Returns:
            Order book.
        """
        await self._require(instrument)
        limit = next((d for d in _BOOK_LIMITS if d >= depth), 100)
        payload = await self._http.get(
            f"{_PREFIX}/orderbook",
            params={
                "symbol": instrument.venue_market_id,
                "productType": PRODUCT,
                "limit": limit,
            },
        )
        data = payload["data"]
        now = self._clock.now_ms()
        return books.snapshot_book(
            instrument,
            [(base.dec(p), base.dec(q)) for p, q in data["bids"]],
            [(base.dec(p), base.dec(q)) for p, q in data["asks"]],
            ts_ms=int(data.get("ts") or now),
            received_ms=now,
            source="bitget.rest",
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Fetches open interest valued at the mark price.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.

        Raises:
            errors.MissingDataError: If the venue omits the value.
        """
        await self._require(instrument)
        params = {"symbol": instrument.venue_market_id, "productType": PRODUCT}
        oi, ticker = await asyncio.gather(
            self._http.get(f"{_PREFIX}/open-interest", params=params),
            self._ticker(instrument),
        )
        entries = oi.get("data", {}).get("openInterestList", [])
        mark = base.opt_dec(ticker.get("markPrice"))
        if not entries or mark is None:
            raise errors.MissingDataError("open interest unavailable")
        size = base.dec(entries[0]["size"])
        return market.OpenInterest(
            instrument_id=instrument,
            base_quantity=size,
            notional_usd=size * mark,
            mark_price=mark,
            source="bitget.rest",
            as_of_ms=int(oi["data"].get("ts") or self._clock.now_ms()),
        )

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Fetches the current-period (predicted) funding rate.

        Args:
            instrument: Instrument.

        Returns:
            Funding.

        Raises:
            errors.MissingDataError: If the venue returns no row.
        """
        await self._require(instrument)
        payload = await self._http.get(
            f"{_PREFIX}/current-fund-rate",
            params={
                "symbol": instrument.venue_market_id,
                "productType": PRODUCT,
            },
        )
        rows = payload.get("data") or []
        if not rows:
            raise errors.MissingDataError("funding unavailable")
        row = rows[0]
        now = self._clock.now_ms()
        next_ms = int(row["nextUpdate"]) if row.get("nextUpdate") else None
        return market.Funding(
            instrument_id=instrument,
            rate=base.dec(row["fundingRate"]),
            interval_hours=float(row.get("fundingRateInterval") or 8),
            kind=market.FundingKind.PREDICTED,
            effective_time_ms=next_ms or now,
            next_funding_ms=next_ms,
            source="bitget.rest",
            as_of_ms=now,
        )

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams candles with REST confirmation of closed bars.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates.
        """
        self._require_timeframe(timeframe)
        await self._require(instrument)
        channel = f"candle{_GRANULARITY[timeframe]}"

        def parse(raw: str | bytes, now_ms: int) -> list[market.Candle]:
            """Parses one frame (snapshot frames keep only the newest bar).

            Args:
                raw: Frame payload.
                now_ms: Receipt time.

            Returns:
                Candles.
            """
            if raw in ("pong", b"pong"):
                return []
            message = json.loads(raw)
            arg = message.get("arg", {})
            if arg.get("channel") != channel or "data" not in message:
                return []
            rows = sorted(message["data"], key=lambda r: int(r[0]))
            if message.get("action") == "snapshot":
                rows = rows[-1:]
            return [
                parse_candle_row(r, instrument, timeframe, now_ms, "bitget.ws")
                for r in rows
            ]

        async def backfill(start: int, end: int) -> list[market.Candle]:
            """Fetches missed or closing bars over REST.

            Args:
                start: Inclusive start.
                end: Exclusive end.

            Returns:
                Candles.
            """
            return await self.fetch_candles(instrument, timeframe, start, end)

        spec = ws.WsCandleSpec(
            url=WS_URL,
            subscribe=(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            {
                                "instType": PRODUCT,
                                "channel": channel,
                                "instId": instrument.venue_market_id,
                            }
                        ],
                    }
                ),
            ),
            parse=parse,
            timeframe=timeframe,
            venue_marks_final=False,
            ping_message="ping",
            ping_interval_s=25.0,
        )
        async for candle in ws.stream_with_recovery(
            spec,
            backfill=backfill,
            clock=self._clock,
            connect=self._connect,
            tracker=tracker,
        ):
            yield candle
