"""Opt-in live tests against the real public venue APIs.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
They make a handful of small public requests per venue and never trade.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import factory

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]

Validate = Callable[[str, str, object], None]
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}


@pytest.mark.parametrize("venue", list(_BTC), ids=lambda v: v.value)
async def test_live_rest_endpoints(
    venue: instruments.Venue, contract_validate: Validate
) -> None:
    """Instruments, candles, snapshot, book, OI and funding work live."""
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [venue], clock, client=httpx.AsyncClient(timeout=20)
    )[venue]
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    listed = {i.instrument_id for i in await adapter.list_instruments()}
    assert instrument in listed
    tf = timeframes.Timeframe.M1
    now = clock.now_ms()
    candles = await adapter.fetch_candles(
        instrument, tf, now - 600_000, now + 60_000
    )
    assert len(candles) >= 5
    assert not candles[-1].is_final
    assert candles[-2].is_final
    for candle in candles:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
    snapshot = await adapter.fetch_snapshot(instrument)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(clock.now_ms(), 30_000),
    )
    book = await adapter.fetch_order_book(instrument, 20)
    assert book.best_bid is not None and book.best_ask is not None
    assert book.best_bid < book.best_ask
    oi = await adapter.fetch_open_interest(instrument)
    assert oi.notional_usd > 0
    funding = await adapter.fetch_funding(instrument)
    assert funding.interval_hours > 0
    await adapter.close()


@pytest.mark.parametrize(
    "venue",
    [
        instruments.Venue.BINANCE,
        instruments.Venue.HYPERLIQUID,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
    ],
    ids=lambda v: v.value,
)
async def test_live_native_candle_stream(venue: instruments.Venue) -> None:
    """Native WebSocket candle streams deliver a live bar."""
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [venue], clock, client=httpx.AsyncClient(timeout=20)
    )[venue]
    stream = adapter.stream_candles(
        instruments.InstrumentId.parse(_BTC[venue]), timeframes.Timeframe.M1
    )
    candle = await asyncio.wait_for(anext(stream), 30)
    await stream.aclose()  # type: ignore[attr-defined]
    assert candle.timeframe is timeframes.Timeframe.M1
    assert candle.start_ms == timeframes.Timeframe.M1.floor(clock.now_ms()) or (
        candle.is_final
    )
    await adapter.close()
