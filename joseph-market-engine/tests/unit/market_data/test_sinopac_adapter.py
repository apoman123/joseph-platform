# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests: TWSE and TPEx adapters on the Shioaji source (fake SDK)."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import pathlib
import zoneinfo
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import bar_archive_sql
from joseph.market_data.adapters import sinopac
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import twse
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.domain import health
from joseph.market_data.domain import snapshot_bars
from joseph.market_data.testing import fake_shioaji
from joseph.platform import db

_D = decimal.Decimal
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_DAY = datetime.date(2026, 11, 23)
_PREV = datetime.date(2026, 11, 20)
_ID = instruments.InstrumentId.parse("twse:stock:2330")
_OTC = instruments.InstrumentId.parse("tpex:stock:6488")
_M1 = timeframes.Timeframe.M1
_M5 = timeframes.Timeframe.M5
_S1 = timeframes.Timeframe.S1


def _local(day: datetime.date, *hms: int) -> datetime.datetime:
    """Builds a naive Taipei time.

    Args:
        day: Date.
        *hms: Hour, minute, second and microsecond.

    Returns:
        Naive datetime.
    """
    stamp = datetime.datetime(day.year, day.month, day.day, *hms, tzinfo=_TPE)
    return stamp.replace(tzinfo=None)  # Shioaji streams naive Taipei time.


def _ms(day: datetime.date, *hms: int) -> int:
    """Returns the UTC epoch ms of a Taipei time.

    Args:
        day: Date.
        *hms: Hour, minute, second and microsecond.

    Returns:
        Milliseconds.
    """
    return int(_local(day, *hms).replace(tzinfo=_TPE).timestamp() * 1000)


def _load(venue: str, name: str) -> Any:
    """Reads a venue fixture.

    Args:
        venue: ``twse`` or ``tpex``.
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / venue / f"{name}.json").read_text("utf-8"))


class _Http:
    """Scripted ``VenueHttp`` stand-in.

    Attributes:
        answers: Response (or callable) per path.
        calls: Requested paths.
    """

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        """Initializes the stub.

        Args:
            answers: Response per path.
        """
        self.answers = answers or {}
        self.calls: list[str] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Returns the scripted answer of a path.

        Args:
            path: Path.
            params: Query parameters.

        Returns:
            Scripted body.

        Raises:
            errors.VenueUnavailableError: For unscripted paths.
        """
        self.calls.append(path)
        answer = self.answers.get(path)
        if answer is None:
            raise errors.VenueUnavailableError(f"unscripted {path}")
        return answer(params) if callable(answer) else answer

    async def close(self) -> None:
        """Closes nothing."""


async def _inline(fn: Callable[[], Any]) -> Any:
    """Runs an SDK call on the loop.

    Args:
        fn: Call.

    Returns:
        Its result.
    """
    return fn()


async def _blocked(seconds: float) -> None:
    """Parks the MIS recorder (tests drive it by hand).

    Args:
        seconds: Ignored.
    """
    del seconds
    await asyncio.Event().wait()


