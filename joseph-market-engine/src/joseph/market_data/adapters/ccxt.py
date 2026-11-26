# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""CCXT REST history and public WebSocket market data for five venues."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from typing import Any
from typing import cast

from ccxt import pro  # type: ignore[import-untyped]  # CCXT has no type marker.

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import mexc
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

EXCHANGES = {
    instruments.Venue.BINANCE: "binanceusdm",
    instruments.Venue.HYPERLIQUID: "hyperliquid",
    instruments.Venue.LIGHTER: "lighter",
    instruments.Venue.BITGET: "bitget",
    instruments.Venue.MEXC: "mexc",
}
_VALID_ID = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")
_PAGE_SIZE = 500
_MISALIGNED = {
    instruments.Venue.HYPERLIQUID: frozenset(
        {timeframes.Timeframe.W1, timeframes.Timeframe.MN1}
    ),
}
"""CCXT periods whose venue starts differ from Joseph's UTC calendar."""


def _canonical_market_id(venue: instruments.Venue, item: dict[str, Any]) -> str:
    """Preserve existing symbol IDs when CCXT uses numeric market IDs.

    Args:
        venue: Exchange venue.
        item: CCXT unified market metadata.

    Returns:
        Canonical market segment for an existing Joseph instrument ID.
    """
    if venue in (instruments.Venue.HYPERLIQUID, instruments.Venue.LIGHTER):
        return str(item.get("base") or item.get("id") or "")
    return str(item.get("id") or "")


_RWA_BASES = frozenset(
    {
        "XAU",
        "XAG",
        "GOLD",
        "SILVER",
        "XAUT",
        "PAXG",
        "WTI",
        "BRENT",
        "OIL",
        "USOIL",
        "UKOIL",
        "CL",
        "NG",
        "COPPER",
        "EUR",
        "GBP",
        "JPY",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "SPX",
        "SP500",
        "SPY",
        "QQQ",
        "NDX",
        "NASDAQ",
        "US500",
        "US100",
        "DXY",
    }
)
_EQUITY_BASES = frozenset(
    {
        "TSLA",
        "NVDA",
        "AAPL",
        "AMZN",
        "MSFT",
        "GOOG",
        "GOOGL",
        "META",
        "COIN",
        "HOOD",
        "MSTR",
        "PLTR",
        "AMD",
        "INTC",
        "NFLX",
        "BABA",
    }
)


def _asset_class(  # noqa: PLR0911 - exchange metadata uses explicit classifiers
    venue: instruments.Venue, item: dict[str, Any]
) -> instruments.AssetClass:
    """Classify exchange-labelled RWA contracts and known underlying assets.

    Args:
        venue: Crypto exchange venue.
        item: CCXT market including untouched exchange metadata in info.

    Returns:
        Equity, RWA or crypto class; unclassified HIP-3 assets stay unknown.
    """
    info = item.get("info") or {}
    if venue is instruments.Venue.BINANCE:
        return binance.asset_class(info)
    if venue is instruments.Venue.BITGET and info.get("isRwa") == "YES":
        return instruments.AssetClass.RWA
    if venue is instruments.Venue.MEXC:
        classified = mexc.asset_class(list(info.get("conceptPlate") or []))
        if classified is not instruments.AssetClass.CRYPTO:
            return classified
    base_name = (
        str(info.get("name") or item.get("base") or "").split(":")[-1].upper()
    )
    if base_name in _EQUITY_BASES:
        return instruments.AssetClass.EQUITY
    if base_name in _RWA_BASES:
        return instruments.AssetClass.RWA
    if info.get("hip3"):
        return instruments.AssetClass.UNKNOWN
    return instruments.AssetClass.CRYPTO


