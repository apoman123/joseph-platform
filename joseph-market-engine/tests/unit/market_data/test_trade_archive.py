"""Unit tests for the recorded-trade archive (memory and SQL) and reader."""

from __future__ import annotations

import dataclasses
import decimal
import pathlib
from collections.abc import Callable

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import trade_archive_sql
from joseph.market_data.app import trade_archive
from joseph.market_data.domain import recorded_trades
from joseph.platform import db

_TF = timeframes.Timeframe
_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("hyperliquid:perp:BTC")
_OTHER = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_T0 = 1_795_349_040_000
"""A minute boundary (2026-11-22)."""

Factory = Callable[[], trade_archive.TradeArchive]


def _second(
    start: int,
    price: str = "10",
    qty: str = "1",
    taker: str | None = "1",
    instrument: instruments.InstrumentId = _ID,
) -> market.Candle:
    """Builds an archived 1s aggregate.

    Args:
        start: Second start.
        price: Price.
        qty: Volume.
        taker: Taker buy volume.
        instrument: Instrument.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=instrument,
        timeframe=_TF.S1,
        start_ms=start,
        end_ms=start + 1_000,
        open=_D(price),
        high=_D(price) + 1,
        low=_D(price) - 1,
        close=_D(price),
        base_volume=_D(qty),
        is_final=True,
        source="hyperliquid.trades",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=start + 1_500,
        quote_turnover=_D(price) * _D(qty),
        trade_count=None if taker is None else 2,
        event_ts_ms=start + 10,
        taker_buy_base_volume=None if taker is None else _D(taker),
    )


def _kline(
    start: int, volume: str, tf: timeframes.Timeframe = _TF.M1
) -> market.Candle:
    """Builds a final kline without taker volume.

    Args:
        start: Bar start.
        volume: Base volume.
        tf: Timeframe.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=tf,
        start_ms=start,
        end_ms=tf.end_of(start),
        open=_D(10),
        high=_D(11),
        low=_D(9),
        close=_D(10),
        base_volume=_D(volume),
        is_final=True,
        source="hyperliquid.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=start,
    )


def _sql() -> trade_archive.TradeArchive:
    """Builds an in-memory SQL archive.

    Returns:
        Archive.
    """
    return trade_archive_sql.SqlTradeArchive(db.create_engine("sqlite://"))


_ARCHIVES = pytest.mark.parametrize(
    "factory",
    [trade_archive.MemoryTradeArchive, _sql],
    ids=["memory", "sql"],
)


@_ARCHIVES
def test_put_and_read_back(factory: Factory) -> None:
    """Aggregates, coverage and roll-ups round-trip exactly."""
    archive = factory()
    rows = [_second(_T0), _second(_T0 + 2_000, "11", "0.5", None)]
    archive.put(_ID, rows, [(_T0, _T0 + 3_000)])
    archive.put(_OTHER, [_second(_T0, instrument=_OTHER)], [(_T0, _T0 + 1_000)])
    assert archive.seconds(_ID, _T0, _T0 + 3_000) == rows
    assert archive.seconds(_ID, _T0 + 1, _T0 + 3_000) == rows[1:]
    assert archive.previous(_ID, _T0 + 2_000) == rows[0]
    assert archive.previous(_ID, _T0) is None
    assert archive.coverage(_ID, _T0, _T0 + 1) == [(_T0, _T0 + 3_000)]
    assert archive.coverage(_ID, _T0 + 3_000, _T0 + 9_000) == []
    assert archive.covered_until(_ID) == _T0 + 3_000
    flows = archive.minute_flows(_ID, _T0, _T0 + 60_000)
    assert flows == [recorded_trades.MinuteFlow(_T0, _D("1.5"), None)]
    assert sorted(map(str, archive.instruments())) == sorted(
        [str(_ID), str(_OTHER)]
    )
    unknown = instruments.InstrumentId.parse("lighter:perp:ETH")
    assert archive.seconds(unknown, 0, _T0) == []
    assert archive.previous(unknown, _T0) is None
    assert archive.coverage(unknown, 0, _T0) == []
    assert archive.covered_until(unknown) is None


@_ARCHIVES
def test_upsert_merges_coverage_and_recomputes_rollups(
    factory: Factory,
) -> None:
    """Rewritten seconds replace old values; touching coverage unites."""
    archive = factory()
    archive.put(_ID, [_second(_T0)], [(_T0, _T0 + 1_000)])
    archive.put(_ID, [], [(_T0 + 5_000, _T0 + 6_000)])
    archive.put(
        _ID,
        [_second(_T0, "12", "3", "2"), _second(_T0 + 1_000)],
        [(_T0 + 1_000, _T0 + 2_000)],
    )
    assert archive.coverage(_ID, 0, _T0 + 10_000) == [
        (_T0, _T0 + 2_000),
        (_T0 + 5_000, _T0 + 6_000),
    ]
    assert archive.seconds(_ID, _T0, _T0 + 1)[0].close == _D(12)
    archive.put(_ID, [], [(_T0 + 2_000, _T0 + 5_000)])
    assert archive.coverage(_ID, 0, _T0 + 10_000) == [(_T0, _T0 + 6_000)]
    assert archive.minute_flows(_ID, _T0, _T0 + 60_000) == [
        recorded_trades.MinuteFlow(_T0, _D(4), _D(3))
    ]


