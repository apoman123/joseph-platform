# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Hyperliquid perpetuals market-data adapter.

REST: ``POST https://api.hyperliquid.xyz/info`` with ``metaAndAssetCtxs``,
``candleSnapshot`` and ``l2Book``. WebSocket: ``wss://api.hyperliquid.xyz/ws``
``candle`` and ``trades`` subscriptions. Candle frames do not flag closed
bars, so each closed bar is confirmed over REST at rollover.

Price precision follows Hyperliquid's rule (``TICK_RULES_URL``, verified
2026-11-24): at most 5 significant figures and ``6 - szDecimals`` decimals,
and integer prices are always allowed, so the tick is never coarser than 1.
``tick_size`` is derived from the current mark price and refreshed with the
instrument cache. Weekly candles are epoch-aligned (Thursday) and 6h candles
do not exist, so neither is used natively. Verified live 2026-11-22: the
``1M`` interval is a 30-day bucket counted from the epoch (bars start e.g.
2026-10-04 and 2026-11-03), not a calendar month, so it is never relabelled
``1M``. The engine aggregates ``6h`` (from 2h), Monday-aligned ``1w`` and
calendar ``1M`` (from 1d) instead (``app.derived_bars``; verified live
2026-11-23 that 24 native 1h bars fold to the native 1d bar exactly).