def _mis(clock: clock_lib.Clock) -> dict[str, Any]:
    """Scripts MIS answers whose volume grows with the clock.

    Args:
        clock: Clock.

    Returns:
        Answers per path.
    """

    def answer(params: dict[str, Any] | None) -> dict[str, Any]:
        """Returns one 2330 record.

        Args:
            params: Query.

        Returns:
            Body.
        """
        del params
        now = clock.now_ms()
        return {
            "rtcode": "0000",
            "msgArray": [
                {
                    "c": "2330",
                    "tlong": str(now),
                    "d": twse.taipei_date(now).strftime("%Y%m%d"),
                    "z": "100.0000",
                    "y": "99.0000",
                    "v": str(1 + (now // 1000) % 1000),
                    "o": "99.5",
                    "h": "100.5",
                    "l": "99.0",
                    "a": "100.5000_101.0000_",
                    "f": "1_2_",
                    "b": "100.0000_",
                    "g": "2_",
                }
            ],
        }

    return {"/stock/api/getStockInfo.jsp": answer}


def _build(
    clock: clock_lib.ManualClock,
    fake: fake_shioaji.FakeShioaji | None = None,
    *,
    archive: bar_archive_sql.SqlBarArchive | None = None,
    venue: str = "twse",
    keys: bool = True,
    **config: Any,
) -> tuple[twse.TaiwanExchangeAdapter, fake_shioaji.FakeShioaji]:
    """Builds a TWSE or TPEx adapter with a Shioaji source.

    Args:
        clock: Clock.
        fake: Fake SDK (a fresh one by default).
        archive: Optional bar archive.
        venue: ``twse`` or ``tpex``.
        keys: Whether credentials are configured.
        **config: ``SinopacConfig`` overrides.

    Returns:
        ``(adapter, fake)``.
    """
    api = fake or fake_shioaji.FakeShioaji()
    source = sinopac.SinopacSource(
        sinopac.SinopacConfig(
            api_key="key-123456" if keys else None,
            secret_key="secret-7890" if keys else None,
            **config,
        ),
        clock,
        factory=lambda: fake_shioaji.FakeShioajiFactory(api),
        runner=_inline,
    )
    www = {
        "/rwd/zh/afterTrading/STOCK_DAY": _load("twse", "stock_day_2330"),
    }
    cls = twse.TwseAdapter if venue == "twse" else tpex.TpexAdapter
    adapter = cls(
        _Http(),  # type: ignore[arg-type]
        _Http(www),  # type: ignore[arg-type]
        _Http(_mis(clock)),  # type: ignore[arg-type]
        clock,
        sleep=_blocked,
        sinopac=source,
        bar_archive=archive,
    )
    now = clock.now_ms()
    if venue == "twse":
        rows, meta = twse.parse_instruments(
            _load("twse", "stock_day_all"),
            _load("twse", "t187ap03_L"),
            _load("twse", "t187ap47_L"),
            _load("twse", "twt88u"),
            _load("twse", "twtawu"),
            now_ms=now,
        )
    else:
        rows, meta = tpex.parse_instruments(
            _load("tpex", "mainboard_quotes"),
            _load("tpex", "t187ap03_O"),
            _load("tpex", "ipo_no_limit"),
            _load("tpex", "cmode"),
            now_ms=now,
        )
    adapter._instruments = {r.instrument_id.venue_market_id: r for r in rows}
    adapter._loaded_at_ms = now
    adapter.listing = meta
    return adapter, api


def _archive() -> bar_archive_sql.SqlBarArchive:
    """Creates an in-memory SQLite bar archive.

    Returns:
        Archive.
    """
    return bar_archive_sql.SqlBarArchive(db.create_engine("sqlite://"))


def _kbar(
    day: datetime.date, hour: int, minute: int, close: float, lots: int
) -> tuple[Any, ...]:
    """Builds a kbar row labeled by its minute end.

    Args:
        day: Date.
        hour: Label hour.
        minute: Label minute.
        close: Close (open/high/low around it).
        lots: Volume in lots.

    Returns:
        Row.
    """
    return (
        _local(day, hour, minute),
        close - 0.5,
        close + 0.5,
        close - 1.0,
        close,
        lots,
        close * lots * 1000,
    )


def _tick(
    at: datetime.datetime, price: str, lots: int, total: int, kind: int = 1
) -> fake_shioaji.FakeTick:
    """Builds a stream tick of 2330.

    Args:
        at: Naive Taipei time.
        price: Price.
        lots: Lots.
        total: Day lots through the tick.
        kind: tick_type.

    Returns:
        Tick.
    """
    return fake_shioaji.FakeTick(
        "2330",
        at,
        price,
        lots,
        total,
        kind,
        open="99",
        high="101",
        low="98",
        price_chg="1",
    )


def _book(at: datetime.datetime) -> fake_shioaji.FakeBidAsk:
    """Builds a 2330 book update.

    Args:
        at: Naive Taipei time.

    Returns:
        Update.
    """
    return fake_shioaji.FakeBidAsk(
        "2330", at, ["100", "99.5"], [3, 4], ["100.5", "101"], [2, 1]
    )


async def _settle() -> None:
    """Lets callbacks hop onto the loop and streams run."""
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _restore_xtai() -> Iterator[None]:
    """Restores runtime XTAI closures after each test.

    Yields:
        Nothing.
    """
    saved = set(sessions.XTAI._extra_closures)
    yield
    sessions.XTAI._extra_closures.clear()
    sessions.XTAI.add_closures(saved)


async def test_capabilities_add_seconds_trades_and_quota_notes() -> None:
    """With Shioaji the venue offers seconds bars and trades, honestly."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    adapter, _ = _build(clock)
    caps = adapter.capabilities()
    assert set(timeframes.SECONDS) <= set(caps.timeframes)
    assert caps.trade_built_timeframes == timeframes.SECONDS
    md = caps.market_data
    assert md.trades_history and md.native_ws_trades and md.native_ws_candles
    assert md.classified_delta and not md.taker_volume
    assert caps.data_delay_ms is None
    assert "SinoPac" in caps.display_name
    text = " ".join(caps.notes)
    for needle in ("classified", "270 kbar", "500 MB", "08:00", "Fallback"):
        assert needle in text
    assert twse.MIS_SECONDS_NOTE not in caps.notes
    assert caps.notes[-1].startswith("Shioaji status: available")
    otc, _ = _build(clock, venue="tpex")
    assert otc.SJ_EXCHANGE == "OTC"
    assert "SinoPac" in otc.capabilities().display_name
    off, _ = _build(clock, keys=False)
    plain = off.capabilities()
    assert timeframes.Timeframe.S1 not in plain.timeframes
    assert (
        "disabled" in plain.notes[-1] and twse.MIS_SECONDS_NOTE in plain.notes
    )
    with pytest.raises(errors.UnsupportedError):
        await off.fetch_candle_coverage(_ID, _S1, 0, 1)
    with pytest.raises(errors.UnsupportedError):
        await off._seconds_history(_ID, _S1, 0, 1, None)
    with pytest.raises(errors.UnsupportedError):
        off.stream_candle_coverage(_ID, _M1)
    with pytest.raises(errors.UnsupportedError):
        off.stream_trades(_ID)
    with pytest.raises(errors.UnsupportedError):
        await off.fetch_trades(_ID, 0, 1)
    for item in (adapter, otc, off):
        await item.close()


async def test_closed_sessions_come_from_kbars_and_survive_restarts() -> None:
    """Kbars serve closed sessions once; the archive spares later queries."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    archive = _archive()
    adapter, fake = _build(clock, archive=archive)
    fake.kbars_rows[("2330", _PREV)] = [
        _kbar(_PREV, 9, 1, 100.0, 10),
        _kbar(_PREV, 9, 2, 100.5, 4),
        _kbar(_PREV, 9, 6, 101.0, 2),
        _kbar(_PREV, 13, 30, 101.5, 30),
    ]
    prev = sessions.XTAI.session(_PREV)
    assert prev is not None
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, prev.open_ms, prev.close_ms
    )
    assert [c.start_ms for c in got.candles] == [
        _ms(_PREV, 9, 0),
        _ms(_PREV, 9, 1),
        _ms(_PREV, 9, 5),
        _ms(_PREV, 13, 29),
    ]
    assert got.covered == ((prev.open_ms, prev.close_ms),)
    assert got.complete and all(c.is_final for c in got.candles)
    assert {c.source for c in got.candles} == {sinopac.KBAR_SOURCE}
    five = await adapter.fetch_candle_coverage(
        _ID, _M5, prev.open_ms, prev.close_ms
    )
    assert [(c.start_ms, c.base_volume) for c in five.candles] == [
        (_ms(_PREV, 9, 0), _D(14_000)),
        (_ms(_PREV, 9, 5), _D(2_000)),
        (_ms(_PREV, 13, 25), _D(30_000)),
    ]
    newest = await adapter.fetch_candles(
        _ID, _M1, prev.open_ms, prev.close_ms, limit=2
    )
    assert [c.start_ms for c in newest] == [
        _ms(_PREV, 9, 5),
        _ms(_PREV, 13, 29),
    ]
    assert sum(1 for c in fake.calls if c.startswith("kbars")) == 1
    restarted, other = _build(clock, archive=archive)
    again = await restarted.fetch_candle_coverage(
        _ID, _M1, prev.open_ms, prev.close_ms
    )
    assert again.candles == got.candles
    assert again.covered == got.covered
    assert not any(c.startswith("kbars") for c in other.calls)
    await adapter.close()
    await restarted.close()


async def test_seconds_bars_and_trades_come_from_the_tick_history() -> None:
    """Seconds bars are real tick aggregates with classified buy volume."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    adapter, fake = _build(clock, seconds_history_days=2)
    fake.ticks_rows[("2330", _PREV)] = [
        (_local(_PREV, 9, 0, 0, 500_000), 100.0, 50, 0),
        (_local(_PREV, 9, 0, 1, 200_000), 100.5, 2, 1),
        (_local(_PREV, 9, 0, 3), 100.0, 1, 2),
        (_local(_PREV, 13, 30), 101.0, 30, 0),
        (_local(_PREV, 14, 30), 101.0, 7, 2),
    ]
    start = _ms(_PREV, 9, 0)
    got = await adapter.fetch_candle_coverage(_ID, _S1, start, start + 5_000)
    bars = {(c.start_ms - start) // 1000: c for c in got.candles}
    assert sorted(bars) == [0, 1, 2, 3]
    assert bars[0].taker_buy_base_volume is None  # Undetermined (auction).
    assert bars[1].taker_buy_base_volume == _D(2_000)
    assert bars[1].taker_buy_method is market.BuyVolumeMethod.CLASSIFIED
    assert bars[2].base_volume == 0 and bars[3].taker_buy_base_volume == 0
    assert all(
        c.is_final and c.source == sinopac.TICK_SOURCE for c in bars.values()
    )
    assert got.covered == ((start, start + 5_000),) and got.complete
    halves = await adapter.fetch_candles(
        _ID, timeframes.Timeframe.S30, start, start + 60_000
    )
    assert [c.base_volume for c in halves] == [_D(53_000)]
    trades = await adapter.fetch_trades(_ID, start, _ms(_PREV, 15))
    assert [t.trade_id for t in trades] == [
        "2330-20261120-50",
        "2330-20261120-52",
        "2330-20261120-53",
        "2330-20261120-83",
    ]
    assert trades[1].aggressor_side is market.AggressorSide.BUY
    assert sum(1 for c in fake.calls if c.startswith("ticks")) == 1
    old = _ms(datetime.date(2026, 11, 17), 9)
    stale = await adapter.fetch_candle_coverage(_ID, _S1, old, old + 5_000)
    assert stale.candles == () and not stale.complete
    assert (
        await adapter.fetch_candle_coverage(_ID, _S1, start, start)
    ).complete
    fake.failures["ticks"] = fake_shioaji.ShioajiConnectionError()
    other = _ms(datetime.date(2026, 11, 19), 9)
    missing = await adapter.fetch_candle_coverage(
        _ID, _S1, other, other + 5_000
    )
    assert missing.candles == () and not missing.complete
    await adapter.close()


async def test_today_joins_kbars_and_the_tick_stream() -> None:
    """Minutes before the stream come from kbars, later ones from ticks."""
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    adapter, fake = _build(clock)
    fake.kbars_rows[("2330", _DAY)] = [
        _kbar(_DAY, 9, 1, 99.0, 150),
        _kbar(_DAY, 10, 1, 100.0, 1),
        _kbar(_DAY, 10, 2, 100.5, 3),
    ]
    session = sessions.XTAI.session(_DAY)
    assert session is not None
    first = await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms() + 60_000
    )
    assert [c.start_ms for c in first.candles] == [_ms(_DAY, 9, 0)]
    assert first.covered == ((session.open_ms, _ms(_DAY, 10, 0)),)
    assert ("2330", "tick") in fake.subscriptions
    fake.push_tick(_tick(_local(_DAY, 10, 0, 40), "100", 1, 151))
    fake.push_tick(_tick(_local(_DAY, 10, 1, 10), "100.5", 3, 154, 2))
    fake.push_bidask(_book(_local(_DAY, 10, 2, 10)))
    await _settle()
    clock.advance(102_000)  # 10:02:12.
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms() + 60_000
    )
    sources = {c.start_ms: c.source for c in got.candles}
    assert sources == {
        _ms(_DAY, 9, 0): sinopac.KBAR_SOURCE,
        _ms(_DAY, 10, 0): sinopac.KBAR_SOURCE,
        _ms(_DAY, 10, 1): sinopac.TICK_SOURCE,
    }
    assert got.covered == ((session.open_ms, _ms(_DAY, 10, 2)),)
    tick_bar = got.candles[-1]
    assert tick_bar.is_final and tick_bar.taker_buy_base_volume == 0
    assert sum(1 for c in fake.calls if c.startswith("kbars")) == 2
    five = await adapter.fetch_candles(
        _ID, _M5, _ms(_DAY, 10), clock.now_ms() + 60_000
    )
    assert [(c.base_volume, c.is_final) for c in five] == [(_D(4_000), False)]
    # Seconds before the stream: today's tick history joins it once.
    fake.ticks_rows[("2330", _DAY)] = [
        (_local(_DAY, 9, 0, 1), 99.0, 150, 0),
        (_local(_DAY, 10, 0, 40), 100.0, 1, 1),
    ]
    secs = await adapter.fetch_candle_coverage(
        _ID, _S1, _ms(_DAY, 10, 0, 40), _ms(_DAY, 10, 0, 42)
    )
    assert [c.base_volume for c in secs.candles] == [_D(1_000)]
    assert secs.covered == ((_ms(_DAY, 10, 0, 40), _ms(_DAY, 10, 0, 42)),)
    trades = await adapter.fetch_trades(_ID, session.open_ms, clock.now_ms())
    assert [t.trade_id[-3:] for t in trades] == ["150", "151", "154"]
    assert sum(1 for c in fake.calls if c.startswith("ticks")) == 1
    snapshot = await adapter.fetch_snapshot(_ID)
    assert snapshot.source == sinopac.BOOK_SOURCE and snapshot.last == _D(
        "100.5"
    )
    book = await adapter.fetch_order_book(_ID, 1)
    assert book.source == sinopac.BOOK_SOURCE and len(book.bids) == 1
    daily = await adapter.fetch_candles(
        _ID, timeframes.Timeframe.D1, session.open_ms, session.close_ms
    )
    assert daily[-1].source == sinopac.BOOK_SOURCE
    assert daily[-1].base_volume == _D(154_000) and not daily[-1].is_final
    await adapter.close()


async def _next(stream: AsyncIterator[Any], push: Callable[[], None]) -> Any:
    """Pushes stream input once the stream waits, then reads one update.

    Args:
        stream: Stream.
        push: Feeds the fake SDK.

    Returns:
        The next update.
    """
    task = asyncio.ensure_future(anext(stream))
    await _settle()
    push()
    return await asyncio.wait_for(task, 5)


async def _until(
    stream: AsyncIterator[Any],
    push: Callable[[], None],
    done: Callable[[Any], bool],
) -> Any:
    """Pushes stream input, then reads updates until one matches.

    Args:
        stream: Stream.
        push: Feeds the fake SDK.
        done: Predicate of the wanted update.

    Returns:
        The matching update.

    Raises:
        AssertionError: When no update matches.
    """
    update = await _next(stream, push)
    for _ in range(10):
        if done(update):
            return update
        update = await asyncio.wait_for(anext(stream), 5)
    raise AssertionError("no matching update")


async def test_seconds_and_trade_streams_follow_the_ticks() -> None:
    """Live seconds bars become final as bid/ask updates prove delivery."""
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    adapter, fake = _build(clock)
    tracker = health.FeedHealthTracker(instruments.Venue.TWSE)
    stream = adapter.stream_candle_coverage(_ID, _S1, tracker)
    first = asyncio.ensure_future(anext(stream))
    await _settle()
    fake.push_tick(_tick(_local(_DAY, 10, 0, 40), "100", 1, 151))
    fake.push_tick(_tick(_local(_DAY, 10, 0, 41, 500_000), "100.5", 2, 153))
    update = await asyncio.wait_for(first, 5)
    assert [c.start_ms for c in update.candles] == [_ms(_DAY, 10, 0, 41)]
    assert tracker.transport is quality.Transport.NATIVE_WS
    clock.advance(20_000)
    proved = await _until(
        stream,
        lambda: fake.push_bidask(_book(_local(_DAY, 10, 0, 45))),
        lambda update: bool(update.candles),
    )
    assert proved.candles[0].is_final
    assert proved.covered == ((_ms(_DAY, 10, 0, 40) + 1, _ms(_DAY, 10, 0, 43)),)
    fake.push_tick(_tick(_local(_DAY, 10, 0, 50), "101", 1, 160))  # A gap.
    after = await _until(
        stream,
        lambda: fake.push_tick(
            _tick(_local(_DAY, 10, 0, 51, 200_000), "101", 1, 161)
        ),
        lambda update: bool(update.candles),
    )
    # Nothing is filled across the break; the new segment's first second
    # (10:00:50) is partial.
    assert [c.start_ms for c in after.candles] == [_ms(_DAY, 10, 0, 51)]
    trades = adapter.stream_trades(_ID)
    trade = await _next(
        trades,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 0, 52), "101", 1, 162)),
    )
    assert trade.trade_id == "2330-20261123-162"
    fake.push_event(2, "connect failed")
    await _settle()
    with pytest.raises(errors.VenueUnavailableError, match="session event"):
        for _ in range(10):  # Updates queued before the failure first.
            await asyncio.wait_for(anext(stream), 5)
    with pytest.raises(errors.VenueUnavailableError):
        await asyncio.wait_for(anext(trades), 5)
    await adapter.close()


async def test_minute_and_daily_streams_seed_then_follow_ticks() -> None:
    """The minute stream seeds today's kbars; the daily one the reports."""
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    adapter, fake = _build(clock)
    fake.kbars_rows[("2330", _DAY)] = [_kbar(_DAY, 10, 0, 99.0, 5)]
    stream = adapter.stream_candle_coverage(_ID, _M5)
    seeded = await asyncio.wait_for(anext(stream), 5)
    assert [(c.start_ms, c.source) for c in seeded.candles] == [
        (_ms(_DAY, 9, 55), sinopac.KBAR_SOURCE)
    ]
    live = await _next(
        stream,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 0, 40), "100", 1, 6)),
    )
    # The stream starts mid-minute: 10:00 is partial, only the seed counts.
    assert live.candles == ()
    assert live.covered == ((_ms(_DAY, 9), _ms(_DAY, 10)),)
    more = await _until(
        stream,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 1, 5), "100.5", 2, 8)),
        lambda update: bool(update.candles),
    )
    assert [(c.start_ms, c.base_volume) for c in more.candles] == [
        (_ms(_DAY, 10, 0), _D(2_000))
    ]
    clock.advance(60_000)
    tick_minutes = (_ms(_DAY, 10, 1), _ms(_DAY, 10, 2))
    proof = await _until(
        stream,
        lambda: fake.push_bidask(_book(_local(_DAY, 10, 2, 10))),
        lambda update: tick_minutes in update.covered,
    )
    assert proof.covered == ((_ms(_DAY, 9), _ms(_DAY, 10)), tick_minutes)
    gap = await _next(
        stream,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 3), "100", 1, 20)),
    )
    assert gap.candles == ()  # The new segment's first minute is partial.
    assert tick_minutes in gap.covered
    days = adapter.stream_candles(_ID, timeframes.Timeframe.D1)
    today = await _next(
        days,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 3, 5), "100", 1, 21)),
    )
    assert today.source == sinopac.BOOK_SOURCE and not today.is_final
    weeks = adapter.stream_candle_coverage(_ID, timeframes.Timeframe.W1)
    week = await _next(
        weeks,
        lambda: fake.push_tick(_tick(_local(_DAY, 10, 3, 6), "100", 1, 22)),
    )
    assert week.candles[0].timeframe is timeframes.Timeframe.W1
    await stream.aclose()  # type: ignore[attr-defined]
    await adapter.close()


