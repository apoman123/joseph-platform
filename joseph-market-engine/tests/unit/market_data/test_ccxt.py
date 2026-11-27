# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tests of the free CCXT data-feed adapter and production wiring."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock as mock_lib

import pytest
from ccxt import pro  # type: ignore[import-untyped]  # CCXT has no type marker.

from joseph import bootstrap
from joseph.domain import clock
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import ccxt
from joseph.market_data.adapters import factory
from joseph.market_data.app import derived_bars
from joseph.platform import config


class FakeExchange:
    """Minimal CCXT exchange with one linear perpetual.

    Attributes:
        id: CCXT exchange name.
        has: Supported unified methods.
        timeframes: Native OHLCV resolutions.
        markets: Unified markets keyed by symbol.
        markets_by_id: Unified markets keyed by native ID.
        calls: OHLCV page start times requested by the adapter.
    """

    def __init__(self) -> None:
        """Initialize deterministic market and OHLCV fixtures."""
        self.id = "binanceusdm"
        self.has = {
            "fetchOHLCV": True,
            "fetchOrderBook": True,
            "fetchOpenInterest": False,
            "fetchFundingRate": False,
            "fetchTickers": False,
        }
        self.timeframes = {"1m": "1m"}
        item = {
            "id": "BTCUSDT",
            "symbol": "BTC/USDT:USDT",
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "swap": True,
            "linear": True,
            "active": True,
            "precision": {"price": 0.1, "amount": 0.001},
            "contractSize": 1,
        }
        self.markets: dict[str, dict[str, Any]] = {str(item["symbol"]): item}
        self.markets_by_id: dict[str, dict[str, Any]] = {str(item["id"]): item}
        self.calls: list[int] = []

    async def load_markets(self) -> dict[str, Any]:
        """Return the market mapping without network access.

        Returns:
            Unified CCXT markets.
        """
        return self.markets

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[int]]:
        """Return two aligned bars on the first page.

        Args:
            symbol: Unified CCXT symbol.
            timeframe: Requested native interval.
            since: Inclusive UTC start in milliseconds.
            limit: Maximum page size.

        Returns:
            OHLCV rows, or an empty page after the fixture.
        """
        assert symbol == "BTC/USDT:USDT"
        assert timeframe == "1m"
        assert limit > 0
        self.calls.append(since)
        if since > 0:
            return []
        return [[0, 100, 110, 90, 105, 2], [60_000, 105, 115, 100, 110, 3]]

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """Return a current ticker for the requested symbol.

        Args:
            symbol: Unified CCXT symbol.

        Returns:
            Ticker fields and timestamp.
        """
        assert symbol == "BTC/USDT:USDT"
        return {"timestamp": 120_000, "last": 110, "bid": 109, "ask": 111}

    async def close(self) -> None:
        """Close the fake exchange.

        Returns:
            None; the fake has no resources.
        """


async def test_ccxt_rest_candles_and_snapshot_are_polled() -> None:
    """CCXT results retain canonical IDs and REST provenance."""
    exchange = FakeExchange()
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.BINANCE, clock.ManualClock(120_000), exchange
    )
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    listed = await adapter.list_instruments()
    candles = await adapter.fetch_candles(
        instrument, timeframes.Timeframe.M1, 0, 180_000
    )
    snapshot = await adapter.fetch_snapshot(instrument)
    assert listed[0].instrument_id == instrument
    assert [bar.start_ms for bar in candles] == [0, 60_000]
    assert all(bar.feed_state is quality.FeedState.POLL for bar in candles)
    assert snapshot.feed_state is quality.FeedState.POLL
    assert snapshot.last == 110
    assert not adapter.capabilities().market_data.native_ws_candles
    assert exchange.calls == [0, 120_000]


async def test_ccxt_numeric_exchange_id_keeps_existing_symbol_id() -> None:
    """Hyperliquid and Lighter retain canonical BTC instrument IDs."""
    for venue in (instruments.Venue.HYPERLIQUID, instruments.Venue.LIGHTER):
        exchange = FakeExchange()
        item = exchange.markets["BTC/USDT:USDT"]
        item["id"] = "1"
        adapter = ccxt.CcxtAdapter(venue, clock.ManualClock(120_000), exchange)
        listed = await adapter.list_instruments()
        assert str(listed[0].instrument_id) == f"{venue.value}:perp:BTC"
        snapshot = await adapter.fetch_snapshot(listed[0].instrument_id)
        assert snapshot.last == 110


