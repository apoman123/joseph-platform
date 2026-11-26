# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in public CCXT history, RWA discovery and WebSocket checks.

These tests exercise the production crypto data-feed adapter without API
keys or trading. Enable them with ``JOSEPH_NETWORK_TESTS=1``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import TypeAlias

import pytest

from joseph.domain import clock
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import ccxt

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit public CCXT APIs",
    ),
]

Validate: TypeAlias = Callable[[str, str, object], None]
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}


@pytest.mark.parametrize("venue", list(_BTC), ids=lambda venue: venue.value)
async def test_live_ccxt_history_rwa_and_websocket(
    venue: instruments.Venue, contract_validate: Validate
) -> None:
    """Loads real history and RWA metadata, then receives a public WS bar.

    Args:
        venue: One of the five production crypto exchanges.
        contract_validate: Shared JSON Schema validation fixture.

    Returns:
        None. All clients and subscriptions close even on assertion failure.
    """
    receipt_clock = clock.SystemClock()
    adapter = ccxt.CcxtAdapter(venue, receipt_clock)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    try:
        listed = await asyncio.wait_for(adapter.list_instruments(), 45)
        assert instrument in {item.instrument_id for item in listed}
        rwa = [
            item
            for item in listed
            if item.asset_class
            in (instruments.AssetClass.RWA, instruments.AssetClass.EQUITY)
        ]
        assert rwa, f"no RWA or equity contracts discovered on {venue.value}"
        for item in rwa:
            contract_validate(
                "market.schema.json", "Instrument", item.to_contract()
            )
        now = receipt_clock.now_ms()
        bars = await asyncio.wait_for(
            adapter.fetch_candles(
                instrument,
                timeframes.Timeframe.M1,
                now - 20 * 60_000,
                now + 1,
                limit=20,
            ),
            45,
        )
        assert len(bars) >= 5
        assert [bar.start_ms for bar in bars] == sorted(
            {bar.start_ms for bar in bars}
        )
        for bar in bars:
            contract_validate("market.schema.json", "Candle", bar.to_contract())
            assert bar.feed_state is quality.FeedState.POLL
        snapshot = await asyncio.wait_for(
            adapter.fetch_snapshot(instrument), 30
        )
        assert snapshot.last is not None
        book = await asyncio.wait_for(
            adapter.fetch_order_book(instrument, 20), 30
        )
        assert book.best_bid is not None and book.best_ask is not None
        assert book.best_bid < book.best_ask
        async with contextlib.aclosing(
            adapter.stream_candles(instrument, timeframes.Timeframe.M1)
        ) as stream:
            live = await asyncio.wait_for(anext(stream), 45)
        contract_validate("market.schema.json", "Candle", live.to_contract())
        assert live.instrument_id == instrument
        assert live.feed_state is quality.FeedState.LIVE
        assert live.source.startswith("ccxt.")
        assert live.source.endswith(".ws")
        assert live.received_at_ms >= now
    finally:
        await adapter.close()