async def test_shioaji_failure_serves_official_data_visibly() -> None:
    """A refused login falls back to MIS, and the router shows it."""
    clock = clock_lib.ManualClock(_ms(_DAY, 10, 0, 30))
    fake = fake_shioaji.FakeShioaji()
    fake.failures["login"] = fake_shioaji.AuthError("bad key")
    adapter, _ = _build(clock, fake)
    session = sessions.XTAI.session(_DAY)
    assert session is not None
    first = await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms() + 60_000
    )
    assert first.candles == ()
    builder = adapter._builders["2330"]  # MIS recording started.
    for offset, lots in ((0, 10), (40_000, 14), (95_000, 15)):
        builder.apply(
            snapshot_bars.Snapshot(
                day=_DAY,
                data_ms=clock.now_ms() + offset,
                cumulative_volume=_D(lots * 1000),
                last_price=_D(100),
                best_bid=_D("99.5"),
                best_ask=_D("100.5"),
            )
        )
    clock.advance(95_000)
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, session.open_ms, clock.now_ms() + 60_000
    )
    assert got.candles and {c.source for c in got.candles} == {twse.MIS_SOURCE}
    caps = adapter.capabilities()
    assert "unavailable (login failed (AuthError))" in caps.notes[-1]
    snapshot = await adapter.fetch_snapshot(_ID)
    assert snapshot.source == twse.MIS_SOURCE
    with pytest.raises(errors.VenueUnavailableError, match="login failed"):
        await anext(adapter.stream_candle_coverage(_ID, _M1))
    store = candle_store.CandleStore()
    router = feed_router.FeedRouter(
        {instruments.Venue.TWSE: adapter},
        store,
        clock,
        poll_interval_s=0.01,
        native_retry_s=3600,
    )
    feed = await router.acquire(_ID, _M1)
    for _ in range(200):
        await asyncio.sleep(0)
        if feed.state is feed_router.SessionState.POLLING:
            break
    snapshot_health = feed.tracker.snapshot(clock.now_ms())
    assert feed.state is feed_router.SessionState.POLLING
    assert snapshot_health.feed_state is quality.FeedState.POLL
    assert snapshot_health.transport is quality.Transport.REST_POLL
    assert snapshot_health.state is quality.HealthState.DEGRADED
    assert "sinopac unavailable: login failed" in (
        snapshot_health.last_error or ""
    )
    await router.close()
    await adapter.close()


