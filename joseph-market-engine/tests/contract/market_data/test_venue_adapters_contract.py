# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Contract tests: captured venue payloads become schema-valid objects.

Each adapter parses real responses captured from the public APIs
(``fixtures/venues``) served through respx; every produced object must
validate against ``joseph-terminal/packages/contracts/schemas``.
"""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import binance
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import ws
from joseph.market_data.app import ports

Validate: TypeAlias = Callable[[str, str, object], None]
_D = decimal.Decimal
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}
# Crypto venues with recorded fixtures (BTC perpetual tests).
_REAL = list(_BTC)
_EXCHANGE = [
    instruments.Venue.TWSE,
    instruments.Venue.TPEX,
    instruments.Venue.NASDAQ,
    instruments.Venue.NYSE,
    instruments.Venue.CBOE,
    instruments.Venue.TREASURY,
]
"""Exchange and rate venues.

Their venue-specific tests live in ``test_exchange_venues_contract.py``.
"""
_ALL_REAL = [*_REAL, *_EXCHANGE]
_ALPACA_TEST_KEYS = alpaca.AlpacaConfig(key_id="test-key", secret_key="test")


def _fixture(venue: str, name: str) -> Any:
    """Reads a captured fixture file.

    Args:
        venue: Venue directory under ``fixtures/venues``.
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


class _FrameConnection:
    """Replays captured frames, then idles.

    Attributes:
        frames: Frames still to deliver, oldest first.
        sent: Frames the adapter sent, in order.
    """

    def __init__(self, frames: list[str]) -> None:
        """Initializes the connection.

        Args:
            frames: Frames to deliver.
        """
        self.frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        """Records a sent frame.

        Args:
            message: Frame payload.
        """
        self.sent.append(message)

    async def recv(self) -> str:
        """Returns the next frame, or idles for an hour once none are left.

        Returns:
            The next frame; an empty string only after the idle wait.
        """
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)
        return ""


def _connector(frames: list[str]) -> ws.ConnectFn:
    """Builds a connector whose every connection replays ``frames``.

    Args:
        frames: Frames each connection delivers.

    Returns:
        The connect function.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_FrameConnection]:
        """Yields a replaying connection.

        Args:
            url: Ignored URL.

        Yields:
            A new replaying connection.
        """
        del url
        yield _FrameConnection(frames)

    return connect


def _adapter(
    venue: instruments.Venue,
    clock: clock_lib.Clock,
    frames: list[str] | None = None,
) -> ports.MarketDataPort:
    """Builds one adapter with an injected client and test Alpaca keys.

    Args:
        venue: Venue.
        clock: Clock the adapter reads.
        frames: WebSocket frames each connection replays, or None for none.

    Returns:
        The adapter the factory builds for ``venue``.
    """
    return factory.build_adapters(
        [venue],
        clock,
        crypto_source="native",
        client=httpx.AsyncClient(),
        connect=_connector(frames or []),
        alpaca_config=_ALPACA_TEST_KEYS,
    )[venue]


@pytest.mark.parametrize("venue", _ALL_REAL, ids=lambda v: v.value)
async def test_instruments_are_contract_valid(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Instrument metadata parses and validates."""
    del venue_api
    rows = await _adapter(venue, capture_clock).list_instruments()
    assert rows
    for row in rows:
        contract_validate("market.schema.json", "Instrument", row.to_contract())
        assert row.instrument_id.venue is venue
        assert row.tick_size > 0 and row.lot_size > 0


