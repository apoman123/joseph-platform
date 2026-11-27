# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public native-source transport, identity, pagination and lifecycle tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import deriv
from joseph.market_data.adapters import http
from joseph.market_data.adapters import public_sources
from joseph.market_data.app import registry

_START = 1_795_000_020_000
_NOW = _START + 600_000


def _response(request: httpx.Request) -> httpx.Response:
    """Answers fixed exchange endpoints with native provider-shaped fixtures.

    Args:
        request: HTTP request inspected for source, interval and page boundary.

    Returns:
        Native metadata or candle response with distinct USD and USDT markets.

    Raises:
        AssertionError: For unexpected requests or missing paging parameters.
    """
    host = request.url.host
    if request.url.path.endswith("instruments-info"):
        category = request.url.params["category"]
        items = []
        if category == "linear":
            items = [
                {
                    "symbol": "PAXGUSDT",
                    "baseCoin": "PAXG",
                    "quoteCoin": "USDT",
                    "status": "Trading",
                    "contractType": "LinearPerpetual",
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }
            ]
        return httpx.Response(
            200, json={"retCode": 0, "result": {"list": items}}
        )
    if request.url.path.endswith("/instruments"):
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {
                        "instId": "PAXG-USDT",
                        "baseCcy": "PAXG",
                        "quoteCcy": "USDT",
                        "state": "live",
                        "tickSz": "0.01",
                        "lotSz": "0.001",
                    }
                ],
            },
        )
    if request.url.path == "/products":
        return httpx.Response(
            200,
            json=[
                {
                    "id": "PAXG-USD",
                    "base_currency": "PAXG",
                    "quote_currency": "USD",
                    "status": "online",
                    "base_increment": "0.001",
                    "quote_increment": "0.01",
                }
            ],
        )
    if request.url.path.endswith("AssetPairs"):
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "PAXGUSD": {
                        "altname": "PAXGUSD",
                        "wsname": "PAXG/USD",
                        "pair_decimals": 2,
                        "lot_decimals": 3,
                        "status": "online",
                    }
                },
            },
        )
    if request.url.path == "/v5/market/tickers":
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "lastPrice": "102",
                            "bid1Price": "101",
                            "ask1Price": "103",
                            "volume24h": "7",
                            "turnover24h": "714",
                            "price24hPcnt": "0.02",
                        }
                    ]
                },
            },
        )
    if request.url.path == "/api/v5/market/ticker":
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {
                        "last": "102",
                        "bidPx": "101",
                        "askPx": "103",
                        "vol24h": "7",
                        "volCcy24h": "714",
                        "open24h": "100",
                        "ts": str(_NOW),
                    }
                ],
            },
        )
    if request.url.path == "/products/PAXG-USD/ticker":
        return httpx.Response(
            200,
            json={
                "price": "102",
                "bid": "101",
                "ask": "103",
                "volume": "7",
                "time": "2026-11-18T12:00:00Z",
            },
        )
    if request.url.path == "/0/public/Ticker":
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "PAXGUSD": {
                        "c": ["102", "1"],
                        "b": ["101", "1"],
                        "a": ["103", "1"],
                        "v": ["1", "7"],
                        "o": "100",
                    }
                },
            },
        )
    if host == "api.bybit.com":
        assert request.url.params["interval"] == "1"
        boundary = int(request.url.params["end"])
        ts = _START + 60_000 if boundary >= _START + 60_000 else _START
        rows = [[str(ts), "100", "103", "99", "102", "7", "714"]]
        return httpx.Response(
            200, json={"retCode": 0, "result": {"list": rows}}
        )
    if host == "www.okx.com":
        assert request.url.params["bar"] == "1m"
        boundary = int(request.url.params["after"])
        ts = _START + 60_000 if boundary > _START + 60_000 else _START
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    [str(ts), "100", "103", "99", "102", "7", "7", "714", "1"]
                ],
            },
        )
    if host == "api.exchange.coinbase.com":
        assert request.url.params["granularity"] == "60"
        return httpx.Response(
            200,
            json=[
                [_START // 1000, 99, 103, 100, 102, 7],
                [_START // 1000 + 60, 100, 104, 102, 103, 8],
            ],
        )
    if host == "api.kraken.com":
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "PAXGUSD": [
                        [
                            _START // 1000,
                            "100",
                            "103",
                            "99",
                            "102",
                            "101",
                            "7",
                            2,
                        ]
                    ],
                    "last": _START // 1000,
                },
            },
        )
    raise AssertionError(f"unexpected source {request.url}")


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", tuple(public_sources.URLS))
async def test_native_history_and_identity(venue: instruments.Venue) -> None:
    """Checks native envelopes, RWA identity and history bounds.

    Args:
        venue: Public source selected by pytest.

    Returns:
        None; asserts canonical contracts without external network access.
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_response)
    ) as client:
        adapter = public_sources.PublicSourceAdapter(
            venue,
            http.VenueHttp(venue, public_sources.URLS[venue], client=client),
            clock.ManualClock(_NOW),
        )
        listed = await adapter.list_instruments()
        assert len(listed) == 1
        asset = listed[0]
        assert asset.asset_class is instruments.AssetClass.RWA
        assert asset.key.quote == (
            "USDT"
            if venue in (instruments.Venue.BYBIT, instruments.Venue.OKX)
            else "USD"
        )
        rows = await adapter.fetch_candles(
            asset.instrument_id,
            timeframes.Timeframe.M1,
            _START,
            _START + 120_000,
            2,
        )
        assert rows
        assert all(_START <= bar.start_ms < _START + 120_000 for bar in rows)
        assert [bar.start_ms for bar in rows] == sorted(
            {bar.start_ms for bar in rows}
        )
        assert rows[0].base_volume == 7
        assert rows[0].feed_state is quality.FeedState.POLL
        snapshot = await adapter.fetch_snapshot(asset.instrument_id)
        assert snapshot.last == 102
        assert snapshot.best_bid == 101
        assert snapshot.best_ask == 103
        if venue is instruments.Venue.KRAKEN:
            assert snapshot.change_24h_pct is None
        assert adapter.capabilities().market_data.native_ws_candles == (
            venue is instruments.Venue.BYBIT
        )
        wrong = instruments.InstrumentId(
            venue,
            instruments.MarketType.FUTURE,
            asset.instrument_id.venue_market_id,
        )
        with pytest.raises(errors.NotFoundError):
            await adapter.fetch_candles(
                wrong, timeframes.Timeframe.M1, _START, _NOW
            )
        await adapter.close()


class _DerivConnection:  # noqa: DOC302 - Document constructor mutation.
    """Answers public Deriv requests while recording reuse and subscriptions.

    Attributes:
        requests: Decoded outgoing public requests.
        pending: Next request whose matching response is to be returned.
    """

    def __init__(self) -> None:  # noqa: DOC303 - Document None after mutation.
        """Initializes in-memory requests and an empty pending response.

        Returns:
            None; opens no network resources.
        """
        self.requests: list[dict[str, Any]] = []
        self.pending: dict[str, Any] = {}

    async def send(self, message: str) -> None:
        """Records a public request for the next receive call.

        Args:
            message: JSON public-feed request.

        Returns:
            None; stores the decoded request without authentication fields.
        """
        self.pending = json.loads(message)
        self.requests.append(self.pending)

    async def recv(self) -> str:
        """Returns a native response with the caller's exact request id.

        Returns:
            JSON symbol, tick or OHLC history response.
        """
        payload: dict[str, Any] = {"req_id": self.pending.get("req_id")}
        if "active_symbols" in self.pending:
            payload["active_symbols"] = [
                {
                    "underlying_symbol": "frxEURGBP",
                    "market": "forex",
                    "pip_size": "0.00001",
                    "underlying_symbol_name": "EUR/GBP",
                }
            ]
        else:
            payload["candles"] = [
                {
                    "epoch": _START // 1000,
                    "open": "100",
                    "high": "103",
                    "low": "99",
                    "close": "102",
                }
            ]
        return json.dumps(payload)


@pytest.mark.asyncio
async def test_deriv_pool_identity_and_no_invented_volume() -> None:
    """Checks reusable request connections and truthful FX/volume metadata.

    Returns:
        None; asserts every pooled connection closes on adapter shutdown.
    """
    connections: list[_DerivConnection] = []
    closed: list[_DerivConnection] = []

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_DerivConnection]:
        """Creates and closes one recorded source connection.

        Args:
            url: Fixed Deriv public WebSocket endpoint.

        Yields:
            In-memory public request connection.
        """
        assert url == deriv.WS_URL
        connection = _DerivConnection()
        connections.append(connection)
        try:
            yield connection
        finally:
            closed.append(connection)

    adapter = deriv.DerivAdapter(clock.ManualClock(_NOW), connect)
    asset = (await adapter.list_instruments())[0]
    assert asset.key.quote == "GBP"
    assert asset.key.canonical_base == "EUR"
    for _ in range(5):
        rows = await adapter.fetch_candles(
            asset.instrument_id, timeframes.Timeframe.M1, _START, _NOW, 1
        )
        assert rows[0].base_volume == 0
        assert rows[0].source == "deriv.history"
    assert len(connections) <= 3
    assert sum(len(connection.requests) for connection in connections) == 6
    await adapter.close()
    assert len(closed) == len(connections)


@pytest.mark.asyncio
async def test_progressive_search_keeps_slow_provider_out_of_the_hot_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves symbol search returns ready sources while a provider is blocked.

    Args:
        monkeypatch: Scoped helper replacing only the slow metadata method.

    Returns:
        None; releases the slow source and verifies it appears on a later read.
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_response)
    ) as client:
        fast = public_sources.PublicSourceAdapter(
            instruments.Venue.COINBASE,
            http.VenueHttp(
                instruments.Venue.COINBASE,
                public_sources.URLS[instruments.Venue.COINBASE],
                client=client,
            ),
            clock.ManualClock(_NOW),
        )
        slow = public_sources.PublicSourceAdapter(
            instruments.Venue.OKX,
            http.VenueHttp(
                instruments.Venue.OKX,
                public_sources.URLS[instruments.Venue.OKX],
                client=client,
            ),
            clock.ManualClock(_NOW),
        )
        release = asyncio.Event()
        original = slow.list_instruments

        async def delayed() -> list[instruments.Instrument]:
            """Waits for the test release before loading source metadata.

            Returns:
                Native metadata after release; never fabricates a result.
            """
            await release.wait()
            return await original()

        monkeypatch.setattr(slow, "list_instruments", delayed)
        catalog = registry.InstrumentRegistry(
            {fast.venue: fast, slow.venue: slow}
        )
        try:
            async with asyncio.timeout(2):
                initial = await catalog.search("PAXG", interactive=True)
            assert {item.instrument_id.venue for item in initial} == {
                fast.venue
            }
            release.set()
            await catalog.all_instruments()
            ready = await catalog.search("PAXG", interactive=True)
            assert {item.instrument_id.venue for item in ready} == {
                fast.venue,
                slow.venue,
            }
        finally:
            release.set()
            await catalog.close()
            await fast.close()
            await slow.close()


class _StreamingConnection(_DerivConnection):
    """Adds deterministic live updates to the reusable metadata fixture.

    Attributes:
        frames: Native candle frames waiting to be received.
        streaming: Whether the client sent a live source subscription.
    """

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        """Initializes an unsubscribed connection with fixed live frames.

        Args:
            frames: Source-native JSON candle messages for this connection.
        """
        super().__init__()
        self.frames = list(frames)
        self.streaming = False

    async def send(self, message: str) -> None:
        """Records subscription frames before allowing live updates.

        Args:
            message: Native source subscribe or heartbeat JSON.

        Returns:
            None; toggles the deterministic stream on real subscribe messages.
        """
        await super().send(message)
        if self.pending.get("op") == "subscribe" or self.pending.get(
            "subscribe"
        ):
            self.streaming = True

    async def recv(self) -> str:
        """Returns metadata before subscribing, then real source-shaped updates.

        Returns:
            JSON metadata or a queued native candle frame.
        """
        if not self.streaming:
            return await super().recv()
        if not self.frames:
            await asyncio.Event().wait()
        return json.dumps(self.frames.pop(0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "venue", [instruments.Venue.BYBIT, instruments.Venue.DERIV]
)
async def test_native_websocket_subscription_updates_and_cleanup(
    venue: instruments.Venue,
) -> None:
    """Proves live prices require source subscriptions and release connections.

    Args:
        venue: Native WebSocket source under test.

    Returns:
        None; checks two provisional price updates and final connection cleanup.
    """
    frames: list[dict[str, Any]] = []
    for close in (101, 102):
        if venue is instruments.Venue.BYBIT:
            frames.append(
                {
                    "data": [
                        {
                            "start": _START,
                            "open": "100",
                            "high": "103",
                            "low": "99",
                            "close": str(close),
                            "volume": "7",
                            "confirm": False,
                        }
                    ]
                }
            )
        else:
            frames.append(
                {
                    "ohlc": {
                        "open_time": _START // 1000,
                        "open": "100",
                        "high": "103",
                        "low": "99",
                        "close": str(close),
                    }
                }
            )
    connections: list[_StreamingConnection] = []
    closed: list[_StreamingConnection] = []

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_StreamingConnection]:
        """Records a native source connection and its eventual closure.

        Args:
            url: Fixed source-native public WebSocket endpoint.

        Yields:
            Native protocol fixture, including source subscription requirements.
        """
        assert (
            url == deriv.WS_URL
            or url == "wss://stream.bybit.com/v5/public/linear"
        )
        connection = _StreamingConnection(frames)
        connections.append(connection)
        try:
            yield connection
        finally:
            closed.append(connection)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_response)
    ) as client:
        adapter: public_sources.PublicSourceAdapter | deriv.DerivAdapter
        frozen = clock.ManualClock(_START + 10_000)
        if venue is instruments.Venue.BYBIT:
            adapter = public_sources.PublicSourceAdapter(
                venue,
                http.VenueHttp(
                    venue, public_sources.URLS[venue], client=client
                ),
                frozen,
                connect,
            )
        else:
            adapter = deriv.DerivAdapter(frozen, connect)
        instrument = (await adapter.list_instruments())[0].instrument_id
        stream = adapter.stream_candles(instrument, timeframes.Timeframe.M1)
        try:
            async with asyncio.timeout(2):
                first, second = await anext(stream), await anext(stream)
            assert first.close == 101 and second.close == 102
            assert first.instrument_id == second.instrument_id == instrument
            assert (
                first.feed_state is second.feed_state is quality.FeedState.LIVE
            )
            assert not first.is_final and not second.is_final
            subscription = connections[-1].requests[0]
            if venue is instruments.Venue.BYBIT:
                assert subscription["args"] == ["kline.1.PAXGUSDT"]
            else:
                assert subscription["ticks_history"] == "frxEURGBP"
                assert subscription["subscribe"] == 1
        finally:
            await stream.aclose()
            await adapter.close()
        assert len(closed) == len(connections)
