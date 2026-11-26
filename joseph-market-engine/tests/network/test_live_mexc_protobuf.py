# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in live tests of MEXC's futures WebSocket in Protobuf and JSON.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
MEXC migrates futures WebSocket market data from JSON to Protobuf by
2026-12-01 10:00 UTC (see ``joseph.market_data.adapters.mexc_protobuf``).
These tests subscribe on the Protobuf endpoint directly and through the
adapter in both formats; once MEXC stops JSON pushes the JSON case is
expected to fail and ``JOSEPH_MEXC_WS_FORMAT=json`` must not be used.
"""

from __future__ import annotations

import asyncio
import decimal
import json
import os
from typing import Any

import httpx
import pytest
import websockets

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import mexc
from joseph.market_data.adapters import mexc_protobuf

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]

_BTC = instruments.InstrumentId.parse("mexc:perp:BTC_USDT")


async def _protobuf_pushes(
    deals: int, klines: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """Subscribes on the Protobuf endpoint and collects frames.

    Args:
        deals: ``push.deal`` frames wanted.
        klines: ``push.kline`` frames wanted.

    Returns:
        The text frames (acks, pongs) and the decoded binary pushes.
    """
    texts: list[str] = []
    pushes: list[dict[str, Any]] = []
    async with websockets.connect(mexc.WS_PROTOBUF_URL) as connection:
        for request in (
            {
                "method": "sub.deal",
                "param": {"symbol": "BTC_USDT", "reduce": False},
            },
            {
                "method": "sub.kline",
                "param": {"symbol": "BTC_USDT", "interval": "Min1"},
            },
            {"method": "ping"},
        ):
            await connection.send(json.dumps(request))

        def missing() -> bool:
            """Tells whether more pushes are needed.

            Returns:
                True until enough deals and klines arrived.
            """
            channels = [push["channel"] for push in pushes]
            return (
                channels.count("push.deal") < deals
                or channels.count("push.kline") < klines
            )

        while missing():
            raw = await asyncio.wait_for(connection.recv(), 60)
            if isinstance(raw, str):
                texts.append(raw)
            else:
                pushes.append(mexc_protobuf.decode(raw))
    return texts, pushes


async def test_live_protobuf_endpoint_pushes_binary_frames() -> None:
    """Acks and pongs stay JSON text; deals and klines arrive as Protobuf."""
    texts, pushes = await _protobuf_pushes(deals=3, klines=1)
    channels = {json.loads(text)["channel"] for text in texts}
    assert {"rs.sub.deal", "rs.sub.kline", "pong"} <= channels
    deals: list[dict[str, Any]] = []
    for push in pushes:
        if push["channel"] == "push.deal":
            deals.extend(push["data"])
    assert len(deals) >= 3
    for deal in deals:
        assert deal["i"] > 0
        assert deal["T"] in (1, 2)
        assert decimal.Decimal(deal["p"]) > 0
        assert decimal.Decimal(deal["v"]) > 0
    kline = next(p for p in pushes if p["channel"] == "push.kline")
    assert kline["symbol"] == "BTC_USDT"
    assert kline["data"]["interval"] == "Min1"
    assert kline["data"]["t"] % 60 == 0


@pytest.mark.parametrize("ws_format", list(mexc.WsFormat), ids=str)
async def test_live_adapter_streams_in_each_format(
    ws_format: mexc.WsFormat,
) -> None:
    """Trades and klines stream through the adapter in either format."""
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [instruments.Venue.MEXC],
        clock,
        crypto_source="native",
        client=httpx.AsyncClient(timeout=20),
        mexc_ws_format=ws_format,
    )[instruments.Venue.MEXC]
    trades = adapter.stream_trades(_BTC)
    got = [await asyncio.wait_for(anext(trades), 60) for _ in range(3)]
    await trades.aclose()  # type: ignore[attr-defined]  # stream_trades is typed AsyncIterator; its generator has aclose()
    assert len({t.trade_id for t in got}) == 3
    assert all(
        t.aggressor_side is not market.AggressorSide.UNKNOWN for t in got
    )
    candles = adapter.stream_candles(_BTC, timeframes.Timeframe.M1)
    candle = await asyncio.wait_for(anext(candles), 30)
    await candles.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    await adapter.close()
    assert candle.timeframe is timeframes.Timeframe.M1
    assert candle.base_volume >= 0
