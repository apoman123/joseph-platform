"""MEXC Futures (contract API) market-data adapter.

REST base: ``https://contract.mexc.com/api/v1/contract`` — ``detail``,
``kline/{symbol}``, ``ticker``, ``funding_rate/{symbol}`` and
``depth/{symbol}``. WebSocket: ``wss://contract.mexc.com/edge`` with
``sub.kline`` and ``sub.deal``. Volumes and depth are quoted in contracts
and converted to base units with ``contractSize``. Kline frames do not flag
closed bars, so closed bars are confirmed over REST. MEXC requires a
``ping`` within one minute (answered by a ``pong`` frame).

Verified live 2026-11-22: ``Month1`` klines open on the 1st 00:00 UTC
(``Week1`` on Monday, ``Day1`` at midnight UTC). ``push.deal`` frames carry
``data`` as an array (or one object) of ``{p, v, T, O, M, t, i}``: ``T`` is
the taker side (1 buy, 2 sell; 96.7% of 152 trades printed through the
matching side of the book), ``v`` is in contracts and ``i`` is a unique,
increasing (non-contiguous) trade id. ``deals/{symbol}`` only returns the
latest 100 trades (no time range), so seconds bars come from the stream
only and outages are recorded as gaps. Klines publish no taker volume.

Market data is public; whether an account may trade a contract over the API
(region, eligibility, ``apiAllowed``) is an execution-engine concern and is
verified per account there.
"""

from __future__ import annotations

import contextlib
import decimal
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

REST_URL = "https://contract.mexc.com"
WS_URL = "wss://contract.mexc.com/edge"
_PREFIX = "/api/v1/contract"
_INTERVAL: dict[timeframes.Timeframe, str] = {
    timeframes.Timeframe.M1: "Min1",
    timeframes.Timeframe.M5: "Min5",
    timeframes.Timeframe.M15: "Min15",
    timeframes.Timeframe.M30: "Min30",
    timeframes.Timeframe.H1: "Min60",
    timeframes.Timeframe.H4: "Hour4",
    timeframes.Timeframe.D1: "Day1",
    timeframes.Timeframe.W1: "Week1",
    timeframes.Timeframe.MN1: "Month1",
}
_TF = (*timeframes.SECONDS, *_INTERVAL)
_MAX_CANDLES = 2000
_SIDES = {1: market.AggressorSide.BUY, 2: market.AggressorSide.SELL}
_EQUITY_TAGS = frozenset(
    {
        "mc-trade-zone-Stock",
        "mc-trade-zone-usstocks",
        "mc-trade-zone-stockindex",
        "mc-trade-zone-ETF",
        "mc-trade-zone-hkstocks",
        "mc-trade-zone-jpstocks",
        "mc-trade-zone-twstocks",
        "mc-trade-zone-stockmeme",
    }
)
_RWA_TAGS = frozenset(
    {
        "mc-trade-zone-Commodities",
        "mc-trade-zone-metals",
        "mc-trade-zone-metalsfutures",
        "mc-trade-zone-Forex",
        "mc-trade-zone-RWA",
        "mc-trade-zone-OIL",
    }
)
_STATUS = {
    0: instruments.InstrumentStatus.TRADING,
    1: instruments.InstrumentStatus.HALTED,
    2: instruments.InstrumentStatus.DELISTED,
    3: instruments.InstrumentStatus.DELISTED,
    4: instruments.InstrumentStatus.HALTED,
}


def asset_class(tags: list[str]) -> instruments.AssetClass:
    """Maps MEXC ``conceptPlate`` tags to an asset class.

    Args:
        tags: Concept tags.

    Returns:
        Asset class.
    """
    tag_set = set(tags)
    if tag_set & _EQUITY_TAGS:
        return instruments.AssetClass.EQUITY
    if tag_set & _RWA_TAGS:
        return instruments.AssetClass.RWA
    return instruments.AssetClass.CRYPTO


