"""Contract tests: MEXC streams in Protobuf and JSON through the adapter.

``ws_deal_protobuf`` and ``ws_kline_1m_protobuf`` were captured live from
``wss://contract.mexc.com/edge?Trans-Protocol=Protobuf`` on 2026-11-23
(binary frames base64-encoded); ``ws_deal`` and ``ws_kline_1m`` are the
JSON captures of 2026-11-22. The adapter subscribes in the chosen format
and parses both, so either capture streams through either setting.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import mexc
from joseph.market_data.app import ports
from joseph.market_data.domain import trades as trades_lib

Validate = Callable[[str, str, object], None]
_D = decimal.Decimal
_BTC = instruments.InstrumentId.parse("mexc:perp:BTC_USDT")
_SIZE = _D("0.0001")
_PB_CAPTURE_MS = 1_795_427_298_200
_FORMATS = list(mexc.WsFormat)


def _fixture(name: str) -> dict[str, Any]:
    """Reads a captured MEXC fixture.

    Args:
        name: File name without extension.

    Returns:
        Parsed fixture.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "fixtures/venues/mexc"
        / f"{name}.json"
    )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _raw(frame: Mapping[str, str]) -> str | bytes:
    """Restores a captured Protobuf-endpoint frame.

    Args:
        frame: ``{"text": ...}`` or ``{"base64": ...}``.

    Returns:
        Payload as delivered by the socket.
    """
    if "base64" in frame:
        return base64.b64decode(frame["base64"])
    return frame["text"]


def _pb_frames(name: str, key: str = "frames") -> list[str | bytes]:
    """Returns the frames of a Protobuf capture.

    Args:
        name: Fixture name.
        key: ``frames`` or ``reduced_frames``.

    Returns:
        Frames.
    """
    return [_raw(frame) for frame in _fixture(name)[key]]