@_ARCHIVES
def test_retention_prunes_old_seconds_and_coverage(factory: Factory) -> None:
    """Pruning drops data before the cut-off and clips coverage."""
    archive = factory()
    rows = [_second(_T0 + i * 1_000) for i in range(5)]
    archive.put(_ID, rows, [(_T0, _T0 + 5_000)])
    archive.put(_ID, [], [(_T0 - 9_000, _T0 - 8_000)])
    deleted = archive.prune(_T0 + 2_000)
    assert deleted == 2
    assert [b.start_ms for b in archive.seconds(_ID, 0, _T0 + 9_000)] == [
        _T0 + 2_000,
        _T0 + 3_000,
        _T0 + 4_000,
    ]
    assert archive.coverage(_ID, 0, _T0 + 9_000) == [(_T0 + 2_000, _T0 + 5_000)]
    assert archive.prune(_T0 + 100_000) == 3
    assert archive.instruments() == []


def test_sql_archive_survives_a_restart(tmp_path: pathlib.Path) -> None:
    """A new engine on the same database file sees the recorded history."""
    url = f"sqlite:///{tmp_path / 'market.db'}"
    engine = db.create_engine(url)
    first = trade_archive_sql.SqlTradeArchive(engine)
    rows = [_second(_T0 + i * 1_000, str(10 + i)) for i in range(3)]
    first.put(_ID, rows, [(_T0, _T0 + 3_000)])
    engine.dispose()
    restarted = trade_archive_sql.SqlTradeArchive(db.create_engine(url))
    assert restarted.seconds(_ID, _T0, _T0 + 3_000) == rows
    assert restarted.coverage(_ID, _T0, _T0 + 3_000) == [(_T0, _T0 + 3_000)]
    reader = trade_archive.ArchiveReader(
        restarted, clock_lib.ManualClock(_T0 + 9_000)
    )
    bars = reader.seconds_bars_now(_ID, _TF.S1, _T0, _T0 + 3_000)
    assert [b.close for b in bars] == [_D(10), _D(11), _D(12)]


async def test_reader_rebuilds_seconds_bars() -> None:
    """The reader serves complete seconds buckets of any width."""
    archive = trade_archive.MemoryTradeArchive()
    archive.put(
        _ID,
        [_second(_T0), _second(_T0 + 4_000, "12")],
        [(_T0, _T0 + 10_000)],
    )
    reader = trade_archive.ArchiveReader(archive, clock_lib.ManualClock(_T0))
    s5 = await reader.seconds_bars(_ID, _TF.S5, _T0, _T0 + 10_000)
    assert [(b.start_ms - _T0, b.close) for b in s5] == [
        (0, _D(12)),
        (5_000, _D(12)),
    ]
    assert s5[1].base_volume == 0
    assert await reader.seconds_bars(_ID, _TF.S5, 0, _T0) == []


def test_reader_enriches_only_fully_covered_bars() -> None:
    """A partially covered 1m bar keeps no taker volume (coverage-gated)."""
    archive = trade_archive.MemoryTradeArchive()
    minute_two = _T0 + 60_000
    archive.put(
        _ID,
        [
            _second(_T0 + 5_000, qty="2", taker="1.5"),
            _second(minute_two + 1_000),
        ],
        [(_T0, _T0 + 60_000), (minute_two + 1_000, minute_two + 60_000)],
    )
    reader = trade_archive.ArchiveReader(
        archive,
        clock_lib.ManualClock(_T0),
        delta_venues=[instruments.Venue.HYPERLIQUID],
    )
    covered = _kline(_T0, "2")
    partial = _kline(minute_two, "1")
    live = dataclasses.replace(_kline(_T0 + 120_000, "1"), is_final=False)
    seconds = _second(_T0)
    out = reader.enrich([covered, partial, live, seconds])
    assert out[0].taker_buy_base_volume == _D("1.5")
    assert out[0].taker_buy_method is market.BuyVolumeMethod.TAKER
    assert out[1].taker_buy_base_volume is None
    assert out[2] is live
    assert out[3] is seconds
    assert reader.flow(_ID, _T0, _T0 + 60_000) == recorded_trades.RecordedFlow(
        _D(2), _D("1.5")
    )
    # Cached: still answered after the data is pruned.
    archive.prune(_T0 + 10**9)
    assert reader.enrich([covered])[0].taker_buy_base_volume == _D("1.5")
    other = trade_archive.ArchiveReader(archive, clock_lib.ManualClock(_T0))
    assert other.enrich([covered])[0] is covered


def test_reader_flow_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flow cache forgets the least recently used spans."""
    monkeypatch.setattr(trade_archive, "_FLOW_CACHE", 2)
    archive = trade_archive.MemoryTradeArchive()
    archive.put(_ID, [], [(_T0, _T0 + 180_000)])
    reader = trade_archive.ArchiveReader(archive, clock_lib.ManualClock(_T0))
    for i in range(3):
        start = _T0 + i * 60_000
        assert reader.flow(_ID, start, start + 60_000) is not None
    assert len(reader._flows) == 2
    assert reader.flow(_ID, 0, 60_000) is None
