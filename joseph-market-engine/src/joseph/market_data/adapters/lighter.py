# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Lighter (zkLighter mainnet) perpetuals market-data adapter.

REST base: ``https://mainnet.zklighter.elliot.ai/api/v1`` — ``orderBooks``,
``orderBookDetails``, ``candles`` (the older ``candlesticks`` route returns
HTTP 403 as of 2026-11), ``fundings`` and ``orderBookOrders``.

Verified behavior and assumptions:

* ``candles`` returns at most 500 bars ending at ``end_timestamp``;
  ``count_back`` is a minimum, so results are clipped to the request range.
* Candle and detail values are JSON floats; they are quantized to the
  market's published price/size decimals.
* ``fundings`` publishes hourly ``rate`` as a percentage with a separate
  ``direction``; ASSUMPTION: ``direction == "short"`` means a negative rate.
* A public WebSocket candle channel (``candle/<market>/<resolution>``) exists
  (verified 2026-11-22) but is not wired yet, so native candle streaming is
  reported unsupported (``AUTO`` polls REST and says so). Resolutions are
  exactly 1m, 5m, 15m, 30m, 1h, 4h, 12h and 1d: ``1w`` and ``1M`` are
  rejected by the venue.
* Asset class is not published; instruments are ``UNKNOWN`` (the stricter
  crypto qualification thresholds apply).

Trades (verified live 2026-11-22, docs.lighter.xyz/api):

* WebSocket ``wss://mainnet.zklighter.elliot.ai/stream`` channel
  ``trade/<market_id>``: ``subscribed/trade`` replays the latest 50 trades,
  ``update/trade`` frames follow; both list trades newest first. The client
  must send a frame at least every 2 minutes (``{"type": "ping"}``).
* ``is_maker_ask`` true means the resting maker sold, so the aggressor
  bought (714/720 trades printed through the matching touch, none against).
* ``trade_id`` is unique and increasing (a global sequence); ``timestamp``
  is the block time in ms, shared by a block's trades; ``size`` is in base
  units and ``usd_amount`` is the exact quote amount.
* REST ``trades`` (``sort_by=timestamp``, ``from`` exclusive, newest first,
  100 per page, ``next_cursor``) only reaches back about 53 minutes, so
  older seconds history is unavailable (never fabricated).
* Only ``type == "trade"`` entries count toward bars. Lighter's own
  candles exclude ``liquidation`` fills from volume and prices (verified
  2026-11-23 against the AWS trade archive: the ``trade`` rows reproduce
  the 1d candle exactly and every sampled 1m candle of a minute with
  liquidations, adding liquidations never does), so seconds bars and
  recorded true delta follow the venue's bars. ``deleverage`` and
  ``market-settlement`` entries are off-book transfers. The separate
  ``liquidation_trades`` list (hours old in the replay) is ignored.
