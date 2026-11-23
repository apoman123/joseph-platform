"""Contract tests: recording captured trade frames through real adapters.

Each venue's captured trade stream (``fixtures/venues/*/ws_*``) is replayed
through the real adapter, ``stream_trade_events`` and the ``TradeRecorder``.
The recorded taker buy volume must equal the buyer-aggressor quantity read
straight from the raw frames with each venue's documented side field:
Binance ``m`` false, Bitget ``side`` ``buy``, Hyperliquid ``side`` ``B``,
Lighter ``is_maker_ask`` true, MEXC ``T`` 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
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
from joseph.market_data.adapters import base
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import ws
from joseph.market_data.app import trade_archive
from joseph.market_data.app import trade_recorder
from joseph.market_data.domain import recorded_trades

_D = decimal.Decimal
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}
_FRAMES = {
    instruments.Venue.BINANCE: ("binance", "ws_agg_trade"),
    instruments.Venue.HYPERLIQUID: ("hyperliquid", "ws_trades"),
    instruments.Venue.LIGHTER: ("lighter", "ws_trade"),
    instruments.Venue.BITGET: ("bitget", "ws_trade"),
    instruments.Venue.MEXC: ("mexc", "ws_deal"),
}
_MEXC_CONTRACT = _D("0.0001")

RawTrade = tuple[int, decimal.Decimal, bool]


def _capture(venue: instruments.Venue) -> dict[str, Any]:
    """Loads a captured trade stream.

    Args:
        venue: Venue.

    Returns:
        Capture with ``frames`` and ``captured_at_ms``.
    """
    folder, name = _FRAMES[venue]
    capture: dict[str, Any] = json.loads(
        (_FIXTURES / folder / f"{name}.json").read_text()
    )
    return capture


def _raw_trades(venue: instruments.Venue, frames: list[str]) -> list[RawTrade]:
    """Reads ``(time, quantity, buyer aggressor)`` from raw frames.

    Args:
        venue: Venue.
        frames: Captured frames.

    Returns:
        Trades by the venue's documented side field.
    """
    out: list[RawTrade] = []
    for frame in frames:
        if frame in ("pong", "ping"):
            continue
        message = json.loads(frame)
        if not isinstance(message, dict):
            continue
        if venue is instruments.Venue.BINANCE:
            if message.get("e") == "aggTrade":
                out.append(
                    (int(message["T"]), _D(message["q"]), not message["m"])
                )
        elif venue is instruments.Venue.BITGET:
            if message.get("arg", {}).get("channel") == "trade":
                out.extend(
                    (int(r["ts"]), _D(r["size"]), r["side"] == "buy")
                    for r in message.get("data", [])
                )
        elif venue is instruments.Venue.HYPERLIQUID:
            if message.get("channel") == "trades":
                out.extend(
                    (int(r["time"]), _D(r["sz"]), r["side"] == "B")
                    for r in message["data"]
                    if r.get("coin") == "BTC"
                )
        elif venue is instruments.Venue.LIGHTER:
            if message.get("type") in ("subscribed/trade", "update/trade"):
                out.extend(
                    (
                        int(r["timestamp"]),
                        _D(r["size"]),
                        bool(r["is_maker_ask"]),
                    )
                    for r in message.get("trades") or []
                    if r.get("type", "trade") in ("trade", "liquidation")
                )
        elif message.get("channel") == "push.deal":
            data = message["data"]
            rows = data if isinstance(data, list) else [data]
            out.extend(
                (int(r["t"]), _D(str(r["v"])) * _MEXC_CONTRACT, r["T"] == 1)
                for r in rows
            )
    return out


class _Replay:
    """WebSocket double replaying frames, then idling."""

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
        await asyncio.sleep(3_600)
        return ""


def _connect(frames: list[str]) -> Callable[[str], Any]:
    """Builds a connector replaying ``frames``.

    Args:
        frames: Frames.

    Returns:
        Connect function.
    """

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Replay]:
        """Yields a replaying connection.

        Args:
            url: Ignored.

        Yields:
            Connection.
        """
        del url
        yield _Replay(frames)

    return connect


@pytest.mark.parametrize("venue", list(_BTC), ids=lambda v: v.value)
async def test_recorded_taker_volume_follows_the_venue_side_field(
    venue: instruments.Venue, venue_api: respx.Router
) -> None:
    """Archived 1s taker volume equals the raw frames' buyer quantity."""
    del venue_api
    capture = _capture(venue)
    frames = [*capture["frames"], *capture.get("object_frames", [])]
    raw = _raw_trades(venue, frames)
    assert raw
    clock = clock_lib.ManualClock(max(t for t, _, _ in raw) + 10_000)
    adapter = factory.build_adapters(
        [venue],
        clock,
        client=httpx.AsyncClient(),
        connect=_connect(frames),  # type: ignore[arg-type]
    )[venue]
    recorder = trade_recorder.TradeRecorder(
        {venue: adapter},
        trade_archive.MemoryTradeArchive(),
        clock,
        trade_recorder.RecorderSettings(backfill_max_calls=0),
    )
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    recorder.start()
    assert recorder.touch(instrument)
    for _ in range(200):
        await asyncio.sleep(0.005)
        if recorder.archive.coverage(instrument, 0, clock.now_ms()):
            break
    await recorder.stop()
    await adapter.close()
    coverage = recorder.archive.coverage(instrument, 0, clock.now_ms())
    assert len(coverage) == 1
    low, high = coverage[0]
    first = min(t for t, _, _ in raw)
    assert low == recorded_trades.ceil_to(first + 1, 1_000)
    inside = [r for r in raw if low <= r[0] < high]
    rows = recorder.archive.seconds(instrument, low, high)
    assert sum(r.base_volume for r in rows) == sum(q for _, q, _ in inside)
    assert sum(r.taker_buy_base_volume or 0 for r in rows) == sum(
        q for _, q, buy in inside if buy
    )
    assert {r.source for r in rows} == {f"{venue.value}.trades"}
    assert recorder.delta_venues == (
        frozenset()
        if venue is instruments.Venue.BINANCE
        else frozenset({venue})
    )


