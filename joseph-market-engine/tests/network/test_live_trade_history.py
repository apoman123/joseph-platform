# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in live tests: archives, aggregated timeframes and recorded delta.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
Requests stay small: one ~0.3 MB archive day of a quiet Binance contract,
the needed column chunks of one Lighter archive day of BTC (about 15-20 MB
of a 150-300 MB file, by HTTP range requests), a few kline pages and a few
trade-history pages; the stream test records Hyperliquid and MEXC trades
for about 75 seconds.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import pathlib

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import binance_vision
from joseph.market_data.adapters import bitget_archive
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import lighter_archive
from joseph.market_data.app import derived_bars
from joseph.market_data.app import ports
from joseph.market_data.app import trade_archive
from joseph.market_data.app import trade_recorder
from joseph.market_data.domain import aggregation
from joseph.market_data.domain import recorded_trades

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]

_TF = timeframes.Timeframe
_DAY = 86_400_000
_QUIET = instruments.InstrumentId.parse("binance:perp:BTCDOMUSDT")
_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}


def _adapter(venue: instruments.Venue) -> ports.MarketDataPort:
    """Builds a live raw adapter.

    Args:
        venue: Venue.

    Returns:
        The adapter, without derived bars or recorded delta.
    """
    return factory.build_adapters(
        [venue], clock_lib.SystemClock(), client=httpx.AsyncClient(timeout=30)
    )[venue]


async def test_live_binance_archive_day_equals_its_daily_kline(
    tmp_path: pathlib.Path,
) -> None:
    """A verified archive day folds to exactly the venue's 1d kline."""
    vision = binance_vision.BinanceVision(tmp_path)
    today = datetime.datetime.now(datetime.UTC).date()
    assert await vision.daily_zip("BTCDOMUSDT", today) is None
    day = today - datetime.timedelta(days=1)
    path = await vision.daily_zip("BTCDOMUSDT", day)
    if path is None:
        day -= datetime.timedelta(days=1)
        path = await vision.daily_zip("BTCDOMUSDT", day)
    assert path is not None
    archive = trade_archive.MemoryTradeArchive()

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Writes one import batch into the memory archive.

        Args:
            rows: 1s aggregates.
            covered: Intervals the rows prove complete.
        """
        archive.put(_QUIET, rows, covered)

    assert await vision.import_day(_QUIET, day, sink, 0)
    start = binance_vision.day_start_ms(day)
    seconds = archive.seconds(_QUIET, start, start + _DAY)
    adapter = _adapter(instruments.Venue.BINANCE)
    (kline,) = await adapter.fetch_candles(_QUIET, _TF.D1, start, start + 1)
    await adapter.close()
    await vision.close()
    folded = aggregation.fold(
        seconds,
        timeframe=_TF.D1,
        start_ms=start,
        end_ms=start + _DAY,
        is_final=True,
        source="x",
    )
    assert (folded.open, folded.high, folded.low, folded.close) == (
        kline.open,
        kline.high,
        kline.low,
        kline.close,
    )
    assert folded.base_volume == kline.base_volume
    assert folded.quote_turnover == kline.quote_turnover
    assert folded.taker_buy_base_volume == kline.taker_buy_base_volume
    assert archive.coverage(_QUIET, 0, start + 2 * _DAY) == [
        (start, start + _DAY)
    ]


async def test_live_binance_archive_rows_equal_rest_agg_trades(
    tmp_path: pathlib.Path,
) -> None:
    """Archive rows equal REST aggTrades for the same ids (``m`` flag)."""
    vision = binance_vision.BinanceVision(tmp_path)
    today = datetime.datetime.now(datetime.UTC).date()
    path = None
    for back in (1, 2):
        path = await vision.daily_zip(
            "BTCDOMUSDT", today - datetime.timedelta(days=back)
        )
        if path is not None:
            break
    assert path is not None
    with vision.open_lines(path) as lines:
        tail = list(binance_vision.parse_rows(lines, _QUIET, 0))[-5:]
    await vision.close()
    async with httpx.AsyncClient(timeout=30) as client:
        rows = (
            await client.get(
                "https://fapi.binance.com/fapi/v1/aggTrades",
                params={
                    "symbol": "BTCDOMUSDT",
                    "fromId": tail[0].trade_id,
                    "limit": 5,
                },
            )
        ).json()
    assert [str(r["a"]) for r in rows] == [t.trade_id for t in tail]
    for row, trade in zip(rows, tail, strict=True):
        assert int(row["T"]) == trade.venue_event_ts_ms
        assert (trade.aggressor_side is market.AggressorSide.SELL) is row["m"]


async def test_live_bitget_archive_day_is_verified_and_matches_rest(
    tmp_path: pathlib.Path,
) -> None:
    """A Bitget archive day folds to its 1D kline and equals fills-history."""
    quiet = instruments.InstrumentId.parse("bitget:perp:XTZUSDT")
    archive = bitget_archive.BitgetArchive(tmp_path)
    now = clock_lib.SystemClock().now_ms()
    today = bitget_archive.day_of(now)
    store = trade_archive.MemoryTradeArchive()

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Writes one import batch into the memory archive.

        Args:
            rows: 1s aggregates.
            covered: Intervals the rows prove complete.
        """
        store.put(quiet, rows, covered)

    day = today - datetime.timedelta(days=2)
    assert await archive.import_day(quiet, day, sink, now)
    start, end = bitget_archive.day_bounds(day)
    assert store.coverage(quiet, 0, end + _DAY) == [(start, end)]
    paths = await archive.day_files("XTZUSDT", day)
    assert paths is not None
    first = next(bitget_archive.BitgetArchive._trades(paths[:1], quiet, 0))
    raw = _adapter(instruments.Venue.BITGET)
    assert isinstance(raw, base.BaseVenueAdapter)
    coverage = await raw.fetch_trade_coverage(
        quiet, first.venue_event_ts_ms, first.venue_event_ts_ms + 1_000
    )
    await raw.close()
    await archive.close()
    rest = {t.trade_id: t for t in coverage.trades}
    match = rest[first.trade_id]
    assert match.aggressor_side is first.aggressor_side
    assert match.base_quantity == first.base_quantity
    assert match.price == first.price
    assert match.venue_event_ts_ms // 1000 * 1000 == first.venue_event_ts_ms