class CcxtAdapter:
    """Serve perpetual history and live candles through free CCXT Pro.

    Attributes:
        venue: The canonical exchange venue used by existing instrument IDs.
    """

    def __init__(
        self,
        venue: instruments.Venue,
        clock: clock_lib.Clock,
        exchange: Any | None = None,
    ) -> None:
        """Create a rate-limited CCXT exchange client.

        Args:
            venue: One of the five supported crypto venues.
            clock: UTC receipt clock.
            exchange: Optional injected CCXT-compatible client for tests.

        Raises:
            ValueError: If the venue has no CCXT mapping.
        """
        if venue not in EXCHANGES:
            raise ValueError(f"CCXT does not support {venue.value}")
        self.venue = venue
        self._clock = clock
        self._history_requests: dict[str, asyncio.Task[Any]] = {}
        self._exchange = exchange or getattr(pro, EXCHANGES[venue])(
            {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        )

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Describe REST history and public WebSocket candle capabilities.

        Returns:
            Venue capabilities, including trade-driven candles on Lighter.
        """
        supported = self._exchange.timeframes or {}
        frames = tuple(
            tf
            for tf in timeframes.MINUTE_AND_ABOVE
            if tf.value in supported
            and tf not in _MISALIGNED.get(self.venue, ())
        )
        has = self._exchange.has
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name=f"CCXT · {self.venue.value.title()}",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=bool(has.get("fetchOHLCV")),
                native_ws_candles=bool(
                    has.get("watchOHLCV") or has.get("watchTrades")
                ),
                book_snapshot=bool(has.get("fetchOrderBook")),
                open_interest=bool(has.get("fetchOpenInterest")),
                funding=bool(has.get("fetchFundingRate")),
            ),
            timeframes=frames,
            notes=(
                "CCXT Pro public WebSocket candles; REST history and "
                "visible polling fallback. Lighter initial volume is partial "
                "until REST reconciliation at bar close.",
            ),
            docs_url="https://docs.ccxt.com/",
        )

    async def _request(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call CCXT and share concurrent identical historical requests.

        Args:
            method: CCXT async method name.
            *args: Positional CCXT method arguments.
            **kwargs: Keyword CCXT method arguments.

        Returns:
            The unified CCXT method result.

        Raises:
            errors.RateLimitedError: If the exchange rejects the rate.
            errors.VenueUnavailableError: If the exchange request fails.
        """
        if method != "fetch_ohlcv":
            return await self._perform_request(method, *args, **kwargs)
        key = json.dumps([args, kwargs], sort_keys=True)
        task = self._history_requests.get(key)
        if task is None:
            task = asyncio.create_task(
                self._perform_request(method, *args, **kwargs)
            )
            self._history_requests[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._history_requests.get(key) is task:
                del self._history_requests[key]

    async def _perform_request(
        self, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Call one CCXT method and normalize provider failures.

        Args:
            method: Unified CCXT async method name.
            *args: Positional exchange arguments.
            **kwargs: Keyword exchange arguments.

        Returns:
            Unified exchange response.

        Raises:
            errors.RateLimitedError: If the provider rate limit is exceeded.
            errors.VenueUnavailableError: If the exchange request fails.
        """
        try:
            return await getattr(self._exchange, method)(*args, **kwargs)
        except pro.RateLimitExceeded as exc:
            raise errors.RateLimitedError(
                f"CCXT {self.venue.value} rate limit exceeded"
            ) from exc
        except pro.BaseError as exc:
            raise errors.VenueUnavailableError(
                f"CCXT {self.venue.value} {method} failed: {exc}"
            ) from exc

    async def _market(
        self, instrument: instruments.InstrumentId
    ) -> dict[str, Any]:
        """Resolve a canonical perpetual ID to a loaded CCXT market.

        Args:
            instrument: Existing venue-scoped perpetual identifier.

        Returns:
            CCXT market dictionary.

        Raises:
            errors.NotFoundError: If the ID is absent or belongs elsewhere.
        """
        if (
            instrument.venue is not self.venue
            or instrument.market_type is not instruments.MarketType.PERP
        ):
            raise errors.NotFoundError(f"unknown CCXT market {instrument}")
        await self._request("load_markets")
        found = next(
            (
                item
                for item in self._exchange.markets.values()
                if item.get("swap")
                and item.get("linear", True)
                and _canonical_market_id(self.venue, item)
                == instrument.venue_market_id
            ),
            None,
        )
        if found is None:
            raise errors.NotFoundError(f"unknown CCXT market {instrument}")
        return cast(dict[str, Any], found)

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Load linear perpetual contracts and their trading status from CCXT.

        Returns:
            Canonical instruments with exchange precision metadata.
        """
        await self._request("load_markets")
        now = self._clock.now_ms()
        rows = []
        seen: set[str] = set()
        for item in self._exchange.markets.values():
            native_id = _canonical_market_id(self.venue, item)
            if (
                not item.get("swap")
                or not item.get("linear", True)
                or not _VALID_ID.fullmatch(native_id)
                or native_id in seen
            ):
                continue
            seen.add(native_id)
            precision = item.get("precision") or {}
            limits = item.get("limits") or {}
            amount = limits.get("amount") or {}
            cost = limits.get("cost") or {}
            multiplier = base.dec(item.get("contractSize") or 1)
            tick = base.dec(precision.get("price") or "0.00000001")
            lot = base.dec(precision.get("amount") or 1)
            rows.append(
                instruments.Instrument(
                    key=instruments.InstrumentKey(
                        instruments.InstrumentId(
                            self.venue, instruments.MarketType.PERP, native_id
                        ),
                        str(item.get("base") or ""),
                        str(item.get("quote") or ""),
                        str(item.get("settle") or item.get("quote") or ""),
                    ),
                    display_symbol=str(item.get("symbol") or native_id),
                    tick_size=tick,
                    lot_size=lot,
                    min_qty=base.dec(amount.get("min") or lot),
                    contract_multiplier=multiplier,
                    linear=True,
                    status=(
                        instruments.InstrumentStatus.TRADING
                        if item.get("active", True)
                        else instruments.InstrumentStatus.HALTED
                    ),
                    asset_class=_asset_class(self.venue, item),
                    source=f"ccxt.{self._exchange.id}.rest",
                    as_of_ms=now,
                    min_notional=base.opt_dec(cost.get("min")),
                )
            )
        return rows

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Page CCXT OHLCV history over the requested half-open range.

        Args:
            instrument: Canonical perpetual ID.
            timeframe: Native CCXT OHLCV resolution.
            start_ms: Inclusive UTC start in milliseconds.
            end_ms: Exclusive UTC end in milliseconds.
            limit: Optional maximum count of newest bars.

        Returns:
            Ascending candles with visible REST polling provenance.

        Raises:
            errors.UnsupportedError: If the exchange lacks this resolution.
        """
        if not self.capabilities().supports_timeframe(timeframe):
            raise errors.UnsupportedError(
                f"CCXT {self.venue.value} does not support {timeframe.value}"
            )
        symbol = (await self._market(instrument))["symbol"]
        cursor = start_ms
        candles: dict[int, market.Candle] = {}
        while cursor < end_ms:
            page = await self._request(
                "fetch_ohlcv",
                symbol,
                timeframe.value,
                since=cursor,
                limit=_PAGE_SIZE,
            )
            if not page:
                break
            for ts, open_, high, low, close, volume in page:
                if start_ms <= ts < end_ms:
                    candles[ts] = base.make_candle(
                        instrument_id=instrument,
                        timeframe=timeframe,
                        start_ms=ts,
                        open_=base.dec(open_),
                        high=base.dec(high),
                        low=base.dec(low),
                        close=base.dec(close),
                        base_volume=base.dec(volume),
                        now_ms=self._clock.now_ms(),
                        source=f"ccxt.{self._exchange.id}.rest",
                        feed_state=quality.FeedState.POLL,
                    )
            next_cursor = int(page[-1][0]) + timeframe.duration_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        result = sorted(candles.values(), key=lambda candle: candle.start_ms)
        return result[-limit:] if limit is not None else result

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetch the latest CCXT ticker for a perpetual contract.

        Args:
            instrument: Canonical perpetual ID.

        Returns:
            Ticker snapshot marked as polled data.
        """
        symbol = (await self._market(instrument))["symbol"]
        ticker = await self._request("fetch_ticker", symbol)
        return self._snapshot(instrument, ticker)

    def _snapshot(
        self, instrument: instruments.InstrumentId, ticker: dict[str, Any]
    ) -> market.MarketSnapshot:
        """Normalize a CCXT ticker without inventing missing fields.

        Args:
            instrument: Canonical perpetual ID.
            ticker: CCXT unified ticker mapping.

        Returns:
            Polled market snapshot.
        """
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.POLL,
            source=f"ccxt.{self._exchange.id}.rest",
            as_of_ms=int(ticker.get("timestamp") or self._clock.now_ms()),
            last=base.opt_dec(ticker.get("last")),
            best_bid=base.opt_dec(ticker.get("bid")),
            best_ask=base.opt_dec(ticker.get("ask")),
            turnover_24h_quote=base.opt_dec(ticker.get("quoteVolume")),
            volume_24h_base=base.opt_dec(ticker.get("baseVolume")),
            change_24h_pct=base.opt_dec(ticker.get("percentage")),
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Fetch all perpetual tickers when the exchange supports bulk reads.

        Returns:
            Polled snapshots for ticker symbols present in the market list.

        Raises:
            errors.UnsupportedError: If bulk tickers are unavailable.
        """
        if not self._exchange.has.get("fetchTickers"):
            raise errors.UnsupportedError(
                f"CCXT {self.venue.value} has no bulk tickers"
            )
        markets = await self.list_instruments()
        by_symbol = {
            item.display_symbol: item.instrument_id for item in markets
        }
        tickers = await self._request("fetch_tickers", list(by_symbol))
        return [
            self._snapshot(by_symbol[symbol], ticker)
            for symbol, ticker in tickers.items()
            if symbol in by_symbol
        ]

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetch a bounded CCXT REST order-book snapshot.

        Additional venue metadata after each level's price and amount is
        ignored, including order counts supplied by some exchanges.

        Args:
            instrument: Canonical perpetual ID.
            depth: Maximum levels per side.

        Returns:
            Book with CCXT price and amount levels.
        """
        symbol = (await self._market(instrument))["symbol"]
        book = await self._request("fetch_order_book", symbol, limit=depth)
        return market.OrderBook(
            instrument_id=instrument,
            bids=tuple(
                market.BookLevel(base.dec(level[0]), base.dec(level[1]))
                for level in book["bids"][:depth]
            ),
            asks=tuple(
                market.BookLevel(base.dec(level[0]), base.dec(level[1]))
                for level in book["asks"][:depth]
            ),
            snapshot_ts_ms=int(book.get("timestamp") or self._clock.now_ms()),
            received_at_ms=self._clock.now_ms(),
            source=f"ccxt.{self._exchange.id}.rest",
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Fetch CCXT open interest when the exchange exposes it.

        Args:
            instrument: Canonical perpetual ID.

        Returns:
            Open interest with USD notional.

        Raises:
            errors.UnsupportedError: If the method or USD notional is absent.
        """
        if not self._exchange.has.get("fetchOpenInterest"):
            raise errors.UnsupportedError("CCXT open interest is unavailable")
        symbol = (await self._market(instrument))["symbol"]
        data = await self._request("fetch_open_interest", symbol)
        quantity = base.opt_dec(data.get("openInterestAmount"))
        notional = base.opt_dec(data.get("openInterestValue"))
        if quantity is None or notional is None:
            raise errors.UnsupportedError(
                "CCXT did not provide comparable open interest"
            )
        return market.OpenInterest(
            instrument,
            quantity,
            notional,
            f"ccxt.{self._exchange.id}.rest",
            int(data.get("timestamp") or self._clock.now_ms()),
        )

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Fetch the current unified CCXT funding rate if available.

        Args:
            instrument: Canonical perpetual ID.

        Returns:
            Predicted funding rate for the next settlement.

        Raises:
            errors.UnsupportedError: If the rate or interval is unavailable.
        """
        if not self._exchange.has.get("fetchFundingRate"):
            raise errors.UnsupportedError("CCXT funding rate is unavailable")
        symbol = (await self._market(instrument))["symbol"]
        data = await self._request("fetch_funding_rate", symbol)
        rate = base.opt_dec(data.get("fundingRate"))
        interval = data.get("interval")
        if (
            rate is None
            or not isinstance(interval, str)
            or not interval.endswith("h")
        ):
            raise errors.UnsupportedError(
                "CCXT did not provide funding rate and interval"
            )
        return market.Funding(
            instrument,
            rate,
            float(interval[:-1]),
            market.FundingKind.PREDICTED,
            int(
                data.get("nextFundingTimestamp")
                or data.get("timestamp")
                or self._clock.now_ms()
            ),
            f"ccxt.{self._exchange.id}.rest",
            self._clock.now_ms(),
            next_funding_ms=data.get("nextFundingTimestamp"),
        )

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Reject trade history because CCXT cannot prove full range coverage.

        Args:
            instrument: Canonical perpetual ID.
            start_ms: Inclusive range start in milliseconds.
            end_ms: Exclusive range end in milliseconds.
            limit: Optional maximum count.

        Returns:
            Never returns.

        Raises:
            errors.UnsupportedError: Always; range coverage is unverified.
        """
        del instrument, start_ms, end_ms, limit
        raise errors.UnsupportedError(
            "CCXT trade range coverage is unavailable"
        )

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncGenerator[market.Candle, None]:
        """Subscribe to public CCXT WebSocket candles or Lighter trades.

        Args:
            instrument: Canonical perpetual ID.
            timeframe: Native candle resolution.
            tracker: Optional tracker updated on received exchange messages.

        Yields:
            Live candles; completed bars retain exchange OHLCV values.

        Raises:
            errors.UnsupportedError: If neither public stream is supported.
            errors.VenueUnavailableError: If the exchange stream fails.
        """
        item = await self._market(instrument)
        # Each session owns its socket so releasing a chart closes subscriptions
        # without cancelling another chart's subscription to the same symbol.
        client = getattr(pro, EXCHANGES[self.venue])({"enableRateLimit": True})
        client.set_markets_from_exchange(self._exchange)
        try:
            if client.has.get("watchOHLCV"):
                previous: market.Candle | None = None
                while True:
                    rows = await client.watch_ohlcv(
                        item["symbol"], timeframe.value
                    )
                    for row in rows:
                        candle = self._live_candle(instrument, timeframe, row)
                        if (
                            previous is not None
                            and candle.start_ms > previous.start_ms
                        ):
                            yield dataclasses.replace(previous, is_final=True)
                        if (
                            previous is None
                            or candle.start_ms >= previous.start_ms
                        ):
                            previous = candle
                            if tracker is not None:
                                tracker.record_message(self._clock.now_ms())
                            yield candle
            elif client.has.get("watchTrades"):
                async for candle in self._trade_candles(
                    client, item, instrument, timeframe
                ):
                    if tracker is not None:
                        tracker.record_message(self._clock.now_ms())
                    yield candle
            else:
                raise errors.UnsupportedError(
                    "CCXT has no public candle or trade stream"
                )
        except pro.BaseError as exc:
            raise errors.VenueUnavailableError(
                f"CCXT {self.venue.value} stream failed: {exc}"
            ) from exc
        finally:
            await client.close()

    def _live_candle(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        row: list[Any],
    ) -> market.Candle:
        """Normalize one exchange WebSocket OHLCV row.

        Args:
            instrument: Canonical perpetual ID.
            timeframe: Native resolution.
            row: Timestamp, open, high, low, close and base volume.

        Returns:
            Candle with live WebSocket provenance and clock-based finality.
        """
        ts, open_, high, low, close, volume = row
        return base.make_candle(
            instrument_id=instrument,
            timeframe=timeframe,
            start_ms=int(ts),
            open_=base.dec(open_),
            high=base.dec(high),
            low=base.dec(low),
            close=base.dec(close),
            base_volume=base.dec(volume),
            now_ms=self._clock.now_ms(),
            source=f"ccxt.{self._exchange.id}.ws",
            feed_state=quality.FeedState.LIVE,
        )

    async def _trade_candles(
        self,
        client: Any,
        item: dict[str, Any],
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> AsyncGenerator[market.Candle, None]:
        """Update Lighter candles with trades and reconcile bars over REST.

        The first bucket can have incomplete volume until its REST repair.
        It is never finalized from a partial trade subscription.

        Args:
            client: Session-owned CCXT Pro client.
            item: Unified market metadata with symbol and contract size.
            instrument: Canonical perpetual ID.
            timeframe: Native OHLCV resolution.

        Yields:
            Live unfinished bars and authoritative REST bars after rollover.
        """
        current: market.Candle | None = None
        seen: set[str] = set()
        while True:
            trades = await client.watch_trades(item["symbol"])
            for trade in sorted(trades, key=lambda value: value["timestamp"]):
                key = str(
                    trade.get("id")
                    or (trade["timestamp"], trade["price"], trade["amount"])
                )
                if key in seen:
                    continue
                ts = int(trade["timestamp"])
                start = timeframe.floor(ts)
                if current is not None and start < current.start_ms:
                    continue
                if current is not None and start > current.start_ms:
                    repaired = await self.fetch_candles(
                        instrument, timeframe, current.start_ms, start
                    )
                    for bar in repaired:
                        yield dataclasses.replace(
                            bar,
                            feed_state=quality.FeedState.LIVE,
                            source=f"ccxt.{self._exchange.id}.ws-rest-repair",
                        )
                    current = None
                    seen.clear()
                seen.add(key)
                price = base.dec(trade["price"])
                quantity = base.dec(trade["amount"]) * base.dec(
                    item.get("contractSize") or 1
                )
                if current is None:
                    current = self._live_candle(
                        instrument,
                        timeframe,
                        [start, price, price, price, price, quantity],
                    )
                else:
                    current = dataclasses.replace(
                        current,
                        high=max(current.high, price),
                        low=min(current.low, price),
                        close=price,
                        base_volume=current.base_volume + quantity,
                        received_at_ms=self._clock.now_ms(),
                        is_final=False,
                    )
                yield dataclasses.replace(current, is_final=False)

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Reject standalone trades until range coverage is implemented.

        Args:
            instrument: Canonical perpetual ID.
            tracker: Optional feed tracker.

        Returns:
            Never returns.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument, tracker
        raise errors.UnsupportedError(
            "Standalone CCXT trade subscriptions are not exposed"
        )

    async def close(self) -> None:
        """Release the CCXT HTTP session.

        Returns:
            None; closes network resources.
        """
        tasks = list(self._history_requests.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._history_requests.clear()
        await self._exchange.close()
