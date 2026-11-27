# Copyright 2026 apoman123
# SPDX-License-Identifier: AGPL-3.0-only

"""Native public sources adapted from TradeX's feed plugin approach.

Modified from TradeX by Saifullah Shafin. The application keeps its existing
MarketDataPort, canonical identities, rate limiting, coverage and recovery.
Coinbase, Kraken and OKX intentionally advertise polling, as the upstream
plugins do. A successful HTTP response never claims WebSocket provenance.
"""

from __future__ import annotations

import contextlib
import datetime
import json
from collections.abc import AsyncGenerator
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.adapters import ws
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

URLS = {
    instruments.Venue.BYBIT: "https://api.bybit.com",
    instruments.Venue.OKX: "https://www.okx.com",
    instruments.Venue.COINBASE: "https://api.exchange.coinbase.com",
    instruments.Venue.KRAKEN: "https://api.kraken.com",
}
"""Fixed official API origins; user input cannot select a remote host."""

_FRAMES = (
    timeframes.Timeframe.M1,
    timeframes.Timeframe.M5,
    timeframes.Timeframe.M15,
    timeframes.Timeframe.M30,
    timeframes.Timeframe.H1,
    timeframes.Timeframe.H4,
    timeframes.Timeframe.D1,
)
_BYBIT_TF = dict(
    zip(_FRAMES, ("1", "5", "15", "30", "60", "240", "D"), strict=True)
)
_OKX_TF = dict(
    zip(_FRAMES, ("1m", "5m", "15m", "30m", "1H", "4H", "1Dutc"), strict=True)
)
_METALS = frozenset({"XAU", "XAG", "XPT", "XPD", "XAUT", "PAXG"})