async def test_quota_on_closed_sessions_falls_back_to_archived_mis() -> None:
    """A used-up allowance serves archived MIS minutes for the session."""
    clock = clock_lib.ManualClock(_ms(_DAY, 7))
    archive = _archive()
    prev = sessions.XTAI.session(_PREV)
    assert prev is not None
    mis_bar = market.Candle(
        instrument_id=_ID,
        timeframe=_M1,
        start_ms=prev.open_ms,
        end_ms=prev.open_ms + 60_000,
        open=_D(100),
        high=_D(100),
        low=_D(100),
        close=_D(100),
        base_volume=_D(1000),
        is_final=True,
        source=twse.MIS_SOURCE,
        feed_state=quality.FeedState.LIVE,
        received_at_ms=1,
    )
    archive.put(
        _ID,
        twse.MIS_SOURCE,
        [mis_bar],
        [(prev.open_ms, prev.open_ms + 120_000)],
    )
    fake = fake_shioaji.FakeShioaji()
    fake.failures["kbars"] = fake_shioaji.MarketDataQueryUsageLimitError()
    adapter, _ = _build(clock, fake, archive=archive)
    got = await adapter.fetch_candle_coverage(
        _ID, _M1, prev.open_ms, prev.close_ms
    )
    assert [c.source for c in got.candles] == [twse.MIS_SOURCE]
    assert got.covered == ((prev.open_ms, prev.open_ms + 120_000),)
    assert "daily data allowance used up" in adapter.capabilities().notes[-1]
    await adapter.close()


