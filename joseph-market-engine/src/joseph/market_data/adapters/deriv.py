# Copyright 2026 apoman123
# SPDX-License-Identifier: AGPL-3.0-only

"""Deriv public reference quotes with reusable bounded WebSocket requests.

Modified from TradeX by Saifullah Shafin: reuse market-data connections,
page candle history, and subscribe directly to the public source. Quotes
are source-specific reference prices, not exchange-traded stock prices.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncGenerator
from typing import Any
from typing import cast

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.adapters import ws
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
"""Official public market-data WebSocket; no login or trading permissions."""
_FRAMES = tuple(
    timeframes.parse(tf)
    for tf in (
        "1m",
        "3m",
        "5m",
        "10m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "8h",
        "1d",
    )
)


class DerivAdapter(base.BaseVenueAdapter):  # noqa: DOC302 - Document constructor mutation.
    """Serves public reference instruments without an account or API key.

    Attributes:
        VENUE: Deriv reference-feed venue; no execution adapter is installed.
        SPARSE_BARS: True; quiet or closed source intervals have no candle.
    """

    VENUE = instruments.Venue.DERIV
    SPARSE_BARS = True

    def __init__(  # noqa: DOC303 - Document None after mutation.
        self,
        clock: clock_lib.Clock,
        connect: ws.ConnectFn = ws.default_connect,
    ) -> None:
        """Initializes three reusable request connections and stream injection.

        Args:
            clock: UTC receipt clock.
            connect: Optional injectable public WebSocket connector.

        Returns:
            None; connections open lazily and are closed by close().
        """
        super().__init__(
            http.VenueHttp(self.VENUE, "https://api.derivws.com"), clock
        )
        self._connect = connect
        self._slots: asyncio.Queue[int] = asyncio.Queue()
        for slot in range(3):
            self._slots.put_nowait(slot)
        self._connections: dict[
            int,
            tuple[
                contextlib.AbstractAsyncContextManager[ws.WsConnection],
                ws.WsConnection,
            ],
        ] = {}
        self._request_id = 0

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Declares candle subscriptions and the absence of volume/order books.

        Returns:
            Source capabilities; history transport is WS request/response.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="Deriv public reference feed",
            market_types=(instruments.MarketType.SPOT,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
            ),
            timeframes=_FRAMES,
            notes=(
                (
                    "Public source-specific FX, commodity, index and crypto "
                    "reference quotes."
                ),
                (
                    "History uses reusable WebSocket requests; no exchange "
                    "volume is supplied."
                ),
                (
                    "Reference quotes do not enable trading or replace "
                    "Alpaca equity prices."
                ),
            ),
            docs_url="https://developers.deriv.com/",
        )

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Makes one bounded request while exclusively leasing a pooled socket.

        Args:
            payload: Public market-data request without authentication fields.

        Returns:
            Matching JSON response, excluding unrelated heartbeat frames.

        Raises:
            errors.VenueUnavailableError: On timeout, disconnect or provider
                error.
        """
        slot = await self._slots.get()
        try:
            async with asyncio.timeout(12):
                if slot not in self._connections:
                    manager = self._connect(WS_URL)
                    connection = await manager.__aenter__()
                    self._connections[slot] = (manager, connection)
                connection = self._connections[slot][1]
                self._request_id += 1
                request_id = self._request_id
                await connection.send(
                    json.dumps({**payload, "req_id": request_id})
                )
                while True:
                    response = json.loads(await connection.recv())
                    if response.get("req_id") != request_id:
                        continue
                    if response.get("error"):
                        raise errors.VenueUnavailableError(
                            "Deriv rejected the public request"
                        )
                    return cast(dict[str, Any], response)
        except asyncio.CancelledError:
            await self._discard(slot)
            raise
        except Exception as exc:
            await self._discard(slot)
            raise errors.VenueUnavailableError(
                "Deriv public feed request failed"
            ) from exc
        finally:
            self._slots.put_nowait(slot)

    async def _discard(self, slot: int) -> None:
        """Removes and closes a failed or interrupted request connection.

        Args:
            slot: Leased connection index, between zero and two.

        Returns:
            None; the next request opens a fresh connection for this slot.
        """
        entry = self._connections.pop(slot, None)
        if entry:
            with contextlib.suppress(Exception):
                await entry[0].__aexit__(None, None, None)

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Any]]:
        """Loads source symbols with their exact reference identities.

        Returns:
            Supported instruments and their source metadata.

        Raises:
            errors.VenueUnavailableError: If the public symbol request fails.
        """
        response = await self._request({"active_symbols": "brief"})
        rows = []
        metadata = {}
        for item in response.get("active_symbols", []):
            if item.get("market") not in {
                "forex",
                "commodities",
                "cryptocurrency",
                "indices",
            }:
                continue
            symbol = item.get("underlying_symbol") or item.get("symbol")
            if not symbol:
                continue
            ident = self.instrument_id(symbol)
            name = (
                item.get("underlying_symbol_name")
                or item.get("display_name")
                or symbol
            )
            tick = base.dec(item.get("pip_size") or item.get("pip") or "0.01")
            pair = re.fullmatch(
                r"(?:frx|cry)(.+)(USD|EUR|JPY|GBP|AUD|CAD|CHF|NZD)", symbol
            )
            quote = (
                pair[2]
                if pair
                else str(item.get("quote_currency") or "REFERENCE")
            )
            rows.append(
                instruments.Instrument(
                    key=instruments.InstrumentKey(
                        ident, pair[1] if pair else symbol, quote, quote
                    ),
                    display_symbol=symbol,
                    name=str(name),
                    tick_size=tick,
                    lot_size=base.dec(1),
                    min_qty=base.dec(1),
                    contract_multiplier=base.dec(1),
                    linear=True,
                    status=instruments.InstrumentStatus.TRADING,
                    asset_class=(
                        instruments.AssetClass.CRYPTO
                        if item["market"] == "cryptocurrency"
                        else instruments.AssetClass.RWA
                    ),
                    source="deriv.ws",
                    as_of_ms=self._clock.now_ms(),
                )
            )
            metadata[symbol] = item
        return rows, metadata

    def instrument_id(self, venue_market_id: str) -> instruments.InstrumentId:
        """Builds a reference quote's canonical spot identity.

        Args:
            venue_market_id: Native public symbol, such as frxXAUUSD.

        Returns:
            Source-specific canonical spot instrument id.
        """
        return instruments.InstrumentId(
            self.VENUE, instruments.MarketType.SPOT, venue_market_id
        )

    def _bar(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        row: dict[str, Any],
        source: str,
    ) -> market.Candle:
        """Converts one source candle without fabricating traded volume.

        Args:
            instrument: Exact reference instrument.
            timeframe: Native candle interval.
            row: Native epoch/open_time and OHLC values.
            source: deriv.ws or deriv.history provenance.

        Returns:
            Canonical OHLC candle with zero unreported volume.
        """
        return base.make_candle(
            instrument_id=instrument,
            timeframe=timeframe,
            start_ms=int(row.get("open_time", row.get("epoch", 0))) * 1000,
            open_=base.dec(row["open"]),
            high=base.dec(row["high"]),
            low=base.dec(row["low"]),
            close=base.dec(row["close"]),
            base_volume=base.dec(0),
            now_ms=self._clock.now_ms(),
            source=source,
            feed_state=quality.FeedState.LIVE
            if source == "deriv.ws"
            else quality.FeedState.POLL,
            is_final=False if source == "deriv.ws" else None,
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Returns actual reference candles without synthesizing closed markets.

        Args:
            instrument: Listed reference instrument.
            timeframe: Supported native candle interval.
            start_ms: Inclusive UTC epoch milliseconds.
            end_ms: Exclusive UTC epoch milliseconds.
            limit: Optional newest count, capped at 5000.

        Returns:
            Sorted source candles, excluding quiet intervals with no quote.

        Raises:
            errors.JosephError: For invalid IDs or failed source requests.
        """
        result = await self.fetch_candle_coverage(
            instrument, timeframe, start_ms, end_ms, limit
        )
        return list(result.candles)

    async def fetch_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> candle_rules.CandleCoverage:
        """Pages newest-first history over pooled public connections.

        Args:
            instrument: Listed reference instrument.
            timeframe: Supported native candle interval.
            start_ms: Inclusive UTC milliseconds.
            end_ms: Exclusive UTC milliseconds.
            limit: Optional newest count, capped at 5000.

        Returns:
            Actual source candles and the request windows proven complete.

        Raises:
            errors.JosephError: For unsupported instruments or provider
                failures.
        """
        await self._require(instrument)
        self._require_timeframe(timeframe)
        cursor = end_ms
        result: dict[int, market.Candle] = {}
        covered: list[candle_rules.Span] = []
        complete = False
        want = min(limit or 5000, 5000)
        for _ in range(20):
            response = await self._request(
                {
                    "ticks_history": instrument.venue_market_id,
                    "style": "candles",
                    "granularity": timeframe.duration_ms // 1000,
                    "count": min(1000, want - len(result)),
                    "end": str((cursor - 1) // 1000),
                    "adjust_start_time": 1,
                }
            )
            rows = [
                self._bar(instrument, timeframe, row, "deriv.history")
                for row in response.get("candles", [])
            ]
            if not rows:
                covered.append((start_ms, cursor))
                complete = True
                break
            for bar in rows:
                if start_ms <= bar.start_ms < end_ms:
                    result[bar.start_ms] = bar
            oldest = min(bar.start_ms for bar in rows)
            if oldest >= cursor:
                break
            covered.append((max(start_ms, oldest), cursor))
            if oldest <= start_ms:
                complete = True
                break
            if len(result) >= want:
                break
            cursor = oldest
        return base.candle_coverage(
            list(result.values()),
            start_ms,
            end_ms,
            want,
            covered,
            complete=complete,
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Reads the latest reference tick without implying exchange liquidity.

        Args:
            instrument: Listed reference instrument.

        Returns:
            Polled source tick with its original event timestamp.

        Raises:
            errors.JosephError: If the instrument or public request fails.
        """
        await self._require(instrument)
        result = await self._request(
            {
                "ticks_history": instrument.venue_market_id,
                "style": "ticks",
                "count": 1,
                "end": "latest",
            }
        )
        ticks = result.get("history") or {}
        return market.MarketSnapshot(
            instrument_id=instrument,
            source="deriv.history",
            feed_state=quality.FeedState.POLL,
            last=base.opt_dec((ticks.get("prices") or [None])[-1]),
            as_of_ms=int((ticks.get("times") or [0])[-1]) * 1000,
        )

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncGenerator[market.Candle, None]:
        """Subscribes native OHLC with closed-bar recovery.

        Args:
            instrument: Listed reference instrument.
            timeframe: Supported candle interval.
            tracker: Optional shared feed-health tracker.

        Yields:
            Provisional live bars; finality is confirmed through history.

        Raises:
            errors.JosephError: For unsupported instruments or failed recovery.
        """
        await self._require(instrument)
        self._require_timeframe(timeframe)

        def parse(frame: str | bytes, received_ms: int) -> list[market.Candle]:
            """Parses native OHLC messages, ignoring initial candle snapshots.

            Args:
                frame: JSON WebSocket payload.
                received_ms: UTC receipt milliseconds, tracked by the stream.

            Returns:
                One provisional candle, or an empty heartbeat/snapshot result.

            Raises:
                errors.VenueUnavailableError: If the subscription is rejected.
            """
            del received_ms
            value = json.loads(frame)
            if value.get("error"):
                raise errors.VenueUnavailableError(
                    "Deriv rejected the public subscription"
                )
            row = value.get("ohlc")
            return (
                [self._bar(instrument, timeframe, row, "deriv.ws")]
                if row
                else []
            )

        async def backfill(start: int, end: int) -> list[market.Candle]:
            """Reads the exact missing stream range from pooled history.

            Args:
                start: Inclusive UTC epoch milliseconds.
                end: Exclusive UTC epoch milliseconds.

            Returns:
                Canonical source candles for finality and gap repair.
            """
            return await self.fetch_candles(instrument, timeframe, start, end)

        spec = ws.WsCandleSpec(
            url=WS_URL,
            timeframe=timeframe,
            venue_marks_final=False,
            parse=parse,
            subscribe=(
                json.dumps(
                    {
                        "ticks_history": instrument.venue_market_id,
                        "style": "candles",
                        "granularity": timeframe.duration_ms // 1000,
                        "count": 1,
                        "end": "latest",
                        "subscribe": 1,
                    }
                ),
            ),
            ping_message=json.dumps({"ping": 1}),
        )
        async with contextlib.aclosing(
            ws.stream_with_recovery(
                spec,
                backfill=backfill,
                clock=self._clock,
                connect=self._connect,
                tracker=tracker,
            )
        ) as stream:
            async for bar in stream:
                yield bar

    async def close(self) -> None:
        """Closes pooled source requests and owned HTTP resources.

        Returns:
            None; called after the router stops active streams and requests.
        """
        for slot in list(self._connections):
            await self._discard(slot)
        await super().close()