async def test_live_lighter_archive_day_folds_to_its_daily_candle() -> None:
    """A Lighter archive day read by ranges reproduces Lighter's own bars.

    The day's 1s aggregates fold to the venue's 1d candle (which opens at
    the previous close), the day's 1m candles all get a recorded true
    delta, and the transfer stays a small part of the day's file.
    """
    clock = clock_lib.SystemClock()
    btc = instruments.InstrumentId.parse(_BTC[instruments.Venue.LIGHTER])
    archive = lighter_archive.LighterArchive()
    store = trade_archive.MemoryTradeArchive()

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Writes one import batch into the memory archive.

        Args:
            rows: 1s aggregates.
            covered: Intervals the rows prove complete.
        """
        store.put(btc, rows, covered)

    now = clock.now_ms()
    day = lighter_archive.day_of(now) - datetime.timedelta(days=1)
    if not await archive.import_day(btc, day, sink, now):
        day -= datetime.timedelta(days=1)
        assert await archive.import_day(btc, day, sink, now)
    transferred, requests = archive.bytes_received, archive.requests
    remote = await archive.open_file(day)
    await archive.close()
    assert remote is not None
    start, end = lighter_archive.day_bounds(day)
    assert store.coverage(btc, 0, end + _DAY) == [(start, end)]
    adapter = _adapter(instruments.Venue.LIGHTER)
    (kline,) = await adapter.fetch_candles(btc, _TF.D1, start, end)
    minutes = await adapter.fetch_candles(btc, _TF.M1, start, start + 3_600_000)
    await adapter.close()
    folded = aggregation.fold(
        store.seconds(btc, start, end),
        timeframe=_TF.D1,
        start_ms=start,
        end_ms=end,
        is_final=True,
        source="x",
    )
    assert folded.close == kline.close
    assert max(folded.high, kline.open) == kline.high
    assert min(folded.low, kline.open) == kline.low
    assert folded.base_volume == kline.base_volume
    assert folded.quote_turnover is not None
    assert kline.quote_turnover is not None
    assert recorded_trades.volumes_agree(
        folded.quote_turnover, kline.quote_turnover
    )
    reader = trade_archive.ArchiveReader(
        store, clock, delta_venues=[instruments.Venue.LIGHTER]
    )
    enriched = reader.enrich(minutes)
    assert len(enriched) == 60
    assert all(
        m.taker_buy_method is market.BuyVolumeMethod.TAKER
        for m in enriched
        if m.base_volume > 0
    )
    print(  # noqa: T201 - the opt-in live run reports how much it downloaded
        f"lighter archive {day}: {transferred} of {remote.size} bytes in "
        f"{requests} requests"
    )
    assert transferred < remote.size // 5


async def test_live_binance_aggregated_month_equals_native() -> None:
    """Live 1d klines fold to the live native 1M kline of a closed month."""
    adapter = _adapter(instruments.Venue.BINANCE)
    btc = instruments.InstrumentId.parse(_BTC[instruments.Venue.BINANCE])
    now = clock_lib.SystemClock().now_ms()
    month = _TF.MN1.shift(_TF.MN1.floor(now), -1)
    days = await adapter.fetch_candles(
        btc, _TF.D1, month, _TF.MN1.end_of(month)
    )
    (native,) = await adapter.fetch_candles(btc, _TF.MN1, month, month + 1)
    await adapter.close()
    (folded,) = aggregation.aggregate(
        days,
        calendar=sessions.for_venue(instruments.Venue.BINANCE),
        target=_TF.MN1,
        child=_TF.D1,
        now_ms=now,
    )
    assert folded.is_final and native.is_final
    assert (folded.open, folded.high, folded.low, folded.close) == (
        native.open,
        native.high,
        native.low,
        native.close,
    )
    assert folded.base_volume == native.base_volume
    assert folded.quote_turnover == native.quote_turnover
    assert folded.trade_count == native.trade_count
    assert folded.taker_buy_base_volume == native.taker_buy_base_volume


@pytest.mark.parametrize(
    "venue",
    [instruments.Venue.HYPERLIQUID, instruments.Venue.LIGHTER],
    ids=lambda v: v.value,
)
async def test_live_aggregated_calendar_months(
    venue: instruments.Venue,
) -> None:
    """Hyperliquid and Lighter serve calendar-month 1M bars from 1d."""
    clock = clock_lib.SystemClock()
    adapter = derived_bars.DerivedBarsAdapter(_adapter(venue), clock)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    assert _TF.MN1 in adapter.capabilities().aggregated_timeframes
    now = clock.now_ms()
    rows = await adapter.fetch_candles(
        instrument, _TF.MN1, now - 100 * _DAY, now + 1
    )
    await adapter.close()
    assert len(rows) >= 3
    for candle in rows:
        start = datetime.datetime.fromtimestamp(
            candle.start_ms / 1000, datetime.UTC
        )
        assert (start.day, start.hour) == (1, 0)
        assert candle.source.endswith(".agg1d")
    assert [c.end_ms for c in rows[:-1]] == [c.start_ms for c in rows[1:]]
    assert not rows[-1].is_final
    assert all(c.is_final for c in rows[:-1])


@pytest.mark.parametrize(
    "venue",
    [instruments.Venue.BITGET, instruments.Venue.LIGHTER],
    ids=lambda v: v.value,
)
async def test_live_trade_history_gives_true_delta(
    venue: instruments.Venue,
) -> None:
    """A closed minute covered by REST trades gets a true taker delta."""
    clock = clock_lib.SystemClock()
    raw = _adapter(venue)
    assert isinstance(raw, base.BaseVenueAdapter)
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    minute = _TF.M1.shift(_TF.M1.floor(clock.now_ms()), -2)
    end = minute + 60_000
    coverage = await raw.fetch_trade_coverage(
        instrument, minute - 1_000, end, max_pages=20
    )
    archive = trade_archive.MemoryTradeArchive()
    rows, window = recorded_trades.archivable_seconds(
        coverage, instrument, source="live", now_ms=clock.now_ms()
    )
    assert window is not None and window[0] <= minute
    archive.put(instrument, rows, [window])
    (kline,) = await raw.fetch_candles(instrument, _TF.M1, minute, end)
    await raw.close()
    reader = trade_archive.ArchiveReader(archive, clock, delta_venues=[venue])
    (enriched,) = reader.enrich([kline])
    assert enriched.taker_buy_method is market.BuyVolumeMethod.TAKER
    assert enriched.taker_buy_base_volume is not None
    assert 0 < enriched.taker_buy_base_volume < kline.base_volume


async def test_live_stream_recording_gives_true_delta() -> None:
    """Hyperliquid and MEXC minutes recorded live carry a true delta.

    Two closed minutes are checked per venue: at least one must carry the
    recorded delta, and a minute without one must be explained by the gate
    (incomplete coverage or a volume mismatch), never silently dropped.
    """
    clock = clock_lib.SystemClock()
    venues = [instruments.Venue.HYPERLIQUID, instruments.Venue.MEXC]
    raws = {venue: _adapter(venue) for venue in venues}
    recorder = trade_recorder.TradeRecorder(
        raws, trade_archive.MemoryTradeArchive(), clock
    )
    ids = [instruments.InstrumentId.parse(_BTC[v]) for v in venues]
    recorder.start()
    for instrument in ids:
        assert recorder.touch(instrument)
    first = _TF.M1.floor(clock.now_ms()) + 60_000
    minutes = [first, first + 60_000]
    await asyncio.sleep((minutes[-1] + 63_000 - clock.now_ms()) / 1000)
    try:
        for venue, instrument in zip(venues, ids, strict=True):
            await recorder.wait_recorded(instrument, minutes[-1] + 60_000, 5)
            klines = await raws[venue].fetch_candles(
                instrument, _TF.M1, minutes[0], minutes[-1] + 60_000
            )
            assert [k.start_ms for k in klines] == minutes
            enriched = recorder.enrich(klines)
            with_delta = [
                k
                for k in enriched
                if k.taker_buy_method is market.BuyVolumeMethod.TAKER
            ]
            assert with_delta, (
                venue,
                recorder.archive.coverage(instrument, 0, 2 * first),
            )
            for kline, out in zip(klines, enriched, strict=True):
                flow = recorder.reader.flow(
                    instrument, kline.start_ms, kline.end_ms
                )
                if out.taker_buy_base_volume is None:
                    assert flow is None or not recorded_trades.volumes_agree(
                        flow.base_volume, kline.base_volume
                    )
                else:
                    assert flow is not None
                    assert flow.base_volume == kline.base_volume
                    assert out.taker_buy_base_volume <= kline.base_volume
    finally:
        await recorder.stop()
        for adapter in raws.values():
            await adapter.close()