"""

from __future__ import annotations

import asyncio
import collections
import decimal
import json
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
from joseph.market_data.domain import trades as trades_lib

REST_URL = "https://mainnet.zklighter.elliot.ai/api/v1"
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
_KLINE_TF = (
    timeframes.Timeframe.M1,
    timeframes.Timeframe.M5,
    timeframes.Timeframe.M15,
    timeframes.Timeframe.M30,
    timeframes.Timeframe.H1,
    timeframes.Timeframe.H4,
    timeframes.Timeframe.H12,
    timeframes.Timeframe.D1,
)
_TF = (*timeframes.SECONDS, *_KLINE_TF)
_MAX_CANDLES = 500
_MAX_TRADES = 100
_BOOK_TRADE_TYPES = frozenset({"trade"})
_MAX_BOOK_ORDERS = 250
_QUOTE_DECIMALS = 6
_HOUR_MS = 3_600_000


def parse_order_books(
    payload: Mapping[str, Any], now_ms: int
) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
    """Converts ``orderBooks`` to perpetual instruments.

    Args:
        payload: ``orderBooks`` response.
        now_ms: Fetch time.

    Returns:
        Instruments and native metadata (market id and decimals) by symbol.
    """
    rows: list[instruments.Instrument] = []
    meta: dict[str, Mapping[str, Any]] = {}
    for book in payload.get("order_books", []):
        if book.get("market_type") != "perp":
            continue
        symbol = str(book["symbol"])
        size_dec = int(book["supported_size_decimals"])
        price_dec = int(book["supported_price_decimals"])
        rows.append(
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=instruments.InstrumentId(
                        instruments.Venue.LIGHTER,
                        instruments.MarketType.PERP,
                        symbol,
                    ),
                    canonical_base=symbol,
                    quote="USD",
                    settlement="USDC",
                ),
                display_symbol=f"{symbol}-PERP",
                tick_size=base.pow10(-price_dec),
                lot_size=base.pow10(-size_dec),
                min_qty=base.dec(book["min_base_amount"]),
                min_notional=base.opt_dec(book.get("min_quote_amount")),
                contract_multiplier=base.dec(book.get("multiplier", "1")),
                linear=True,
                status=(
                    instruments.InstrumentStatus.TRADING
                    if book.get("status") == "active"
                    and not book.get("is_frozen")
                    else instruments.InstrumentStatus.HALTED
                ),
                asset_class=instruments.AssetClass.UNKNOWN,
                source="lighter.rest",
                as_of_ms=now_ms,
                funding_interval_hours=1.0,
            )
        )
        meta[symbol] = {
            "market_id": int(book["market_id"]),
            "size_decimals": size_dec,
            "price_decimals": price_dec,
        }
    return rows, meta


def parse_candle(
    row: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    *,
    price_dec: int,
    size_dec: int,
    now_ms: int,
) -> market.Candle:
    """Converts a ``candles`` row, quantizing float artifacts.

    Args:
        row: ``{t, o, h, l, c, v, V, i}``.
        instrument_id: Instrument.
        timeframe: Resolution.
        price_dec: Published price decimals.
        size_dec: Published size decimals.
        now_ms: Receipt time.

    Returns:
        Candle.
    """

    def price(key: str) -> decimal.Decimal:
        """Quantizes one price field.

        Args:
            key: Field name.

        Returns:
            Price.
        """
        return base.quantize(base.dec(row[key]), price_dec)

    return base.make_candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=int(row["t"]),
        open_=price("o"),
        high=price("h"),
        low=price("l"),
        close=price("c"),
        base_volume=base.quantize(base.dec(row["v"]), size_dec),
        quote_turnover=base.quantize(base.dec(row["V"]), _QUOTE_DECIMALS),
        now_ms=now_ms,
        source="lighter.rest",
    )


def parse_funding(
    row: Mapping[str, Any], instrument_id: instruments.InstrumentId, now_ms: int
) -> market.Funding:
    """Converts a settled hourly ``fundings`` row.

    Args:
        row: ``{timestamp (s), rate (%), direction}``.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Settled funding with the rate as a decimal fraction.
    """
    rate = base.dec(row["rate"]) / decimals.HUNDRED
    if str(row.get("direction", "long")).lower() == "short":
        rate = -rate
    settled_ms = int(row["timestamp"]) * 1000
    return market.Funding(
        instrument_id=instrument_id,
        rate=rate,
        interval_hours=1.0,
        kind=market.FundingKind.SETTLED,
        effective_time_ms=settled_ms,
        next_funding_ms=settled_ms + _HOUR_MS,
        source="lighter.rest",
        as_of_ms=now_ms,
    )


def parse_trade(
    row: Mapping[str, Any], instrument_id: instruments.InstrumentId, now_ms: int
) -> market.Trade | None:
    """Converts one REST or WebSocket trade object.

    Args:
        row: Trade object (``trade_id``, ``price``, ``size``, ...).
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Trade, or ``None`` for entries Lighter's candles do not count
        (liquidation, deleverage, settlement).
    """
    if str(row.get("type", "trade")) not in _BOOK_TRADE_TYPES:
        return None
    usd = base.opt_dec(row.get("usd_amount"))
    return market.Trade(
        instrument_id=instrument_id,
        trade_id=str(row["trade_id"]),
        venue_event_ts_ms=int(row["timestamp"]),
        received_at_ms=now_ms,
        price=base.dec(row["price"]),
        base_quantity=base.dec(row["size"]),
        aggressor_side=(
            market.AggressorSide.BUY
            if row["is_maker_ask"]
            else market.AggressorSide.SELL
        ),
        is_aggregated=False,
        quote_quantity=usd,
    )


def parse_trade_rows(
    rows: Sequence[Mapping[str, Any]],
    instrument_id: instruments.InstrumentId,
    now_ms: int,
) -> list[market.Trade]:
    """Converts newest-first trade rows to ascending book trades.

    Args:
        rows: Trade objects.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Trades ascending by time and trade id.
    """
    parsed = [parse_trade(row, instrument_id, now_ms) for row in rows]
    return sorted(
        (t for t in parsed if t is not None),
        key=lambda t: (t.venue_event_ts_ms, int(t.trade_id)),
    )


def parse_trade_frame(
    raw: str | bytes,
    instrument_id: instruments.InstrumentId,
    market_id: int,
    now_ms: int,
) -> list[market.Trade]:
    """Parses a ``trade`` channel frame (replay or update).

    Args:
        raw: Frame payload.
        instrument_id: Instrument.
        market_id: Numeric market id.
        now_ms: Receipt time.

    Returns:
        Trades ascending (empty for ``connected``, pongs and other frames).

    Raises:
        ValueError: On a venue error frame.
    """
    message = json.loads(raw)
    if not isinstance(message, dict):
        return []
    if "error" in message:
        raise ValueError(f"lighter stream error: {message['error']}")
    if message.get("type") not in ("subscribed/trade", "update/trade"):
        return []
    if message.get("channel") != f"trade:{market_id}":
        return []
    return parse_trade_rows(message.get("trades") or [], instrument_id, now_ms)


def aggregate_orders(
    orders: Sequence[Mapping[str, Any]],
) -> list[tuple[decimal.Decimal, decimal.Decimal]]:
    """Aggregates individual resting orders into price levels.

    Args:
        orders: ``orderBookOrders`` rows.

    Returns:
        ``(price, quantity)`` levels.
    """
    levels: collections.defaultdict[decimal.Decimal, decimal.Decimal] = (
        collections.defaultdict(decimal.Decimal)
    )
    for order in orders:
        levels[base.dec(order["price"])] += base.dec(
            order["remaining_base_amount"]
        )
    return list(levels.items())


class LighterAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for Lighter perpetuals (REST candles, WS trades)."""

    VENUE = instruments.Venue.LIGHTER

    def __init__(
        self,
        http_client: http.VenueHttp,
        clock: clock_lib.Clock,
        verified_at_ms: int | None = None,
        connect: ws.ConnectFn = ws.default_connect,
    ) -> None:
        """Initializes the adapter.

        Args:
            http_client: HTTP client for ``REST_URL``.
            clock: Time source.
            verified_at_ms: When capabilities were last verified live.
            connect: WebSocket connector (trade stream).
        """
        super().__init__(http_client, clock)
        self._verified_at_ms = verified_at_ms
        self._connect = connect

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="Lighter",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_trades=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
                trades_history=True,
            ),
            timeframes=_TF,
            trade_built_timeframes=timeframes.SECONDS,
            notes=(
                "Native WebSocket candles are not wired (the venue channel "
                "exists); AUTO polls REST for minute and longer bars.",
                "Funding is the last settled hourly rate.",
                "Asset class is not published (treated as UNKNOWN).",
                "3m, 2h, 6h, 1w and 1M candles are not published by the venue.",
                "Trade history reaches back only about 53 minutes; older "
                "seconds bars exist only where the engine recorded trades.",
                "Klines publish no taker buy volume (taker_volume is false); "
                "seconds bars built from trades do carry it.",
            ),
            docs_url="https://docs.lighter.xyz/api",
            verified_at_ms=self._verified_at_ms,
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Loads order-book metadata.

        Returns:
            Instruments and metadata.
        """
        payload = await self._http.get("/orderBooks")
        return parse_order_books(payload, self._clock.now_ms())

    def _market_id(self, instrument: instruments.InstrumentId) -> int:
        """Returns the numeric market id.

        Args:
            instrument: Instrument.

        Returns:
            Market id.
        """
        return int(self._meta_for(instrument)["market_id"])

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches candles, paging backwards 500 bars at a time.

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
        meta = self._meta_for(instrument)
        start = timeframe.floor(start_ms)
        if limit is not None:
            start = base.newest_window_start(timeframe, start_ms, end_ms, limit)
        rows: list[market.Candle] = []
        cursor_end = end_ms
        while cursor_end > start:
            wanted = min(
                _MAX_CANDLES, timeframe.bars_between(start, cursor_end)
            )
            payload = await self._http.get(
                "/candles",
                params={
                    "market_id": meta["market_id"],
                    "resolution": timeframe.value,
                    "start_timestamp": start,
                    "end_timestamp": cursor_end,
                    "count_back": wanted,
                },
            )
            now = self._clock.now_ms()
            batch = [
                parse_candle(
                    r,
                    instrument,
                    timeframe,
                    price_dec=int(meta["price_decimals"]),
                    size_dec=int(meta["size_decimals"]),
                    now_ms=now,
                )
                for r in payload.get("c", [])
            ]
            if not batch:
                break
            rows.extend(batch)
            earliest = min(c.start_ms for c in batch)
            if earliest <= start or len(batch) < wanted:
                break
            cursor_end = earliest
        return base.clip_range(rows, start_ms, end_ms, limit)

    async def _details(
        self, market_id: int | None = None
    ) -> list[Mapping[str, Any]]:
        """Fetches ``orderBookDetails`` (one market or all perps).

        Args:
            market_id: Optional market id.

        Returns:
            Detail rows.
        """
        params = {} if market_id is None else {"market_id": market_id}
        payload = await self._http.get("/orderBookDetails", params=params)
        return list(payload.get("order_book_details", []))

    async def _latest_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding | None:
        """Fetches the most recent settled hourly funding.

        Args:
            instrument: Instrument.

        Returns:
            Funding or ``None`` when none was published.
        """
        now = self._clock.now_ms()
        payload = await self._http.get(
            "/fundings",
            params={
                "market_id": self._market_id(instrument),
                "resolution": "1h",
                "start_timestamp": now - 6 * _HOUR_MS,
                "end_timestamp": now,
                "count_back": 6,
            },
        )
        rows = sorted(
            payload.get("fundings", []), key=lambda r: int(r["timestamp"])
        )
        if not rows:
            return None
        return parse_funding(rows[-1], instrument, now)

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        detail: Mapping[str, Any],
        book: market.OrderBook | None,
        funding: market.Funding | None,
        now_ms: int,
    ) -> market.MarketSnapshot:
        """Assembles a snapshot from a detail row.

        Args:
            instrument: Instrument.
            detail: ``orderBookDetails`` row.
            book: Optional top of book.
            funding: Optional funding.
            now_ms: Receipt time.

        Returns:
            Snapshot.
        """
        price_dec = int(
            detail.get(
                "price_decimals", detail.get("supported_price_decimals", 6)
            )
        )
        mark = base.opt_dec(detail.get("mark_price"))
        last_raw = detail.get("last_trade_price")
        oi_usd = base.opt_dec(detail.get("open_interest_usd"))
        oi_base = base.opt_dec(detail.get("open_interest"))
        open_interest = None
        if oi_usd is not None and oi_base is not None:
            open_interest = market.OpenInterest(
                instrument_id=instrument,
                base_quantity=base.quantize(
                    oi_base, int(detail.get("size_decimals", 8))
                ),
                notional_usd=oi_usd,
                mark_price=mark,
                source="lighter.rest",
                as_of_ms=now_ms,
            )
        change = base.opt_dec(detail.get("daily_price_change"))
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source="lighter.rest",
            as_of_ms=now_ms,
            last=(
                base.quantize(base.dec(last_raw), price_dec)
                if last_raw not in (None, "")
                else None
            ),
            mark=mark,
            index=base.opt_dec(detail.get("index_price")),
            best_bid=book.best_bid if book else None,
            best_ask=book.best_ask if book else None,
            turnover_24h_quote=(
                base.quantize(
                    base.dec(detail["daily_quote_token_volume"]),
                    _QUOTE_DECIMALS,
                )
                if detail.get("daily_quote_token_volume") is not None
                else None
            ),
            volume_24h_base=base.opt_dec(detail.get("daily_base_token_volume")),
            change_24h_pct=(
                None if change is None else base.quantize(change, 4)
            ),
            funding=funding,
            open_interest=open_interest,
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches details, top of book and latest funding.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.

        Raises:
            errors.MissingDataError: If the venue returns no details.
        """
        await self._require(instrument)
        details, book, funding = await asyncio.gather(
            self._details(self._market_id(instrument)),
            self.fetch_order_book(instrument, 1),
            self._latest_funding(instrument),
        )
        if not details:
            raise errors.MissingDataError(f"no details for {instrument}")
        return self._snapshot(
            instrument, details[0], book, funding, self._clock.now_ms()
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Builds snapshots for every active perpetual from one request.

        Returns:
            Snapshots without best bid/ask and funding.
        """
        await self.list_instruments()
        now = self._clock.now_ms()
        rows = []
        for detail in await self._details():
            if detail.get("market_type") != "perp":
                continue
            symbol = str(detail["symbol"])
            if symbol not in self._instruments:
                continue
            rows.append(
                self._snapshot(
                    self.instrument_id(symbol), detail, None, None, now
                )
            )
        return rows

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches resting orders and aggregates them into levels.

        Args:
            instrument: Instrument.
            depth: Approximate levels per side.

        Returns:
            Order book.
        """
        await self._require(instrument)
        payload = await self._http.get(
            "/orderBookOrders",
            params={
                "market_id": self._market_id(instrument),
                "limit": max(1, min(_MAX_BOOK_ORDERS, depth)),
            },
        )
        now = self._clock.now_ms()
        return books.snapshot_book(
            instrument,
            aggregate_orders(payload.get("bids", [])),
            aggregate_orders(payload.get("asks", [])),
            ts_ms=now,
            received_ms=now,
            source="lighter.rest",
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Returns venue-published open interest.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.

        Raises:
            errors.MissingDataError: If not published.
        """
        await self._require(instrument)
        details = await self._details(self._market_id(instrument))
        if not details:
            raise errors.MissingDataError(f"no details for {instrument}")
        snapshot = self._snapshot(
            instrument, details[0], None, None, self._clock.now_ms()
        )
        if snapshot.open_interest is None:
            raise errors.MissingDataError("open interest not published")
        return snapshot.open_interest

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Returns the latest settled hourly funding.

        Args:
            instrument: Instrument.

        Returns:
            Funding.

        Raises:
            errors.MissingDataError: If no recent funding exists.
        """
        await self._require(instrument)
        funding = await self._latest_funding(instrument)
        if funding is None:
            raise errors.MissingDataError("no recent funding published")
        return funding

    def _trade_spec(
        self, instrument: instruments.InstrumentId
    ) -> ws.WsTradeSpec:
        """Describes the ``trade/<market_id>`` channel.

        Args:
            instrument: Instrument (already resolved).

        Returns:
            Trade stream spec.
        """
        market_id = self._market_id(instrument)

        def parse(raw: str | bytes, now_ms: int) -> list[market.Trade]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time.

            Returns:
                Trades.
            """
            return parse_trade_frame(raw, instrument, market_id, now_ms)

        return ws.WsTradeSpec(
            url=WS_URL,
            subscribe=(
                json.dumps(
                    {"type": "subscribe", "channel": f"trade/{market_id}"}
                ),
            ),
            parse=parse,
            ping_message=json.dumps({"type": "ping"}),
            ping_interval_s=5.0,
            idle_timeout_s=30.0,
            replays_recent=True,
        )

    async def _trade_history(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int,
    ) -> trades_lib.TradeCoverage:
        """Pages ``trades`` newest first from ``end_ms`` back to ``start_ms``.

        Args:
            instrument: Instrument (already resolved).
            start_ms: Inclusive start.
            end_ms: Exclusive end (``from`` is exclusive at the venue).
            max_pages: Request budget.

        Returns:
            Complete suffix of the range.
        """
        market_id = self._market_id(instrument)
        params: dict[str, Any] = {
            "market_id": market_id,
            "sort_by": "timestamp",
            "limit": _MAX_TRADES,
            "from": end_ms,
        }
        collected: list[market.Trade] = []
        complete = False
        for _ in range(max_pages):
            payload = await self._http.get("/trades", params=params)
            rows = payload.get("trades") or []
            collected.extend(
                parse_trade_rows(rows, instrument, self._clock.now_ms())
            )
            if any(int(row["timestamp"]) < start_ms for row in rows):
                complete = True
                break
            cursor = payload.get("next_cursor")
            if len(rows) < _MAX_TRADES or not cursor:
                break
            params = {
                "market_id": market_id,
                "sort_by": "timestamp",
                "limit": _MAX_TRADES,
                "cursor": cursor,
            }
        return trades_lib.coverage_from_suffix(
            trades_lib.sort_trades(collected), start_ms, end_ms, complete
        )