def test_unconfigured_key_feeds_are_not_started() -> None:
    """An absent free-plan key does not create a permanently connecting feed."""
    settings = config.Settings.from_env({})
    assert instruments.Venue.BINANCE in settings.active_venues
    assert instruments.Venue.NASDAQ not in settings.active_venues
    assert instruments.Venue.TWSE not in settings.active_venues
    assert instruments.Venue.TREASURY in settings.active_venues


async def test_running_market_engine_defaults_to_native_crypto_sources() -> (
    None
):
    """The composition root uses native feeds with canonical identities."""
    settings = config.Settings(
        database_url="sqlite://",
        enabled_venues=tuple(ccxt.EXCHANGES),
        pipeline_enabled=False,
        trade_recording=False,
    )
    container = bootstrap.build_container(settings)
    try:
        assert set(container.adapters) == set(ccxt.EXCHANGES)
        assert all(
            isinstance(adapter, derived_bars.DerivedBarsAdapter)
            and not isinstance(adapter.inner, ccxt.CcxtAdapter)
            for adapter in container.adapters.values()
        )
        instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
        state, source = container.market_data._provenance(
            instrument, timeframes.Timeframe.M1
        )
        assert state is quality.FeedState.LIVE
        assert source.startswith("binance.")
        await container.market_data._call(
            instruments.Venue.BINANCE, asyncio.sleep(0, result=None)
        )
        assert (
            container.router.rest_tracker(instruments.Venue.BINANCE).feed_state
            is quality.FeedState.LIVE
        )
    finally:
        await container.aclose()


async def test_factory_keeps_ccxt_as_an_explicit_compatibility_choice() -> None:
    """Explicit compatibility callers can still select CCXT."""
    adapters = factory.build_adapters(
        tuple(ccxt.EXCHANGES), clock.ManualClock(0), crypto_source="ccxt"
    )
    try:
        assert all(
            isinstance(adapter, ccxt.CcxtAdapter)
            for adapter in adapters.values()
        )
    finally:
        for adapter in adapters.values():
            await adapter.close()


class FakeSocket:
    """Session-owned public WebSocket fixture.

    Attributes:
        has: Supported subscription methods.
        closed: Whether socket resources were released.
        subscribed: Unified symbol and interval subscribed by the adapter.
        index: Number of candle updates emitted.
    """

    def __init__(self) -> None:
        """Initialize a socket that emits two updates then waits."""
        self.has = {"watchOHLCV": True}
        self.closed = False
        self.subscribed: tuple[str, str] | None = None
        self.index = 0

    def set_markets_from_exchange(self, exchange: Any) -> None:
        """Accept the parent exchange's loaded market metadata.

        Args:
            exchange: CCXT REST client with loaded markets.

        Returns:
            None; validates the fixture market exists.
        """
        assert exchange.markets

    async def watch_ohlcv(self, symbol: str, timeframe: str) -> list[list[int]]:
        """Emit successive prices for the same unfinished candle.

        Args:
            symbol: Unified market symbol.
            timeframe: Native resolution.

        Returns:
            One OHLCV update with a changing close.
        """
        self.subscribed = (symbol, timeframe)
        self.index += 1
        return [[120_000, 100, 110, 90, 100 + self.index, 2]]

    async def close(self) -> None:
        """Release the fixture socket.

        Returns:
            None; records socket closure.
        """
        self.closed = True


async def test_websocket_updates_price_and_closes_subscription(
    monkeypatch: Any,
) -> None:
    """Two exchange events update price without polling and release the socket.

    Args:
        monkeypatch: Pytest scoped patch helper for the CCXT constructor.
    """
    socket = FakeSocket()
    monkeypatch.setattr(pro, "binanceusdm", lambda _: socket)
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.BINANCE, clock.ManualClock(130_000), FakeExchange()
    )
    stream = adapter.stream_candles(
        instruments.InstrumentId.parse("binance:perp:BTCUSDT"),
        timeframes.Timeframe.M1,
    )
    first = await anext(stream)
    second = await anext(stream)
    assert socket.subscribed == ("BTC/USDT:USDT", "1m")
    assert first.close == 101 and second.close == 102
    assert not first.is_final and not second.is_final
    assert second.feed_state is quality.FeedState.LIVE
    await stream.aclose()
    assert socket.closed