def _iso(timestamp_ms: int) -> str:
    """Formats a Coinbase request boundary without local time conversions.

    Args:
        timestamp_ms: Non-negative UTC epoch milliseconds.

    Returns:
        UTC ISO 8601 string with a trailing Z.
    """
    return (
        datetime.datetime.fromtimestamp(timestamp_ms / 1000, datetime.UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


class PublicSourceAdapter(base.BaseVenueAdapter):  # noqa: DOC302 - Document constructor mutation.
    """Serves a TradeX-style public source inside the existing backend.

    Attributes:
        VENUE: Source venue, fixed at construction; never a trading gateway.
    """

    def __init__(  # noqa: DOC303 - Document None after mutation.
        self,
        venue: instruments.Venue,
        transport: http.VenueHttp,
        clock: clock_lib.Clock,
        connect: ws.ConnectFn = ws.default_connect,
    ) -> None:
        """Binds a public API to an injected, bounded HTTP/WS transport.

        Args:
            venue: Bybit, OKX, Coinbase or Kraken.
            transport: Rate-limited HTTP client owned by this adapter.
            clock: UTC receipt clock.
            connect: Optional injectable WebSocket connector.

        Returns:
            None; initializes metadata caching and transport ownership.

        Raises:
            ValueError: If the venue has no supported public source.
        """
        if venue not in URLS:
            raise ValueError(f"unsupported public source {venue}")
        self.VENUE = venue
        super().__init__(transport, clock)
        self._connect = connect

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Reports actual source transports and native timeframe limits.

        Returns:
            Public-source capabilities; Coinbase 30m/4h are derived upstream.
        """
        frames: tuple[timeframes.Timeframe, ...] = _FRAMES
        if self.venue is instruments.Venue.COINBASE:
            frames = tuple(
                tf
                for tf in _FRAMES
                if tf not in (timeframes.Timeframe.M30, timeframes.Timeframe.H4)
            )
        bybit = self.venue is instruments.Venue.BYBIT
        kinds: tuple[instruments.MarketType, ...] = (
            instruments.MarketType.SPOT,
        )
        transport_note = "Public exchange API; explicit REST polling, no key."
        history_note = (
            "History retains exact source, quote and market identity."
        )
        if bybit:
            kinds = (
                instruments.MarketType.SPOT,
                instruments.MarketType.PERP,
                instruments.MarketType.FUTURE,
            )
            transport_note = "Public Bybit candle WebSockets and REST; no key."
        if self.venue is instruments.Venue.KRAKEN:
            history_note = "Kraken serves only its newest 720 native candles."
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name=f"{self.venue.value.title()} public API",
            market_types=kinds,
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=bybit,
            ),
            timeframes=frames,
            notes=(transport_note, history_note),
            docs_url=URLS[self.venue],
        )

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        """Reads and validates an exchange's native success envelope.

        Args:
            path: Fixed endpoint path relative to the source origin.
            params: Endpoint-specific scalar query parameters.

        Returns:
            Unwrapped result, data or original Coinbase response.

        Raises:
            errors.VenueUnavailableError: For native API error envelopes.
            errors.JosephError: For HTTP, JSON or rate-limit failures.
        """
        result = await self._http.get(path, params=params)
        if self.venue is instruments.Venue.BYBIT:
            if result.get("retCode") != 0:
                raise errors.VenueUnavailableError(
                    "Bybit rejected the public request"
                )
            return result["result"]
        if self.venue is instruments.Venue.OKX:
            if result.get("code") != "0":
                raise errors.VenueUnavailableError(
                    "OKX rejected the public request"
                )
            return result["data"]
        if self.venue is instruments.Venue.KRAKEN:
            if result.get("error"):
                raise errors.VenueUnavailableError(
                    "Kraken rejected the public request"
                )
            return result["result"]
        return result

    async def _bybit_instrument_rows(self) -> list[dict[str, Any]]:
        """Pages each Bybit category with cursor-cycle and request budgets.

        Returns:
            Native spot, linear and inverse instrument metadata.

        Raises:
            errors.JosephError: If the source fails or rejects a request.
        """
        rows: list[dict[str, Any]] = []
        for category, prefix in (
            ("linear", ""),
            ("spot", "S_"),
            ("inverse", "I_"),
        ):
            cursor = ""
            seen: set[str] = set()
            for _ in range(20):
                result = await self._get(
                    "/v5/market/instruments-info",
                    {
                        "category": category,
                        "limit": 1000,
                        "cursor": cursor,
                    },
                )
                for item in result["list"]:
                    if item.get("status") != "Trading":
                        continue
                    contract = str(item.get("contractType", ""))
                    kind = instruments.MarketType.PERP
                    if category == "spot":
                        kind = instruments.MarketType.SPOT
                    elif "Futures" in contract:
                        kind = instruments.MarketType.FUTURE
                    rows.append(
                        {
                            "id": prefix + item["symbol"],
                            "native": item["symbol"],
                            "base": item["baseCoin"],
                            "quote": item["quoteCoin"],
                            "category": category,
                            "kind": kind,
                            "tick": (item.get("priceFilter") or {}).get(
                                "tickSize", "0.00000001"
                            ),
                            "lot": (item.get("lotSizeFilter") or {}).get(
                                "qtyStep", "0.00000001"
                            ),
                            "settle": item.get("settleCoin")
                            or item["quoteCoin"],
                        }
                    )
                cursor = result.get("nextPageCursor") or ""
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
        return rows

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Any]]:
        """Validates provider metadata failures at the adapter trust boundary.

        Returns:
            Listed canonical instruments and native metadata.

        Raises:
            errors.VenueUnavailableError: For malformed source metadata.
            errors.JosephError: For transport or provider failures.
        """
        try:
            return await self._load_native_instruments()
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise errors.VenueUnavailableError(
                f"{self.venue.value} returned invalid instrument metadata"
            ) from exc

    async def _load_native_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Any]]:
        """Discovers real listed instruments rather than assuming coin lists.

        Returns:
            Instruments and native metadata indexed by unique source id.

        Raises:
            errors.VenueUnavailableError: For invalid provider responses.
        """
        rows: list[dict[str, Any]] = []
        if self.venue is instruments.Venue.BYBIT:
            rows = await self._bybit_instrument_rows()
        elif self.venue is instruments.Venue.OKX:
            for item in await self._get(
                "/api/v5/public/instruments", {"instType": "SPOT"}
            ):
                if item.get("state") == "live":
                    rows.append(
                        {
                            "id": item["instId"],
                            "base": item["baseCcy"],
                            "quote": item["quoteCcy"],
                            "tick": item["tickSz"],
                            "lot": item["lotSz"],
                        }
                    )
        elif self.venue is instruments.Venue.COINBASE:
            for item in await self._get("/products", {}):
                if item.get("status") == "online" and not item.get(
                    "trading_disabled"
                ):
                    rows.append(
                        {
                            "id": item["id"],
                            "base": item["base_currency"],
                            "quote": item["quote_currency"],
                            "tick": item["quote_increment"],
                            "lot": item["base_increment"],
                        }
                    )
        else:
            for native, item in (
                await self._get("/0/public/AssetPairs", {})
            ).items():
                if item.get(
                    "status", "online"
                ) != "online" or "/" not in item.get("wsname", ""):
                    continue
                base_name, quote = item["wsname"].split("/", 1)
                rows.append(
                    {
                        "id": item.get("altname", native),
                        "native": native,
                        "base": "BTC" if base_name == "XBT" else base_name,
                        "quote": quote,
                        "tick": str(base.pow10(-int(item["pair_decimals"]))),
                        "lot": str(base.pow10(-int(item["lot_decimals"]))),
                    }
                )
        result_rows = []
        metadata = {}
        for item in rows:
            kind = item.get("kind", instruments.MarketType.SPOT)
            ident = instruments.InstrumentId(self.venue, kind, item["id"])
            tick, lot = base.dec(item["tick"]), base.dec(item["lot"])
            result_rows.append(
                instruments.Instrument(
                    key=instruments.InstrumentKey(
                        ident,
                        item["base"],
                        item["quote"],
                        item.get("settle", item["quote"]),
                    ),
                    display_symbol=f"{item['base']}/{item['quote']}",
                    tick_size=tick,
                    lot_size=lot,
                    min_qty=lot,
                    contract_multiplier=base.dec(1),
                    linear=item.get("category") != "inverse",
                    status=instruments.InstrumentStatus.TRADING,
                    asset_class=(
                        instruments.AssetClass.RWA
                        if item["base"] in _METALS
                        else instruments.AssetClass.CRYPTO
                    ),
                    source=f"{self.venue.value}.rest",
                    as_of_ms=self._clock.now_ms(),
                )
            )
            metadata[item["id"]] = item
        return result_rows, metadata

    async def _history_page(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        cursor: int,
        remaining: int,
        *,
        meta: dict[str, Any],
    ) -> list[list[Any]]:
        """Reads one bounded native history page and checks its row shape.

        Args:
            instrument: Exact source instrument.
            timeframe: Native candle interval.
            start_ms: Inclusive UTC millisecond range start.
            cursor: Exclusive UTC millisecond end of this page.
            remaining: Positive number of newest candles still wanted.
            meta: Native instrument metadata resolved from the registry.

        Returns:
            Native OHLCV rows, at most one thousand, or an empty history page.

        Raises:
            errors.VenueUnavailableError: For invalid native row containers.
            errors.JosephError: If the provider request fails.
        """
        native = meta.get("native", instrument.venue_market_id)
        if self.venue is instruments.Venue.BYBIT:
            result = await self._get(
                "/v5/market/kline",
                {
                    "category": meta["category"],
                    "symbol": native,
                    "interval": _BYBIT_TF[timeframe],
                    "end": cursor - 1,
                    "limit": min(remaining, 1000),
                },
            )
            rows = result["list"]
        elif self.venue is instruments.Venue.OKX:
            rows = await self._get(
                "/api/v5/market/history-candles",
                {
                    "instId": native,
                    "bar": _OKX_TF[timeframe],
                    "after": cursor,
                    "limit": min(remaining, 100),
                },
            )
        elif self.venue is instruments.Venue.COINBASE:
            rows = await self._get(
                f"/products/{native}/candles",
                {
                    "granularity": timeframe.duration_ms // 1000,
                    "start": _iso(
                        max(start_ms, cursor - 299 * timeframe.duration_ms)
                    ),
                    "end": _iso(cursor - 1),
                },
            )
        else:
            result = await self._get(
                "/0/public/OHLC",
                {
                    "pair": native,
                    "interval": timeframe.duration_ms // 60000,
                    "since": start_ms // 1000,
                },
            )
            rows = next(value for key, value in result.items() if key != "last")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise errors.VenueUnavailableError("invalid native history page")
        if any(not isinstance(row, list) or len(row) < 6 for row in rows):
            raise errors.VenueUnavailableError("invalid native OHLCV row")
        return rows

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Pages history backward with hard budgets and non-progress guards.

        Args:
            instrument: Exact listed instrument, including spot/perp identity.
            timeframe: A native timeframe advertised by this source.
            start_ms: Inclusive UTC epoch milliseconds.
            end_ms: Exclusive UTC epoch milliseconds.
            limit: Optional newest bar count, bounded by the service to 5000.

        Returns:
            Sorted, deduplicated native candles with truthful POLL provenance.

        Raises:
            errors.NotFoundError: For an unknown or mismatched instrument.
            errors.UnsupportedError: For a non-native timeframe.
            errors.JosephError: For provider or transport failures.
        """
        await self._require(instrument)
        self._require_timeframe(timeframe)
        meta = self._meta_for(instrument)
        cursor = end_ms
        want = min(limit or 5000, 5000)
        found: dict[int, market.Candle] = {}
        for _ in range(50):
            rows = await self._history_page(
                instrument,
                timeframe,
                start_ms,
                cursor,
                want - len(found),
                meta=dict(meta),
            )
            if not rows:
                break
            oldest = cursor
            for row in rows:
                multiplier = 1
                if self.venue in (
                    instruments.Venue.KRAKEN,
                    instruments.Venue.COINBASE,
                ):
                    multiplier = 1000
                ts = int(row[0]) * multiplier
                oldest = min(oldest, ts)
                if not start_ms <= ts < end_ms:
                    continue
                if self.venue is instruments.Venue.COINBASE:
                    values = (row[3], row[2], row[1], row[4], row[5])
                else:
                    values = (
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[6]
                        if self.venue is instruments.Venue.KRAKEN
                        else row[5],
                    )
                if (
                    self.venue is instruments.Venue.BYBIT
                    and meta["category"] == "inverse"
                ):
                    values = (*values[:4], row[6])
                found[ts] = base.make_candle(
                    instrument_id=instrument,
                    timeframe=timeframe,
                    start_ms=ts,
                    open_=base.dec(values[0]),
                    high=base.dec(values[1]),
                    low=base.dec(values[2]),
                    close=base.dec(values[3]),
                    base_volume=base.dec(values[4]),
                    now_ms=self._clock.now_ms(),
                    source=f"{self.venue.value}.rest",
                    feed_state=quality.FeedState.POLL,
                )
            if (
                oldest >= cursor
                or oldest <= start_ms
                or len(found) >= want
                or self.venue is instruments.Venue.KRAKEN
            ):
                break
            cursor = oldest
        return sorted(found.values(), key=lambda bar: bar.start_ms)[-want:]

    async def fetch_snapshot(
        self,
        instrument: instruments.InstrumentId,
    ) -> market.MarketSnapshot:
        """Reads the native ticker directly without downloading candle history.

        Args:
            instrument: Exact listed source instrument.

        Returns:
            Polled quote with native fields and a provider time where available.
            Unpublished fields remain absent; Kraken day-open is not 24h change.

        Raises:
            errors.NotFoundError: If the source does not list the instrument.
            errors.JosephError: If the source request fails.
        """
        await self._require(instrument)
        meta = self._meta_for(instrument)
        native = meta.get("native", instrument.venue_market_id)
        now = self._clock.now_ms()
        change = None
        turnover = None
        if self.venue is instruments.Venue.BYBIT:
            result = await self._get(
                "/v5/market/tickers",
                {
                    "category": meta["category"],
                    "symbol": native,
                },
            )
            row = result["list"][0]
            last, bid, ask = (
                row["lastPrice"],
                row.get("bid1Price"),
                row.get("ask1Price"),
            )
            volume = row.get("volume24h")
            turnover = row.get("turnover24h")
            if meta["category"] == "inverse":
                volume, turnover = turnover, volume
            percentage = base.opt_dec(row.get("price24hPcnt"))
            if percentage is not None:
                change = percentage * 100
        elif self.venue is instruments.Venue.OKX:
            row = (
                await self._get("/api/v5/market/ticker", {"instId": native})
            )[0]
            last, bid, ask = row["last"], row.get("bidPx"), row.get("askPx")
            volume, turnover = row.get("vol24h"), row.get("volCcy24h")
            now = int(row["ts"])
            opening = base.opt_dec(row.get("open24h"))
            if opening:
                change = (base.dec(last) / opening - 1) * 100
        elif self.venue is instruments.Venue.COINBASE:
            row = await self._get(f"/products/{native}/ticker", {})
            last, bid, ask = row["price"], row.get("bid"), row.get("ask")
            volume = row.get("volume")
            now = int(
                datetime.datetime.fromisoformat(
                    row["time"].replace("Z", "+00:00")
                ).timestamp()
                * 1000
            )
        else:
            result = await self._get("/0/public/Ticker", {"pair": native})
            row = next(iter(result.values()))
            last, bid, ask = row["c"][0], row["b"][0], row["a"][0]
            volume = row["v"][1]
        return market.MarketSnapshot(
            instrument_id=instrument,
            source=f"{self.venue.value}.rest",
            feed_state=quality.FeedState.POLL,
            as_of_ms=now,
            last=base.opt_dec(last),
            best_bid=base.opt_dec(bid),
            best_ask=base.opt_dec(ask),
            volume_24h_base=base.opt_dec(volume),
            turnover_24h_quote=base.opt_dec(turnover),
            change_24h_pct=change,
        )

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncGenerator[market.Candle, None]:
        """Subscribes Bybit candles with existing recovery and finality checks.

        Args:
            instrument: Listed Bybit instrument; no automatic market
                substitution.
            timeframe: Native Bybit candle interval.
            tracker: Optional shared health tracker.

        Yields:
            Exchange candle updates; reconnect gaps are repaired over REST.

        Raises:
            errors.UnsupportedError: For REST-only sources or unsupported
                frames.
        """
        if self.venue is not instruments.Venue.BYBIT:
            raise errors.UnsupportedError(f"{self.venue} uses REST polling")
        await self._require(instrument)
        self._require_timeframe(timeframe)
        meta = self._meta_for(instrument)

        def parse(frame: str | bytes, received_ms: int) -> list[market.Candle]:
            """Normalizes Bybit data frames and rejects subscription errors.

            Args:
                frame: JSON WebSocket frame.
                received_ms: Receipt time in UTC milliseconds.

            Returns:
                Canonical candles, or no rows for heartbeat acknowledgments.

            Raises:
                errors.VenueUnavailableError: If the subscription is rejected.
                ValueError: If a data frame is malformed.
            """
            message = json.loads(frame)
            if message.get("success") is False:
                raise errors.VenueUnavailableError(
                    "Bybit rejected the candle subscription"
                )
            return [
                base.make_candle(
                    instrument_id=instrument,
                    timeframe=timeframe,
                    start_ms=int(row["start"]),
                    open_=base.dec(row["open"]),
                    high=base.dec(row["high"]),
                    low=base.dec(row["low"]),
                    close=base.dec(row["close"]),
                    base_volume=base.dec(
                        row["turnover"]
                        if meta["category"] == "inverse"
                        else row["volume"]
                    ),
                    now_ms=received_ms,
                    source="bybit.ws",
                    is_final=bool(row["confirm"]),
                    feed_state=quality.FeedState.LIVE,
                )
                for row in message.get("data", [])
            ]

        async def backfill(start: int, end: int) -> list[market.Candle]:
            """Repairs the interrupted stream's exact half-open range.

            Args:
                start: Inclusive UTC epoch milliseconds.
                end: Exclusive UTC epoch milliseconds.

            Returns:
                Historical canonical candles.
            """
            return await self.fetch_candles(instrument, timeframe, start, end)

        spec = ws.WsCandleSpec(
            url=f"wss://stream.bybit.com/v5/public/{meta['category']}",
            subscribe=(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [
                            f"kline.{_BYBIT_TF[timeframe]}.{meta['native']}"
                        ],
                    }
                ),
            ),
            parse=parse,
            timeframe=timeframe,
            venue_marks_final=True,
            ping_message=json.dumps({"op": "ping"}),
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
            async for candle in stream:
                yield candle