@pytest.mark.parametrize("venue", _REAL, ids=lambda v: v.value)
async def test_candles_are_aligned_and_never_final_in_progress(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Klines become UTC-aligned candles; the live bar is not final."""
    del venue_api
    adapter = _adapter(venue, capture_clock)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    tf = timeframes.Timeframe.M5
    now = capture_clock.now_ms()
    rows = await adapter.fetch_candles(
        instrument, tf, now - 1_800_000, now + 300_000
    )
    assert rows
    for candle in rows:
        contract_validate("market.schema.json", "Candle", candle.to_contract())
        assert tf.is_aligned(candle.start_ms)
        assert candle.is_final == (candle.end_ms <= now)
        assert candle.feed_state is quality.FeedState.LIVE
    assert not rows[-1].is_final


@pytest.mark.parametrize("venue", _REAL, ids=lambda v: v.value)
async def test_snapshot_book_oi_funding_are_contract_valid(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Snapshot, book, open interest and funding validate."""
    del venue_api
    adapter = _adapter(venue, capture_clock)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    snapshot = await adapter.fetch_snapshot(instrument)
    contract_validate(
        "market.schema.json",
        "MarketSnapshot",
        snapshot.to_contract(capture_clock.now_ms(), 30_000),
    )
    assert snapshot.mark is not None and snapshot.mark > 0
    book = await adapter.fetch_order_book(instrument, 10)
    contract_validate("market.schema.json", "OrderBook", book.to_contract())
    assert book.best_bid is not None and book.best_ask is not None
    assert book.best_bid < book.best_ask
    oi = await adapter.fetch_open_interest(instrument)
    contract_validate("market.schema.json", "OpenInterest", oi.to_contract())
    assert oi.notional_usd > 10_000_000
    funding = await adapter.fetch_funding(instrument)
    contract_validate("market.schema.json", "Funding", funding.to_contract())
    assert funding.interval_hours > 0


@pytest.mark.parametrize("venue", _ALL_REAL, ids=lambda v: v.value)
async def test_bulk_snapshots_cover_listed_instruments(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Bulk snapshots validate and reference listed instruments only."""
    del venue_api
    adapter = _adapter(venue, capture_clock)
    listed = {i.instrument_id for i in await adapter.list_instruments()}
    rows = await adapter.fetch_snapshots()
    assert rows
    for row in rows:
        assert row.instrument_id in listed
        contract_validate(
            "market.schema.json",
            "MarketSnapshot",
            row.to_contract(capture_clock.now_ms(), 30_000),
        )


@pytest.mark.parametrize(
    "venue", [*_ALL_REAL, instruments.Venue.MOCK], ids=lambda v: v.value
)
def test_capabilities_are_contract_valid(
    venue: instruments.Venue,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Capability declarations validate and never claim unverified data."""
    caps = _adapter(venue, capture_clock).capabilities()
    payload = caps.to_contract()
    contract_validate("market.schema.json", "VenueCapabilities", payload)
    assert caps.market_data.rest_history
    assert not caps.market_data.native_ws_book
    listed = payload["timeframes"]
    built = payload["trade_built_timeframes"]
    aggregated = payload["aggregated_timeframes"]
    assert isinstance(listed, list) and isinstance(built, list)
    assert isinstance(aggregated, list)
    assert set(built) <= set(listed)
    assert set(aggregated) <= set(listed)
    if venue in _EXCHANGE:
        assert payload["calendar"] != "24x7"
        assert not caps.market_data.taker_volume
        assert not caps.market_data.funding
        assert not caps.market_data.open_interest
        assert caps.docs_url.startswith("https://")
        return
    assert caps.trade_built_timeframes == timeframes.SECONDS
    assert caps.market_data.native_ws_trades


async def test_binance_metadata_details(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Binance filters, asset classes and statuses map correctly."""
    del venue_api
    rows = {
        str(r.instrument_id): r
        for r in await _adapter(
            instruments.Venue.BINANCE, capture_clock
        ).list_instruments()
    }
    btc = rows["binance:perp:BTCUSDT"]
    assert btc.tick_size == _D("0.10")
    assert btc.lot_size == _D("0.001")
    assert btc.min_notional == _D("50")
    assert rows["binance:perp:TSLAUSDT"].asset_class is (
        instruments.AssetClass.EQUITY
    )
    assert rows["binance:perp:XAUUSDT"].asset_class is (
        instruments.AssetClass.RWA
    )
    assert rows["binance:perp:OMGUSDT"].status is (
        instruments.InstrumentStatus.DELISTED
    )


async def test_hyperliquid_tick_rule_and_delisting(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Ticks follow the 5-significant-figure rule; delisted coins are kept."""
    del venue_api
    rows = {
        r.instrument_id.venue_market_id: r
        for r in await _adapter(
            instruments.Venue.HYPERLIQUID, capture_clock
        ).list_instruments()
    }
    assert rows["BTC"].tick_size == _D(1)
    assert rows["BTC"].lot_size == _D("0.00001")
    assert any(
        r.status is instruments.InstrumentStatus.DELISTED for r in rows.values()
    )


async def test_lighter_quantizes_float_payloads(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Float artifacts are rounded to published precision."""
    del venue_api
    adapter = _adapter(instruments.Venue.LIGHTER, capture_clock)
    instrument = instruments.InstrumentId.parse("lighter:perp:BTC")
    now = capture_clock.now_ms()
    rows = await adapter.fetch_candles(
        instrument, timeframes.Timeframe.M5, now - 900_000, now + 300_000
    )
    for candle in rows:
        assert int(candle.close.as_tuple().exponent) >= -1
        assert int(candle.base_volume.as_tuple().exponent) >= -5
    funding = await adapter.fetch_funding(instrument)
    assert funding.kind.value == "SETTLED"
    assert funding.rate == _D("0.000012")


async def test_mexc_converts_contracts_to_base_units(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Depth and volume quantities are multiplied by contractSize."""
    del venue_api
    adapter = _adapter(instruments.Venue.MEXC, capture_clock)
    instrument = instruments.InstrumentId.parse("mexc:perp:BTC_USDT")
    listed = {i.instrument_id: i for i in await adapter.list_instruments()}
    assert listed[instrument].contract_multiplier == _D("0.0001")
    book = await adapter.fetch_order_book(instrument, 5)
    raw = _fixture("mexc", "depth")["data"]["bids"][0]
    assert book.bids[0].quantity == _D(str(raw[1])) * _D("0.0001")


async def test_unsupported_timeframe_is_rejected(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Venues without UTC-aligned bars for a timeframe refuse it."""
    del venue_api
    adapter = _adapter(instruments.Venue.HYPERLIQUID, capture_clock)
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_candles(
            instruments.InstrumentId.parse("hyperliquid:perp:BTC"),
            timeframes.Timeframe.W1,
            0,
            1,
        )


async def test_lighter_has_no_native_candle_stream(
    capture_clock: clock_lib.ManualClock,
) -> None:
    """REST-only venues raise instead of faking a stream."""
    adapter = _adapter(instruments.Venue.LIGHTER, capture_clock)
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_candles(
            instruments.InstrumentId.parse("lighter:perp:BTC"),
            timeframes.Timeframe.M1,
        )


def test_binance_ws_frame_parses_with_finality(
    contract_validate: Validate,
) -> None:
    """The captured kline frame parses; ``x`` drives finality."""
    frames = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[3]
            / "fixtures/venues/binance/ws_kline_1m.json"
        ).read_text()
    )["frames"]
    instrument = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    assert (
        binance.parse_ws_kline(
            frames[0], instrument, timeframes.Timeframe.M1, 0
        )
        == []
    )
    rows = binance.parse_ws_kline(
        frames[1], instrument, timeframes.Timeframe.M1, 1_795_277_392_100
    )
    assert len(rows) == 1
    assert not rows[0].is_final
    contract_validate("market.schema.json", "Candle", rows[0].to_contract())


@pytest.mark.parametrize(
    "venue",
    [
        instruments.Venue.HYPERLIQUID,
        instruments.Venue.BITGET,
        instruments.Venue.MEXC,
    ],
    ids=lambda v: v.value,
)
async def test_ws_frames_parse_to_live_candles(
    venue: instruments.Venue,
    venue_api: respx.Router,
    contract_validate: Validate,
) -> None:
    """Captured WebSocket frames stream as non-final LIVE candles."""
    del venue_api
    frames = _fixture(venue.value, "ws_kline_1m")["frames"]
    clock = clock_lib.ManualClock(1_795_277_392_100)
    adapter = _adapter(venue, clock, frames)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    stream = adapter.stream_candles(instrument, timeframes.Timeframe.M1)
    first = await asyncio.wait_for(anext(stream), 5)
    await stream.aclose()  # type: ignore[attr-defined]  # stream_candles is typed AsyncIterator; its generator has aclose()
    contract_validate("market.schema.json", "Candle", first.to_contract())
    assert first.start_ms == 1_795_277_340_000
    assert not first.is_final