class _Connection:
    """Replays frames and records what the adapter sends."""

    def __init__(self, frames: list[str | bytes]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
        """
        self.frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent frame.

        Args:
            message: Frame.
        """
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        """Returns the next frame or idles.

        Returns:
            Frame.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


class _Connector:
    """``ws.ConnectFn`` recording the URLs it opens."""

    def __init__(self, frames: list[str | bytes]) -> None:
        """Initializes the connector.

        Args:
            frames: Frames of every connection.
        """
        self.frames = frames
        self.urls: list[str] = []
        self.connections: list[_Connection] = []

    @contextlib.asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[_Connection]:
        """Opens a replaying connection.

        Args:
            url: WebSocket URL.

        Yields:
            Connection.
        """
        self.urls.append(url)
        connection = _Connection(self.frames)
        self.connections.append(connection)
        yield connection


def _adapter(
    ws_format: mexc.WsFormat, connector: _Connector, clock: clock_lib.Clock
) -> ports.MarketDataPort:
    """Builds the MEXC adapter through the factory.

    Args:
        ws_format: Push format.
        connector: Fake connector.
        clock: Clock.

    Returns:
        Adapter.
    """
    return factory.build_adapters(
        [instruments.Venue.MEXC],
        clock,
        client=httpx.AsyncClient(),
        connect=connector,  # type: ignore[arg-type]
        mexc_ws_format=ws_format,
    )[instruments.Venue.MEXC]


def test_protobuf_deal_frames_convert_contracts(
    contract_validate: Validate,
) -> None:
    """PB ``t`` 1/2 is buy/sell, ``v`` contracts become base units."""
    frames = _pb_frames("ws_deal_protobuf")
    assert mexc.parse_deal_frame(frames[0], _BTC, _SIZE, 0) == []
    trades = [
        t for f in frames[1:] for t in mexc.parse_deal_frame(f, _BTC, _SIZE, 1)
    ]
    assert [t.trade_id for t in trades] == [
        "17133941459",
        "17133941562",
        "17133941601",
        "17133941604",
    ]
    first = trades[0]
    assert first.price == _D("61450.2")
    assert first.base_quantity == _D("12") * _SIZE
    assert first.venue_event_ts_ms == 1_795_427_295_236
    assert first.aggressor_side is market.AggressorSide.BUY
    assert trades[1].aggressor_side is market.AggressorSide.SELL
    for trade in trades:
        contract_validate(
            "market.schema.json", "Trade", trades_lib.to_contract(trade)
        )


def test_reduced_protobuf_deals_are_rejected() -> None:
    """Deals without trade ids (``reduce`` left on) are unparseable."""
    reduced = _pb_frames("ws_deal_protobuf", "reduced_frames")
    assert mexc.parse_deal_frame(reduced[0], _BTC, _SIZE, 0) == []
    with pytest.raises(ValueError, match="trade ids"):
        mexc.parse_deal_frame(reduced[1], _BTC, _SIZE, 1)


@pytest.mark.parametrize("ws_format", _FORMATS, ids=str)
@pytest.mark.parametrize("capture", ["protobuf", "json"])
async def test_trade_stream_subscribes_and_parses_in_each_format(
    ws_format: mexc.WsFormat,
    capture: str,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """The chosen URL is opened, ``reduce`` is off, both captures parse."""
    del venue_api
    frames: list[str | bytes] = (
        _pb_frames("ws_deal_protobuf")
        if capture == "protobuf"
        else list(_fixture("ws_deal")["frames"])
    )
    connector = _Connector(frames)
    clock = clock_lib.ManualClock(_PB_CAPTURE_MS - 10 * 86_400_000)
    adapter = _adapter(ws_format, connector, clock)
    await adapter.list_instruments()
    stream = adapter.stream_trades(_BTC)
    got = [await asyncio.wait_for(anext(stream), 5) for _ in range(3)]
    await stream.aclose()  # type: ignore[attr-defined]
    assert connector.urls[0] == (
        mexc.WS_PROTOBUF_URL
        if ws_format is mexc.WsFormat.PROTOBUF
        else mexc.WS_URL
    )
    assert json.loads(connector.connections[0].sent[0]) == {
        "method": "sub.deal",
        "param": {"symbol": "BTC_USDT", "reduce": False},
    }
    assert len({t.trade_id for t in got}) == 3
    for trade in got:
        contract_validate(
            "market.schema.json", "Trade", trades_lib.to_contract(trade)
        )


@pytest.mark.parametrize("ws_format", _FORMATS, ids=str)
async def test_protobuf_kline_frames_stream_live_candles(
    ws_format: mexc.WsFormat,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """Binary ``push.kline`` frames become non-final LIVE candles."""
    del venue_api
    connector = _Connector(_pb_frames("ws_kline_1m_protobuf"))
    adapter = _adapter(
        ws_format, connector, clock_lib.ManualClock(_PB_CAPTURE_MS)
    )
    stream = adapter.stream_candles(_BTC, timeframes.Timeframe.M1)
    first = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()  # type: ignore[attr-defined]
    contract_validate("market.schema.json", "Candle", first.to_contract())
    assert first.start_ms == 1_795_427_280_000
    assert not first.is_final
    assert first.close == _D("61443.9")
    assert first.base_volume == _D("28219") * _SIZE
    assert first.quote_turnover == _D("173418.63077")
    assert json.loads(connector.connections[0].sent[0]) == {
        "method": "sub.kline",
        "param": {"symbol": "BTC_USDT", "interval": "Min1"},
    }
    assert connector.urls[0].endswith("?Trans-Protocol=Protobuf") is (
        ws_format is mexc.WsFormat.PROTOBUF
    )


def test_capabilities_name_the_chosen_format() -> None:
    """The capability notes say which push format the streams use."""
    clock = clock_lib.ManualClock(0)
    notes = {
        fmt: " ".join(_adapter(fmt, _Connector([]), clock).capabilities().notes)
        for fmt in _FORMATS
    }
    assert "Trans-Protocol=Protobuf" in notes[mexc.WsFormat.PROTOBUF]
    assert "JOSEPH_MEXC_WS_FORMAT=json" in notes[mexc.WsFormat.JSON]