def parse_detail(
    row: Mapping[str, Any], now_ms: int
) -> instruments.Instrument | None:
    """Converts a ``detail`` row.

    Args:
        row: Contract metadata.
        now_ms: Fetch time.

    Returns:
        Instrument, or ``None`` for non-perpetuals.
    """
    if int(row.get("futureType", 1)) != 1:
        return None
    native = str(row["symbol"])
    size = base.dec(row["contractSize"])
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(
                instruments.Venue.MEXC, instruments.MarketType.PERP, native
            ),
            canonical_base=str(row["baseCoin"]),
            quote=str(row["quoteCoin"]),
            settlement=str(row.get("settleCoin", row["quoteCoin"])),
        ),
        display_symbol=native,
        tick_size=base.dec(row["priceUnit"]),
        lot_size=size * base.dec(row.get("volUnit", 1)),
        min_qty=size * base.dec(row.get("minVol", 1)),
        max_qty=(size * base.dec(row["maxVol"]) if row.get("maxVol") else None),
        contract_multiplier=size,
        linear=True,
        status=_STATUS.get(
            int(row.get("state", -1)), instruments.InstrumentStatus.UNKNOWN
        ),
        asset_class=asset_class(list(row.get("conceptPlate") or [])),
        source="mexc.rest",
        as_of_ms=now_ms,
        funding_interval_hours=None,
        max_leverage=base.opt_dec(row.get("maxLeverage")),
    )


def parse_deal_frame(
    raw: str | bytes,
    instrument_id: instruments.InstrumentId,
    contract_size: decimal.Decimal,
    now_ms: int,
) -> list[market.Trade]:
    """Parses a ``push.deal`` frame (array or single-object ``data``).

    Args:
        raw: Frame payload.
        instrument_id: Instrument.
        contract_size: Base units per contract.
        now_ms: Receipt time.

    Returns:
        Trades ascending by time and id (empty for acks and pongs).
    """
    message = json.loads(raw)
    if not isinstance(message, dict) or message.get("channel") != "push.deal":
        return []
    if message.get("symbol") not in (None, instrument_id.venue_market_id):
        return []
    data = message.get("data")
    rows: list[Mapping[str, Any]] = (
        data if isinstance(data, list) else [data] if data else []
    )
    trades = [
        market.Trade(
            instrument_id=instrument_id,
            trade_id=str(row["i"]),
            venue_event_ts_ms=int(row["t"]),
            received_at_ms=now_ms,
            price=base.dec(row["p"]),
            base_quantity=base.dec(row["v"]) * contract_size,
            aggressor_side=_SIDES.get(
                int(row["T"]), market.AggressorSide.UNKNOWN
            ),
            is_aggregated=False,
        )
        for row in rows
    ]
    return sorted(trades, key=lambda t: (t.venue_event_ts_ms, int(t.trade_id)))


class MexcAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for MEXC perpetual contracts."""

    VENUE = instruments.Venue.MEXC

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
            display_name="MEXC Futures",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                native_ws_trades=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
            ),
            timeframes=_TF,
            trade_built_timeframes=timeframes.SECONDS,
            notes=(
                "Volumes and depth converted from contracts via contractSize.",
                "Funding interval is per contract (collectCycle) and only "
                "known after a funding request.",
                "API trading eligibility is verified per account in the "
                "execution engine.",
                "Seconds bars are built from the live deal stream only: the "
                "deals endpoint has no time range, so there is no seconds "
                "history and outages are recorded as gaps.",
                "Klines publish no taker buy volume (taker_volume is false); "
                "seconds bars built from trades do carry it.",
            ),
            docs_url="https://www.mexc.com/api-docs/futures/integration-guide",
            verified_at_ms=self._verified_at_ms,
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Loads contract details.

        Returns:
            Instruments and contract-size metadata.
        """
        payload = await self._http.get(f"{_PREFIX}/detail")
        now = self._clock.now_ms()
        rows = []
        meta: dict[str, Mapping[str, Any]] = {}
        for row in payload.get("data", []):
            parsed = parse_detail(row, now)
            if parsed is not None:
                rows.append(parsed)
                meta[parsed.instrument_id.venue_market_id] = {
                    "contract_size": parsed.contract_multiplier,
                    "price_scale": int(row.get("priceScale", 8)),
                }
        return rows, meta

    def _contract_size(self, instrument: instruments.InstrumentId) -> Any:
        """Returns base units per contract.

        Args:
            instrument: Instrument.

        Returns:
            Contract size as ``Decimal``.
        """
        return self._meta_for(instrument)["contract_size"]

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches klines (seconds-based API) with pagination.

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
        if timeframe.is_seconds:
            return await self._fetch_trade_candles(
                instrument, timeframe, start_ms, end_ms, limit
            )
        await self._require(instrument)
        size = self._contract_size(instrument)
        cursor = timeframe.floor(start_ms)
        if limit is not None:
            cursor = base.newest_window_start(
                timeframe, start_ms, end_ms, limit
            )
        rows: list[market.Candle] = []
        while cursor < end_ms:
            # Window sizing only: the nominal (upper-bound) bar length.
            window_end = min(
                end_ms, cursor + _MAX_CANDLES * timeframe.duration_ms
            )
            payload = await self._http.get(
                f"{_PREFIX}/kline/{instrument.venue_market_id}",
                params={
                    "interval": _INTERVAL[timeframe],
                    "start": cursor // 1000,
                    "end": (window_end - 1) // 1000,
                },
            )
            data = payload.get("data") or {}
            now = self._clock.now_ms()
            for i, ts in enumerate(data.get("time", [])):
                rows.append(
                    base.make_candle(
                        instrument_id=instrument,
                        timeframe=timeframe,
                        start_ms=int(ts) * 1000,
                        open_=base.dec(data["open"][i]),
                        high=base.dec(data["high"][i]),
                        low=base.dec(data["low"][i]),
                        close=base.dec(data["close"][i]),
                        base_volume=base.dec(data["vol"][i]) * size,
                        quote_turnover=base.dec(data["amount"][i]),
                        now_ms=now,
                        source="mexc.rest",
                    )
                )
            cursor = window_end
        return base.clip_range(rows, start_ms, end_ms, limit)

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        ticker: Mapping[str, Any],
        now_ms: int,
    ) -> market.MarketSnapshot:
        """Assembles a snapshot from a ticker row.

        Args:
            instrument: Instrument.
            ticker: Ticker row.
            now_ms: Receipt time.

        Returns:
            Snapshot (funding interval unknown here, so funding is omitted).
        """
        size = self._contract_size(instrument)
        mark = base.opt_dec(ticker.get("fairPrice"))
        hold = base.opt_dec(ticker.get("holdVol"))
        volume = base.opt_dec(ticker.get("volume24"))
        change = base.opt_dec(ticker.get("riseFallRate"))
        ts = int(ticker.get("timestamp") or now_ms)
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source="mexc.rest",
            as_of_ms=ts,
            last=base.opt_dec(ticker.get("lastPrice")),
            mark=mark,
            index=base.opt_dec(ticker.get("indexPrice")),
            best_bid=base.opt_dec(ticker.get("bid1")),
            best_ask=base.opt_dec(ticker.get("ask1")),
            turnover_24h_quote=base.opt_dec(ticker.get("amount24")),
            volume_24h_base=None if volume is None else volume * size,
            change_24h_pct=None
            if change is None
            else change * decimals.HUNDRED,
            open_interest=(
                None
                if hold is None or mark is None
                else market.OpenInterest(
                    instrument_id=instrument,
                    base_quantity=hold * size,
                    notional_usd=hold * size * mark,
                    mark_price=mark,
                    source="mexc.rest",
                    as_of_ms=ts,
                )
            ),
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches the ticker and funding for one contract.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.

        Raises:
            errors.MissingDataError: If the venue returns no ticker.
        """
        await self._require(instrument)
        payload = await self._http.get(
            f"{_PREFIX}/ticker", params={"symbol": instrument.venue_market_id}
        )
        ticker = payload.get("data")
        if not ticker:
            raise errors.MissingDataError(f"no ticker for {instrument}")
        snapshot = self._snapshot(instrument, ticker, self._clock.now_ms())
        funding = await self.fetch_funding(instrument)
        return market.MarketSnapshot(
            instrument_id=snapshot.instrument_id,
            feed_state=snapshot.feed_state,
            source=snapshot.source,
            as_of_ms=snapshot.as_of_ms,
            last=snapshot.last,
            mark=snapshot.mark,
            index=snapshot.index,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            turnover_24h_quote=snapshot.turnover_24h_quote,
            volume_24h_base=snapshot.volume_24h_base,
            change_24h_pct=snapshot.change_24h_pct,
            funding=funding,
            open_interest=snapshot.open_interest,
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetches all contract tickers (funding omitted).

        Returns:
            Snapshots.
        """
        await self.list_instruments()
        payload = await self._http.get(f"{_PREFIX}/ticker")
        now = self._clock.now_ms()
        rows = []
        for ticker in payload.get("data") or []:
            native = str(ticker.get("symbol"))
            if native in self._instruments:
                rows.append(
                    self._snapshot(self.instrument_id(native), ticker, now)
                )
        return rows

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches depth, converting contracts to base units.

        Args:
            instrument: Instrument.
            depth: Levels per side.

        Returns:
            Order book.
        """
        await self._require(instrument)
        size = self._contract_size(instrument)
        payload = await self._http.get(
            f"{_PREFIX}/depth/{instrument.venue_market_id}",
            params={"limit": max(1, depth)},
        )
        data = payload["data"]
        now = self._clock.now_ms()
        return books.snapshot_book(
            instrument,
            [
                (base.dec(lvl[0]), base.dec(lvl[1]) * size)
                for lvl in data["bids"]
            ],
            [
                (base.dec(lvl[0]), base.dec(lvl[1]) * size)
                for lvl in data["asks"]
            ],
            ts_ms=int(data.get("timestamp") or now),
            received_ms=now,
            source="mexc.rest",
            sequence=int(data["version"]) if data.get("version") else None,
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Returns open interest from the ticker.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.

        Raises:
            errors.MissingDataError: If not published.
        """
        await self._require(instrument)
        payload = await self._http.get(
            f"{_PREFIX}/ticker", params={"symbol": instrument.venue_market_id}
        )
        snapshot = self._snapshot(
            instrument, payload.get("data") or {}, self._clock.now_ms()
        )
        if snapshot.open_interest is None:
            raise errors.MissingDataError("open interest unavailable")
        return snapshot.open_interest

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Fetches the current-period funding and its collection cycle.

        Args:
            instrument: Instrument.

        Returns:
            Funding.

        Raises:
            errors.MissingDataError: If the venue returns no data.
        """
        await self._require(instrument)
        payload = await self._http.get(
            f"{_PREFIX}/funding_rate/{instrument.venue_market_id}"
        )
        data = payload.get("data")
        if not data:
            raise errors.MissingDataError("funding unavailable")
        next_ms = (
            int(data["nextSettleTime"]) if data.get("nextSettleTime") else None
        )
        now = self._clock.now_ms()
        return market.Funding(
            instrument_id=instrument,
            rate=base.dec(data["fundingRate"]),
            interval_hours=float(data.get("collectCycle") or 8),
            kind=market.FundingKind.PREDICTED,
            effective_time_ms=next_ms or now,
            next_funding_ms=next_ms,
            source="mexc.rest",
            as_of_ms=int(data.get("timestamp") or now),
        )

    def _trade_spec(
        self, instrument: instruments.InstrumentId
    ) -> ws.WsTradeSpec:
        """Describes the ``sub.deal`` stream.

        Args:
            instrument: Instrument (already resolved).

        Returns:
            Trade stream spec.
        """
        size = self._contract_size(instrument)

        def parse(raw: str | bytes, now_ms: int) -> list[market.Trade]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time.

            Returns:
                Trades.
            """
            return parse_deal_frame(raw, instrument, size, now_ms)

        return ws.WsTradeSpec(
            url=WS_URL,
            subscribe=(
                json.dumps(
                    {
                        "method": "sub.deal",
                        "param": {"symbol": instrument.venue_market_id},
                    }
                ),
            ),
            parse=parse,
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=5.0,
            idle_timeout_s=30.0,
        )

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams klines with REST confirmation of closed bars.

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates.
        """
        self._require_timeframe(timeframe)
        if timeframe.is_seconds:
            bars = self._stream_trade_candles(instrument, timeframe, tracker)
            async with contextlib.aclosing(bars):
                async for bar in bars:
                    yield bar
            return
        await self._require(instrument)
        size = self._contract_size(instrument)
        interval = _INTERVAL[timeframe]

        def parse(raw: str | bytes, now_ms: int) -> list[market.Candle]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time.

            Returns:
                Candles.
            """
            message = json.loads(raw)
            data = message.get("data")
            if message.get("channel") != "push.kline" or not isinstance(
                data, dict
            ):
                return []
            if data.get("interval") != interval:
                return []
            return [
                base.make_candle(
                    instrument_id=instrument,
                    timeframe=timeframe,
                    start_ms=int(data["t"]) * 1000,
                    open_=base.dec(data["o"]),
                    high=base.dec(data["h"]),
                    low=base.dec(data["l"]),
                    close=base.dec(data["c"]),
                    base_volume=base.dec(data["q"]) * size,
                    quote_turnover=base.dec(data["a"]),
                    event_ts_ms=int(message.get("ts", now_ms)),
                    now_ms=now_ms,
                    source="mexc.ws",
                    is_final=False,
                )
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
                        "method": "sub.kline",
                        "param": {
                            "symbol": instrument.venue_market_id,
                            "interval": interval,
                        },
                    }
                ),
            ),
            parse=parse,
            timeframe=timeframe,
            venue_marks_final=False,
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=15.0,
        )
        candles = ws.stream_with_recovery(
            spec,
            backfill=backfill,
            clock=self._clock,
            connect=self._connect,
            tracker=tracker,
        )
        async with contextlib.aclosing(candles):
            async for candle in candles:
                yield candle
