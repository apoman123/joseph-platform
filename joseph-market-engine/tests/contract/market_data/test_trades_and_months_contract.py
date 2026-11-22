"""Contract tests: captured trade frames, trade history and monthly klines.

Fixtures under ``fixtures/venues`` were captured from the live public APIs
on 2026-11-22 (account identifiers removed). Every produced object must
validate against the JSON contracts, and aggressor sides must follow each
venue's documented (and live-verified) convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import bitget
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import hyperliquid
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import mexc
from joseph.market_data.app import ports
from joseph.market_data.domain import trades as trades_lib

Validate = Callable[[str, str, object], None]
_D = decimal.Decimal
_BUY = market.AggressorSide.BUY
_SELL = market.AggressorSide.SELL
_MN1 = timeframes.Timeframe.MN1
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}
_REAL = list(_BTC)
_CAPTURE_MS = 1_795_349_300_000
"""A time just after the 2026-11-22 trade captures."""


def _fixture(venue: str, name: str) -> Any:
    """Reads a captured fixture.

    Args:
        venue: Venue directory.
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "fixtures"
        / "venues"
        / venue
        / f"{name}.json"
    )
    return json.loads(path.read_text())


def _id(venue: instruments.Venue) -> instruments.InstrumentId:
    """Returns the BTC perpetual of a venue.

    Args:
        venue: Venue.

    Returns:
        Instrument id.
    """
    return instruments.InstrumentId.parse(_BTC[venue])


def _utc(ms: int) -> datetime.datetime:
    """Converts epoch milliseconds to an aware UTC datetime.

    Args:
        ms: Epoch milliseconds.

    Returns:
        Datetime.
    """
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC)