def _bar(start: int, source: str, final: bool = True) -> market.Candle:
    """Builds a one-minute bar.

    Args:
        start: Start.
        source: Source label.
        final: Finality.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=_M1,
        start_ms=start,
        end_ms=start + 60_000,
        open=_D(1),
        high=_D(1),
        low=_D(1),
        close=_D(1),
        base_volume=_D(1),
        is_final=final,
        source=source,
        feed_state=quality.FeedState.LIVE,
        received_at_ms=1,
    )


def test_bars_never_mix_sources() -> None:
    """Better sources claim minutes; aggregates spanning two are dropped."""
    nine = _ms(_DAY, 9)
    best = (
        [_bar(nine + 60_000, "sinopac.ticks")],
        [(nine + 60_000, nine + 180_000)],
    )
    kbars = (
        [_bar(nine, "sinopac.kbars"), _bar(nine + 60_000, "sinopac.kbars")],
        [(nine, nine + 120_000)],
    )
    mis = (
        [_bar(nine + 120_000, "twse.mis"), _bar(nine + 180_000, "twse.mis")],
        [(nine, nine + 240_000)],
    )
    bars, covered = twse.combine_layers([best, kbars, mis])
    assert [(b.start_ms - nine, b.source) for b in bars] == [
        (0, "sinopac.kbars"),
        (60_000, "sinopac.ticks"),
        (180_000, "twse.mis"),
    ]
    assert covered == [(nine, nine + 240_000)]
    rows, spans = twse.aggregate_minutes(bars, _M5, covered, nine + 600_000)
    assert rows == [] and spans == []  # 09:00-09:05 would mix sources.
    same, spans = twse.aggregate_minutes(bars[:2], _M5, covered, nine + 600_000)
    assert len(same) == 1 and spans == [(nine, nine + 240_000)]
    assert not same[0].is_final  # Coverage ends before the bar does.
    assert twse.aggregate_minutes(bars, _M1, covered, 0) == (bars, covered)
    skipped, _ = twse.aggregate_minutes(
        bars[:1], _M5, covered, nine, covered_to=lambda start: None
    )
    assert skipped == []
    assert twse.provider(bars[2]) == "twse"
