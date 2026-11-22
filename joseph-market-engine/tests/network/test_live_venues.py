"""Opt-in live tests against the real public venue APIs.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
They make a handful of small public requests per venue and never trade.
"""

from __future__ import annotations

import asyncio
import datetime
import os
from collections.abc import Callable

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import factory
from joseph.market_data.app import ports
from joseph.market_data.domain import health
from joseph.market_data.domain import trades as trades_lib

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
_HISTORY = [
    instruments.Venue.BINANCE,
    instruments.Venue.BITGET,
    instruments.Venue.LIGHTER,
]
_MONTHLY = [
    instruments.Venue.BINANCE,
    instruments.Venue.BITGET,
    instruments.Venue.MEXC,
]


def _live(
    venue: instruments.Venue,
) -> tuple[ports.MarketDataPort, instruments.InstrumentId, clock_lib.Clock]:
    """Builds a live adapter for the BTC perpetual of a venue.

    Args:
        venue: Venue.

    Returns:
        Adapter, instrument and clock.
    """
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [venue], clock, client=httpx.AsyncClient(timeout=20)
    )[venue]
    return adapter, instruments.InstrumentId.parse(_BTC[venue]), clock


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


@pytest.mark.parametrize("venue", list(_BTC), ids=lambda v: v.value)
async def test_live_trade_streams(
    venue: instruments.Venue, contract_validate: Validate
) -> None:
    """Native trade streams deliver schema-valid trades with a side."""
    adapter, instrument, _ = _live(venue)
    snapshot = await adapter.fetch_snapshot(instrument)
    reference = snapshot.mark or snapshot.last
    stream = adapter.stream_trades(instrument)
    trades: list[market.Trade] = []
    for _ in range(3):
        trades.append(await asyncio.wait_for(anext(stream), 60))
    await stream.aclose()  # type: ignore[attr-defined]
    await adapter.close()
    for trade in trades:
        contract_validate(
            "market.schema.json", "Trade", trades_lib.to_contract(trade)
        )
        assert trade.aggressor_side is not market.AggressorSide.UNKNOWN
        assert reference is not None
        assert abs(trade.price - reference) / reference < 0.02


@pytest.mark.parametrize("venue", _HISTORY, ids=lambda v: v.value)
async def test_live_trade_history_and_seconds_bars(
    venue: instruments.Venue, contract_validate: Validate
) -> None:
    """REST trade history serves recent trades and seconds bars."""
    adapter, instrument, clock = _live(venue)
    now = clock.now_ms()
    trades = await adapter.fetch_trades(instrument, now - 20_000, now)
    assert trades
    assert all(now - 20_000 <= t.venue_event_ts_ms < now for t in trades)
    tf = timeframes.Timeframe.S5
    bars = await adapter.fetch_candles(
        instrument, tf, now - 60_000, now + 5_000
    )
    await adapter.close()
    assert any(c.is_final for c in bars)
    for candle in bars:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.taker_buy_base_volume is not None
        assert candle.source.endswith(".trades.rest")


@pytest.mark.parametrize("venue", list(_BTC), ids=lambda v: v.value)
async def test_live_seconds_candles_from_trade_stream(
    venue: instruments.Venue,
) -> None:
    """1s bars built from the live trade stream finalize."""
    adapter, instrument, _ = _live(venue)
    tracker = health.FeedHealthTracker(venue)
    stream = adapter.stream_candles(
        instrument, timeframes.Timeframe.S1, tracker=tracker
    )

    async def first_final() -> market.Candle:
        """Waits for the first final bar.

        Returns:
            Final candle.
        """
        async for candle in stream:
            if candle.is_final:
                return candle
        raise AssertionError("stream ended")

    candle = await asyncio.wait_for(first_final(), 90)
    await stream.aclose()  # type: ignore[attr-defined]
    await adapter.close()
    assert candle.timeframe is timeframes.Timeframe.S1
    assert candle.source == f"{venue.value}.trades"
    assert tracker.gaps == 0


@pytest.mark.parametrize("venue", _MONTHLY, ids=lambda v: v.value)
async def test_live_monthly_klines_are_calendar_months(
    venue: instruments.Venue, contract_validate: Validate
) -> None:
    """Native 1M bars start on the 1st 00:00 UTC, one per month."""
    adapter, instrument, clock = _live(venue)
    now = clock.now_ms()
    tf = timeframes.Timeframe.MN1
    rows = await adapter.fetch_candles(
        instrument, tf, now - 200 * 86_400_000, now + 1
    )
    await adapter.close()
    assert len(rows) >= 6
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        start = datetime.datetime.fromtimestamp(
            candle.start_ms / 1000, datetime.UTC
        )
        assert (start.day, start.hour, start.minute) == (1, 0, 0)
    assert [c.end_ms for c in rows[:-1]] == [c.start_ms for c in rows[1:]]
    assert not rows[-1].is_final


async def test_live_binance_taker_buy_volume() -> None:
    """Binance klines carry taker buy base volume within the bar volume."""
    adapter, instrument, clock = _live(instruments.Venue.BINANCE)
    now = clock.now_ms()
    rows = await adapter.fetch_candles(
        instrument, timeframes.Timeframe.M1, now - 300_000, now + 60_000
    )
    await adapter.close()
    assert rows
    for candle in rows:
        assert candle.taker_buy_base_volume is not None
        assert 0 <= candle.taker_buy_base_volume <= candle.base_volume