Trades (verified live 2026-11-22): ``side`` is the aggressor ("B" buy, "A"
sell; 193/193 trades printed through the matching side of the BBO) and
``users`` is ``[buyer, seller]``. ``tid`` is a hash, unique but not
monotonic, so trades are keyed by ``time`` and ``tid``. Subscribing replays
the latest 30 trades, which proves continuity after a short reconnect when
the replay overlaps the last trade seen. There is no public time-range trade
history (``recentTrades`` returns 10 trades), so longer outages are gaps.
"""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import json
import math
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

REST_URL = "https://api.hyperliquid.xyz"
WS_URL = "wss://api.hyperliquid.xyz/ws"
TICK_RULES_URL = (
    "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/"
    "tick-and-lot-size"
)
_KLINE_TF = tuple(
    tf
    for tf in timeframes.MINUTE_AND_ABOVE
    if tf
    not in (
        timeframes.Timeframe.H6,
        timeframes.Timeframe.M10,
        timeframes.Timeframe.M45,
        timeframes.Timeframe.H8,
        timeframes.Timeframe.W1,
        timeframes.Timeframe.MN1,
    )
)
_TF = (*timeframes.SECONDS, *_KLINE_TF)
_SIDES = {"B": market.AggressorSide.BUY, "A": market.AggressorSide.SELL}
_MAX_DECIMALS = 6
"""Perp prices carry at most ``_MAX_DECIMALS - szDecimals`` decimals."""
_CTX_TTL_MS = 2_000
"""Milliseconds a ``metaAndAssetCtxs`` payload is reused across lookups."""
_HOUR_MS = 3_600_000
"""Funding is paid every hour on the hour."""


def tick_size(
    mark: decimal.Decimal | None, sz_decimals: int
) -> decimal.Decimal:
    """Derives the price increment from Hyperliquid's precision rule.

    The tick is the coarser of the fifth significant figure of ``mark``
    and the ``6 - sz_decimals`` decimal limit, but never coarser than 1:
    integer prices are always valid, so a mark of 100,000 or more has a
    tick of 1 (``123456`` is a valid price, ``12345.6`` is not).

    Args:
        mark: Current mark price (``None`` or non-positive falls back to the
            decimal limit).
        sz_decimals: Size decimals of the asset.

    Returns:
        Tick size.
    """
    floor_tick = base.pow10(-(_MAX_DECIMALS - sz_decimals))
    if mark is None or mark <= decimals.ZERO:
        return floor_tick
    magnitude = math.floor(math.log10(float(mark)))
    sig_tick = base.pow10(min(max(magnitude - 4, -_MAX_DECIMALS), 0))
    return max(sig_tick, floor_tick)


def parse_universe(
    meta: Mapping[str, Any], ctxs: Sequence[Mapping[str, Any]], now_ms: int
) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
    """Converts ``metaAndAssetCtxs`` to instruments.

    Args:
        meta: Universe metadata.
        ctxs: Asset contexts aligned with the universe.
        now_ms: Fetch time, epoch milliseconds.

    Returns:
        Instruments and native metadata (``szDecimals``) keyed by coin.
    """
    rows: list[instruments.Instrument] = []
    meta_by_coin: dict[str, Mapping[str, Any]] = {}
    for asset, ctx in zip(meta["universe"], ctxs, strict=False):
        coin = str(asset["name"])
        sz = int(asset["szDecimals"])
        mark = base.opt_dec(ctx.get("markPx"))
        lot = base.pow10(-sz)
        rows.append(
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=instruments.InstrumentId(
                        instruments.Venue.HYPERLIQUID,
                        instruments.MarketType.PERP,
                        coin,
                    ),
                    canonical_base=coin,
                    quote="USD",
                    settlement="USDC",
                ),
                display_symbol=f"{coin}-PERP",
                tick_size=tick_size(mark, sz),
                lot_size=lot,
                min_qty=lot,
                # Hyperliquid rejects orders worth less than 10 USDC.
                min_notional=decimal.Decimal(10),
                contract_multiplier=decimals.ONE,
                linear=True,
                status=(
                    instruments.InstrumentStatus.DELISTED
                    if asset.get("isDelisted")
                    else instruments.InstrumentStatus.TRADING
                ),
                asset_class=instruments.AssetClass.CRYPTO,
                source="hyperliquid.rest",
                as_of_ms=now_ms,
                funding_interval_hours=1.0,
                max_leverage=base.opt_dec(asset.get("maxLeverage")),
            )
        )
        meta_by_coin[coin] = {"szDecimals": sz}
    return rows, meta_by_coin


def parse_candle(
    row: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    now_ms: int,
    source: str,
) -> market.Candle:
    """Converts a Hyperliquid candle object.

    Args:
        row: ``{t, T, o, c, h, l, v, n, ...}`` (``t`` is the bar start in
            epoch milliseconds).
        instrument_id: Instrument.
        timeframe: Resolution.
        now_ms: Receipt time, epoch milliseconds.
        source: Provenance label; one ending in ``.ws`` marks a WebSocket
            row.

    Returns:
        Candle (final once its end has passed for REST rows; never final for
        WebSocket rows, which are confirmed over REST).
    """
    return base.make_candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=int(row["t"]),
        open_=base.dec(row["o"]),
        high=base.dec(row["h"]),
        low=base.dec(row["l"]),
        close=base.dec(row["c"]),
        base_volume=base.dec(row["v"]),
        trade_count=int(row.get("n", 0)),
        now_ms=now_ms,
        source=source,
        is_final=False if source.endswith(".ws") else None,
    )


def _frame_object(raw: str | bytes) -> Mapping[str, Any]:
    """Decodes a WebSocket frame, which is always a JSON object.

    Args:
        raw: Frame payload.

    Returns:
        The decoded object.

    Raises:
        ValueError: If the frame is not JSON.
        TypeError: If the frame decodes to anything but an object.
    """
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TypeError(f"frame is a JSON {type(message).__name__}")
    return message


def parse_candle_frame(
    raw: str | bytes,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    now_ms: int,
) -> list[market.Candle]:
    """Parses a ``candle`` channel frame.

    Args:
        raw: Frame payload.
        instrument_id: Instrument (coin).
        timeframe: Subscribed interval.
        now_ms: Receipt time, epoch milliseconds.

    Returns:
        Never-final candles of this coin and interval (empty for acks,
        pongs and other subscriptions).

    Raises:
        ValueError: If the frame is not JSON.
        TypeError: If the frame is not a JSON object.
        KeyError: If a candle row lacks a field.
    """
    message = _frame_object(raw)
    if message.get("channel") != "candle":
        return []
    data = message.get("data")
    rows = data if isinstance(data, list) else [data]
    return [
        parse_candle(r, instrument_id, timeframe, now_ms, "hyperliquid.ws")
        for r in rows
        if isinstance(r, dict)
        and r.get("s") == instrument_id.venue_market_id
        and r.get("i") == timeframe.value
    ]


def parse_trades_frame(
    raw: str | bytes, instrument_id: instruments.InstrumentId, now_ms: int
) -> list[market.Trade]:
    """Parses a ``trades`` channel frame.

    Args:
        raw: Frame payload.
        instrument_id: Instrument (coin).
        now_ms: Receipt time, epoch milliseconds.

    Returns:
        Trades ascending by time (empty for acks, pongs and other coins);
        ids are ``{time}-{tid}`` because ``tid`` is a hash.

    Raises:
        ValueError: If the frame is not JSON.
        TypeError: If the frame is not a JSON object or its ``data`` is not
            a list of objects.
        KeyError: If a trade row lacks a field.
    """
    message = _frame_object(raw)
    if message.get("channel") != "trades":
        return []
    rows = message["data"]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise TypeError("trades data is not a list of objects")
    coin = instrument_id.venue_market_id
    trades = [
        market.Trade(
            instrument_id=instrument_id,
            trade_id=f"{int(row['time'])}-{row['tid']}",
            venue_event_ts_ms=int(row["time"]),
            received_at_ms=now_ms,
            price=base.dec(row["px"]),
            base_quantity=base.dec(row["sz"]),
            aggressor_side=_SIDES.get(
                str(row["side"]), market.AggressorSide.UNKNOWN
            ),
            is_aggregated=False,
        )
        for row in rows
        if row.get("coin") == coin
    ]
    return sorted(trades, key=lambda t: t.venue_event_ts_ms)


class HyperliquidAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for Hyperliquid perpetuals.

    Attributes:
        VENUE: ``hyperliquid``.
    """

    VENUE = instruments.Venue.HYPERLIQUID

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
        self._ctx_cache: tuple[int, Any] | None = None

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="Hyperliquid",
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
                "Main perp dex only; HIP-3 builder dexes are not listed.",
                "No last-trade price in asset contexts; `last` is null.",
                "The venue's weekly candles start Thursday and it has no "
                "6h, so neither is used natively.",
                "The venue's 1M interval is 30-day epoch buckets, not "
                "calendar months, so it is never relabelled as 1M.",
                "Tick size is derived from the 5-significant-figure rule; "
                "integer prices are always valid, so it is at most 1.",
                "Seconds bars are built from the live trade stream only (no "
                "public trade history; the S3 archive is requester-pays and "
                "needs AWS credentials); outages are recorded as gaps unless "
                "the 30-trade replay on resubscribe overlaps.",
                "Klines publish no taker buy volume (taker_volume is false); "
                "seconds bars built from trades do carry it.",
            ),
            docs_url=(
                "https://hyperliquid.gitbook.io/hyperliquid-docs/"
                "for-developers/api"
            ),
            verified_at_ms=self._verified_at_ms,
        )

    async def _contexts(self) -> Any:
        """Fetches ``metaAndAssetCtxs``, reused for ``_CTX_TTL_MS``.

        Returns:
            ``[meta, ctxs]`` payload.
        """
        now = self._clock.now_ms()
        if self._ctx_cache and now - self._ctx_cache[0] < _CTX_TTL_MS:
            return self._ctx_cache[1]
        payload = await self._http.post("/info", {"type": "metaAndAssetCtxs"})
        self._ctx_cache = (now, payload)
        return payload

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Loads the universe.

        Returns:
            Instruments and their ``szDecimals`` keyed by coin.
        """
        meta, ctxs = await self._contexts()
        return parse_universe(meta, ctxs, self._clock.now_ms())

    async def _ctx_for(self, coin: str) -> Mapping[str, Any]:
        """Returns the asset context for a coin.

        Args:
            coin: Coin name.

        Returns:
            Asset context.

        Raises:
            errors.NotFoundError: If the coin is not in the universe.
        """
        meta, ctxs = await self._contexts()
        for asset, ctx in zip(meta["universe"], ctxs, strict=False):
            if asset["name"] == coin:
                found: Mapping[str, Any] = ctx
                return found
        raise errors.NotFoundError(f"unknown hyperliquid coin {coin}")

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches candles in one request (Hyperliquid caps it at 5000 bars).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles.

        Raises:
            errors.UnsupportedError: For a resolution Hyperliquid does not
                serve natively, and for seconds bars (built from the live
                stream only).
        """
        self._require_timeframe(timeframe)
        if timeframe.is_seconds:
            return await self._fetch_trade_candles(
                instrument, timeframe, start_ms, end_ms, limit
            )
        await self._require(instrument)
        start = timeframe.floor(start_ms)
        if limit is not None:
            start = base.newest_window_start(timeframe, start_ms, end_ms, limit)
        payload = await self._http.post(
            "/info",
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": instrument.venue_market_id,
                    "interval": timeframe.value,
                    "startTime": start,
                    # ``endTime`` is inclusive.
                    "endTime": end_ms - 1,
                },
            },
        )
        now = self._clock.now_ms()
        rows = [
            parse_candle(r, instrument, timeframe, now, "hyperliquid.rest")
            for r in payload
        ]
        return base.clip_range(rows, start_ms, end_ms, limit)

    def _snapshot(
        self,
        instrument: instruments.InstrumentId,
        ctx: Mapping[str, Any],
        book: market.OrderBook | None,
        now_ms: int,
    ) -> market.MarketSnapshot:
        """Assembles a snapshot from an asset context.

        Args:
            instrument: Instrument.
            ctx: Asset context.
            book: Optional book for best bid/ask.
            now_ms: Receipt time, epoch milliseconds.

        Returns:
            Snapshot: no last price, change against ``prevDayPx``, and the
            predicted hourly funding due at the next full hour.
        """
        mark = base.dec(ctx["markPx"])
        prev = base.opt_dec(ctx.get("prevDayPx"))
        change = (
            (mark - prev) / prev * decimals.HUNDRED
            if prev is not None and prev > decimals.ZERO
            else None
        )
        oi_base = base.dec(ctx["openInterest"])
        next_hour = (now_ms // _HOUR_MS + 1) * _HOUR_MS
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source="hyperliquid.rest",
            as_of_ms=now_ms,
            mark=mark,
            index=base.opt_dec(ctx.get("oraclePx")),
            best_bid=book.best_bid if book else None,
            best_ask=book.best_ask if book else None,
            turnover_24h_quote=base.opt_dec(ctx.get("dayNtlVlm")),
            volume_24h_base=base.opt_dec(ctx.get("dayBaseVlm")),
            change_24h_pct=change,
            funding=market.Funding(
                instrument_id=instrument,
                rate=base.dec(ctx["funding"]),
                interval_hours=1.0,
                kind=market.FundingKind.PREDICTED,
                effective_time_ms=next_hour,
                next_funding_ms=next_hour,
                source="hyperliquid.rest",
                as_of_ms=now_ms,
            ),
            open_interest=market.OpenInterest(
                instrument_id=instrument,
                base_quantity=oi_base,
                notional_usd=oi_base * mark,
                mark_price=mark,
                source="hyperliquid.rest",
                as_of_ms=now_ms,
            ),
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches context plus the top of book.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.

        Raises:
            errors.NotFoundError: If the coin is not in the universe.
        """
        await self._require(instrument)
        ctx, book = await asyncio.gather(
            self._ctx_for(instrument.venue_market_id),
            self.fetch_order_book(instrument, 1),
        )
        return self._snapshot(instrument, ctx, book, self._clock.now_ms())

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Builds snapshots for the whole universe from one request.

        Returns:
            Snapshots without best bid/ask, skipping delisted coins and
            coins without a mark price.
        """
        await self.list_instruments()
        meta, ctxs = await self._contexts()
        now = self._clock.now_ms()
        rows = []
        for asset, ctx in zip(meta["universe"], ctxs, strict=False):
            if asset.get("isDelisted") or not ctx.get("markPx"):
                continue
            rows.append(
                self._snapshot(
                    self.instrument_id(str(asset["name"])), ctx, None, now
                )
            )
        return rows

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches the L2 book (Hyperliquid returns up to 20 levels).

        Args:
            instrument: Instrument.
            depth: Levels per side (at most 20 are available).

        Returns:
            Order book.
        """
        await self._require(instrument)
        payload = await self._http.post(
            "/info", {"type": "l2Book", "coin": instrument.venue_market_id}
        )
        now = self._clock.now_ms()
        bids, asks = payload["levels"]
        return books.snapshot_book(
            instrument,
            [
                (base.dec(lvl["px"]), base.dec(lvl["sz"]))
                for lvl in bids[:depth]
            ],
            [
                (base.dec(lvl["px"]), base.dec(lvl["sz"]))
                for lvl in asks[:depth]
            ],
            ts_ms=int(payload.get("time", now)),
            received_ms=now,
            source="hyperliquid.rest",
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Returns open interest from the asset context.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.

        Raises:
            errors.NotFoundError: If the coin is not in the universe.
            errors.MissingDataError: If the snapshot carries no open
                interest (a guard: ``_snapshot`` always sets it).
        """
        await self._require(instrument)
        snapshot = self._snapshot(
            instrument,
            await self._ctx_for(instrument.venue_market_id),
            None,
            self._clock.now_ms(),
        )
        if snapshot.open_interest is None:  # pragma: no cover
            raise errors.MissingDataError("open interest unavailable")
        return snapshot.open_interest

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Returns the predicted hourly funding.

        Args:
            instrument: Instrument.

        Returns:
            Funding.

        Raises:
            errors.NotFoundError: If the coin is not in the universe.
            errors.MissingDataError: If the snapshot carries no funding (a
                guard: ``_snapshot`` always sets it).
        """
        await self._require(instrument)
        snapshot = self._snapshot(
            instrument,
            await self._ctx_for(instrument.venue_market_id),
            None,
            self._clock.now_ms(),
        )
        if snapshot.funding is None:  # pragma: no cover
            raise errors.MissingDataError("funding unavailable")
        return snapshot.funding

    def _trade_spec(
        self, instrument: instruments.InstrumentId
    ) -> ws.WsTradeSpec:
        """Describes the ``trades`` subscription.

        Args:
            instrument: Instrument.

        Returns:
            Trade stream spec.
        """

        def parse(raw: str | bytes, now_ms: int) -> list[market.Trade]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time, epoch milliseconds.

            Returns:
                Trades.
            """
            return parse_trades_frame(raw, instrument, now_ms)

        return ws.WsTradeSpec(
            url=WS_URL,
            subscribe=(
                json.dumps(
                    {
                        "method": "subscribe",
                        "subscription": {
                            "type": "trades",
                            "coin": instrument.venue_market_id,
                        },
                    }
                ),
            ),
            parse=parse,
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=5.0,
            idle_timeout_s=30.0,
            replays_recent=True,
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
        if timeframe.is_seconds:
            bars = self._stream_trade_candles(instrument, timeframe, tracker)
            async with contextlib.aclosing(bars):
                async for bar in bars:
                    yield bar
            return
        await self._require(instrument)
        coin = instrument.venue_market_id

        def parse(raw: str | bytes, now_ms: int) -> list[market.Candle]:
            """Parses one frame.

            Args:
                raw: Frame payload.
                now_ms: Receipt time, epoch milliseconds.

            Returns:
                Candles of this coin and interval (none for other frames);
                a malformed frame raises, and the stream records and skips
                it.
            """
            return parse_candle_frame(raw, instrument, timeframe, now_ms)

        async def backfill(start: int, end: int) -> list[market.Candle]:
            """Fetches missed or closing bars over REST.

            Args:
                start: Inclusive start, epoch milliseconds.
                end: Exclusive end, epoch milliseconds.

            Returns:
                Candles.
            """
            return await self.fetch_candles(instrument, timeframe, start, end)

        spec = ws.WsCandleSpec(
            url=WS_URL,
            subscribe=(
                json.dumps(
                    {
                        "method": "subscribe",
                        "subscription": {
                            "type": "candle",
                            "coin": coin,
                            "interval": timeframe.value,
                        },
                    }
                ),
            ),
            parse=parse,
            timeframe=timeframe,
            venue_marks_final=False,
            # Hyperliquid closes a connection that sent nothing for 60 s.
            ping_message=json.dumps({"method": "ping"}),
            ping_interval_s=30.0,
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
