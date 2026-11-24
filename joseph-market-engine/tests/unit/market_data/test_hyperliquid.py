# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for Hyperliquid tick sizes and WebSocket frame parsing."""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import json
from collections.abc import AsyncIterator

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import ws
from joseph.market_data.domain import health

_D = decimal.Decimal
_BTC = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_M1 = timeframes.Timeframe.M1
_T0 = 1_795_277_340_000
_CANDLE = json.dumps(
    {
        "channel": "candle",
        "data": {
            "t": _T0,
            "T": _T0 + 59_999,
            "s": "BTC",
            "i": "1m",
            "o": "100500",
            "c": "100501",
            "h": "100510",
            "l": "100490",
            "v": "1.5",
            "n": 12,
        },
    }
)


@pytest.mark.parametrize(
    ("mark", "sz_decimals", "tick"),
    [
        ("123456", 5, "1"),
        ("100000", 5, "1"),
        ("2345678", 0, "1"),
        ("99999", 5, "1"),
        ("12345.6", 5, "1"),
        ("1234.5", 4, "0.1"),
        ("0.001234", 0, "0.000001"),
        ("0.01234", 1, "0.00001"),
    ],
)
def test_tick_is_five_significant_figures_but_at_most_one(
    mark: str, sz_decimals: int, tick: str
) -> None:
    """Integer prices are always valid, so no tick is coarser than 1."""
    assert hyperliquid.tick_size(_D(mark), sz_decimals) == _D(tick)


@pytest.mark.parametrize(
    "raw",
    ["[]", "[1, 2]", '"candle"', "7", "null"],
    ids=["empty-list", "list", "string", "number", "null"],
)
def test_non_object_candle_frame_is_malformed(raw: str) -> None:
    """A non-object frame raises TypeError (never AttributeError)."""
    with pytest.raises(TypeError):
        hyperliquid.parse_candle_frame(raw, _BTC, _M1, _T0)


def test_candle_frame_parses_only_this_coin_and_interval() -> None:
    """Acks and other subscriptions yield nothing; the candle is never final."""
    ack = json.dumps({"channel": "subscriptionResponse", "data": {}})
    assert hyperliquid.parse_candle_frame(ack, _BTC, _M1, _T0) == []
    other = _CANDLE.replace('"BTC"', '"ETH"')
    assert hyperliquid.parse_candle_frame(other, _BTC, _M1, _T0) == []
    (candle,) = hyperliquid.parse_candle_frame(_CANDLE, _BTC, _M1, _T0 + 1)
    assert candle.close == _D("100501") and not candle.is_final


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        json.dumps({"channel": "trades", "data": {"coin": "BTC"}}),
        json.dumps({"channel": "trades", "data": ["BTC"]}),
    ],
    ids=["list-frame", "object-data", "string-rows"],
)
def test_malformed_trades_frame_raises_type_error(raw: str) -> None:
    """Trade frames of the wrong shape are malformed, not attribute errors."""
    with pytest.raises(TypeError):
        hyperliquid.parse_trades_frame(raw, _BTC, _T0)


class _Connection:
    """Plays scripted frames, then idles.

    Attributes:
        frames: Frames left to deliver.
    """

    def __init__(self, frames: list[str]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver, in order.
        """
        self.frames = list(frames)

    async def send(self, message: str) -> None:
        """Accepts a sent frame.

        Args:
            message: Ignored.
        """
        del message

    async def recv(self) -> str:
        """Returns the next frame, or idles for an hour once none are left.

        Returns:
            The next frame; an empty string only after the idle wait.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


@contextlib.asynccontextmanager
async def _malformed_then_candle(url: str) -> AsyncIterator[_Connection]:
    """Opens a connection playing two malformed frames and a candle.

    Args:
        url: Ignored.

    Yields:
        The scripted connection.
    """
    del url
    yield _Connection(["[]", "[1, 2]", _CANDLE])


async def _no_backfill(start: int, end: int) -> list[market.Candle]:
    """Serves no REST bars.

    Args:
        start: Ignored.
        end: Ignored.

    Returns:
        An empty list.
    """
    del start, end
    return []


def _parse_btc_1m(raw: str | bytes, now_ms: int) -> list[market.Candle]:
    """Parses with the adapter's candle frame parser.

    Args:
        raw: Frame payload.
        now_ms: Receipt time.

    Returns:
        Parsed BTC 1m candles.
    """
    return hyperliquid.parse_candle_frame(raw, _BTC, _M1, now_ms)


async def test_malformed_candle_frames_are_recorded_and_skipped() -> None:
    """Malformed list frames are skipped; the next candle still arrives."""
    tracker = health.FeedHealthTracker(instruments.Venue.HYPERLIQUID)
    spec = ws.WsCandleSpec(
        url=hyperliquid.WS_URL,
        subscribe=(),
        parse=_parse_btc_1m,
        timeframe=_M1,
        venue_marks_final=False,
    )
    stream = ws.stream_with_recovery(
        spec,
        backfill=_no_backfill,
        clock=clock_lib.ManualClock(_T0 + 1_000),
        connect=_malformed_then_candle,
        tracker=tracker,
    )
    candle = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()
    assert candle.start_ms == _T0
    assert tracker.last_error is not None
    assert "unparseable frame" in tracker.last_error