async def test_order_book_accepts_exchange_level_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read price and amount when levels also contain an order count.

    Args:
        monkeypatch: Scoped helper injecting a public order-book response.

    Returns:
        None. Checks depth limiting and normalized price/amount values.
    """
    exchange = FakeExchange()
    monkeypatch.setattr(
        exchange,
        "fetch_order_book",
        mock_lib.AsyncMock(
            return_value={
                "bids": [[100, 2, 7], [99, 3]],
                "asks": [[101, 4, 8], [102, 5]],
            }
        ),
        raising=False,
    )
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.BINANCE, clock.ManualClock(130_000), exchange
    )
    book = await adapter.fetch_order_book(
        instruments.InstrumentId.parse("binance:perp:BTCUSDT"), 1
    )
    assert len(book.bids) == len(book.asks) == 1
    assert book.bids[0].price == 100 and book.bids[0].quantity == 2
    assert book.asks[0].price == 101 and book.asks[0].quantity == 4


async def test_rwa_metadata_is_preserved_in_ccxt_instruments() -> None:
    """Commodity and stock perpetuals retain their classes and IDs."""
    exchange = FakeExchange()
    item = exchange.markets["BTC/USDT:USDT"]
    item["info"] = {"underlyingType": "COMMODITY"}
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.BINANCE, clock.ManualClock(0), exchange
    )
    assert (await adapter.list_instruments())[
        0
    ].asset_class is instruments.AssetClass.RWA
    item["info"] = {"underlyingType": "EQUITY"}
    assert (await adapter.list_instruments())[
        0
    ].asset_class is instruments.AssetClass.EQUITY
    item["base"] = "XYZ-TSLA"
    item["info"] = {"hip3": True, "name": "xyz:TSLA"}
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.HYPERLIQUID, clock.ManualClock(0), exchange
    )
    listed = (await adapter.list_instruments())[0]
    assert str(listed.instrument_id) == "hyperliquid:perp:XYZ-TSLA"
    assert listed.asset_class is instruments.AssetClass.EQUITY


async def test_lighter_updates_price_and_repairs_partial_volume() -> None:
    """Lighter live prices change and REST repairs the first partial bar."""
    exchange = FakeExchange()
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.LIGHTER, clock.ManualClock(180_000), exchange
    )
    batches = [
        [{"id": "a", "timestamp": 10_000, "price": 101, "amount": 1}],
        [
            {"id": "a", "timestamp": 10_000, "price": 101, "amount": 1},
            {"id": "b", "timestamp": 20_000, "price": 102, "amount": 2},
        ],
        [{"id": "c", "timestamp": 70_000, "price": 103, "amount": 1}],
    ]

    class Trades:
        """Supply deterministic exchange trade batches."""

        async def watch_trades(self, symbol: str) -> list[dict[str, Any]]:
            """Return the next batch of WebSocket trades.

            Args:
                symbol: Unified symbol being subscribed.

            Returns:
                Timestamped public trades with stable IDs.
            """
            assert symbol == "BTC/USDT:USDT"
            return batches.pop(0)

    instrument = instruments.InstrumentId.parse("lighter:perp:BTC")
    stream = adapter._trade_candles(
        Trades(),
        exchange.markets["BTC/USDT:USDT"],
        instrument,
        timeframes.Timeframe.M1,
    )
    first = await anext(stream)
    second = await anext(stream)
    repaired = await anext(stream)
    assert first.close == 101 and second.close == 102
    assert not second.is_final and second.base_volume == 3
    assert repaired.start_ms == 0 and repaired.is_final
    assert repaired.base_volume == 2
    await stream.aclose()


async def test_concurrent_history_downloads_share_one_request(
    monkeypatch: Any,
) -> None:
    """Chart and indicator history requests reuse one provider round trip.

    Args:
        monkeypatch: Scoped patch helper for delayed exchange responses.
    """
    exchange = FakeExchange()
    adapter = ccxt.CcxtAdapter(
        instruments.Venue.BINANCE, clock.ManualClock(120_000), exchange
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[bool] = []

    async def delayed(*args: Any, **kwargs: Any) -> list[list[int]]:
        """Waits for the test to release a simulated provider response.

        Args:
            *args: CCXT positional arguments.
            **kwargs: CCXT keyword arguments.

        Returns:
            An empty historical page.
        """
        del args, kwargs
        calls.append(True)
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(exchange, "fetch_ohlcv", delayed)
    tasks = [
        asyncio.create_task(
            adapter._request(
                "fetch_ohlcv", "BTC/USDT:USDT", "1m", since=0, limit=500
            )
        )
        for _ in range(5)
    ]
    await entered.wait()
    release.set()
    assert await asyncio.gather(*tasks) == [[]] * 5
    assert len(calls) == 1
    await adapter.close()