class _FrameConnection:
    """Replays captured frames, then idles."""

    def __init__(self, frames: list[str]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
        """
        self.frames = list(frames)

    async def send(self, message: str) -> None:
        """Accepts a sent frame.

        Args:
            message: Frame.
        """
        del message

    async def recv(self) -> str:
        """Returns the next frame or idles.

        Returns:
            Frame.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


def _adapter(
    venue: instruments.Venue,
    clock: clock_lib.Clock,
    frames: list[str] | None = None,
) -> ports.MarketDataPort:
    """Builds an adapter whose WebSocket replays ``frames``.

    Args:
        venue: Venue.
        clock: Clock.
        frames: Optional frames.

    Returns:
        Adapter.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_FrameConnection]:
        """Yields a replaying connection.

        Args:
            url: Ignored.

        Yields:
            Connection.
        """
        del url
        yield _FrameConnection(frames or [])

    return factory.build_adapters(
        [venue],
        clock,
        client=httpx.AsyncClient(),
        connect=connect,  # type: ignore[arg-type]
    )[venue]


def _check_trades(
    trades: list[market.Trade], contract_validate: Validate
) -> None:
    """Asserts trades are contract-valid, unique and ascending.

    Args:
        trades: Trades.
        contract_validate: Schema validator.
    """
    assert trades
    for trade in trades:
        contract_validate(
            "market.schema.json", "Trade", trades_lib.to_contract(trade)
        )
        assert trade.base_quantity > 0
        assert trade.aggressor_side is not market.AggressorSide.UNKNOWN
    assert len({t.trade_id for t in trades}) == len(trades)
    times = [t.venue_event_ts_ms for t in trades]
    assert times == sorted(times)


def test_binance_agg_trade_frames(contract_validate: Validate) -> None:
    """``m`` (buyer is maker) maps to a SELL aggressor."""
    instrument = _id(instruments.Venue.BINANCE)
    frames = _fixture("binance", "ws_agg_trade")["frames"]
    assert binance.parse_ws_agg_trade(frames[0], instrument, 0) == []
    trades = [
        t for f in frames for t in binance.parse_ws_agg_trade(f, instrument, 1)
    ]
    _check_trades(trades, contract_validate)
    raw = json.loads(frames[1])
    first = trades[0]
    assert first.trade_id == str(raw["a"])
    assert first.base_quantity == _D(raw["q"])
    assert first.aggressor_side is (_SELL if raw["m"] else _BUY)
    assert all(t.is_aggregated for t in trades)
    assert {t.aggressor_side for t in trades} == {_BUY, _SELL}


def test_bitget_trade_frames(contract_validate: Validate) -> None:
    """Bitget ``side`` is the taker side; frames list newest first."""
    instrument = _id(instruments.Venue.BITGET)
    frames = _fixture("bitget", "ws_trade")["frames"]
    assert bitget.parse_trade_frame(frames[0], instrument, 0) == []
    assert bitget.parse_trade_frame("pong", instrument, 0) == []
    trades = [
        t for f in frames for t in bitget.parse_trade_frame(f, instrument, 1)
    ]
    _check_trades(trades, contract_validate)
    raw = json.loads(frames[1])["data"][0]
    match = next(t for t in trades if t.trade_id == raw["tradeId"])
    assert match.aggressor_side is (_BUY if raw["side"] == "buy" else _SELL)


def test_mexc_deal_frames_convert_contracts(
    contract_validate: Validate,
) -> None:
    """``T`` 1/2 is buy/sell and ``v`` contracts become base units."""
    instrument = _id(instruments.Venue.MEXC)
    capture = _fixture("mexc", "ws_deal")
    size = _D("0.0001")
    assert (
        mexc.parse_deal_frame(capture["frames"][0], instrument, size, 0) == []
    )
    frames = capture["frames"][1:] + capture["object_frames"]
    trades = [
        t for f in frames for t in mexc.parse_deal_frame(f, instrument, size, 1)
    ]
    _check_trades(trades[: len(trades) - 3], contract_validate)
    raw = json.loads(capture["frames"][1])["data"][0]
    first = trades[0]
    assert first.base_quantity == _D(raw["v"]) * size
    assert first.aggressor_side is (_BUY if raw["T"] == 1 else _SELL)
    multi = json.loads(capture["frames"][-1])["data"]
    parsed = mexc.parse_deal_frame(capture["frames"][-1], instrument, size, 1)
    assert [t.trade_id for t in parsed] == sorted(r["i"] for r in multi)


def test_hyperliquid_trade_frames(contract_validate: Validate) -> None:
    """Side B is a buy aggressor, A a sell; ids combine time and tid."""
    instrument = _id(instruments.Venue.HYPERLIQUID)
    frames = _fixture("hyperliquid", "ws_trades")["frames"]
    assert hyperliquid.parse_trades_frame(frames[0], instrument, 0) == []
    replay = hyperliquid.parse_trades_frame(frames[1], instrument, 1)
    assert len(replay) == 30
    trades = [
        t
        for f in frames
        for t in hyperliquid.parse_trades_frame(f, instrument, 1)
    ]
    _check_trades(trades, contract_validate)
    raw = json.loads(frames[1])["data"][0]
    first = replay[0]
    assert first.trade_id == f"{raw['time']}-{raw['tid']}"
    assert first.aggressor_side is (_BUY if raw["side"] == "B" else _SELL)


def test_lighter_trade_frames(contract_validate: Validate) -> None:
    """``is_maker_ask`` true means the aggressor bought."""
    instrument = _id(instruments.Venue.LIGHTER)
    frames = _fixture("lighter", "ws_trade")["frames"]
    assert lighter.parse_trade_frame(frames[0], instrument, 1, 0) == []
    trades = [
        t
        for f in frames
        for t in lighter.parse_trade_frame(f, instrument, 1, 1)
    ]
    _check_trades(trades, contract_validate)
    raw = json.loads(frames[2])["trades"][0]
    match = next(t for t in trades if t.trade_id == str(raw["trade_id"]))
    assert match.aggressor_side is (_BUY if raw["is_maker_ask"] else _SELL)
    assert match.quote_quantity == _D(raw["usd_amount"])
    error = json.dumps({"error": {"code": 30005, "message": "bad"}})
    with pytest.raises(ValueError, match="lighter"):
        lighter.parse_trade_frame(error, instrument, 1, 0)
    deleverage = {**raw, "type": "deleverage"}
    assert lighter.parse_trade(deleverage, instrument, 0) is None


@pytest.mark.parametrize(
    ("venue", "start", "end"),
    [
        (instruments.Venue.BINANCE, 1_795_348_603_000, 1_795_348_605_000),
        (instruments.Venue.BITGET, 1_795_348_666_000, 1_795_348_668_500),
        (instruments.Venue.LIGHTER, 1_795_348_767_000, 1_795_348_769_900),
    ],
    ids=lambda v: getattr(v, "value", str(v)),
)
async def test_rest_trade_history_is_contract_valid(
    venue: instruments.Venue,
    start: int,
    end: int,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """``fetch_trades`` returns the captured page within the range."""
    del venue_api
    adapter = _adapter(venue, clock_lib.ManualClock(_CAPTURE_MS))
    trades = await adapter.fetch_trades(_id(venue), start, end)
    _check_trades(trades, contract_validate)
    assert all(start <= t.venue_event_ts_ms < end for t in trades)
    latest = await adapter.fetch_trades(_id(venue), start, end, limit=2)
    assert [t.trade_id for t in latest] == [t.trade_id for t in trades[-2:]]


@pytest.mark.parametrize(
    "venue",
    [instruments.Venue.HYPERLIQUID, instruments.Venue.MEXC],
    ids=lambda v: v.value,
)
async def test_venues_without_trade_history_refuse(
    venue: instruments.Venue,
) -> None:
    """No time-range history: trades and seconds history are refused."""
    adapter = _adapter(venue, clock_lib.ManualClock(_CAPTURE_MS))
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_trades(_id(venue), 0, 1)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(_id(venue), timeframes.Timeframe.S5, 0, 1)


async def test_binance_seconds_history_builds_from_agg_trades(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """REST seconds bars come from aggTrades with true taker volume."""
    del venue_api
    clock = clock_lib.ManualClock(1_795_348_607_000)
    adapter = _adapter(instruments.Venue.BINANCE, clock)
    instrument = _id(instruments.Venue.BINANCE)
    rows = await adapter.fetch_candles(
        instrument,
        timeframes.Timeframe.S1,
        1_795_348_603_000,
        1_795_348_605_000,
    )
    raw = _fixture("binance", "agg_trades")
    assert [c.start_ms for c in rows] == [1_795_348_603_000, 1_795_348_604_000]
    assert sum(c.base_volume for c in rows) == sum(_D(r["q"]) for r in raw)
    assert sum(c.taker_buy_base_volume or 0 for c in rows) == sum(
        _D(r["q"]) for r in raw if not r["m"]
    )
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert candle.is_final
        assert candle.source == "binance.trades.rest"
        assert candle.trade_count is None


async def test_binance_klines_carry_taker_buy_volume(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Kline row index 9 becomes ``taker_buy_base_volume``."""
    del venue_api
    adapter = _adapter(instruments.Venue.BINANCE, capture_clock)
    now = capture_clock.now_ms()
    rows = await adapter.fetch_candles(
        _id(instruments.Venue.BINANCE),
        timeframes.Timeframe.M5,
        now - 900_000,
        now + 300_000,
    )
    raw = {int(r[0]): r for r in _fixture("binance", "klines_5m")}
    for candle in rows:
        assert candle.taker_buy_base_volume == _D(raw[candle.start_ms][9])
    assert adapter.capabilities().market_data.taker_volume


def test_binance_ws_kline_carries_taker_buy_volume() -> None:
    """WebSocket ``k.V`` becomes ``taker_buy_base_volume`` (also for 1M)."""
    instrument = _id(instruments.Venue.BINANCE)
    frames = _fixture("binance", "ws_kline_1M")["frames"]
    assert binance.parse_ws_kline(frames[0], instrument, _MN1, 0) == []
    candle = binance.parse_ws_kline(frames[1], instrument, _MN1, _CAPTURE_MS)[0]
    kline = json.loads(frames[1])["k"]
    assert candle.taker_buy_base_volume == _D(kline["V"])
    assert _utc(candle.start_ms) == datetime.datetime(
        2026, 11, 1, tzinfo=datetime.UTC
    )
    assert candle.end_ms == int(kline["T"]) + 1
    assert not candle.is_final


@pytest.mark.parametrize(
    "venue",
    [
        instruments.Venue.BINANCE,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
    ],
    ids=lambda v: v.value,
)
async def test_monthly_klines_are_calendar_months(
    venue: instruments.Venue,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """Native 1M bars open on the 1st 00:00 UTC and end a month later."""
    del venue_api
    clock = clock_lib.ManualClock(_CAPTURE_MS)
    adapter = _adapter(venue, clock)
    assert adapter.capabilities().supports_timeframe(_MN1)
    rows = await adapter.fetch_candles(
        _id(venue), _MN1, _CAPTURE_MS - 200 * 86_400_000, _CAPTURE_MS + 1
    )
    assert len(rows) >= 5
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        start = _utc(candle.start_ms)
        assert (start.day, start.hour, start.minute) == (1, 0, 0)
        end = _utc(candle.end_ms)
        assert end.day == 1
        assert (end.year * 12 + end.month) - (
            start.year * 12 + start.month
        ) == 1
    assert [c.start_ms for c in rows] == sorted({c.start_ms for c in rows})
    assert not rows[-1].is_final
    assert all(c.is_final for c in rows[:-1])


def test_hyperliquid_1m_interval_is_not_a_calendar_month() -> None:
    """The venue's 30-day "1M" buckets are refused, not relabelled."""
    instrument = _id(instruments.Venue.HYPERLIQUID)
    rows = _fixture("hyperliquid", "candles_1M")
    assert all(int(r["t"]) % (30 * 86_400_000) == 0 for r in rows)
    with pytest.raises(errors.UnsupportedError):
        hyperliquid.parse_candle(rows[-1], instrument, _MN1, 0, "x")
    caps = _adapter(
        instruments.Venue.HYPERLIQUID, clock_lib.ManualClock(0)
    ).capabilities()
    assert not caps.supports_timeframe(_MN1)


@pytest.mark.parametrize(
    "venue",
    [
        instruments.Venue.BINANCE,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
        instruments.Venue.HYPERLIQUID,
        instruments.Venue.LIGHTER,
    ],
    ids=lambda v: v.value,
)
async def test_trade_streams_replay_captured_frames(
    venue: instruments.Venue,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """``stream_trades`` parses each venue's captured frames."""
    del venue_api
    name = {
        instruments.Venue.BINANCE: ("binance", "ws_agg_trade"),
        instruments.Venue.BITGET: ("bitget", "ws_trade"),
        instruments.Venue.MEXC: ("mexc", "ws_deal"),
        instruments.Venue.HYPERLIQUID: ("hyperliquid", "ws_trades"),
        instruments.Venue.LIGHTER: ("lighter", "ws_trade"),
    }[venue]
    frames = _fixture(*name)["frames"]
    clock = clock_lib.ManualClock(_CAPTURE_MS - 10 * 86_400_000)
    adapter = _adapter(venue, clock, frames)
    await adapter.list_instruments()
    stream = adapter.stream_trades(_id(venue))
    got: list[market.Trade] = []
    for _ in range(3):
        got.append(await asyncio.wait_for(anext(stream), 5))
    await stream.aclose()  # type: ignore[attr-defined]
    _check_trades(got, contract_validate)


async def test_seconds_candles_stream_from_replayed_trades(
    venue_api: respx.Router, contract_validate: Validate
) -> None:
    """Hyperliquid seconds bars are folded from the 30-trade replay."""
    del venue_api
    frames = _fixture("hyperliquid", "ws_trades")["frames"]
    clock = clock_lib.ManualClock(_CAPTURE_MS)
    adapter = _adapter(instruments.Venue.HYPERLIQUID, clock, frames)
    stream = adapter.stream_candles(
        _id(instruments.Venue.HYPERLIQUID), timeframes.Timeframe.S5
    )
    first = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()  # type: ignore[attr-defined]
    contract_validate("market.schema.json", "Candle", first.to_contract())
    assert first.source == "hyperliquid.trades"
    assert first.timeframe is timeframes.Timeframe.S5
    assert first.taker_buy_base_volume is not None


def test_trade_capabilities_are_truthful() -> None:
    """Seconds bars are declared only where a trade stream exists."""
    clock = clock_lib.ManualClock(0)
    caps = {
        v: _adapter(v, clock).capabilities()
        for v in [*_REAL, instruments.Venue.MOCK]
    }
    for cap in caps.values():
        seconds = {tf for tf in cap.timeframes if tf.is_seconds}
        assert seconds == set(cap.trade_built_timeframes)
        if cap.trade_built_timeframes:
            assert cap.market_data.native_ws_trades
    monthly = {v for v, c in caps.items() if c.supports_timeframe(_MN1)}
    assert monthly == {
        instruments.Venue.BINANCE,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
        instruments.Venue.MOCK,
    }
    taker = {v for v, c in caps.items() if c.market_data.taker_volume}
    assert taker == {instruments.Venue.BINANCE, instruments.Venue.MOCK}
    history = {v for v, c in caps.items() if c.market_data.trades_history}
    assert history == {
        instruments.Venue.BINANCE,
        instruments.Venue.BITGET,
        instruments.Venue.LIGHTER,
        instruments.Venue.MOCK,
    }