@pytest.mark.parametrize(
    "venue",
    [
        instruments.Venue.BINANCE,
        instruments.Venue.BITGET,
        instruments.Venue.LIGHTER,
    ],
    ids=lambda v: v.value,
)
async def test_trade_coverage_states_what_it_covers(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
) -> None:
    """REST trade history comes back with its complete interval."""
    del venue_api
    clock = clock_lib.ManualClock(1_795_349_300_000)
    del capture_clock
    adapter = factory.build_adapters(
        [venue], clock, client=httpx.AsyncClient()
    )[venue]
    assert isinstance(adapter, base.BaseVenueAdapter)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    now = clock.now_ms()
    coverage = await adapter.fetch_trade_coverage(
        instrument, now - 600_000, now
    )
    assert coverage.to_ms == now
    assert coverage.from_ms >= now - 600_000
    assert all(
        coverage.from_ms <= t.venue_event_ts_ms < coverage.to_ms
        for t in coverage.trades
    )
    empty = await adapter.fetch_trade_coverage(instrument, now, now)
    assert empty.is_empty
    await adapter.close()


@pytest.mark.parametrize(
    "venue",
    [instruments.Venue.HYPERLIQUID, instruments.Venue.MEXC],
    ids=lambda v: v.value,
)
async def test_venues_without_trade_history_refuse_coverage(
    venue: instruments.Venue, capture_clock: clock_lib.ManualClock
) -> None:
    """Hyperliquid and MEXC publish no time-range trade history."""
    adapter = factory.build_adapters(
        [venue], capture_clock, client=httpx.AsyncClient()
    )[venue]
    assert isinstance(adapter, base.BaseVenueAdapter)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_trade_coverage(instrument, 0, 1)
    await adapter.close()


def test_trade_events_need_a_native_trade_stream(
    capture_clock: clock_lib.ManualClock,
) -> None:
    """Adapters without a wired trade stream refuse trade events."""
    adapter = factory.build_adapters(
        [instruments.Venue.LIGHTER], capture_clock, client=httpx.AsyncClient()
    )[instruments.Venue.LIGHTER]
    assert isinstance(adapter, base.BaseVenueAdapter)
    caps = adapter.capabilities()
    quiet = dataclasses.replace(
        caps,
        market_data=dataclasses.replace(
            caps.market_data, native_ws_trades=False
        ),
    )
    adapter.capabilities = lambda: quiet  # type: ignore[method-assign]
    with pytest.raises(errors.UnsupportedError):
        adapter.stream_trade_events(
            instruments.InstrumentId.parse(_BTC[instruments.Venue.LIGHTER])
        )


async def test_primed_trade_events_start_coverage_at_the_prime_point(
    venue_api: respx.Router,
) -> None:
    """A primed Bitget stream proves coverage from the prime point."""
    del venue_api
    capture = _capture(instruments.Venue.BITGET)
    clock = clock_lib.ManualClock(1_795_349_300_000)
    adapter = factory.build_adapters(
        [instruments.Venue.BITGET],
        clock,
        client=httpx.AsyncClient(),
        connect=_connect(capture["frames"]),  # type: ignore[arg-type]
    )[instruments.Venue.BITGET]
    assert isinstance(adapter, base.BaseVenueAdapter)
    instrument = instruments.InstrumentId.parse(_BTC[instruments.Venue.BITGET])
    events = adapter.stream_trade_events(
        instrument, prime_from_ms=clock.now_ms() - 1_000
    )
    first = await asyncio.wait_for(anext(events), 5)
    await events.aclose()  # type: ignore[attr-defined]
    await adapter.close()
    assert first == ws.CoverageStart(clock.now_ms() - 1_000)
